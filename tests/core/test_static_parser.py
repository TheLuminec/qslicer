"""StaticQiskitParser: the same IR as QiskitParser, without running the file."""

import glob
import io
import math
import os
from contextlib import redirect_stdout

import pytest

from Parsers.qiskitparser import QiskitParser
from Parsers.staticqiskitparser import StaticParseError, StaticQiskitParser

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "webapp", "examples")
# Built from a library QFT, whose commuting gates Qiskit's decompose() reorders.
REORDERED = {"library_qft.py"}


def shape(program):
    return [(i.name, list(i.qubits), list(i.clbits)) for i in program.instructions]


def parse(source, **kwargs):
    return StaticQiskitParser(source, **kwargs)


@pytest.mark.requirement("FR-05")
@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.py"))),
                         ids=os.path.basename)
def test_examples_match_the_executing_parser(path):
    static = StaticQiskitParser().parse_file(path)
    with redirect_stdout(io.StringIO()):
        executed = QiskitParser().parse_file(path)
    assert (static.num_qubits, static.num_clbits) == (executed.num_qubits, executed.num_clbits)
    if os.path.basename(path) in REORDERED:
        assert sorted(map(repr, shape(static))) == sorted(map(repr, shape(executed)))
    else:
        assert shape(static) == shape(executed)
        for mine, theirs in zip(static.instructions, executed.instructions):
            assert (mine.metadata["condition_clbits"]
                    == list(theirs.metadata["condition_clbits"]))


def test_the_file_is_never_executed(tmp_path):
    marker = tmp_path / "ran.txt"
    source = f"""
import os
from qiskit import QuantumCircuit
open({str(marker)!r}, "w").write("ran")
os.system("echo ran > {marker.as_posix()}")
qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)
"""
    program = parse(source).program
    assert shape(program) == [("h", [0], []), ("measure", [0], [0])]
    assert not marker.exists()


def test_does_not_need_qiskit_to_build_the_ir():
    # Nothing in the parser imports qiskit; the IR is built from its own model.
    import Parsers.staticqiskitparser as module
    assert "qiskit" not in module.__dict__


@pytest.mark.requirement("FR-07")
def test_parameters_and_source_lines_are_recorded():
    program = parse("""from qiskit import QuantumCircuit
import numpy as np
qc = QuantumCircuit(1)
qc.rz(np.pi / 4, 0)
""").program
    assert program[0].params == [pytest.approx(math.pi / 4)]
    assert program[0].metadata["lineno"] == 4


def test_loops_functions_and_comprehensions():
    program = parse("""
from qiskit import QuantumCircuit
def ladder(circuit, n):
    for q in range(n - 1):
        circuit.cx(q, q + 1)
n = 4
qc = QuantumCircuit(n)
qc.h([q for q in range(n) if q % 2 == 0])
ladder(qc, n)
""").program
    assert shape(program) == [("h", [0], []), ("h", [2], []), ("cx", [0, 1], []),
                              ("cx", [1, 2], []), ("cx", [2, 3], [])]


def test_registers_slices_and_broadcasting():
    program = parse("""
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
a = QuantumRegister(2, "a")
b = QuantumRegister(3, "b")
c = ClassicalRegister(2, "c")
qc = QuantumCircuit(a, b, c)
qc.h(b)
qc.cx(a[0], b[1:3])
qc.measure(b[0:2], c)
""").program
    assert (program.num_qubits, program.num_clbits) == (5, 2)
    assert shape(program) == [("h", [2], []), ("h", [3], []), ("h", [4], []),
                              ("cx", [0, 3], []), ("cx", [0, 4], []),
                              ("measure", [2], [0]), ("measure", [3], [1])]


def test_if_test_with_else_collects_both_bodies():
    program = parse("""
from qiskit import QuantumCircuit
qc = QuantumCircuit(4, 3)
qc.measure(0, 0)
with qc.if_test((qc.clbits[2], 1)) as else_:
    qc.x(3)
    qc.measure(1, 1)
with else_:
    qc.z(0)
""").program
    instruction = program[1]
    assert instruction.name == "if_else"
    assert (instruction.qubits, instruction.clbits) == ([3, 1, 0], [2, 1])
    assert instruction.metadata["condition_clbits"] == [2]
    assert [len(body.data) for body in instruction.params] == [2, 1]


def test_compose_gates_and_measure_all():
    program = parse("""
from qiskit import QuantumCircuit
oracle = QuantumCircuit(2, name="Oracle")
oracle.cz(0, 1)
qc = QuantumCircuit(3)
qc.compose(oracle.to_gate(), qubits=[2, 0], inplace=True)
qc.compose(oracle, qubits=[1, 2], inplace=True)
qc.append(oracle.to_gate().inverse(), [0, 1])
qc.measure_all()
""").program
    assert shape(program) == [
        ("Oracle", [2, 0], []), ("cz", [1, 2], []), ("Oracle_dg", [0, 1], []),
        ("barrier", [0, 1, 2], []),
        ("measure", [0], [0]), ("measure", [1], [1]), ("measure", [2], [2]),
    ]
    assert program.num_clbits == 3


def test_seeded_random_angles_match_a_real_run():
    import numpy as np
    program = parse("""
import numpy as np
from qiskit import QuantumCircuit
rng = np.random.default_rng(7)
qc = QuantumCircuit(1)
qc.ry(float(rng.uniform(0, np.pi)), 0)
""").program
    expected = np.random.default_rng(7).uniform(0, np.pi)
    assert program[0].params == [pytest.approx(expected)]


def test_unknown_values_are_symbols_not_failures():
    parser = parse("""
import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
theta = Parameter("t")
qc = QuantumCircuit(1)
qc.rx(2 * theta, 0)
qc.ry(np.random.uniform(), 0)
""")
    assert str(parser.program[0].params[0]) == "2*t"
    assert str(parser.program[1].params[0]) == "np.random.uniform()"
    assert any("unseeded" in w for w in parser.warnings)


def test_unrelated_unknown_code_is_skipped_with_a_warning():
    parser = parse("""
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)
counts = AerSimulator().run(transpile(qc)).result().get_counts()
for key in counts:
    print(key, counts[key])
""")
    assert len(parser.program) == 2
    assert any("skipped" in w for w in parser.warnings)


@pytest.mark.parametrize("body, message", [
    ("for q in some_unknown_call():\n    qc.h(q)", "works on a circuit"),
    ("qc.h(some_unknown_call())", "cannot be determined"),
    ("while True:\n    pass", "too long"),
    ("x = 'a' * 10 ** 9", "too large"),
    ("qc.h(5)", "out of range"),
    ("eval('qc.h(0)')", "eval/exec/compile"),
    ("qc.__class__", "private attribute"),
])
def test_what_cannot_be_known_is_refused_with_a_line(body, message):
    source = "from qiskit import QuantumCircuit\nfrom lib import some_unknown_call\n"
    source += "qc = QuantumCircuit(2)\n" + body + "\n"
    with pytest.raises(StaticParseError, match=message) as info:
        parse(source)
    assert info.value.lineno is not None


def test_getattr_dispatch_is_followed():
    program = parse("""
from qiskit import QuantumCircuit
qc = QuantumCircuit(2)
for name in ("h", "x"):
    getattr(qc, name)(1)
""").program
    assert shape(program) == [("h", [1], []), ("x", [1], [])]


def test_missing_circuit_uses_the_qiskit_parser_message():
    with pytest.raises(ValueError, match="No QuantumCircuit assignment found in script"):
        parse("x = 1\n")


def test_main_block_follows_run_name():
    source = """
from qiskit import QuantumCircuit
if __name__ == "__main__":
    qc = QuantumCircuit(1)
    qc.x(0)
"""
    with pytest.raises(ValueError):
        parse(source)
    assert len(parse(source, run_name="__main__").program) == 1


@pytest.mark.requirement("NFR-08")
def test_parse_is_deterministic_and_reparse_does_not_duplicate():
    source = open(os.path.join(EXAMPLES_DIR, "variational_ansatz.py")).read()
    parser = parse(source)
    first = shape(parser.program)
    assert shape(parser.parse()) == first
    assert shape(parse(source).program) == first
