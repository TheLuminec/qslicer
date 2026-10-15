"""Three-qubit bit-flip code: detect and repair a single X error.

The logical qubit is spread across q0..q2. Two ancillas measure the parity of
neighboring pairs, which identifies which qubit flipped without ever learning
the logical state itself. The correction is applied conditionally.

An X error is injected on q1 on purpose. Move it to q0 or q2 and the syndrome
changes accordingly.

What to look for: the longest chain of measurement dependencies in the
collection. The corrections depend on syndrome bits, which depend on the
ancilla measurements, which depend on the encoding.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(5, 3)

# Encode: |psi) on q0 spread over q0, q1, q2.
qc.h(0)
qc.cx(0, 1)
qc.cx(0, 2)

# The error we are trying to survive.
qc.x(1)

# Syndrome extraction onto the ancillas q3 and q4.
qc.cx(0, 3)
qc.cx(1, 3)
qc.cx(1, 4)
qc.cx(2, 4)
qc.measure(3, 0)
qc.measure(4, 1)

# Correct the qubit the syndrome points at.
with qc.if_test((qc.clbits[0], 1)):
    qc.x(1)

qc.measure(0, 2)

print(qc.draw())
