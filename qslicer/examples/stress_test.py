"""A deliberately large circuit, for testing the viewer rather than the physics.

Six qubits, a full QFT, an entangling ladder and a second rotation layer --
around sixty instructions and a dense edge set. Nothing here computes anything
meaningful; it exists so the graph has enough in it to be awkward.

What to look for: the Range control. At full width the graph is a strip; narrow
it to ten or twenty instructions and it becomes readable again. This is also
the example to use when checking that pan, zoom and node dragging still behave
once there is real content on screen.
"""

import numpy as np
from qiskit import QuantumCircuit

n = 6
qc = QuantumCircuit(n, n)

# Layer 1: put everything in motion.
for qubit in range(n):
    qc.h(qubit)
    qc.rz(np.pi / (qubit + 2), qubit)

# Layer 2: a full QFT over all six wires.
for target in range(n):
    qc.h(target)
    for control in range(target + 1, n):
        qc.cp(np.pi / 2 ** (control - target), control, target)

# Layer 3: entangling ladder back down.
for qubit in range(n - 1):
    qc.cx(qubit, qubit + 1)

qc.measure(range(n), range(n))

print(qc.draw())
