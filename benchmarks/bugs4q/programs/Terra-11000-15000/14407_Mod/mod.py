

#add
qr = QuantumRegister(2)
cr = ClassicalRegister(2)
qc.add_register(qr)
qc.add_register(cr)
qc.x(qr[0])
qc.x(qr[1])
qc.measure(qr[0], cr[0]) 
qc.measure(qr[1], cr[1]) 
with qc.if_test((cr, 0b11)) as else_1: 
	qc.append(RXGate(4.113), [qreg[1]])