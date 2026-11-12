"""Swap test: how similar are two quantum states?

The controlled swap plus two Hadamards turn the overlap between the states on
q1 and q2 into a measurement probability on the control qubit q0. Identical
states always read 0; orthogonal states read 0 or 1 with equal probability.

Here the two states are deliberately different, so the control is biased.

What to look for: the cswap (#3), a three-qubit gate drawn as one node across
all three wires. The slice from the measurement reaches every instruction.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(3, 1)

# Two states to compare.
qc.h(1)
qc.x(2)

# The test itself.
qc.h(0)
qc.cswap(0, 1, 2)
qc.h(0)

qc.measure(0, 0)

print(qc.draw())
