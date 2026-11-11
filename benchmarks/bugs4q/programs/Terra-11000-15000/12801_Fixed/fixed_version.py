from qiskit import QuantumCircuit
from qiskit.circuit import Parameter

theta1 = Parameter('θ1')

qc = QuantumCircuit(5)
qc.ry(theta1, 4)
qc.control(4, ctrl_state=0, annotated=True)
qc.draw() 