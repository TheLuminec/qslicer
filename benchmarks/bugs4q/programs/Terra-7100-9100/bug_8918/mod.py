#change
import qiskit_aer
from qiskit import QuantumCircuit, Aer


#to
from qiskit import QuantumCircuit, Aer, transpile


#add
tqc = transpile(qc, backend)