"""Bell pair: the smallest circuit that entangles two qubits.

A Hadamard puts q0 into superposition, then the CNOT copies that indecision
onto q1. The two measurements always agree -- 00 or 11, never 01 or 10.

What to look for: the backward slice from either measurement contains both
gates, and that slice marks the other qubit as possibly correlated with it.
"""

from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)

qc.h(0)
qc.cx(0, 1)

qc.measure(0, 0)
qc.measure(1, 1)

print(qc.draw())
