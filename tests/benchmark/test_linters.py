"""The linter registry, rule selection, and the dependence-checks add-on."""

import textwrap
import time

import pytest

import linters
from linters import LintContext, Linter, Rule
from Parsers.staticqiskitparser import StaticParseError, StaticQiskitParser

BAD_INDEX = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(5)\n"


def py(body):
    return "from qiskit import QuantumCircuit\n" + textwrap.dedent(body)


# --------------------------------------------------------------------------
# Registry and selection
# --------------------------------------------------------------------------

def test_catalog_has_the_quick_checks_and_the_dependence_add_on():
    entries = {entry["id"]: entry for entry in linters.catalog()}
    assert {"quick", "dependence"} <= set(entries)
    assert [r["code"] for r in entries["quick"]["rules"]][:3] == ["QS000", "QS001", "QS002"]
    assert entries["quick"]["default"] and entries["dependence"]["default"]
    assert not entries["lintq"]["default"]
    assert linters.plugin_errors() == []


def test_default_selection_is_the_quick_and_dependence_checks():
    assert linters.default_selection() == (linters.get("quick").rule_ids()
                                           + linters.get("dependence").rule_ids())
    assert linters.normalize_selection(None) == linters.default_selection()


def test_quick_checks_are_errors_and_dependence_checks_the_quantum_side():
    # Quick checks: what Python or Qiskit would reject, so errors; the warnings are API
    # misuse that still runs (QS009 execute(), QS005 deprecated APIs, QS006 a discarded
    # result, QS007 an unpacked list in a fixed-size gate, QS015 a classical bit assumed
    # on a given circuit, QS016 counts from a circuit that may never be measured). The
    # quantum-side rules live in the dependence checks.
    quick = {r.code: r.severity for r in linters.get("quick").rules}
    assert {code for code, severity in quick.items() if severity != "error"} == {
        "QS005", "QS006", "QS007", "QS009", "QS015", "QS016"}
    assert list(quick) == [f"QS{n:03d}" for n in range(len(quick))]     # no gaps, in order
    codes = [r.code for r in linters.get("dependence").rules]
    assert codes == ["QD01", "QD02", "QD03", "QD05", "QD06", "QD07", "QD08", "QD09", "QD10"]


def test_bare_linter_id_selects_all_its_rules_and_unknown_ids_drop():
    chosen = linters.normalize_selection(["dependence", "quick/QS002", "nope/X", "quick/ZZ"])
    assert chosen == ["quick/QS002"] + linters.get("dependence").rule_ids()


def test_run_reports_only_selected_rules_and_tags_them():
    assert [d["code"] for d in linters.run(BAD_INDEX, selection=["quick/QS001"])] == []
    found = linters.run(BAD_INDEX, selection=["quick/QS002"])
    assert [d["code"] for d in found] == ["QS002"]
    assert found[0]["tool"] == "quick" and found[0]["rule"] == "quick/QS002"
    assert linters.run(BAD_INDEX, selection=[]) == []


def test_python_only_linter_skips_openqasm():
    qasm = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\nh q[0];\n'
    assert linters.run(qasm, "a.qasm", "qasm-static", ["quick"]) == []


def test_crashing_linter_reports_instead_of_raising(monkeypatch):
    def boom(context):
        raise RuntimeError("kaput")
    monkeypatch.setitem(linters._LINTERS, "boom",
                        Linter("boom", "Boom", "", [Rule("B1", "b")], boom, default=False))
    found = linters.run("x = 1\n", selection=["boom"])
    assert len(found) == 1 and "kaput" in found[0]["message"] and found[0]["tool"] == "boom"


def test_unavailable_linter_does_not_run(monkeypatch):
    monkeypatch.setitem(linters._LINTERS, "gone", Linter(
        "gone", "Gone", "", [Rule("G1", "g")],
        lambda c: [{"code": "G1", "severity": "info", "line": 1, "column": 0,
                    "message": "", "hint": ""}],
        default=False, available=lambda: (False, "not installed")))
    assert linters.run("x = 1\n", selection=["gone"]) == []
    entry = next(e for e in linters.catalog() if e["id"] == "gone")
    assert entry["available"] is False and entry["unavailable_reason"] == "not installed"


def test_add_on_named_in_the_environment_is_loaded(monkeypatch, tmp_path):
    (tmp_path / "my_addon.py").write_text(textwrap.dedent("""
        from linters import Linter, Rule
        LINTER = Linter("mine", "Mine", "", [Rule("M1", "m")], lambda c: [], default=False)
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("QSLICER_LINTER_PLUGINS", "my_addon, missing_addon")
    monkeypatch.setattr(linters, "_LOADED", False)
    monkeypatch.setattr(linters, "_LINTERS", dict(linters._LINTERS))
    monkeypatch.setattr(linters, "_PLUGIN_ERRORS", [])
    assert linters.get("mine") is not None
    assert any("missing_addon" in error for error in linters.plugin_errors())


def test_context_parses_once():
    context = LintContext(py("qc = QuantumCircuit(1, 1)\nqc.measure(0, 0)\n"))
    first = context.program()
    assert first[0] is not None and context.program() is first


# --------------------------------------------------------------------------
# Dependence checks
# --------------------------------------------------------------------------

def dependence(source, code):
    return [d for d in linters.run(source, selection=[f"dependence/{code}"])]


def quick(source, code):
    return [d for d in linters.run(source, selection=[f"quick/{code}"])]


def test_dead_gate_after_the_last_measurement():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.measure(0, 0)
        qc.x(0)
        qc.h(1)
    """)
    lines = [d["line"] for d in dependence(source, "QD01")]
    assert lines == [6, 7]       # x after the measurement; h on a qubit never measured


def test_gate_feeding_a_measurement_is_live():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.h(1)
        qc.cx(1, 0)
        qc.measure(0, 0)
    """)
    assert dependence(source, "QD01") == []


def test_block_that_measures_inside_is_live():
    # The if_test lists only its condition bit; the measurement in its body writes
    # the output, so the block is not dead.
    source = py("""
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.measure(0, 0)
        with qc.if_test((qc.clbits[0], 1)):
            qc.x(1)
            qc.measure(1, 0)
    """)
    assert dependence(source, "QD01") == []


def test_block_without_a_write_after_the_last_measurement_is_dead():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.measure(0, 0)
        with qc.if_test((qc.clbits[0], 1)):
            qc.x(1)
    """)
    assert [d["line"] for d in dependence(source, "QD01")] == [6]


def test_store_after_the_last_measurement_is_live():
    qasm = ('OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[1] q;\nbit[2] c;\n'
            'h q[0];\nc[0] = measure q[0];\nc[1] = c[0];\n')
    assert linters.run(qasm, "a.qasm", "qasm-static", ["dependence/QD01"]) == []


def test_dead_gate_in_a_loop_is_reported_once():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.measure(0, 0)
        for _ in range(3):
            qc.h(1)
    """)
    found = dependence(source, "QD01")
    assert len(found) == 1 and "3 instances" in found[0]["message"]


def test_overwritten_measurement():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.h(1)
        qc.measure(0, 0)
        qc.measure(1, 0)
    """)
    found = dependence(source, "QD02")
    assert [d["line"] for d in found] == [7] and "line 6" in found[0]["message"]


def test_measurement_read_by_a_condition_is_not_overwritten():
    source = py("""
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.measure(0, 0)
        with qc.if_test((qc.clbits[0], 1)):
            qc.x(1)
        qc.measure(1, 0)
    """)
    assert dependence(source, "QD02") == []


def test_constant_measurement():
    source = py("""
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.measure(0, 0)
        qc.measure(1, 1)
    """)
    assert [d["line"] for d in dependence(source, "QD03")] == [6]


def test_constant_measurement_leaves_a_given_circuit_to_its_caller():
    # A kata test passes |0>; the solution measuring it is not wrong for that.
    source = py("""
        def measure_qubit(qc: QuantumCircuit, q: int):
            qc.measure(q, 0)
            return qc

        qc = measure_qubit(QuantumCircuit(1, 1), 0)
    """)
    assert dependence(source, "QD03") == []


def test_qubit_index_out_of_range_inside_a_function():
    source = py("""
        def entangle(qc, qs):
            qc.h(qs[0])
            qc.cx(qs[0], qs[1])
            return qc

        qc = entangle(QuantumCircuit(2), [0, 2])
    """)
    found = quick(source, "QS014")
    assert [d["line"] for d in found] == [5]       # the gate, not the call
    assert found[0]["severity"] == "error" and "Index 2 out of range" in found[0]["message"]


def test_same_qubit_twice_in_a_loop():
    source = py("""
        qc = QuantumCircuit(3)
        for i in range(2):
            qc.cx(i, i)
    """)
    found = quick(source, "QS014")
    assert [d["line"] for d in found] == [5] and "duplicate qubit" in found[0]["message"]


def test_ghz_with_control_and_target_swapped():
    source = py("""
        def ghz(n):
            qc = QuantumCircuit(n)
            qc.h(0)
            for i in range(n - 1):
                qc.cx(i + 1, i)
            return qc

        qc = ghz(4)
    """)
    found = dependence(source, "QD05")
    assert [d["line"] for d in found] == [7] and "3 instances" in found[0]["message"]


def test_ghz_built_correctly_is_quiet():
    source = py("""
        def ghz(n):
            qc = QuantumCircuit(n)
            qc.h(0)
            for i in range(n - 1):
                qc.cx(i, i + 1)
            return qc

        qc = ghz(4)
    """)
    assert dependence(source, "QD05") == []


def test_control_prepared_too_late():
    source = py("""
        qc = QuantumCircuit(2, 2)
        qc.cx(0, 1)
        qc.h(0)
        qc.measure([0, 1], [0, 1])
    """)
    found = dependence(source, "QD05")
    assert [d["line"] for d in found] == [4] and "[0]" in found[0]["message"]


@pytest.mark.parametrize("body", [
    # A loop line that acts on some iterations: general code meeting one input (a QFT on |010>).
    "qc = QuantumCircuit(3)\nqc.x(1)\nfor t in range(3):\n    qc.h(t)\n"
    "    for c in range(t + 1, 3):\n        qc.cp(0.5, c, t)\n",
    # A function given its circuit is written for any input.
    "def oracle(qc, a, b):\n    qc.cx(a, b)\n    return qc\n\nqc = oracle(QuantumCircuit(2), 0, 1)\n",
    # Diagonal gates and a measurement leave |0> where it was; x then reset then x again acts.
    "qc = QuantumCircuit(2, 1)\nqc.x(0)\nqc.reset(0)\nqc.x(0)\nqc.cx(0, 1)\n",
    # Open controls fire on |0>; the IR does not record ctrl_state, so the rule stays out.
    "qc = QuantumCircuit(3)\nqc.mcx([0, 1], 2, ctrl_state='00')\n",
    # A classically conditioned gate may or may not have run.
    "qc = QuantumCircuit(2, 1)\nqc.h(1)\nqc.measure(1, 0)\nwith qc.if_test((qc.clbits[0], 1)):\n"
    "    qc.x(0)\nqc.cx(0, 1)\n",
])
def test_qd05_negative(body):
    assert dependence(py(body), "QD05") == []


def test_reset_makes_a_control_zero_again():
    source = py("""
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.reset(0)
        qc.cx(0, 1)
    """)
    assert [d["line"] for d in dependence(source, "QD05")] == [6]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(1)\nqc.h(undefined_name)\n",   # a Python error: maybe the parser's limit
    "qc = QuantumCircuit(2)\nqc.cx(0, 1)\n",            # fine
])
def test_qs014_reports_only_qiskit_errors(body):
    assert quick(py(body), "QS014") == []


# QD06 no measurement --------------------------------------------------------

def test_no_measurement():
    found = dependence(py("qc = QuantumCircuit(1)\nqc.h(0)\n"), "QD06")
    assert [d["line"] for d in found] == [2] and found[0]["severity"] == "warning"


@pytest.mark.parametrize("call", ["qc.measure(0, 0)", "qc.measure_all()", "qc.measure_active()"])
def test_measured_circuits_are_quiet(call):
    assert dependence(py(f"qc = QuantumCircuit(1, 1)\nqc.h(0)\n{call}\n"), "QD06") == []


def test_a_measurement_in_a_helper_counts():
    source = py("def finish(c):\n    c.measure_all()\n\nqc = QuantumCircuit(2)\nqc.h(0)\n"
                "finish(qc)\n")
    assert dependence(source, "QD06") == []


@pytest.mark.parametrize("use", [
    "state = Statevector(qc)",
    "state = Statevector.from_instruction(qc)",
    "unitary = Operator(qc)",
    "job = estimator.run([(qc, observable)])",              # reads expectation values
    "flip(qc, 0)",                                           # a helper may return it to measure
    "state = execute(qc, sim).result().get_statevector()",   # statevector simulator
])
def test_no_measurement_is_fine_when_the_state_is_read(use):
    source = py(f"def flip(qc, q):\n    qc.x(q)\n\nqc = QuantumCircuit(1)\nqc.h(0)\n{use}\n")
    assert dependence(source, "QD06") == []


@pytest.mark.parametrize("use", [
    "counts = backend.run(qc).result().get_counts()",
    "job = backend.run(qc)",
    "job = sampler.run([qc])",
    "flip(qc, 0)\njob = backend.run(qc)",                    # also sampled: still a mistake
])
def test_sampling_an_unmeasured_circuit(use):
    # The static parser has to rebuild the program, so the backends are real names.
    setup = ("from qiskit_aer import AerSimulator\nfrom qiskit.primitives import StatevectorSampler\n"
             "backend = AerSimulator()\nsampler = StatevectorSampler()\n")
    source = py(setup + f"def flip(qc, q):\n    qc.x(q)\n\nqc = QuantumCircuit(1)\nqc.h(0)\n{use}\n")
    assert [d["code"] for d in dependence(source, "QD06")] == ["QD06"]


# QD07 idle qubit ------------------------------------------------------------

def test_idle_qubits():
    found = dependence(py("qc = QuantumCircuit(4, 1)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(0, 0)\n"),
                       "QD07")
    assert [d["line"] for d in found] == [2] and found[0]["severity"] == "info"
    assert "[2, 3]" in found[0]["message"]
    assert found[0]["qubits"] == [2, 3]          # the graph marks these wires red


@pytest.mark.parametrize("body, idle", [
    # The rebuilt circuit sees through loops and helpers, which a literal reading cannot.
    ("qc = QuantumCircuit(4)\nfor q in range(3):\n    qc.h(q)\n", "[3]"),
    ("def prepare(qc):\n    qc.x(2)\n\nqc = QuantumCircuit(3)\nqc.h(0)\nprepare(qc)\n", "[1]"),
    # A barrier spans every qubit without using any.
    ("qc = QuantumCircuit(2)\nqc.h(0)\nqc.barrier()\n", "[1]"),
])
def test_idle_qubits_through_loops_helpers_and_barriers(body, idle):
    found = dependence(py(body), "QD07")
    assert len(found) == 1 and idle in found[0]["message"]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(3)\nfor i in range(3):\n    qc.h(i)\n",
    "qc = QuantumCircuit(3)\nqc.h(0)\nqc.measure_all()\n",   # measured qubits are used (QD03's)
])
def test_qd07_negative(body):
    assert dependence(py(body), "QD07") == []


# QD02 also covers two measurements into one literal bit (the old quick QS007) ------

def test_two_literal_measurements_into_one_bit():
    found = dependence(py("qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.measure(0, 0)\n"
                          "qc.measure(1, 0)\n"), "QD02")
    assert [d["line"] for d in found] == [5] and "line 4" in found[0]["message"]


# QD08 cancelling pair -------------------------------------------------------

def test_cancelling_pair():
    found = dependence(py("qc = QuantumCircuit(2, 2)\nqc.cx(0, 1)\nqc.cx(0, 1)\nqc.measure_all()\n"),
                       "QD08")
    assert [d["line"] for d in found] == [4] and found[0]["severity"] == "info"
    assert "line 3" in found[0]["message"] and found[0]["lines"] == [3, 4]
    assert found[0]["nodes"] == [0, 1]


def test_cancelling_pair_from_a_loop():
    found = dependence(py("qc = QuantumCircuit(1, 1)\nfor _ in range(2):\n    qc.h(0)\n"
                          "qc.measure(0, 0)\n"), "QD08")
    assert [d["line"] for d in found] == [4] and "this same line" in found[0]["message"]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(2)\nqc.cx(0, 1)\nqc.cx(1, 0)\nqc.measure_all()\n",      # other order
    "qc = QuantumCircuit(1)\nqc.h(0)\nqc.barrier()\nqc.h(0)\nqc.measure_all()\n",  # a barrier
    "qc = QuantumCircuit(1)\nqc.s(0)\nqc.s(0)\nqc.measure_all()\n",              # not self-inverse
    "qc = QuantumCircuit(2)\nqc.x(0)\nqc.cx(0, 1)\nqc.x(0)\nqc.measure_all()\n",  # cx in between
    "qc = QuantumCircuit(2, 1)\nqc.h(1)\nqc.measure(1, 0)\nqc.x(0)\n"
    "with qc.if_test((qc.clbits[0], 1)):\n    qc.x(0)\nqc.measure_all()\n",       # conditional
])
def test_qd08_negative(body):
    assert dependence(py(body), "QD08") == []


def test_showcase_example_trips_the_advertised_checks():
    import examples
    found = {d["code"] for d in linters.run(examples.load("linter_showcase")["source"],
                                              selection=["dependence"])}
    assert {"QD06", "QD07", "QD08"} <= found


def test_unreadable_program_gets_no_dependence_findings():
    assert linters.run("import os\nos.system('x')\n", selection=["dependence"]) == []


def test_dependence_checks_read_openqasm():
    qasm = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[1];\n'
            'h q[0];\nmeasure q[0] -> c[0];\nx q[1];\n')
    found = linters.run(qasm, "a.qasm", "qasm-static", ["dependence/QD01"])
    assert [d["line"] for d in found] == [7]


# --------------------------------------------------------------------------
# The static parser's time limit, which keeps a benchmark run bounded
# --------------------------------------------------------------------------

def test_static_parse_time_limit():
    source = "jobs = [object()]\nwhile jobs:\n    for job in jobs:\n        job.status()\n"
    parser = StaticQiskitParser(filename="poll.py", time_limit=0.3)
    parser.source = source
    started = time.monotonic()
    with pytest.raises(StaticParseError, match="time limit"):
        parser.parse()
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("body", [
    # The test's preparation and the solution's own gate: they cancel only for this input.
    "def flip(qc, q):\n    qc.x(q)\n    return qc\n\nqc = QuantumCircuit(1, 1)\nqc.x(0)\n"
    "flip(qc, 0)\nqc.measure(0, 0)\n",
    # Two calls of one helper.
    "def flip(qc):\n    qc.x(0)\n\nqc = QuantumCircuit(1, 1)\nflip(qc)\nflip(qc)\nqc.measure(0, 0)\n",
    # An oracle handed its circuit: compute/uncompute meets nothing in between on this input.
    "def oracle(qc, q, t):\n    qc.x(q)\n    if q == t:\n        qc.h(t)\n    qc.x(q)\n    return qc\n\n"
    "qc = oracle(QuantumCircuit(2, 1), 0, 1)\nqc.measure(0, 0)\n",
])
def test_qd08_needs_both_gates_from_the_same_code(body):
    assert dependence(py(body), "QD08") == []


# QD09 condition never set ---------------------------------------------------

@pytest.mark.parametrize("body, line", [
    # The measurement comes after the if_test.
    ("qc = QuantumCircuit(2, 1)\nqc.h(0)\nwith qc.if_test((qc.clbits[0], 1)):\n    qc.x(1)\n"
     "qc.measure(0, 0)\n", 4),
    # The measurement writes another bit.
    ("qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.measure(0, 1)\nwith qc.if_test((qc.clbits[0], 1)):\n"
     "    qc.x(1)\n", 5),
])
def test_condition_never_set(body, line):
    found = dependence(py(body), "QD09")
    assert [d["line"] for d in found] == [line] and found[0]["severity"] == "warning"


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.measure(0, 0)\nwith qc.if_test((qc.clbits[0], 1)):\n"
    "    qc.x(1)\n",
    # A bit written inside an earlier branch counts as written.
    "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.measure(0, 0)\nwith qc.if_test((qc.clbits[0], 1)):\n"
    "    qc.measure(1, 1)\nwith qc.if_test((qc.clbits[1], 1)):\n    qc.x(0)\n",
])
def test_qd09_negative(body):
    assert dependence(py(body), "QD09") == []


# QD10 gate has no effect -----------------------------------------------------

@pytest.mark.parametrize("body, line, ket", [
    ("qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.x(0)\nqc.measure(0, 0)\n", 4, "|+>"),
    ("qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.s(0)\nqc.y(0)\nqc.measure(0, 0)\n", 5, "|+i>"),
    ("qc = QuantumCircuit(1, 1)\nqc.x(0)\nqc.h(0)\nqc.sx(0)\nqc.measure(0, 0)\n", 5, "|->"),
])
def test_gate_with_no_effect(body, line, ket):
    found = dependence(py(body), "QD10")
    assert [d["line"] for d in found] == [line] and ket in found[0]["message"]
    assert len(found[0]["nodes"]) == 2               # the gate that set the state, and this one


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(1, 1)\nqc.x(0)\nqc.h(0)\nqc.measure(0, 0)\n",             # x on |0> acts
    "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.z(0)\nqc.measure(0, 0)\n",             # z on |+> acts
    # x ... x around a control: an open control, written by hand.
    "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.x(0)\nqc.cx(0, 1)\nqc.x(0)\nqc.measure(1, 0)\n",
    # The caller's h and the helper's x: other inputs reach the helper too.
    "def f(qc):\n    qc.x(0)\n\nqc = QuantumCircuit(1, 1)\nqc.h(0)\nf(qc)\nqc.measure(0, 0)\n",
    # Entangled: no single-qubit state to follow.
    "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.x(0)\nqc.measure([0, 1], [0, 1])\n",
])
def test_qd10_negative(body):
    assert dependence(py(body), "QD10") == []
