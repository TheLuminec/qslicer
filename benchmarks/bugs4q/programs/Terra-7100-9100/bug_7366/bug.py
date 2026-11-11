from qiskit import QuantumCircuit
from qiskit.algorithms import EstimationProblem
from qiskit.circuit.library import PhaseOracle, GroverOperator
from qiskit.algorithms import (AmplitudeEstimation,MaximumLikelihoodAmplitudeEstimation)
from qiskit.providers.aer import QasmSimulator

backend = QasmSimulator()
n = 3

# create state preparation operator
state_prep = QuantumCircuit(n)
state_prep.h(range(n))

# create Grover operator from problem file
oracle = PhaseOracle.from_dimacs_file("examples/3sat.dimacs")
grover_op = GroverOperator(oracle)
problem = EstimationProblem(state_prep,[*range(n)],grover_operator=grover_op)

# Correct result
estimator = AmplitudeEstimation(7, quantum_instance=backend)
result = estimator.estimate(problem)
result.estimation

# Incorrect result!
estimator = MaximumLikelihoodAmplitudeEstimation(7, quantum_instance=backend)
result = estimator.estimate(problem)
result.estimation

#Qiskit Terra version:0.18.3
#url：https://github.com/Qiskit/qiskit/issues/7366
#type：Logical Error