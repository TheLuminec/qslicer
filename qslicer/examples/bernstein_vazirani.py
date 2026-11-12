"""Bernstein-Vazirani: recover a hidden bit string in a single query.

The oracle hides s = 1011 and computes s.x mod 2. One evaluation is enough:
the final measurement reads back s directly, where a classical algorithm would
need one query per bit.

What to look for: only the qubits whose bit of s is 1 are touched by the
oracle. A backward slice from the untouched wire's measurement (q2, #15) is 3
instructions; one from q3's measurement (#16) is 10.
"""

from qiskit import QuantumCircuit

SECRET = "1011"
n = len(SECRET)

qc = QuantumCircuit(n + 1, n)

qc.x(n)
qc.h(n)
qc.h(range(n))

# Oracle: one CNOT per set bit of the secret.
for index, bit in enumerate(reversed(SECRET)):
    if bit == "1":
        qc.cx(index, n)

qc.h(range(n))
qc.measure(range(n), range(n))

print(qc.draw())
