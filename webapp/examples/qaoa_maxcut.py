"""One QAOA layer for MaxCut on a four-node ring.

Each graph edge becomes an rzz interaction (the cost layer), followed by an rx
on every qubit (the mixer). A real run alternates many such layers and lets an
optimiser tune gamma and beta.

What to look for: rzz gates are both entangling and phase-carrying, which
makes this the example where the disabled entanglement and phase rules would
change the graph most.
"""

import numpy as np
from qiskit import QuantumCircuit

EDGES = [(0, 1), (1, 2), (2, 3), (3, 0)]
gamma = np.pi / 4
beta = np.pi / 8

qc = QuantumCircuit(4, 4)

qc.h(range(4))

# Cost layer: one interaction per edge of the graph.
for left, right in EDGES:
    qc.rzz(2 * gamma, left, right)

# Mixer layer.
for qubit in range(4):
    qc.rx(2 * beta, qubit)

qc.measure(range(4), range(4))

print(qc.draw())
