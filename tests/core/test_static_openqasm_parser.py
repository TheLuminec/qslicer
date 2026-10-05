"""StaticOpenQASMParser: the qiskit.qasm3 route's IR, plus where each instruction came from."""

import glob
import os

import pytest
from qiskit import QuantumCircuit, qasm3

from Parsers.openqasmparser import OpenQASMParser
from Parsers.staticopenqasmparser import StaticOpenQASMParser
from Parsers.staticqiskitparser import StaticParseError

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "webapp", "examples")

HEAD = 'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[3] q;\nbit[3] c;\n'


def shape(program):
    return [(i.name, list(i.qubits), list(i.clbits), list(i.metadata["condition_clbits"]))
            for i in program.instructions]


def parse(source):
    return StaticOpenQASMParser(source).program


def lines(program):
    return [i.location.line for i in program.instructions]


@pytest.mark.requirement("FR-06")
@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.qasm"))),
                         ids=os.path.basename)
def test_examples_match_the_qiskit_route(path):
    static = StaticOpenQASMParser().parse_file(path)
    imported = OpenQASMParser().parse_file(path)
    assert (static.num_qubits, static.num_clbits) == (imported.num_qubits, imported.num_clbits)
    assert shape(static) == shape(imported)


@pytest.mark.requirement("FR-06")
def test_gates_modifiers_and_branches_match_the_qiskit_route():
    body = """gate mygate(a) x, y { rx(a) x; cx x, y; }
U(1, 2, 3) q[0];
CX q[0], q[1];
phase(0.5) q[0];
ctrl @ inv @ s q[0], q[1];
ctrl(2) @ x q[0], q[1], q[2];
negctrl @ x q[0], q[1];
inv @ mygate(0.3) q[0], q[1];
c = measure q;
if (c == 5) { x q[0]; }
if (c[1]) { x q[1]; } else { z q[2]; }
if (!c[0]) { y q[1]; }
barrier;
reset q;
"""
    assert shape(parse(HEAD + body)) == shape(OpenQASMParser(qasm3.loads(HEAD + body)).parse())


@pytest.mark.requirement("FR-07")
def test_every_instruction_has_its_span():
    program = parse(HEAD + "h q[0];\ncx q[0], q[1];\nc[0] = measure q[0];\n")
    assert lines(program) == [5, 6, 7]
    location = program[1].location
    assert (location.column, location.end_line, location.end_column) == (0, 6, 14)


@pytest.mark.requirement("FR-07")
def test_broadcast_loops_and_branches_keep_their_lines():
    program = parse(HEAD + """h q;
for int i in [0:1] {
    cx q[i], q[i + 1];
}
c[0] = measure q[0];
if (c[0] == 1) {
    x q[2];
}
""")
    assert [(i.name, i.location.line) for i in program.instructions] == [
        ("h", 5), ("h", 5), ("h", 5), ("cx", 7), ("cx", 7), ("measure", 9), ("if_else", 10)]
    if_else = program[6]
    assert (if_else.location.line, if_else.location.end_line) == (10, 12)
    assert if_else.params[0].data[0].location.line == 11


@pytest.mark.requirement("FR-07")
def test_subroutine_gates_record_the_call():
    program = parse(HEAD + "def entangle(qubit a, qubit b) {\n    h a;\n    cx a, b;\n}\n"
                           "entangle(q[0], q[1]);\nentangle(q[1], q[2]);\n")
    assert [(i.location.line, i.location.via) for i in program.instructions] == [
        (6, (9,)), (7, (9,)), (6, (10,)), (7, (10,))]


def test_openqasm_2_is_read_too():
    program = parse('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
                    'h q[0];\ncx q[0],q[1];\nmeasure q -> c;\nif (c == 3) x q[0];\n')
    assert shape(program) == [("h", [0], [], []), ("cx", [0, 1], [], []),
                              ("measure", [0], [0], []), ("measure", [1], [1], []),
                              ("if_else", [0], [0, 1], [0, 1])]


def test_compile_time_values_are_evaluated():
    program = parse(HEAD + "const int n = 2;\ninput float theta;\n"
                           "if (n > 1) { rx(theta * 2) q[n]; } else { x q[0]; }\n"
                           "int k = 0;\nwhile (k < 2) { h q[k]; k += 1; }\n")
    assert [(i.name, list(i.qubits)) for i in program.instructions] == [
        ("rx", [2]), ("h", [0]), ("h", [1])]
    assert str(program[0].params[0]) == "theta*2"


@pytest.mark.parametrize("body, message, line", [
    ("h q[0]\nx q[1];\n", "syntax error", 6),
    ("foo q[0];\n", "gate 'foo' is not defined", 5),
    ("h q[7];\n", "out of range", 5),
    ("c[0] = measure q[0];\nwhile (c[0]) { x q[0]; }\n", "run time", 6),
    ('include "mine.inc";\n', "cannot be resolved", 5),
    ("h $0;\n", "physical qubits", 5),
])
def test_what_cannot_be_read_is_refused_with_a_line(body, message, line):
    with pytest.raises(StaticParseError, match=message) as info:
        parse(HEAD + body)
    assert info.value.lineno == line


def test_gates_need_the_standard_include():
    with pytest.raises(StaticParseError, match="stdgates.inc"):
        parse("OPENQASM 3.0;\nqubit q;\nh q;\n")
    assert len(parse("OPENQASM 3.0;\nqubit q;\nU(0, 0, 0) q;\n")) == 1


@pytest.mark.requirement("NFR-08")
def test_parse_is_deterministic_and_reparse_does_not_duplicate():
    circuit = QuantumCircuit(2, 2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure([0, 1], [0, 1])
    parser = StaticOpenQASMParser(qasm3.dumps(circuit))
    first = shape(parser.program)
    assert shape(parser.parse()) == first == shape(parse(qasm3.dumps(circuit)))
