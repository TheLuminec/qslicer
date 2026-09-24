"""A variational ansatz with its parameters bound to concrete angles.

Hardware-efficient ansatze like this one are the workhorse of VQE and QAOA:
layers of single-qubit rotations separated by a fixed entangling pattern, with
a classical optimiser choosing the angles.

What to look for: lots of rotation gates, so a good test of how the graph
handles parameters, and of a phase rule if you enable one. Also note that the
entangling layer makes every qubit depend on every other after two layers.
"""

import numpy as np
from qiskit import QuantumCircuit

n = 3
layers = 2
rng = np.random.default_rng(7)

qc = QuantumCircuit(n, n)

for layer in range(layers):
    for qubit in range(n):
        qc.ry(float(rng.uniform(0, np.pi)), qubit)
        qc.rz(float(rng.uniform(0, 2 * np.pi)), qubit)
    for qubit in range(n - 1):
        qc.cx(qubit, qubit + 1)

qc.measure(range(n), range(n))

print(qc.draw())
