"""StaticQiskitParser: the same IR as QiskitParser, without running the file."""

import glob
import io
import math
import os
import time
from contextlib import redirect_stdout

import pytest

from qslicer.parsers.qiskitparser import QiskitParser
from qslicer.parsers.staticqiskitparser import StaticParseError, StaticQiskitParser

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "qslicer", "examples")
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
import time
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
    import qslicer.parsers.staticqiskitparser as module
    assert "qiskit" not in module.__dict__


@pytest.mark.requirement("FR-07")
def test_parameters_and_source_lines_are_recorded():
    program = parse("""from qiskit import QuantumCircuit
import numpy as np
qc = QuantumCircuit(1)
qc.rz(np.pi / 4, 0)
""").program
    assert program[0].params == [pytest.approx(math.pi / 4)]
    location = program[0].location
    assert (location.line, location.column, location.end_line, location.end_column) == (4, 0, 4, 19)
    assert location.via == ()


@pytest.mark.requirement("FR-07")
def test_locations_follow_calls_loops_and_blocks():
    program = parse("""from qiskit import QuantumCircuit
def entangle(circuit, a, b):
    circuit.h(a)
    circuit.cx(a, b)

qc = QuantumCircuit(3, 1)
entangle(qc, 0, 1)
for q in range(2):
    qc.x(q)
qc.measure(0, 0)
with qc.if_test((qc.clbits[0], 1)):
    qc.z(2)
""").program
    lines = [(i.name, i.location.line, i.location.via) for i in program.instructions]
    assert lines == [("h", 3, (7,)), ("cx", 4, (7,)), ("x", 9, ()), ("x", 9, ()),
                     ("measure", 10, ()), ("if_else", 11, ()), ("z", 12, ())]
    # The predicate spans the whole `with` block; its body gate keeps its own line.
    predicate, body = program[5], program[6]
    assert (predicate.location.line, predicate.location.end_line) == (11, 12)
    assert body.metadata["guards"] == ((5, 0),)


@pytest.mark.requirement("FR-07")
def test_composed_gates_keep_where_they_were_written():
    program = parse("""from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT
sub = QuantumCircuit(2)
sub.cz(0, 1)
qc = QuantumCircuit(2)
qc.compose(sub, inplace=True)
qc.compose(QFT(2).decompose(), inplace=True)
""").program
    assert program[0].location.line == 4          # where the cz was written
    assert {i.location.line for i in program.instructions[1:]} == {7}   # library gates: the compose


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
    # measure, if_else (predicate), x, measure (true body), z (else body)
    instruction = program[1]
    assert instruction.name == "if_else"
    assert (instruction.qubits, instruction.clbits, instruction.params) == ([], [], [])
    assert instruction.metadata["condition_clbits"] == [2]
    assert shape(program)[2:] == [("x", [3], []), ("measure", [1], [1]), ("z", [0], [])]
    assert [i.metadata["guards"] for i in program.instructions[2:]] == [
        ((1, 0),), ((1, 0),), ((1, 1),)]
    assert all(i.metadata["condition"] == instruction.metadata["condition"]
               and i.metadata["condition_clbits"] == [] for i in program.instructions[2:])


NESTED_IF = """
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
q = QuantumRegister(3, "q")
c = ClassicalRegister(2, "c")
d = ClassicalRegister(1, "d")
qc = QuantumCircuit(q, c, d)
qc.h(0)
qc.measure(0, 0)
with qc.if_test((c[0], 1)) as else_:
    qc.x(1)
    qc.measure(1, 1)
    with qc.if_test((c, 3)):
        qc.cx(1, 2)
    with qc.if_test((d[0], 1)):
        qc.z(0)
with else_:
    qc.y(2)
    qc.measure(2, 2)
qc.h(2)
"""


def test_if_else_with_nested_ifs_flattens_the_same_from_both_frontends(tmp_path):
    path = tmp_path / "nested_if.py"
    path.write_text(NESTED_IF)
    static = StaticQiskitParser().parse_file(str(path))
    executed = QiskitParser().parse_file(str(path))

    def flat(program):
        return [(i.name, list(i.qubits), list(i.clbits), tuple(i.metadata["guards"]),
                 list(i.metadata["condition_clbits"])) for i in program.instructions]

    assert flat(static) == flat(executed) == [
        ("h", [0], [], (), []),
        ("measure", [0], [0], (), []),
        ("if_else", [], [], (), [0]),                         # 2
        ("x", [1], [], ((2, 0),), []),
        ("measure", [1], [1], ((2, 0),), []),
        ("if_else", [], [], ((2, 0),), [0, 1]),               # 5  reads the register
        ("cx", [1, 2], [], ((2, 0), (5, 0)), []),
        ("if_else", [], [], ((2, 0),), [2]),                  # 7
        ("z", [0], [], ((2, 0), (7, 0)), []),
        ("y", [2], [], ((2, 1),), []),                        # the else body
        ("measure", [2], [2], ((2, 1),), []),
        ("h", [2], [], (), []),
    ]
    # A predicate keeps its own condition; every body instruction reads as
    # conditioned, on the condition of the predicate it sits directly under.
    for program in (static, executed):
        for i in program.instructions:
            guards = i.metadata["guards"]
            if i.name == "if_else":
                assert i.metadata["condition"] is not None
            elif guards:
                assert i.metadata["condition"] is program[guards[-1][0]].metadata["condition"]
            else:
                assert i.metadata["condition"] is None


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


def test_polling_loop_ends_once_skipped_code_may_have_emptied_its_list():
    # Bugs4Q StackExchange/6: the skipped `if` would remove finished jobs, so
    # `while job_list` must not keep seeing the full list until the time limit.
    started = time.monotonic()
    parser = parse("""
from qiskit import QuantumCircuit
from lib import run, DONE
qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)
job_list = [run(qc), run(qc)]
while job_list:
    for job in job_list:
        if job.status() in DONE:
            job_list.remove(job)
            print(job.result())
""", time_limit=5)
    assert time.monotonic() - started < 1
    assert len(parser.program) == 2
    assert any("stops being known" in w for w in parser.warnings)


def test_skipped_code_forgets_what_it_may_have_changed_in_place():
    source = """
from qiskit import QuantumCircuit
from lib import some_unknown_call
qc = QuantumCircuit(2)
targets = [0]
if some_unknown_call():
    targets.append(1)
for t in targets:
    qc.h(t)
"""
    with pytest.raises(StaticParseError, match="works on a circuit"):
        parse(source)


def test_while_condition_turning_unknown_over_circuit_code_is_refused():
    source = """
from qiskit import QuantumCircuit
from lib import some_unknown_call
qc = QuantumCircuit(1)
pending = [1]
while pending:
    qc.h(0)
    if some_unknown_call():
        pending.pop()
"""
    with pytest.raises(StaticParseError, match="part-way"):
        parse(source)


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
    assert len(parse(source).program) == 1          # read as a script by default
    with pytest.raises(ValueError):
        parse(source, run_name="<run_path>")


@pytest.mark.requirement("NFR-08")
def test_parse_is_deterministic_and_reparse_does_not_duplicate():
    source = open(os.path.join(EXAMPLES_DIR, "variational_ansatz.py")).read()
    parser = parse(source)
    first = shape(parser.program)
    assert shape(parser.parse()) == first
    assert shape(parse(source).program) == first


# --------------------------------------------------------------------------
# from qiskit import *
# --------------------------------------------------------------------------

STAR_PROGRAM = """\
from qiskit import *
qr = QuantumRegister(2)
cr = ClassicalRegister(2)
qc = QuantumCircuit(qr, cr)
qc.h(qr[0])
qc.cx(qr[0], qr[1])
qc.measure(qr, cr)
backend = Aer.get_backend('qasm_simulator')
compiled = transpile(qc, backend)
counts = execute(qc, backend).result().get_counts()
"""


def test_star_import_resolves_modelled_names_and_tolerates_the_rest():
    parser = StaticQiskitParser()
    parser.source = STAR_PROGRAM
    program = parser.parse()
    assert shape(program) == [("h", [0], []), ("cx", [0, 1], []),
                              ("measure", [0], [0]), ("measure", [1], [1])]
    noted = " ".join(parser.warnings)
    for name in ("Aer", "transpile", "execute"):
        assert f"`{name}` is taken to come from `from qiskit import *`" in noted


def test_star_import_name_a_circuit_depends_on_still_stops_the_parse():
    source = "from qiskit import *\nqc = QuantumCircuit(mystery_size)\nqc.h(0)\n"
    with pytest.raises(StaticParseError):
        parse(source)


def test_without_a_star_import_an_undefined_name_is_still_an_error():
    with pytest.raises(StaticParseError, match="NameError"):
        parse("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)\nAer.x\n")


# --------------------------------------------------------------------------
# Scripts: main(), argparse, values only a run decides, several circuits
# --------------------------------------------------------------------------

SCRIPT = '''
import argparse
import random
from functools import lru_cache
from qiskit import QuantumCircuit


def ghz(n):
    qc = QuantumCircuit(n, name=f"ghz{n}")
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    return qc


@lru_cache(maxsize=None)
def build(n, k):
    outer = QuantumCircuit(n, n, name="outer")
    outer.compose(ghz(n), inplace=True)
    outer.rz(0.5, k - 1)       # k picks the qubit, so the circuit depends on it
    outer.measure(range(n), range(n))
    return outer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default="3")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    k = rng.randrange(1, 4)
    for _ in range(5):
        circuit = build(args.n, k)
    return circuit


if __name__ == "__main__":
    main()
'''


def test_script_asks_for_an_unseeded_draw_by_its_variable():
    with pytest.raises(StaticParseError) as info:
        parse(SCRIPT)
    assert info.value.needs == {"name": "k", "line": 32, "kind": "variable",
                                "expression": "rng.randrange(1, 4)"}


def test_bindings_fill_in_the_value_and_argparse_defaults_apply():
    parser = parse(SCRIPT, bindings={(32, "k"): 2})
    assert parser.used_bindings == [(32, "k")]
    # n comes from the string default "3", run through type=int as argparse would.
    assert parser.circuit.name == "outer" and parser.circuit.num_qubits == 3
    assert ("rz", [1], []) in shape(parser.program)
    assert any("take their defaults (n=3, seed=None, verbose=False)" in w for w in parser.warnings)


def test_lru_cache_returns_the_same_circuit_and_circuits_are_listed():
    parser = parse(SCRIPT, bindings={(32, "k"): 2})
    # Five calls, one cached circuit: "outer" appears once, the GHZ inside it after.
    assert [(c["name"], c["used_inside"]) for c in parser.circuits] == [
        ("outer", False), ("ghz3", True)]
    assert parser.circuits[0]["line"] == 18 and parser.circuits[1]["line"] == 9
    sub = parser.select(1)
    assert shape(sub) == [("h", [0], []), ("cx", [0, 1], []), ("cx", [1, 2], [])]


def test_required_command_line_argument_asks_for_a_value():
    source = '''
import argparse
from qiskit import QuantumCircuit

ap = argparse.ArgumentParser()
ap.add_argument("qubits", type=int)
args = ap.parse_args()
qc = QuantumCircuit(args.qubits)
qc.h(0)
'''
    with pytest.raises(StaticParseError) as info:
        parse(source)
    assert info.value.needs["name"] == "qubits" and info.value.needs["kind"] == "argument"
    assert info.value.needs["line"] == 6
    assert parse(source, bindings={(6, "qubits"): 4}).circuit.num_qubits == 4


def test_module_level_circuits_keep_the_largest_as_main_and_list_the_rest():
    parser = parse('''
from qiskit import QuantumCircuit
small = QuantumCircuit(1)
small.x(0)
big = QuantumCircuit(2)
big.h(0)
big.cx(0, 1)
''')
    assert [(c["variable"], c["size"]) for c in parser.circuits] == [("big", 2), ("small", 1)]


def test_an_if_else_spans_its_else_block():
    program = parse(
        "from qiskit import QuantumCircuit\n"
        "qc = QuantumCircuit(2, 1)\n"
        "qc.measure(0, 0)\n"
        "with qc.if_test((qc.clbits[0], 1)) as else_:\n"   # line 4
        "    qc.x(1)\n"
        "with else_:\n"
        "    qc.z(1)\n"
        "    qc.h(1)\n"                                     # line 8
    ).program
    predicate = program[1]
    assert predicate.name == "if_else"
    assert (predicate.location.line, predicate.location.end_line) == (4, 8)


STANDALONE_BITS = """
from qiskit import QuantumRegister
from qiskit.circuit import QuantumCircuit, Qubit, Clbit, AncillaQubit
bits = [Qubit(), Qubit(), Qubit(), Clbit(), Clbit()]
qc = QuantumCircuit(bits)
qc.h(0)
qc.cx(0, 1)
qc.measure(0, 0)
qc.measure(bits[2], bits[4])
qc.h(bits[0])
qc.x(qc.qubits[1:])
qc.add_bits((AncillaQubit(), Clbit()))
q = QuantumRegister(2, "q")
qc.add_register(q, [Qubit()])
qc.cx(3, q[1])
qc.measure(q, [bits[3], qc.clbits[2]])
qc.h(Qubit(q, 0))                   # the old form names q[0]
qc.cx(4, -1)
mixed = QuantumCircuit(QuantumRegister(1, "r"), [Clbit(), Clbit()])
mixed.measure(0, 1)
"""


def test_bits_without_a_register_match_the_executing_parser(tmp_path):
    path = tmp_path / "standalone_bits.py"
    path.write_text(STANDALONE_BITS)
    static = StaticQiskitParser().parse_file(str(path))
    with redirect_stdout(io.StringIO()):
        executed = QiskitParser().parse_file(str(path))
    assert (static.num_qubits, static.num_clbits) == (executed.num_qubits, executed.num_clbits) == (7, 3)
    assert shape(static) == shape(executed)
    assert shape(static)[:4] == [("h", [0], []), ("cx", [0, 1], []), ("measure", [0], [0]),
                                 ("measure", [2], [1])]


@pytest.mark.parametrize("body, message", [
    # Each mirrors the error Qiskit itself raises.
    ("qc = QuantumCircuit([b], [b])", "CircuitError: Attempted to add bits found already"),
    ("qc = QuantumCircuit([b])\nqc.add_bits([b])", "CircuitError: Attempted to add bits found already"),
    ("qc = QuantumCircuit([b, b])", "ValueError"),
    ("qc = QuantumCircuit((b,))", "Circuit args must be Registers or integers"),
    ("qc = QuantumCircuit(2, [Clbit()])", "Circuit args must be Registers or integers"),
    ("qc = QuantumCircuit([b, 3])", "Expected an instance of Qubit, Clbit, or AncillaQubit"),
    ("qc = QuantumCircuit([b])\nqc.add_bits(Qubit())", "object is not iterable"),
    ("qc = QuantumCircuit([b])\nqc.add_register((Qubit(),))", "expected a register"),
    ("qc = QuantumCircuit([b])\nqc.h(Qubit())", "is not a qubit of circuit"),
    ("qc = QuantumCircuit([b])\nqc.h(1)", "out of range"),
    ("qc = QuantumCircuit(q)\nqc.h(Qubit(q, 5))", "IndexError"),
    ("qc = QuantumCircuit(q)\nqc.measure(0, Clbit(q, 0))", "not an instance of 'ClassicalRegister'"),
    ("qc = QuantumCircuit([some_unknown_call()])", "cannot be determined"),
])
def test_standalone_bits_are_refused_where_qiskit_refuses_them(body, message):
    source = ("from qiskit.circuit import QuantumCircuit, QuantumRegister, Qubit, Clbit\n"
              "from lib import some_unknown_call\nb = Qubit()\nq = QuantumRegister(2)\n" + body + "\n")
    with pytest.raises(StaticParseError, match=message) as info:
        parse(source)
    assert info.value.lineno is not None


def test_bits_come_only_from_where_qiskit_exports_them():
    # qiskit.circuit has Qubit; the qiskit package does not, so a real run fails to import it.
    program = parse("import qiskit.circuit as qcm\n"
                    "qc = qcm.QuantumCircuit([qcm.Qubit(), qcm.Clbit()])\n"
                    "qc.measure(0, 0)\n").program
    assert shape(program) == [("measure", [0], [0])]
    with pytest.raises(StaticParseError, match="cannot be determined"):
        parse("from qiskit import QuantumCircuit, Qubit\nqc = QuantumCircuit([Qubit()])\n")
