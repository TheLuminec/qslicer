"""GHZ state: entanglement spread across three qubits.

The chain of CNOTs grows one entangled group outwards from q0. Measuring any
one qubit collapses all three, which is why the entanglement analyzer closes
the whole group on the first measurement it sees.

What to look for: the group in the Entanglement tab picks up a qubit at each
CNOT, and the def-use chain through q0 is what carries the dependency.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(3, 3)

qc.h(0)
qc.cx(0, 1)
qc.cx(0, 2)

qc.measure([0, 1, 2], [0, 1, 2])

print(qc.draw())
