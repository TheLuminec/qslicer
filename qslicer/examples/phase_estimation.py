"""Quantum phase estimation of the T gate.

T applies a phase of e^(i*pi/4), so its eigenphase is 1/8. Three counting
qubits read that back as the binary fraction 0.001.

What to look for: the counting register is entangled with the eigenstate
qubit by the controlled powers of T, then disentangled by the inverse QFT.
The slice from any counting measurement runs back through every controlled
rotation that touched it.
"""

import numpy as np
from qiskit import QuantumCircuit

counting = 3
qc = QuantumCircuit(counting + 1, counting)

# Eigenstate of T is |1).
qc.x(counting)

qc.h(range(counting))

# Controlled-T^(2^k), written as controlled phase rotations.
for k in range(counting):
    for _ in range(2 ** k):
        qc.cp(np.pi / 4, k, counting)

# Inverse QFT on the counting register.
for qubit in range(counting // 2):
    qc.swap(qubit, counting - qubit - 1)
for target in reversed(range(counting)):
    for control in reversed(range(target + 1, counting)):
        qc.cp(-np.pi / 2 ** (control - target), control, target)
    qc.h(target)

qc.measure(range(counting), range(counting))

print(qc.draw())
