from qiskit.circuit import QuantumCircuit, Parameter
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.synthesis import SuzukiTrotter, MatrixExponential
from qiskit.opflow import I, X, Y, Z
from qiskit.quantum_info import Operator

# evolution time and operator we evolve
time = Parameter("t")
op = (I ^ X ^ Y) + (I ^ Y ^ X) - (Z ^ Z ^ I)

# evolution gate
synth = MatrixExponential()
evo = PauliEvolutionGate(op, time=time, synthesis=synth)

# plug into circuit
circuit = QuantumCircuit(op.num_qubits)
circuit.append(evo, range(op.num_qubits))
print(circuit.draw())

# bind time to some value and obtain matrix
value = 0.23
bound = circuit.bind_parameters([value])  # or {time: value}

print(bound.decompose())


#Qiskit Terra version:0.19.0
#url：https://github.com/Qiskit/qiskit/issues/7507
#type：RuntimeError