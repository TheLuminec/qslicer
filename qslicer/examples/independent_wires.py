"""Two qubits that never interact.

There is no two-qubit gate here, so nothing entangles: q0 is a coin flip and
q1 is deterministically 1. This is the control case for slicing -- a slice
from one measurement must never reach the other wire.

What to look for: slicing backward from the q1 measurement (#3) gives two
instructions, not four. If it ever gives four, a dependency rule is too eager.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)

# Wire 0: superposition.
qc.h(0)

# Wire 1: a plain bit flip, entirely unrelated to wire 0.
qc.x(1)

qc.measure(0, 0)
qc.measure(1, 1)

print(qc.draw())
