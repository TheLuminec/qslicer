from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from qiskit.visualization import plot_histogram
import matplotlib.pyplot as plt

# 1. Build the circuit
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure(1, 1)
qc.measure(0, 0)

# 2. Draw the circuit
print("Circuit Diagram:")
print(qc.draw(output='text'))
