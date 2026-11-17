"""Front-end coverage additions: removed Qiskit APIs read as the gates they placed,
c_if, QuantumCircuit.control(), opaque appends, Aer save instructions, for_loop,
and operators on modules the parser does not model."""
from qslicer.parsers.staticqiskitparser import StaticQiskitParser


def shape(program):
    return [(i.name, list(i.qubits), list(i.clbits)) for i in program.instructions]


def parse(source):
    return StaticQiskitParser(source).program


def test_removed_gate_methods_are_read_as_their_gates():
    program = parse("""from qiskit import QuantumCircuit
qc = QuantumCircuit(3)
qc.u1(0.5, 0)
qc.u3(1, 2, 3, 1)
qc.iden(2)
qc.cnot(0, 1)
qc.cu1(0.2, 1, 2)
qc.toffoli(0, 1, 2)
qc.fredkin(0, 1, 2)
""")
    assert shape(program) == [("u1", [0], []), ("u3", [1], []), ("id", [2], []), ("cx", [0, 1], []),
                              ("cu1", [1, 2], []), ("ccx", [0, 1, 2], []), ("cswap", [0, 1, 2], [])]
    assert program[0].params == [0.5]


def test_c_if_conditions_the_instruction_on_its_register():
    program = parse("""from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
q = QuantumRegister(2); c = ClassicalRegister(2)
qc = QuantumCircuit(q, c)
qc.measure(0, 0)
qc.x(1).c_if(c, 1)
""")
    assert program[1].name == "x"
    assert program[1].metadata["condition_clbits"] == [0, 1]


def test_circuit_control_is_a_controlled_gate():
    program = parse("""from qiskit import QuantumCircuit
sub = QuantumCircuit(2)
sub.h(0)
sub.cx(0, 1)
qc = QuantumCircuit(3)
qc.h(2)
qc.x(0)
qc.append(sub.control(1), [2, 0, 1])
""")
    assert program.num_qubits == 3 and len(program) == 3
    assert list(program[2].qubits) == [2, 0, 1]


def test_unmodelled_instruction_on_given_qubits_is_one_opaque_instruction():
    program = parse("""from qiskit import QuantumCircuit, QuantumRegister
from qiskit.circuit.library import Diagonal
q = QuantumRegister(3)
qc = QuantumCircuit(q)
qc.append(Diagonal([1, 1, -1, 1, 1, 1, 1, 1]), [q])
""")
    assert shape(program) == [("diagonal", [0, 1, 2], [])]


def test_aer_save_instructions_add_nothing():
    program = parse("""from qiskit import QuantumCircuit
qc = QuantumCircuit(1)
qc.h(0)
qc.save_statevector()
""")
    assert shape(program) == [("h", [0], [])]


def test_for_loop_is_unrolled_over_its_index_set():
    program = parse("""from qiskit import QuantumCircuit
qc = QuantumCircuit(2)
with qc.for_loop(range(3)) as i:
    qc.rx(i, 0)
    qc.cx(0, 1)
""")
    assert [i.name for i in program.instructions] == ["rx", "cx"] * 3
    assert [p for i in program.instructions if i.name == "rx" for p in i.params] == [0, 1, 2]


def test_operators_on_unmodelled_modules_become_unknown():
    program = parse("""from qiskit import QuantumCircuit
from qiskit.opflow import Z, I
qc = QuantumCircuit(2)
qc.h(0)
qc.cx(0, 1)
obs = (Z ^ I) - 1j * (Z ^ Z)
""")
    assert shape(program) == [("h", [0], []), ("cx", [0, 1], [])]
