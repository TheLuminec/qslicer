bell = QuantumCircuit(2)
bell.h(0)
bell.cx(0, 1)

c = QuantumCircuit(QuantumRegister(2), ClassicalRegister(2), AncillaRegister(2))
print(c.ancillas)

# output:
# [AncillaQubit(AncillaRegister(2, 'a8'), 0),
# AncillaQubit(AncillaRegister(2, 'a8'), 1)]

c.tensor(bell, inplace=True)
print(c.ancillas)

# output:
# [AncillaQubit(AncillaRegister(2, 'a8'), 0),
# AncillaQubit(AncillaRegister(2, 'a8'), 1),
# AncillaQubit(AncillaRegister(2, 'a8'), 0),
# AncillaQubit(AncillaRegister(2, 'a8'), 1)]


#Qiskit Terra version:0.19.1
#url：https://github.com/Qiskit/qiskit/issues/7445
#type：Logical Error