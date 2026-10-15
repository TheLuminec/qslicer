"""Simon's algorithm: find the hidden period of a 2-to-1 function.

The oracle implements f(x) = f(x XOR s) for the hidden string s = 11. Each run
returns a bit string y with y.s = 0; collect a couple of independent ones and
solve the linear system classically to recover s.

Only the quantum half is here -- the classical post-processing is what turns
the measurements into s.

What to look for: the two registers are joined only by the oracle's CNOTs, so
the shape of the dependence graph is two parallel halves with a bridge.
"""

from qiskit import QuantumCircuit

n = 2
qc = QuantumCircuit(2 * n, n)

# Input register into superposition.
qc.h(range(n))

# Oracle for s = 11: copy the input, then fold in the period.
qc.cx(0, n)
qc.cx(1, n + 1)
qc.cx(0, n + 1)
qc.cx(1, n)

qc.h(range(n))
qc.measure(range(n), range(n))

print(qc.draw())
