#add
qr = QuantumRegister(2)
cr = ClassicalRegister(2)
qc.add_register(qr)
qc.add_register(cr)
qc.measure(qr[0], cr[0])
qc.measure(qr[1], cr[1])
with qc.if_test((cr, 0b00)):
    qc.cx(3, 1)