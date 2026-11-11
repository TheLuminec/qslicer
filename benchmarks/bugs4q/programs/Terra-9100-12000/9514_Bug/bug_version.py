from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.circuit.library import RXGate
from qiskit import Aer, transpile

beta = Parameter('θ')
circ = QuantumCircuit(3)
circ.crx(beta, 2, 0)
c1 = circ.control(1)
c1.measure_all()
backend = Aer.get_backend('aer_simulator')
c = transpile(c1, backend)
circuits=c.bind_parameters({c.parameters[0]: 2})
job = backend.run(circuits)
counts = job.result().get_counts()
print(counts)

