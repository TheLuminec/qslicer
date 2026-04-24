import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator


def build_teleportation_circuit(theta=None, varphi=None):
    """Build a small teleportation circuit with mid-circuit measurement.

    Qubit 0: Alice's unknown state
    Qubit 1: Alice's half of the Bell pair
    Qubit 2: Bob's half of the Bell pair

    Classical bits 0 and 1 store Alice's mid-circuit measurements.
    Classical bit 2 stores Bob's final verification measurement.
    """
    if theta is None or varphi is None:
        np.random.seed(1)
        theta = np.random.uniform(0.0, 1.0) * np.pi
        varphi = np.random.uniform(0.0, 2.0) * np.pi

    qc = QuantumCircuit(3, 3)

    # Prepare Alice's unknown state on qubit 0.
    qc.u(theta, varphi, 0.0, 0)
    qc.barrier(label="prepare_psi")

    # Create Bell pair between qubits 1 and 2.
    qc.h(1)
    qc.cx(1, 2)
    qc.barrier(label="create_bell_pair")

    # Bell-basis operations on Alice's side.
    qc.cx(0, 1)
    qc.h(0)
    qc.barrier(label="alice_bell_measurement")

    # Mid-circuit measurements.
    qc.measure(0, 0)
    qc.measure(1, 1)
    qc.barrier(label="mid_circuit_measurements")

    # Classical feed-forward corrections on Bob's qubit.
    with qc.if_test((qc.clbits[0], 1)):
        qc.z(2)
    with qc.if_test((qc.clbits[1], 1)):
        qc.x(2)
    qc.barrier(label="bob_corrections")

    # Verify teleportation by undoing the original state prep on Bob's qubit.
    qc.u(theta, varphi, 0.0, 2).inverse()
    qc.measure(2, 2)

    return qc, theta, varphi


qc, theta, varphi = build_teleportation_circuit()

print(f"theta = {theta}")
print(f"varphi = {varphi}")
print(qc.draw())

sim = AerSimulator()
compiled = transpile(qc, sim)
result = sim.run(compiled, shots=2000).result()
counts = result.get_counts()

print("Counts:", counts)
