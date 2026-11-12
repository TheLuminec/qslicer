"""Grover search over two qubits, marking |11).

With four possible answers, one Grover iteration is enough to make the marked
state certain. The oracle flips the phase of |11) and the diffuser turns that
phase difference into amplitude.

What to look for: the smallest complete algorithm in this collection, which
makes it a good place to read every edge in the graph. A backward slice from
either measurement (#12, #13) is 11 of 14: the diffuser's closing gates
on the *other* wire cannot affect the bit you sliced from, so they drop out.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)

qc.h([0, 1])

# Oracle: phase-flip the |11) state.
qc.cz(0, 1)

# Diffuser: reflect about the average amplitude.
qc.h([0, 1])
qc.x([0, 1])
qc.cz(0, 1)
qc.x([0, 1])
qc.h([0, 1])

qc.measure([0, 1], [0, 1])

print(qc.draw())
