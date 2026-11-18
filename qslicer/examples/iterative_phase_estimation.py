"""Iterative phase estimation: read an eigenphase one bit at a time.

Textbook phase estimation needs one counting qubit per bit of precision.
The iterative version, which IBM runs on real hardware as a dynamic
circuit, uses a single ancilla over and over. Each round measures one bit
of the phase, least significant first, resets the ancilla, and feeds the
bits already read forward as phase corrections on the next round.

The unitary here is a controlled phase on two system qubits, so |11) is an
eigenstate with eigenphase PHASE. With PHASE = 0.1011 in binary (11/16),
four rounds read it exactly.

What to look for:
- Backward from the last phase bit (#33): 34 of 36 instructions. Only the
  final check on the system qubits is outside it.
- The same slice with only Qubit under Follow: 19. The reset cuts the
  ancilla wire, so the earlier rounds' measurements and corrections drop
  out; what is left reaches back only through the controlled-U gates on the
  system qubits.
- Forward from the first phase bit (#6): every later correction and phase
  bit, but neither system check. Reading the phase never feeds back into the
  system.
- A copy-paste bug to plant: measure into phase_bits[0] instead of
  phase_bits[k]. Nothing raises, yet the dependence checks find conditions
  on bits no measurement wrote (QD09) and a phase bit never written (QD12).
"""

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit_aer import AerSimulator

PHASE = 11 / 16          # the eigenphase we want to recover: 0.1011 in binary
BITS = 4                 # rounds of estimation, one bit each

ancilla = QuantumRegister(1, "anc")
system = QuantumRegister(2, "sys")
phase_bits = ClassicalRegister(BITS, "phase")
check = ClassicalRegister(2, "check")
qc = QuantumCircuit(ancilla, system, phase_bits, check)


def prepare_eigenstate(circuit, qubits):
    """|11) is an eigenstate of the controlled phase used as U."""
    circuit.x(qubits[0])
    circuit.x(qubits[1])


def controlled_u_power(circuit, control, targets, power):
    """Apply controlled-U^power, where U = CP(2*pi*PHASE) on the system."""
    angle = 2 * np.pi * PHASE * power
    circuit.mcp(angle, [control, targets[0]], targets[1])


prepare_eigenstate(qc, system)

# Read the phase from its least significant bit to its most significant.
for k in range(BITS):
    power = 2 ** (BITS - 1 - k)

    qc.reset(ancilla[0])
    qc.h(ancilla[0])
    controlled_u_power(qc, ancilla[0], system, power)

    # Undo the contribution of every bit measured in an earlier round.
    for j in range(k):
        with qc.if_test((phase_bits[j], 1)):
            qc.p(-np.pi / 2 ** (k - j), ancilla[0])

    qc.h(ancilla[0])
    qc.measure(ancilla[0], phase_bits[k])

# Sanity check: the system should still be in |11), untouched by the rounds.
qc.measure(system, check)

# --- Run it and decode the estimate (the analyzer reads none of this) ---
backend = AerSimulator()
counts = backend.run(transpile(qc, backend), shots=1024).result().get_counts()
best = max(counts, key=counts.get)
check_bits, read_bits = best.split()
estimate = sum(int(b) / 2 ** (i + 1) for i, b in enumerate(read_bits))
print(qc.draw())
print(f"system check: {check_bits}  phase bits: {read_bits}  estimate: {estimate}")
