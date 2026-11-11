from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.circuit.library import RXGate
from qiskit import Aer, transpile

beta = Parameter('θ')
gate = RXGate(beta).control(2)
circ = QuantumCircuit(3)
circ.append(gate, [0, 1, 2])
circ.measure_all()

backend = Aer.get_backend('aer_simulator')
bound = circ.bind_parameters([2])
tqc = transpile(bound, backend)
job = backend.run(tqc)
counts = job.result().get_counts()
print(counts)
