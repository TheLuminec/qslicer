"""Uncomputation: a gate followed at once by its own inverse.

The two x gates on q1 cancel: the second undoes the first, leaving the state
exactly as it was. They are adjacent on the wire and x is its own inverse, so
the graph links the pair as mirrors and the dependence checks report QD08 on
the second one.

What to look for: the mirror link between instructions #2 and #3, and the QD08
note on #3. Both gates still appear in a backward slice.
Compare with ancilla_uncompute.py, where the same idea is expressed with
Toffolis.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)

qc.h(0)
qc.cx(0, 1)

# These two cancel: a mirror pair, and QD08.
qc.x(1)
qc.x(1)

qc.measure([0, 1], [0, 1])

print(qc.draw())
