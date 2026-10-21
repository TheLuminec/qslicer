from qiskit import IBMQ, QuantumCircuit, execute
from qiskit.circuit.random import random_circuit


provider = IBMQ.load_account()
backend = provider.get_backend('ibmq_qasm_simulator')
qc = random_circuit(3, 3)
qc.metadata = {'test_header': 'test'}
test_job = execute(qc, backend=backend)