from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
from qiskit import execute, Aer
qr = QuantumRegister(1, "q")
cr0 = ClassicalRegister(4, "c0")
cr1 = ClassicalRegister(4, "c1")
ref = QuantumCircuit(qr, cr0, cr1)
ref.h(qr[0])
ref.x(qr[0]).c_if(cr1, 1)
ref.measure(qr[0], cr0[0])
backend = Aer.get_backend('qasm_simulator')
counts = execute(ref, backend).result().get_counts()
print(counts)