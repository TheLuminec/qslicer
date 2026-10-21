from qiskit.opflow import EvolvedOp, X

op = EvolvedOp(0.5 * X)
op.to_matrix_op()