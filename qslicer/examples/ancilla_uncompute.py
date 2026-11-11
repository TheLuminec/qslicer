"""An ancilla qubit computed and then uncomputed -- a gap in the analysis.

q2 is scratch space. The first Toffoli writes q0 AND q1 into it and the
second, identical Toffoli erases it again, which is the standard way to
return borrowed scratch space to |0) so it carries no leftover entanglement.

What to look for: the two Toffolis are linked as mirrors and QD08 is reported
on the second one, but a backward slice from a measurement still contains
both. The slicer follows dependences and does not yet drop a pair that
cancels.

That is a known limit rather than a bug in this circuit: removing
uncomputed work from a slice is what the analysis still has to grow into.

uncomputation.py shows the same thing with single-qubit gates.
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
