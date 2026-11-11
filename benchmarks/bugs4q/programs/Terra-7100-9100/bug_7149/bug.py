from qiskit import IBMQ, QuantumCircuit, execute
from qiskit.circuit.random import random_circuit


provider = IBMQ.load_account()
backend = provider.get_backend('ibmq_qasm_simulator')
qc = random_circuit(3, 3)

## https://qiskit.org/documentation/apidoc/execute.html

test_job = execute(
    qc,
    qobj_header={
        'test_header': 'test'
    },
    backend=backend
)

test_job.result().header.to_dict()


#Qiskit Terra version: 0.18.3
#url：https://github.com/Qiskit/qiskit/issues/7149
#type：Logical Error