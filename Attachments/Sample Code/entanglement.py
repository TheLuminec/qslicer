from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from qiskit.visualization import plot_histogram
import matplotlib.pyplot as plt

# 1. Build the circuit
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.x(1)
qc.measure(0, 0)
qc.measure(1, 1)

# 2. Draw the circuit
print("Circuit Diagram:")
print(qc.draw(output='text'))

# 3. Simulate the circuit
simulator = AerSimulator()
job = simulator.run(qc, shots=1024)

# 4. Get the results and visualize
counts = result.get_counts(qc)
print("\nTotal counts for 00 and 11 are:", counts)

# 5. Plot a histogram of the results
plot_histogram(counts)
plt.show()
