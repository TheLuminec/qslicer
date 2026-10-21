import math

from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.quantum_info import Operator

x = Parameter("x")
custom = QuantumCircuit(1, global_phase=x)
base = QuantumCircuit(1)
base.append(custom, [0], [])

Operator(custom.assign_parameters({x: math.pi}))