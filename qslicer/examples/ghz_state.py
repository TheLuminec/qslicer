"""GHZ state: entanglement spread across three qubits.

The chain of CNOTs spreads the entanglement outwards from q0, so each
measurement's outcome is correlated with the other two qubits.

What to look for: a backward slice from any measurement marks the other two
qubits as possibly correlated, and the def-use chain through q0 is what
carries the dependency.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(3, 3)

qc.h(0)
qc.cx(0, 1)
qc.cx(0, 2)

qc.measure([0, 1, 2], [0, 1, 2])

print(qc.draw())
