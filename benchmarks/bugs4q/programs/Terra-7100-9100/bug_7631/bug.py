from qiskit import QuantumCircuit
from qiskit.providers.aer import AerSimulator
from qiskit.quantum_info import Statevector

sim = AerSimulator(method='statevector')

#Does not work and results in mentioned error
circ = QuantumCircuit(3)
circ.initialize('+++')
circ.save_statevector()
sv = sim.run(circ).result().get_statevector(circ)

#Qiskit Terra version:0.19.1
#url：https://github.com/Qiskit/qiskit/issues/7631
#type：QiskitError