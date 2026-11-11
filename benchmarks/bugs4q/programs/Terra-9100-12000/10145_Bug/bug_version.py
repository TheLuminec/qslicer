rom qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from qiskit.transpiler import CouplingMap
from qiskit import transpile

c1 = QuantumCircuit(3)
c1.cnot(0, 2)
coupling_map = CouplingMap([[0, 1], [1, 2]])
c2 = transpile(c1, coupling_map=coupling_map)
print(c2.layout.final_layout is None)  # True