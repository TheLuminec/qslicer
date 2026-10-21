"""Ground truth, grading, comparison and external results of the Bugs4Q benchmark."""

import json

import pytest

import benchmark
import linters
from linters import Linter, Rule


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------

def test_edit_marks_the_changed_lines():
    buggy = "a = 1\nb = 2\nc = 3\nd = 4\n"
    fixed = "a = 1\nb = 20\nc = 3\nd = 4\n"
    truth = benchmark.ground_truth(buggy, fixed)
    assert truth["kind"] == "edit" and truth["lines"] == {2}


def test_comments_blank_lines_and_indentation_are_not_faults():
    buggy = "a = 1\n# old note\n\nif a:\n  b = 2\n"
    fixed = "a = 1\n# new note\nif a:\n    b = 2\n"
    assert benchmark.ground_truth(buggy, fixed)["kind"] == "none"


def test_deleted_line_is_a_fault():
    truth = benchmark.ground_truth("a = 1\nqc.barrier()\nb = 2\n", "a = 1\nb = 2\n")
    assert truth["kind"] == "edit" and truth["lines"] == {2}


def test_omission_anchors_on_the_surrounding_code():
    buggy = "qc = QuantumCircuit(2)\n# prepare\nqc.cx(0, 1)\n"
    fixed = "qc = QuantumCircuit(2)\n# prepare\nqc.h(0)\nqc.cx(0, 1)\n"
    truth = benchmark.ground_truth(buggy, fixed)
    assert truth["kind"] == "omission"
    assert truth["lines"] == {1, 3}


def test_rewrite_when_most_of_the_program_changes():
    buggy = "a = 1\nb = 2\nc = 3\n"
    fixed = "x = 1\ny = 2\nc = 3\n"
    assert benchmark.ground_truth(buggy, fixed)["kind"] == "rewrite"


# --------------------------------------------------------------------------
# Grading one bug
# --------------------------------------------------------------------------

BUG = {"truth": {"kind": "edit", "lines": {3}}}


def warning(line, rule="t/A"):
    return {"line": line, "rule": rule, "code": rule.split("/")[-1]}


def test_detected_needs_a_hit_that_the_fix_silences():
    grade = benchmark.grade_bug(BUG, [warning(3)], [])
    assert grade["detected"] and grade["hit"] and grade["detected_by"] == ["t/A"]


def test_hit_that_survives_the_fix_is_not_a_detection():
    grade = benchmark.grade_bug(BUG, [warning(3)], [warning(3)])
    assert grade["hit"] and not grade["detected"]


def test_fewer_firings_on_the_fixed_program_is_enough():
    grade = benchmark.grade_bug(BUG, [warning(3), warning(7)], [warning(5)])
    assert grade["detected"]


def test_warning_off_the_fault_lines_is_only_warned():
    grade = benchmark.grade_bug(BUG, [warning(9)], [])
    assert grade["warned"] and not grade["hit"] and not grade["detected"]


def test_ungradable_bug_is_never_detected():
    grade = benchmark.grade_bug({"truth": {"kind": "none", "lines": set()}}, [warning(1)], [])
    assert not grade["gradable"] and not grade["detected"]


# --------------------------------------------------------------------------
# Whole runs
# --------------------------------------------------------------------------

@pytest.fixture
def fake_linters(monkeypatch):
    """Two linters that flag fixed lines of specific bugs, registered for one test."""
    def flag(key, line):
        def run(context):
            bug = benchmark.get_bug(key)
            if context.source == bug["sources"]["buggy"]:
                return [{"code": "F1", "severity": "warning", "line": line, "column": 0,
                         "message": "found it", "hint": ""}]
            return []
        return run

    github12 = sorted(benchmark.get_bug("github-12")["truth"]["lines"])[0]
    one = Linter("fake1", "Fake one", "", [Rule("F1", "first")], flag("github-12", github12),
                 default=False)
    two = Linter("fake2", "Fake two", "", [Rule("F1", "first")], flag("github-1", 10_000),
                 default=False)
    monkeypatch.setitem(linters._LINTERS, "fake1", one)
    monkeypatch.setitem(linters._LINTERS, "fake2", two)
    monkeypatch.setattr(benchmark, "EXTERNAL_DIR", "/nonexistent")
    return one, two


def test_run_compares_the_selection_and_each_linter(fake_linters):
    report = benchmark.run(["fake1", "fake2"], use_cache=False)
    assert [c["id"] for c in report["configs"]] == ["selection", "fake1", "fake2"]
    assert report["totals"]["selection"]["detected"] == 1
    assert report["totals"]["fake1"]["detected"] == 1
    assert report["totals"]["fake2"]["detected"] == 0
    assert report["totals"]["fake2"]["warned"] == 1          # warned, off every fault line
    row = next(b for b in report["bugs"] if b["key"] == "github-12")
    assert row["grades"]["selection"]["detected_by"] == ["fake1/F1"]
    rules = {r["rule"]: r for r in report["rules"]}
    assert rules["fake1/F1"]["detected"] == 1 and rules["fake2/F1"]["detected"] == 0


def test_single_linter_selection_has_one_row(fake_linters):
    report = benchmark.run(["fake1/F1"], use_cache=False)
    assert [c["id"] for c in report["configs"]] == ["selection"]
    assert report["selection"] == ["fake1/F1"]


def test_report_is_json_safe(fake_linters):
    json.dumps(benchmark.run(["fake1"], use_cache=False))


def test_external_results_join_the_comparison(fake_linters, tmp_path, monkeypatch):
    bug = benchmark.get_bug("github-12")
    line = sorted(bug["truth"]["lines"])[0]
    (tmp_path / "other.json").write_text(json.dumps({
        "tool": "other", "label": "Other tool", "version": "9",
        "results": {bug["buggy"]: [{"line": line, "code": "X1", "message": "m"}]},
    }), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(benchmark, "EXTERNAL_DIR", str(tmp_path))
    report = benchmark.run(["fake2"], use_cache=False)
    assert [c["id"] for c in report["configs"]] == ["selection", "external:other"]
    assert report["totals"]["external:other"]["detected"] == 1
    assert report["totals"]["selection"]["detected"] == 0


def test_import_sarif_matches_programs_by_path_suffix(tmp_path):
    bug = benchmark.get_bug("github-12")
    sarif = {"runs": [{
        "tool": {"driver": {"name": "CodeQL", "semanticVersion": "2.11.2"}},
        "results": [
            {"ruleId": "ql-measure-all-abuse", "message": {"text": "abuse"},
             "locations": [{"physicalLocation": {
                 "artifactLocation": {"uri": "file:///C:/work/programs/" + bug["buggy"]},
                 "region": {"startLine": 6}}}]},
            {"ruleId": "elsewhere", "message": {"text": "x"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "other.py"},
                                                 "region": {"startLine": 1}}}]},
        ]}]}
    path = tmp_path / "run.sarif"
    path.write_text(json.dumps(sarif), encoding="utf-8")
    record = benchmark.import_sarif(str(path), "lintq", "LintQ")
    assert record["version"] == "2.11.2" and record["unmatched_results"] == 1
    assert record["results"] == {bug["buggy"]: [
        {"line": 6, "code": "ql-measure-all-abuse", "message": "abuse"}]}


def test_dependence_checks_detect_the_grover_reset_bug():
    """QCSE 5959: x() and reset() on ancillas no measured bit depends on."""
    report = benchmark.run(["dependence/QD01"], include_external=False, use_cache=False)
    row = next(b for b in report["bugs"] if b["key"] == "stackexchange-8")
    assert row["grades"]["selection"]["detected_by"] == ["dependence/QD01"]
    assert report["front_end"]["attempted"] == 124


def test_default_selection_report_is_consistent():
    report = benchmark.run(include_external=False)
    t = report["totals"]["selection"]
    assert t["gradable"] == 62
    assert t["detected"] <= t["hit"] <= t["warned"] <= t["bugs"]
    assert t["detected_no_rewrites"] <= t["detected"]
    assert report["selection"] == linters.default_selection()
