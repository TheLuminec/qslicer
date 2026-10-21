"""The LintQ port reports exactly what the original LintQ reports.

``reference.json`` holds the original's warnings (LintQ's CodeQL queries,
``LintQ-all.qls``) on LintQ's own test programs, on probe programs written for
each query's edge cases, on the bundled examples and on every Bugs4Q program;
``record_reference.py`` re-records it. Every file must get the same warnings,
compared by LintQ query id and line, from the port as from the original.
"""

import json
import os
import shutil

import pytest

from Linters import codeql_lintq, lintq_port

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
with open(os.path.join(HERE, "reference.json"), encoding="utf-8") as _handle:
    REFERENCE = json.load(_handle)


def _port(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    return sorted({(w["lintq_id"], w["line"]) for w in lintq_port.check_source(source, rel)})


def _original(rel):
    return sorted({(lintq_id, line) for lintq_id, line, _col, _msg in REFERENCE["results"][rel]})


def test_reference_covers_every_fixture_example_and_bugs4q_program():
    expected = set()
    for tree in ("tests/lintq/programs", "webapp/examples", "benchmarks/bugs4q/programs"):
        for dirpath, _dirs, names in os.walk(os.path.join(ROOT, tree)):
            expected |= {os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/")
                         for n in names if n.endswith(".py")}
    assert set(REFERENCE["results"]) == expected
    assert REFERENCE["warnings"] == sum(len(v) for v in REFERENCE["results"].values())


@pytest.mark.parametrize("rel", sorted(REFERENCE["results"]))
def test_port_matches_the_original(rel):
    assert _port(rel) == _original(rel)


def test_every_query_is_exercised():
    """Each of the ten queries fires somewhere in the reference, so each is checked."""
    seen = {lintq_id for found in REFERENCE["results"].values() for lintq_id, *_ in found}
    assert seen == {lintq_id for lintq_id, _, _ in lintq_port.RULES.values()}


def test_codes_follow_lintq_ids():
    finding = lintq_port.check_source(
        "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2, 2)\nqc.h(0)\n"
        "qc.measure(0, 0)\nqc.x(0)\nqc.measure(1, 1)\n")
    by_code = {f["code"]: f for f in finding}
    assert by_code["LQ02"]["lintq_id"] == "ql-operation-after-measurement"
    assert by_code["LQ02"]["line"] == 5
    assert by_code["LQ07"]["line"] == 2      # qubit 1 measured, never touched


def test_star_import_resolves_nothing_as_in_lintq():
    source = "from qiskit import *\nqc = QuantumCircuit(3, 1)\nqc.measure(0, 0)\nqc.h(0)\n"
    assert lintq_port.check_source(source) == []


def test_unparseable_program_gets_no_warnings():
    assert lintq_port.check_source("def broken(:\n") == []


def test_huge_program_gets_one_note(monkeypatch):
    monkeypatch.setattr(lintq_port, "MAX_OPERATORS", 5)
    source = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\n" + "qc.h(0)\n" * 6
    found = lintq_port.check_source(source)
    assert [f["code"] for f in found] == [lintq_port.TOO_LARGE]


# --------------------------------------------------------------------------
# The original, when CodeQL is configured here
# --------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not codeql_lintq.available()[0], reason="CodeQL and LintQ not configured")
def test_original_still_gives_the_recorded_warnings(tmp_path):
    """Re-run the original on the probe programs; it must still match the record."""
    probes = os.path.join(ROOT, "tests", "lintq", "programs", "probes")
    shutil.copytree(probes, tmp_path / "probes")
    live = {}
    for w in codeql_lintq.analyze_directory(str(tmp_path)):
        live.setdefault("tests/lintq/programs/" + w["path"], set()).add((w["lintq_id"], w["line"]))
    for rel, found in REFERENCE["results"].items():
        if rel.startswith("tests/lintq/programs/probes/"):
            assert live.get(rel, set()) == {(lid, line) for lid, line, *_ in found}, rel
