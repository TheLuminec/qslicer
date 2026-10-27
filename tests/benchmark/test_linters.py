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
    assert entries["quick"]["default"] and not entries["dependence"]["default"]
    assert linters.plugin_errors() == []


def test_default_selection_is_the_quick_checks():
    assert linters.default_selection() == linters.get("quick").rule_ids()
    assert linters.normalize_selection(None) == linters.default_selection()


def test_bare_linter_id_selects_all_its_rules_and_unknown_ids_drop():
    chosen = linters.normalize_selection(["dependence", "quick/QS002", "nope/X", "quick/ZZ"])
    assert chosen == ["quick/QS002"] + linters.get("dependence").rule_ids()


def test_run_reports_only_selected_rules_and_tags_them():
    assert [d["code"] for d in linters.run(BAD_INDEX, selection=["quick/QS005"])] == ["QS005"]
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


def test_qubit_index_out_of_range_inside_a_function():
    source = py("""
        def entangle(qc, qs):
            qc.h(qs[0])
            qc.cx(qs[0], qs[1])
            return qc

        qc = entangle(QuantumCircuit(2), [0, 2])
    """)
    found = dependence(source, "QD04")
    assert [d["line"] for d in found] == [5]       # the gate, not the call
    assert found[0]["severity"] == "error" and "Index 2 out of range" in found[0]["message"]


def test_same_qubit_twice_in_a_loop():
    source = py("""
        qc = QuantumCircuit(3)
        for i in range(2):
            qc.cx(i, i)
    """)
    found = dependence(source, "QD04")
    assert [d["line"] for d in found] == [5] and "duplicate qubit" in found[0]["message"]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(1)\nqc.h(undefined_name)\n",   # a Python error: maybe the parser's limit
    "qc = QuantumCircuit(2)\nqc.cx(0, 1)\n",            # fine
])
def test_qd04_reports_only_qiskit_errors(body):
    assert dependence(py(body), "QD04") == []


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
