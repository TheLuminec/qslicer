"""Teleportation: move a state from q0 to q2 using entanglement and two bits.

Alice holds an unknown state on q0 and half of a Bell pair on q1; Bob holds
the other half on q2. Alice measures her two qubits and sends the results;
Bob applies X and Z corrections based on them. No qubit travels -- only the
two classical bits do.

What to look for: the single best example of a measurement dependency. Bob's
corrections read classical bits that Alice's measurements wrote, and no
data-qubit edge could ever connect them, because the corrections are on q2 and
the measurements are on q0 and q1.
"""

import numpy as np
from qiskit import QuantumCircuit

qc = QuantumCircuit(3, 3)

# The state to teleport: an arbitrary point on the Bloch sphere.
qc.ry(np.pi / 3, 0)
qc.rz(np.pi / 5, 0)

# Bell pair shared between Alice (q1) and Bob (q2).
qc.h(1)
qc.cx(1, 2)

# Alice measures in the Bell basis.
qc.cx(0, 1)
qc.h(0)
qc.measure(0, 0)
qc.measure(1, 1)

# Bob corrects, conditioned on what Alice sent.
with qc.if_test((qc.clbits[1], 1)):
    qc.x(2)
with qc.if_test((qc.clbits[0], 1)):
    qc.z(2)

qc.measure(2, 2)

print(qc.draw())
