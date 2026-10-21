import qiskit_aer
from qiskit import QuantumCircuit, Aer
from qiskit.circuit.library import Diagonal

qc = QuantumCircuit(3)
qc.append(Diagonal([1,1,-1,1,1,1,1,1]), [0, 1, 2])
backend = Aer.get_backend('unitary_simulator')
job = backend.run(qc)
print(job.result().get_unitary(qc, decimals=3))

#Qiskit Terra version:0.21.2
#url：https://github.com/Qiskit/qiskit/issues/8918
#type：QiskitError