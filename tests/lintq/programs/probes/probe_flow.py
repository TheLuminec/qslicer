import sys
import qiskit as qk
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit import QuantumCircuit as QC
from qiskit.circuit.library import RealAmplitudes

flag = len(sys.argv) > 1

# the branch cannot be decided: both sides count
qc_a = QuantumCircuit(2, 2)
qc_a.h(0)
qc_a.measure(0, 0)
if flag:
    qc_a.x(0)
else:
    qc_a.measure(1, 1)

# if True / if False
qc_b = QuantumCircuit(2, 2)
qc_b.h(0)
qc_b.measure(0, 0)
if True:
    qc_b.x(0)
if False:
    qc_b.y(0)

# while True with a break: the measurement after the loop
qc_c = QuantumCircuit(1, 1)
while True:
    qc_c.h(0)
    break
qc_c.measure(0, 0)
qc_c.z(0)

# try/except
qc_d = QuantumCircuit(1, 2)
try:
    qc_d.measure(0, 0)
except Exception:
    qc_d.h(0)
qc_d.measure(0, 1)

# alias imports
qc_e = qk.QuantumCircuit(3, 1)
qc_e.h(0)
qc_f = QC(2, 2)
qc_f.measure(0, 0)
qc_f.x(0)

# library circuit: a circuit with a size
ansatz = RealAmplitudes(4, reps=1)
bound = ansatz.assign_parameters([0.1] * 8)


# a method building a circuit
class Builder:
    def make(self):
        qc = QuantumCircuit(2, 2)
        qc.measure(0, 0)
        qc.measure(0, 1)
        return qc


# whole registers
qr = QuantumRegister(2)
cr = ClassicalRegister(2)
qc_g = QuantumCircuit(qr, cr)
qc_g.h(qr)
qc_g.measure(qr, cr)
qc_g.x(qr)

# measuring twice in a loop
qc_h = QuantumCircuit(1, 3)
for step in range(3):
    qc_h.measure(0, step)

# initialize and unitary
qc_i = QuantumCircuit(2, 2)
qc_i.initialize([1, 0, 0, 0], [0, 1])
qc_i.measure(0, 0)
qc_j = QuantumCircuit(2, 2)
qc_j.unitary([[1, 0], [0, 1]], [0])
qc_j.measure(0, 0)
qc_j.measure(1, 1)

# comprehension of circuits
circuits = [QuantumCircuit(3) for _ in range(2)]
