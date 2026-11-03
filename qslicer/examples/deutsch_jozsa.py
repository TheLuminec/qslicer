"""Deutsch-Jozsa: constant or balanced, in one query.

Classically you might need 2^(n-1)+1 evaluations of f to be sure. Here one
call to the oracle decides it: measuring all zeros means f is constant,
anything else means it is balanced.

The oracle below is balanced -- it computes the parity of the input register
onto the output qubit. Swap the CNOTs for an identity to make it constant.

What to look for: the output qubit q3 feeds the answer back to q0..q2 by
phase kickback alone, so the slice from any measurement runs through it.
"""

from qiskit import QuantumCircuit

N = 3
qc = QuantumCircuit(N + 1, N)

# Output qubit starts in |->.
qc.x(N)
qc.h(N)

qc.h(range(N))

# Balanced oracle: parity of the input register.
for qubit in range(N):
    qc.cx(qubit, N)

qc.h(range(N))

qc.measure(range(N), range(N))

print(qc.draw())
