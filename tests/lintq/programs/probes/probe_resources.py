import qiskit
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister, transpile


def three():
    return 3


def build(n):
    circuit = QuantumCircuit(n, n)
    circuit.h(0)
    circuit.measure(0, 0)
    return circuit


# measures a qubit nothing touched: constant classical bit
qc_const = QuantumCircuit(2, 2)
qc_const.h(0)
qc_const.measure(0, 0)
qc_const.measure(1, 1)

# oversized: no qubit is ever used
qc_idle = QuantumCircuit(4, 4)
qc_idle.barrier()

# oversized, but it has an unknown operation appended
qc_idle_unknown = QuantumCircuit(3)
qc_idle_unknown.append(qc_const.to_gate(), [0, 1])

# unmeasurable with a register sized by a helper
qr = QuantumRegister(three())
qc_helper_size = QuantumCircuit(qr)
qc_helper_size.h(qr)

# unmeasurable through qiskit.QuantumCircuit
qc_module = qiskit.QuantumCircuit(5, 2)
qc_module.h(0)
qc_module.measure(0, 0)

# add_register adds classical bits
qc_added = QuantumCircuit(QuantumRegister(2))
qc_added.add_register(ClassicalRegister(2))
qc_added.h(0)
qc_added.measure(0, 0)
qc_added.measure(1, 1)

# a copy keeps the original's size
qc_copy = qc_const.copy()

# circuit returned by a helper
qc_from_helper = build(4)
qc_from_helper.x(1)

# measure_all on a circuit that already has bits; add_bits=False is fine
qc_ma = QuantumCircuit(2, 2)
qc_ma.h(0)
qc_ma.measure_all()
qc_ma_ok = QuantumCircuit(2, 2)
qc_ma_ok.h(0)
qc_ma_ok.measure_all(add_bits=False)

# deprecated identity
qc_iden = QuantumCircuit(1, 1)
qc_iden.iden(0)
qc_iden.measure(0, 0)

# transpiled at level 3, then modified
qc_t = QuantumCircuit(2, 2)
qc_t.h(0)
qc_t.cx(0, 1)
level = 3
qc_opt = transpile(qc_t, optimization_level=level)
qc_opt.measure(0, 0)
qc_opt2 = transpile(qc_t, None, None, None, None, None, None, 3)
qc_opt2.x(1)
qc_lvl1 = transpile(qc_t, optimization_level=1)
qc_lvl1.x(1)
