from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit.library import XGate, HGate, Measure

# double measurement through a variable index
qc_a = QuantumCircuit(2, 2)
k = 1
qc_a.h(k)
qc_a.measure(k, 0)
qc_a.measure(1, 1)

# measurement then reset then measurement: reset is an operator in between
qc_b = QuantumCircuit(1, 2)
qc_b.x(0)
qc_b.measure(0, 0)
qc_b.reset(0)
qc_b.measure(0, 1)

# a barrier is not an operator for LintQ
qc_c = QuantumCircuit(1, 2)
qc_c.h(0)
qc_c.measure(0, 0)
qc_c.barrier()
qc_c.measure(0, 1)

# gate after measurement in only one branch
qc_d = QuantumCircuit(2, 2)
qc_d.h(0)
qc_d.measure(0, 0)
if k > 0:
    qc_d.x(0)
else:
    qc_d.measure(1, 1)

# conditional gate after measurement is fine
cr = ClassicalRegister(1)
qr = QuantumRegister(1)
qc_e = QuantumCircuit(qr, cr)
qc_e.h(0)
qc_e.measure(0, 0)
qc_e.x(0).c_if(cr, 1)

# gate objects appended after a measurement
qc_f = QuantumCircuit(2, 2)
qc_f.append(HGate(), [0])
qc_f.append(Measure(), [0], [0])
qc_f.append(XGate(), [0])
qc_f.measure(1, 1)

# measure_all then a gate
qc_g = QuantumCircuit(2)
qc_g.h(0)
qc_g.cx(0, 1)
qc_g.measure_all()
qc_g.x(1)

# a while loop that measures then rotates
qc_h = QuantumCircuit(1, 1)
n = 0
while n < 3:
    qc_h.ry(0.3, 0)
    qc_h.measure(0, 0)
    n += 1

# list of qubits
qc_i = QuantumCircuit(3, 3)
qc_i.h([0, 1, 2])
qc_i.measure([0, 1, 2], [0, 1, 2])
qc_i.cx(0, 2)

# arithmetic index
qc_j = QuantumCircuit(3, 3)
qc_j.h(1 + 1)
qc_j.measure(2, 2)
qc_j.z(3 - 1)
qc_j.measure(0, 0)
qc_j.measure(1, 1)
