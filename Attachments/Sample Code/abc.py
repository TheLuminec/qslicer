from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
import numpy as np

# 1. Create a 3-qubit circuit
qc = QuantumCircuit(3)

# 2. Add parameterized gates (complex/dynamic)
theta = Parameter('θ')
qc.rx(theta, 0)
qc.h(1)
qc.h(2)

# 3. Create complex entanglement (GHZ-like structure)
qc.cx(0, 1)
qc.cx(1, 2)
qc.barrier() # For visualization

# 4. Apply additional gate operations
qc.swap(0, 2)
qc.ccx(0, 1, 2) # Toffoli gate

# 5. Measure
qc.measure_all()

# Visualize
print(qc.draw(output='text'))
