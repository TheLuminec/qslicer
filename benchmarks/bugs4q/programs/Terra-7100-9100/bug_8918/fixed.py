from qiskit import QuantumCircuit, Aer, transpile
from qiskit.circuit.library import Diagonal

qc = QuantumCircuit(3)
qc.append(Diagonal([1,1,-1,1,1,1,1,1]), [0, 1, 2])
backend = Aer.get_backend('unitary_simulator')
tqc = transpile(qc, backend)
job = backend.run(tqc)
print(job.result().get_unitary(tqc, decimals=3))