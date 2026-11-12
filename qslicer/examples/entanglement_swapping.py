"""Entanglement swapping: entangle two qubits that never met.

Two independent Bell pairs are made, (q0, q1) and (q2, q3). A Bell measurement
on the middle pair (q1, q2) leaves q0 and q3 entangled with each other, even
though no gate ever acted on both.

What to look for: the strongest argument for an entanglement-aware dependence
rule. Slice backward from the q3 measurement (#13) following every dependency
and you reach q0's gates through the Bell measurement's classical bits. Now
keep only the Qubit chip under Follow: the slice drops to five
instructions and q0 disappears, because no wire ever carried q0 to q3.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(4, 4)

# Two separate Bell pairs.
qc.h(0)
qc.cx(0, 1)
qc.h(2)
qc.cx(2, 3)

# Bell measurement on the inner pair swaps the entanglement outwards.
qc.cx(1, 2)
qc.h(1)
qc.measure(1, 1)
qc.measure(2, 2)

# Corrections that make q3 match q0.
with qc.if_test((qc.clbits[2], 1)):
    qc.x(3)
with qc.if_test((qc.clbits[1], 1)):
    qc.z(3)

qc.measure(0, 0)
qc.measure(3, 3)

print(qc.draw())
