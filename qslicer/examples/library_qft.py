"""The same QFT, taken from qiskit.circuit.library instead of written out.

qiskit.circuit.library is a catalog of ready-made circuits: QFT, Grover
operators, arithmetic blocks, variational ansatze. Composing one in is the
normal way to build a real program.

What to look for: the parser runs your file and reads the circuit Qiskit
actually built, so a library block arrives fully expanded, in whatever basis
Qiskit chose. Compare the gate names here with quantum_fourier_transform.py,
which does the same job by hand.
"""

from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT

n = 3
qc = QuantumCircuit(n, n)

qc.x(0)
qc.h(2)

qc.compose(QFT(num_qubits=n, do_swaps=True).decompose(), inplace=True)

qc.measure(range(n), range(n))

print(qc.draw())
