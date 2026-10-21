from qiskit import QuantumCircuit
from qiskit.algorithms import EstimationProblem
from qiskit.circuit.library import PhaseOracle, GroverOperator
from qiskit.algorithms import MaximumLikelihoodAmplitudeEstimation, IterativeAmplitudeEstimation
from qiskit.providers.aer import StatevectorSimulator

backend = StatevectorSimulator()
n = 3

# create state preparation operator
state_prep = QuantumCircuit(n)
state_prep.h(range(n))

# create Grover operator from problem file
# oracle = PhaseOracle.from_dimacs_file("3sat.dimacs")

oracle = QuantumCircuit(n)
# oracle.x(2)  # no X here!
oracle.h(2)
oracle.ccx(0,1,2)
oracle.h(2)
# oracle.x(2)  # no X here!
grover_op = GroverOperator(oracle, state_preparation=state_prep)

problem = EstimationProblem(state_prep, objective_qubits=list(range(n)), grover_operator=grover_op)

# Correct result
estimator = IterativeAmplitudeEstimation(0.01, 0.05, quantum_instance=backend)
result1 = estimator.estimate(problem)
print(result1.estimation)

# Now, also correct result!
estimator = MaximumLikelihoodAmplitudeEstimation(3, quantum_instance=backend)
result2 = estimator.estimate(problem)
print(result2.estimation)