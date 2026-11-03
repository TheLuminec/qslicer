"""A three-qubit QFT, written out gate by gate.

The QFT maps amplitudes into the frequency domain; it is the engine inside
phase estimation and Shor's algorithm. Written explicitly here rather than
pulled from qiskit.circuit.library so every controlled-phase rotation shows up
as its own node.

What to look for: a dense web of cp edges. Every qubit ends up depending on
every earlier one, which is exactly what makes a QFT expensive to slice
usefully -- the answer is almost always "all of it".
"""

import numpy as np
from qiskit import QuantumCircuit

n = 3
qc = QuantumCircuit(n, n)

# Some input state to transform.
qc.x(0)
qc.h(2)

# QFT proper.
for target in range(n):
    qc.h(target)
    for control in range(target + 1, n):
        qc.cp(np.pi / 2 ** (control - target), control, target)

# Reverse the qubit order the QFT leaves behind.
for qubit in range(n // 2):
    qc.swap(qubit, n - qubit - 1)

qc.measure(range(n), range(n))

print(qc.draw())
