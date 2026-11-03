from qiskit import QuantumCircuit

def bell(qc, a, b):
    qc.h(a)
    qc.cx(a, b)

qc = QuantumCircuit(4, 3)
qc.ry(0.6, 0)
bell(qc, 1, 2)
qc.cx(0, 1)
qc.h(0)
qc.measure(0, 0)
qc.measure(1, 1)
with qc.if_test((qc.clbits[1], 1)):
    qc.x(2)
with qc.if_test((qc.clbits[0], 1)):
    qc.z(2)
for _ in range(2):
    qc.x(3)
qc.measure(2, 2)
