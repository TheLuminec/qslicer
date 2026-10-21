from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister, transpile
from qiskit_aer import Aer
from qiskit.transpiler.passes import *
from qiskit.transpiler import PassManager
import numpy as np

qreg = QuantumRegister(4)
creg = ClassicalRegister(4)
qc = QuantumCircuit(qreg, creg)

qc.iswap(3, 1)

qr = QuantumRegister(2)
cr = ClassicalRegister(2)
qc.add_register(qr)
qc.add_register(cr)
qc.measure(qr[0], cr[0])
qc.measure(qr[1], cr[1])
with qc.if_test((cr, 0b00)):
    qc.cx(3, 1)

qc.h(1)

qc.measure(qreg[0], creg[0])
qc.measure(qreg[1], creg[1])
qc.measure(qreg[2], creg[2])
qc.measure(qreg[3], creg[3])

simulator = Aer.get_backend("aer_simulator")

p = PassManager([CollectCliffords(), CommutativeInverseCancellation()])
qc = p.run(qc)

compiled_circuit = transpile(qc, backend=simulator)

job = simulator.run(compiled_circuit, shots=500)
result = job.result().get_counts()
print(result)