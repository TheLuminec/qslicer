from qiskit import QuantumCircuit, ClassicalRegister, QuantumRegister

main = QuantumCircuit(3, 3)
sub = QuantumCircuit(3)
sub.h(0)

# ghost: result dropped
main.compose(sub)
# kept
main = main.compose(sub)
# in place
main.compose(sub, inplace=True)
# passed on
print(main.compose(sub))
# returned


def combine(a, b):
    return a.compose(b)


# ghost inside a function
def broken(a, b):
    a.compose(b)
    return a


# conditional gate with no measurement before it
cr = ClassicalRegister(2)
qr = QuantumRegister(2)
qc_cond = QuantumCircuit(qr, cr)
qc_cond.x(0).c_if(cr, 1)
qc_cond.h(1)
qc_cond.measure(1, 1)

# conditional after a measurement: fine
qc_cond_ok = QuantumCircuit(2, 2)
qc_cond_ok.h(0)
qc_cond_ok.measure(0, 0)
qc_cond_ok.x(1).c_if(qc_cond_ok.clbits[0], 1)

# a helper that extends the circuit hides its qubits
qc_ext = QuantumCircuit(3, 3)


def extend(circuit):
    circuit.h(0)


extend(qc_ext)
qc_ext.measure(1, 1)

# implicit extender: uses the global circuit
qc_glob = QuantumCircuit(2, 2)


def touch():
    qc_glob.x(1)


touch()
qc_glob.measure(1, 1)

# for loop over qubits: index unknown, so the operator is unknown
qc_loop = QuantumCircuit(3, 3)
for q in range(3):
    qc_loop.h(q)
qc_loop.measure(0, 0)
qc_loop.measure(2, 2)
