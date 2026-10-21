#change
from qiskit.algorithms import (AmplitudeEstimation,MaximumLikelihoodAmplitudeEstimation)
from qiskit.providers.aer import QasmSimulator

backend = QasmSimulator()

oracle = PhaseOracle.from_dimacs_file("examples/3sat.dimacs")
grover_op = GroverOperator(oracle)
problem = EstimationProblem(state_prep,[*range(n)],grover_operator=grover_op)


#to
from qiskit.algorithms import MaximumLikelihoodAmplitudeEstimation, IterativeAmplitudeEstimation
from qiskit.providers.aer import StatevectorSimulator

backend = StatevectorSimulator()

oracle = QuantumCircuit(n)
oracle.h(2)
oracle.ccx(0,1,2)
oracle.h(2)
grover_op = GroverOperator(oracle, state_preparation=state_prep)
problem = EstimationProblem(state_prep, objective_qubits=list(range(n)), grover_operator=grover_op)