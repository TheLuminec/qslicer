"""StaticOpenQASMParser: the qiskit.qasm3 route's IR, plus where each instruction came from."""

import glob
import os

import pytest
from qiskit import QuantumCircuit, qasm3

from qslicer.parsers.openqasmparser import OpenQASMParser
from qslicer.parsers.staticopenqasmparser import StaticOpenQASMParser
from qslicer.parsers.staticqiskitparser import StaticParseError
from qslicer.dependencies.dependency_rules import DependencyType
from qslicer.qdg import QDG
from qslicer.slicer import QuantumSlicer, SlicingCriterion

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "qslicer", "examples")

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
        ("h", 5), ("h", 5), ("h", 5), ("cx", 7), ("cx", 7), ("measure", 9), ("if_else", 10),
        ("x", 11)]
    # The predicate spans the whole block; its body gate keeps its own line.
    if_else, body = program[6], program[7]
    assert (if_else.location.line, if_else.location.end_line) == (10, 12)
    assert (body.location.line, body.metadata["guards"]) == (11, ((6, 0),))


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
                              ("if_else", [], [], [0, 1]), ("x", [0], [], [])]
    assert program[5].metadata["guards"] == ((4, 0),)


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
    ("c[0] = measure q[0];\nint i = c[0];\nx q[i];\n", "run time", 7),
    ("c[0] = measure q[0];\nrx(c[0] * pi) q[1];\n", "run time", 6),
    ('include "mine.inc";\n', "cannot be resolved", 5),
    ("def f(qubit a) -> bit {\n  bit r = measure a;\n  if (r) { return 1; }\n  return 0;\n}\n"
     "c[0] = f(q[0]);\n", "'return' under a run-time condition", 7),
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


# --------------------------------------------------------------------------
# OpenQASM 3 beyond what the qiskit.qasm3 route can import
# --------------------------------------------------------------------------

def backward(program, idx, deps=None):
    return QuantumSlicer.backward_slice(QDG(program), SlicingCriterion(node_idx=idx, dep_types=deps))


def names(program):
    return [i.name for i in program.instructions]


@pytest.mark.requirement("FR-06")
@pytest.mark.parametrize("body", [
    "c[0] = measure q[0];\nwhile (c[0]) { x q[0]; c[0] = measure q[0]; }\n",
    "c = measure q;\nwhile (c == 2) { x q[1]; }\n",
    "for int i in [0:2] { c[0] = measure q[0]; if (c[0]) { x q[1]; continue; } y q[2]; }\n",
], ids=["while bit", "while register", "for with run-time continue"])
def test_loops_on_run_time_values_match_the_qiskit_route(body):
    source = HEAD + body
    assert shape(parse(source)) == shape(OpenQASMParser(qasm3.loads(source)).parse())


@pytest.mark.requirement("FR-06")
def test_run_time_while_is_one_instruction_that_reads_its_condition():
    program = parse(HEAD + "bit f = 1;\nwhile (f) {\n  reset q[0];\n  h q[0];\n"
                           "  f = measure q[0];\n}\nx q[1];\n")
    # The first test of `f` is known (it is 1), so one iteration is unrolled;
    # after that `f` holds a measurement and the rest is a while_loop.
    assert names(program) == ["reset", "h", "measure", "while_loop", "x"]
    loop = program[3]
    assert loop.qubits == [0] and loop.metadata["condition_clbits"] == loop.clbits == [3]
    assert [i.name for i in loop.params[0].data] == ["reset", "h", "measure"]
    assert backward(program, 3) == {0, 1, 2, 3}


@pytest.mark.requirement("FR-06")
def test_break_under_a_run_time_condition_keeps_everything_the_loop_does():
    # Unrolling this would have to drop the gates after the break; the loop
    # becomes one for_loop over every qubit and bit any iteration touches.
    program = parse(HEAD + "for int i in [0:1] {\n  c[i] = measure q[i];\n"
                           "  if (c[i]) break;\n  h q[i + 1];\n}\nx q[2];\n")
    assert names(program) == ["for_loop", "x"]
    loop = program[0]
    assert loop.qubits == [0, 1, 2] and loop.clbits == [0, 1]
    body = loop.params[0].data
    assert [i.name for i in body] == ["measure", "if_else", "h", "measure", "if_else", "h"]
    assert body[1].params[0].data[0].name == "break_loop"
    assert backward(program, 1) == {0, 1}


@pytest.mark.requirement("FR-06")
def test_switch_on_a_measured_register():
    program = parse(HEAD + "c = measure q;\nint i = c;\nswitch (i) {\n"
                           "  case 0 { x q[0]; }\n  case 1, 2 { y q[1]; }\n  default { z q[2]; }\n}\n")
    switch = program[3]
    assert switch.name == "switch_case"
    assert switch.metadata["condition_clbits"] == [0, 1, 2]
    assert switch.qubits == [0, 1, 2] and len(switch.params) == 3
    assert backward(program, 3, {DependencyType.MEASUREMENT}) == {0, 1, 2, 3}


@pytest.mark.requirement("FR-06")
@pytest.mark.parametrize("condition", ["c[0] && c[1]", "c[0] || c[1]", "c > 2", "c != 1",
                                       "(c & 3) == 1"])
def test_conditions_qiskit_cannot_import_still_read_their_bits(condition):
    program = parse(HEAD + f"c = measure q;\nif ({condition}) x q[2];\n")
    branch = program[3]
    assert branch.name == "if_else"
    expected = [0, 1] if "c[" in condition else [0, 1, 2]
    assert branch.metadata["condition_clbits"] == expected
    assert branch.metadata["condition"] == condition


def test_constant_bits_are_settled_at_compile_time():
    program = parse(HEAD + 'bit[2] d = "01";\nif (d[0]) x q[0];\nif (d == 2) y q[1];\n'
                           "c[0] = measure q[0];\nc[0] = 1;\nif (c[0]) z q[2];\n")
    assert names(program) == ["x", "measure", "z"]


@pytest.mark.requirement("FR-06")
def test_copying_a_measured_bit_keeps_the_dependency():
    program = parse(HEAD + "bit b;\nc[0] = measure q[0];\nb = c[0];\nif (b) x q[1];\n")
    assert shape(program) == [("measure", [0], [0], []), ("store", [], [3], [0]),
                              ("if_else", [], [], [3]), ("x", [1], [], [])]
    assert backward(program, 2) == {0, 1, 2}
    assert backward(program, 3) == {0, 1, 2, 3}


def test_a_variable_set_on_a_run_time_path_carries_its_condition():
    program = parse(HEAD + "int n = 0;\nc[0] = measure q[0];\nif (c[0]) { n = 1; }\n"
                           "if (n == 1) x q[1];\n")
    # No empty if_else for the assignment; the later branch reads c[0] itself.
    assert shape(program) == [("measure", [0], [0], []), ("if_else", [], [], [0]),
                              ("x", [1], [], [])]
    assert program[2].metadata["condition"] == "n == 1"


def test_measuring_into_variables_and_subroutines():
    program = parse(HEAD + "bool b = measure q[0];\nif (b) x q[1];\n"
                           "def m(qubit a) -> bit { return measure a; }\n"
                           "c[2] = m(q[2]);\nif (c[2]) h q[0];\nmeasure q[1];\n")
    assert shape(program) == [("measure", [0], [3], []), ("if_else", [], [], [3]),
                              ("x", [1], [], []),
                              ("measure", [2], [2], []), ("if_else", [], [], [2]),
                              ("h", [0], [], []),
                              ("measure", [1], [], [])]
    assert program.num_clbits == 4


def test_physical_qubits_arrays_externs_and_builtins():
    program = parse('OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[1] c;\n'
                    "array[float[64], 3] a = {0.1, 0.2, 0.3};\na[2] = a[1] * 2;\n"
                    'const int n = popcount("0111");\nconst bit[4] w = rotl("0001", 1);\n'
                    "extern coin(int) -> bit;\n"
                    "rx(a[2]) $1;\nry(n) $0;\nif (w == 2) cx $0, $1;\n"
                    "c[0] = measure $1;\nif (coin(c[0])) x $0;\n")
    assert [(i.name, list(i.qubits)) for i in program.instructions] == [
        ("rx", [1]), ("ry", [0]), ("cx", [0, 1]), ("measure", [1]), ("if_else", []),
        ("x", [0])]
    assert program[0].params[0] == pytest.approx(0.4)
    assert program[1].params[0] == 3.0
    assert program[4].metadata["condition_clbits"] == [0]


def test_modifiers_on_gphase_boxes_and_end():
    program = parse(HEAD + "gphase(pi);\nctrl @ gphase(pi / 2) q[0];\n"
                           "negctrl(2) @ inv @ gphase(1) q[0], q[1];\n"
                           "pow(0.5) @ x q[2];\nbox[100ns] { h q[0]; cx q[0], q[1]; }\n"
                           "end;\nx q[2];\n")
    assert [(i.name, list(i.qubits)) for i in program.instructions] == [
        ("p", [0]), ("cp_o0", [0, 1]), ("x_pow", [2]), ("h", [0]), ("cx", [0, 1])]
    assert program[1].params[0] == -1.0
    assert [i.location.line for i in program.instructions] == [6, 7, 8, 9, 9]


def test_openqasm_2_opaque_gates():
    program = parse('OPENQASM 2.0;\ninclude "qelib1.inc";\nopaque magic(a) x, y;\n'
                    "qreg q[2];\nmagic(0.5) q[0], q[1];\n")
    assert shape(program) == [("magic", [0, 1], [], [])]
    assert program[0].location.line == 5
