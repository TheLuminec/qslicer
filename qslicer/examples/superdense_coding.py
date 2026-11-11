"""Superdense coding: two classical bits down one qubit.

The mirror image of teleportation. Alice and Bob share a Bell pair up front;
Alice encodes two bits by applying I, X, Z or ZX to her half alone, then sends
that single qubit to Bob, who decodes both bits.

The message below is 10. Change MESSAGE to see the encoding gates change.

What to look for: the encoding gates act only on q0, yet the backward slice
from Bob's measurement of q1 reaches them -- through the Bell pair, then back
through the decoding CNOT.
"""

from qiskit import QuantumCircuit

MESSAGE = "10"

qc = QuantumCircuit(2, 2)

# Shared Bell pair.
qc.h(0)
qc.cx(0, 1)

# Alice encodes two bits into her half.
if MESSAGE[1] == "1":
    qc.x(0)
if MESSAGE[0] == "1":
    qc.z(0)

# Bob decodes.
qc.cx(0, 1)
qc.h(0)

qc.measure([0, 1], [0, 1])

print(qc.draw())
