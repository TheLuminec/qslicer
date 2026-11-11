"""A circuit that runs, but that the dependence checks have opinions about.

Nothing here is a Python error and nothing raises at build time, so the
analysis succeeds and you still get a graph. The Diagnostics panel is the
point: this file trips three checks at once, marked on the graph's nodes too.

Expect: no measurement at all (QD06), a declared qubit nothing ever touches
(QD07), and a pair of identical adjacent gates that cancel (QD08).
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(4)

qc.h(0)

# These two cancel each other out.
qc.x(1)
qc.x(1)

qc.cx(0, 1)
qc.cz(0, 2)

# q3 is declared but never used, and nothing is ever measured.

print(qc.draw())
