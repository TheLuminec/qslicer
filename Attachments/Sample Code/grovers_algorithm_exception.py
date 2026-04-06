from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit.quantum_info import Statevector


# Checks for exactly two sequential ones given 3 qubits using logic
def oracle():
    qc = QuantumCircuit(4, name="Oracle")
    
    qc.cx(0, 2)
    qc.mcx([1, 2], 3)
    qc.cx(0, 2)

    print(qc.draw())

    return qc

def diffuser():
    qc = QuantumCircuit (3, name="Diffuser")
    
    qc.h([0,1,2])    #h to everything
    qc.x([0,1,2])    #x to everything
    
    qc.h(2)             #h to last qubit
    qc.ccx(0,1,2)       #controlled not based on all qubits with target as last qubit
    qc.h(2)             #h to last qubit
    
    qc.x([0,1,2])       #undo everything
    qc.h([0,1,2])        #undo everything
    
    print(qc.draw())
    return qc

qc = QuantumCircuit(4, 3)

qc.x(3)
qc.h([0, 1, 2, 3])

qc.compose(oracle().to_gate(), inplace = True)
qc.compose(diffuser().to_gate(), inplace = True)

qc.measure([0, 1, 2], [0, 1, 2])

print(qc.draw())

sim = AerSimulator()
compiled = transpile(qc, sim)
result = sim.run(compiled, shots=2000).result()
counts = result.get_counts()

print(f"Counts: {counts}")
