import numpy as np
from scipy.optimize import minimize

from qiskit import QuantumCircuit
from qiskit.circuit import ParameterVector
from qiskit.quantum_info import SparsePauliOp
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.transpiler import generate_preset_pass_manager


# ------------------------------------------------------------
# Problem definition: Max-Cut on a graph
# ------------------------------------------------------------
# Vertices: 0,1,2,3,4
# Edges define the Max-Cut objective.
EDGES = [
    (0, 1),
    (0, 2),
    (1, 2),
    (1, 3),
    (2, 4),
    (3, 4),
]
N_QUBITS = 5


# ------------------------------------------------------------
# Build the Max-Cut cost Hamiltonian
# MaxCut edge term for (i,j): (1 - Z_i Z_j)/2
# Total cost = sum over edges
#
# Since Estimator returns expectation values of observables,
# we encode the full Hamiltonian as a SparsePauliOp.
# ------------------------------------------------------------
def build_maxcut_hamiltonian(n_qubits: int, edges: list[tuple[int, int]]) -> SparsePauliOp:
    paulis = []
    coeffs = []

    # Constant offset: +1/2 per edge
    identity = "I" * n_qubits
    paulis.append(identity)
    coeffs.append(len(edges) * 0.5)

    # -1/2 * Z_i Z_j for each edge
    for i, j in edges:
        label = ["I"] * n_qubits
        # Qiskit Pauli strings are ordered left-to-right as q_(n-1)...q_0
        label[n_qubits - 1 - i] = "Z"
        label[n_qubits - 1 - j] = "Z"
        paulis.append("".join(label))
        coeffs.append(-0.5)

    return SparsePauliOp(paulis, coeffs=np.array(coeffs, dtype=float))


# ------------------------------------------------------------
# Manual QAOA ansatz
# For each layer:
#   1) Cost unitary U_C(gamma)
#   2) Mixer unitary U_B(beta)
#
# Cost unitary for one edge (i,j):
#   exp(-i * gamma * (1 - Z_i Z_j)/2)
# Up to a global phase, this is equivalent to an RZZ-like evolution.
#
# We implement ZZ evolution with CX-RZ-CX:
#   CX(i,j), RZ(2*gamma, j), CX(i,j)
#
# Mixer:
#   RX(2*beta) on each qubit
# ------------------------------------------------------------
def build_qaoa_circuit(n_qubits: int, edges: list[tuple[int, int]], p: int) -> tuple[QuantumCircuit, ParameterVector, ParameterVector]:
    gammas = ParameterVector("gamma", p)
    betas = ParameterVector("beta", p)

    qc = QuantumCircuit(n_qubits)

    # Initial state |+>^n
    qc.h(range(n_qubits))

    for layer in range(p):
        gamma = gammas[layer]
        beta = betas[layer]

        # Cost unitary
        for i, j in edges:
            qc.cx(i, j)
            qc.rz(2 * gamma, j)
            qc.cx(i, j)

        # Mixer unitary
        for q in range(n_qubits):
            qc.rx(2 * beta, q)

    return qc, gammas, betas


# ------------------------------------------------------------
# Utility: bind parameters in the right order
# ------------------------------------------------------------
def parameter_vector_to_list(gammas, betas, x):
    p = len(gammas)
    gamma_vals = x[:p]
    beta_vals = x[p:]
    return list(gamma_vals) + list(beta_vals)


# ------------------------------------------------------------
# Objective function using StatevectorEstimator
#
# Current Qiskit docs describe StatevectorEstimator as the local
# exact-reference Estimator primitive for expectation values. We
# keep this circuit measurement-free because Estimator requires it. :contentReference[oaicite:1]{index=1}
# ------------------------------------------------------------
class QAOAObjective:
    def __init__(self, circuit, hamiltonian, gammas, betas):
        self.circuit = circuit
        self.hamiltonian = hamiltonian
        self.gammas = gammas
        self.betas = betas
        self.estimator = StatevectorEstimator()
        self.history = []

    def __call__(self, x):
        params = [parameter_vector_to_list(self.gammas, self.betas, x)]

        # EstimatorV2-style pub: (circuit, observable, parameter_values)
        job = self.estimator.run([(self.circuit, self.hamiltonian, params)])
        result = job.result()

        energy = float(result[0].data.evs[0])
        self.history.append(energy)

        # For Max-Cut, larger cost is better, so minimize negative cost
        return -energy


# ------------------------------------------------------------
# Solve the problem
# ------------------------------------------------------------
def solve_qaoa_maxcut(n_qubits: int, edges: list[tuple[int, int]], p: int = 3):
    hamiltonian = build_maxcut_hamiltonian(n_qubits, edges)
    circuit, gammas, betas = build_qaoa_circuit(n_qubits, edges, p)

    # Optional transpilation / optimization pass
    # IBM docs show generate_preset_pass_manager as the standard route
    # for producing optimized/ISA-ready circuits. :contentReference[oaicite:2]{index=2}
    pm = generate_preset_pass_manager(optimization_level=2)
    transpiled_circuit = pm.run(circuit)

    # The transpiler may relabel qubits/layouts; align observable layout if needed
    isa_hamiltonian = hamiltonian.apply_layout(transpiled_circuit.layout)

    objective = QAOAObjective(transpiled_circuit, isa_hamiltonian, gammas, betas)

    # Random initial point
    rng = np.random.default_rng(7)
    x0 = np.concatenate([
        rng.uniform(0, np.pi, size=p),       # gammas
        rng.uniform(0, np.pi / 2, size=p),   # betas
    ])

    result = minimize(
        objective,
        x0=x0,
        method="COBYLA",
        options={"maxiter": 120, "rhobeg": 0.3}
    )

    best_x = result.x
    best_cost = -result.fun

    return {
        "transpiled_circuit": transpiled_circuit,
        "hamiltonian": isa_hamiltonian,
        "optimization_result": result,
        "best_cost": best_cost,
        "best_gammas": best_x[:p],
        "best_betas": best_x[p:],
        "history": objective.history,
    }


# ------------------------------------------------------------
# Sample the final circuit to get likely bitstrings
#
# IBM docs note Sampler circuits should include measurements, while
# Estimator circuits should not. We therefore create a measured copy
# only for sampling. :contentReference[oaicite:3]{index=3}
# ------------------------------------------------------------
def sample_best_bitstrings(transpiled_circuit: QuantumCircuit, gammas, betas, best_x, shots: int = 4000):
    measured = transpiled_circuit.copy()
    measured.measure_all()

    param_values = [parameter_vector_to_list(gammas, betas, best_x)]

    sampler = StatevectorSampler()
    job = sampler.run([(measured, param_values)], shots=shots)
    pub_result = job.result()[0]

    # Current Sampler pub results expose classical register data.
    # For measure_all(), the default classical register is usually "meas".
    data_bin = pub_result.data.meas.get_counts()
    return dict(sorted(data_bin.items(), key=lambda kv: kv[1], reverse=True))


# ------------------------------------------------------------
# Classical scoring for Max-Cut bitstrings
# ------------------------------------------------------------
def bitstring_cut_value(bitstring: str, edges: list[tuple[int, int]]) -> int:
    # bitstring arrives as q_(n-1)...q_0, so flip index mapping
    bits = bitstring[::-1]
    score = 0
    for i, j in edges:
        if bits[i] != bits[j]:
            score += 1
    return score



p = 3
hamiltonian = build_maxcut_hamiltonian(N_QUBITS, EDGES)
circuit, gammas, betas = build_qaoa_circuit(N_QUBITS, EDGES, p)

solution = solve_qaoa_maxcut(N_QUBITS, EDGES, p=p)

print("Best expected cut value:", solution["best_cost"])
print("Best gammas:", solution["best_gammas"])
print("Best betas:", solution["best_betas"])
print("\nTranspiled circuit:")
print(solution["transpiled_circuit"].draw())

counts = sample_best_bitstrings(
    solution["transpiled_circuit"],
    gammas,
    betas,
    np.concatenate([solution["best_gammas"], solution["best_betas"]]),
    shots=4000,
)

print("\nTop sampled bitstrings:")
top_items = list(counts.items())[:10]
for bitstring, count in top_items:
    cut_val = bitstring_cut_value(bitstring, EDGES)
    print(f"{bitstring} : {count:4d} shots, cut={cut_val}")

best_sampled = max(counts, key=lambda b: bitstring_cut_value(b, EDGES))
print("\nBest sampled solution:")
print("bitstring =", best_sampled)
print("cut value =", bitstring_cut_value(best_sampled, EDGES))