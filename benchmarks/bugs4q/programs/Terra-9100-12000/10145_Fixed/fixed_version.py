from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from qiskit.transpiler import CouplingMap
from qiskit.transpiler.passes.routing.lookahead_swap import LookaheadSwap

coupling_map = CouplingMap([[0, 1], [1, 2]])
c2 = transpile(c1, coupling_map=coupling_map)
to_layout = Layout.generate_trivial_layout(*c2.qregs)
c3 = LayoutTransformation(coupling_map, c2.layout.final_layout, to_layout)(c2)
print(Operator(c1).equiv(Operator(c2)))  # False
print(Operator(c1).equiv(Operator(c3)))  # Depends on input