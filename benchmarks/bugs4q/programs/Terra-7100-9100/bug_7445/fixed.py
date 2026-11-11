bell = QuantumCircuit(2)
bell.h(0)
bell.cx(0, 1)

c = QuantumCircuit(QuantumRegister(2), ClassicalRegister(2), AncillaRegister(2))
print(c.ancillas)
d = c.tensor(bell, inplace=False)
print(d.ancillas)