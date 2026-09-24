"""An ancilla qubit computed and then uncomputed -- a gap in the analysis.

q2 is scratch space. The first Toffoli writes q0 AND q1 into it and the
second, identical Toffoli erases it again, which is the standard way to
return borrowed scratch space to |0) so it carries no leftover entanglement.

What to look for: the analyser does NOT currently see this as uncomputation.
Its cancellation check only compares a gate against the last gate recorded in
a group, and entangling gates are never recorded there -- they open a new
group instead. So you get two nested entangled groups over q0, q1 and q2, and
nothing in any uncomputed list.

That is a known limit rather than a bug in this circuit: uncomputation across
entangling gates is exactly the case the analysis still has to grow into.
uncomputation.py shows the single-qubit case, which is detected today.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(3, 2)

qc.h(0)
qc.h(1)

# Compute q0 AND q1 into the ancilla.
qc.ccx(0, 1, 2)

# Uncompute it: the same gate again, undoing the first.
qc.ccx(0, 1, 2)

qc.measure([0, 1], [0, 1])

print(qc.draw())
