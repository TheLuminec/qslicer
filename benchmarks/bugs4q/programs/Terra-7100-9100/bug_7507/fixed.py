from qiskit.circuit import QuantumCircuit, Parameter
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.synthesis import SuzukiTrotter, MatrixExponential
from qiskit.quantum_info import Operator, SparsePauliOp

# evolution time and operator we evolve
time = Parameter("t")
op = SparsePauliOp.from_list([("IXY", 1), ("IYX", 1), ("ZZI", -1)])

# evolution gate
synth = MatrixExponential()
evo = PauliEvolutionGate(op, time=time, synthesis=synth)

# plug into circuit
circuit = QuantumCircuit(op.num_qubits)
circuit.append(evo, range(op.num_qubits))
print(circuit.draw())

# bind time to some value and obtain matrix
value = 0.23
bound = circuit.assign_parameters([value])  # or {time: value}

print(bound.decompose())