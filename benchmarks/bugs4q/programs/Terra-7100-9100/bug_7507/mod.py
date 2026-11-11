#change
from qiskit.opflow import I, X, Y, Z
from qiskit.quantum_info import Operator

op = (I ^ X ^ Y) + (I ^ Y ^ X) - (Z ^ Z ^ I)

bound = circuit.bind_parameters([value])


#to
from qiskit.quantum_info import Operator, SparsePauliOp

op = SparsePauliOp.from_list([("IXY", 1), ("IYX", 1), ("ZZI", -1)])

bound = circuit.assign_parameters([value]