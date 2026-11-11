import math
from qiskit import QuantumCircuit, ClassicalRegister, QuantumRegister

qc = QuantumCircuit(4, name="U")

def CPhase(angle, exponent):

    qc = QuantumCircuit(1, name=f"U^{exponent}")
    qc.p(angle*exponent, 0)
    phase_gate = qc.to_gate().control(1)
    phase_gate.name = f"controled_phase_{angle}_{exponent}"

    return phase_gate, qc

repetition = 1
for j in (range(3)):
        cu,Pgate= CPhase(2*math.pi*(1/8), repetition)
        qc.append(cu, [j, 3])  #this applied the controlled gate to each qubit
        repetition *= 2
qc.qasm(filename = "qpe.qasm")
