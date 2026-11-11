from qiskit import QuantumCircuit
from qiskit.providers.aer import AerSimulator
from qiskit.quantum_info import Statevector

sim = AerSimulator(method='statevector')
circ = QuantumCircuit(3)
circ.initialize(Statevector.from_label('+++'))
circ.save_statevector()
sv = sim.run(circ).result().get_statevector(circ)