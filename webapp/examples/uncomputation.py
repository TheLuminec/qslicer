"""Uncomputation that the entanglement analyser actually detects.

The two x gates on q1 cancel: the second undoes the first, leaving the state
exactly as it was. The analyser spots this because they are adjacent and
identical inside the same entanglement group, and records the second one in
that group's uncomputed list rather than its computed list.

What to look for: open the Entanglement tab and find the group holding q0 and
q1. Instruction #3 appears under "uncomputed", not "computed". Compare with
ancilla_uncompute.py, where the same idea is expressed with two-qubit gates
and the analyser does not catch it.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)

qc.h(0)
qc.cx(0, 1)

# These two cancel, and the analyser notices.
qc.x(1)
qc.x(1)

qc.measure([0, 1], [0, 1])

print(qc.draw())
