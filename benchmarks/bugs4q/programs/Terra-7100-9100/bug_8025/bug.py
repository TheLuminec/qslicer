from qiskit.opflow import EvolvedOp, X

op = EvolvedOp(0.5 * X)
op.to_instruction()

#Qiskit Terra version:0.19.1
#url：https://github.com/Qiskit/qiskit/issues/8025
#type：ExtensionError