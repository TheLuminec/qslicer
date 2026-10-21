
#change
with qc.for_loop(range(3)) as i:
    qc.cx(2, 0)


#to
with qc.for_loop(range(3)) as i:
    qc.cx(2, 0)
    qr = QuantumRegister(2)
    cr = ClassicalRegister(2)
    qc.add_register(qr)
    qc.add_register(cr)
    qc.measure(qr[0], cr[0])
    qc.measure(qr[1], cr[1])