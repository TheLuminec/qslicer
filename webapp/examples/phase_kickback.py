"""Phase kickback: a controlled gate that changes the control, not the target.

q1 is prepared in |-), the eigenstate of X with eigenvalue -1. The CNOT then
leaves q1 alone and flips the phase of q0 instead -- invisible until the final
Hadamard turns that phase back into something a Z-basis measurement can see.

What to look for: this is the mechanism every oracle-based algorithm on this
list runs on. It is also the clearest case for why a phase dependency rule is
worth having: without one, the phase gates look inert.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 1)

# Put the target into |-> so the CNOT kicks its phase back onto the control.
qc.x(1)
qc.h(1)

qc.h(0)
qc.cx(0, 1)
qc.h(0)

qc.measure(0, 0)

print(qc.draw())
