"""LintQ as linters: the port and the CodeQL adapter, on the slicer and in the benchmark."""

import json

import benchmark
from qslicer.lint import registry as linters
from qslicer.lint.lintq import codeql as codeql_lintq
from qslicer.lint.registry import Linter, Rule

SHOWCASE = (
    "from qiskit import QuantumCircuit\n"
    "qc = QuantumCircuit(3, 3)\n"
    "qc.h(0)\n"
    "qc.measure(0, 0)\n"
    "qc.x(0)\n"
    "qc.measure_all()\n"
)


def test_both_linters_are_registered():
    entries = {e["id"]: e for e in linters.catalog()}
    assert [r["code"] for r in entries["lintq"]["rules"]][:10] == [f"LQ{i:02d}" for i in range(1, 11)]
    assert entries["lintq"]["available"] and not entries["lintq"]["default"]
    assert entries["lintq-codeql"]["batch"]
    assert [r["code"] for r in entries["lintq-codeql"]["rules"]] == \
        [f"LQ{i:02d}" for i in range(1, 11)]


def test_original_is_unavailable_without_codeql(monkeypatch):
    monkeypatch.delenv("QSLICER_CODEQL", raising=False)
    ok, reason = codeql_lintq.available()
    assert not ok and "QSLICER_CODEQL" in reason
    monkeypatch.setenv("QSLICER_CODEQL", "/no/such/codeql")
    assert "does not exist" in codeql_lintq.available()[1]


def test_port_runs_through_the_registry():
    found = linters.run(SHOWCASE, "showcase.py", "python-static", ["lintq"])
    codes = {f["code"] for f in found}
    assert {"LQ02", "LQ03"} <= codes
    assert all(f["tool"] == "lintq" and f["rule"] == f"lintq/{f['code']}" for f in found)
    only = linters.run(SHOWCASE, "showcase.py", "python-static", ["lintq/LQ03"])
    assert [f["code"] for f in only] == ["LQ03"]


def test_analyze_reports_lintq_with_the_other_linters():
    import app as app_module
    body = app_module.app.test_client().post("/api/analyze", json={
        # The repeated x() is a cancelling pair for the dependence checks.
        "source": SHOWCASE + "qc.x(1)\nqc.x(1)\n", "filename": "showcase.py",
        "language": "qiskit-static",
        "lint_rules": ["dependence", "lintq"]}).get_json()
    assert body["ok"], body
    tools = {d.get("tool") for d in body["diagnostics"]}
    assert {"dependence", "lintq"} <= tools


def test_read_sarif_maps_ids_to_codes(tmp_path):
    sarif = {"runs": [{"results": [{
        "ruleId": "ql-measure-all-abuse", "message": {"text": "measure_all()\non 'qc'"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a\\b.py"},
                                            "region": {"startLine": 6, "startColumn": 1}}}]}]}]}
    path = tmp_path / "r.sarif"
    path.write_text(json.dumps(sarif), encoding="utf-8")
    assert codeql_lintq.read_sarif(str(path)) == [{
        "path": "a/b.py", "line": 6, "column": 1, "lintq_id": "ql-measure-all-abuse",
        "code": "LQ03", "message": "measure_all() on 'qc'"}]


def test_benchmark_uses_a_batch_linter_once(monkeypatch):
    calls = []
    bug = benchmark.get_bug("github-12")
    line = sorted(bug["truth"]["lines"])[0]

    def batch(programs):
        calls.append(len(programs))
        return {path: ([{"code": "B1", "severity": "warning", "line": line, "column": 1,
                         "message": "batched", "hint": ""}] if path == bug["buggy"] else [])
                for path, _ in programs}

    def never(context):
        raise AssertionError("a batch linter is not run per program in the benchmark")

    monkeypatch.setitem(linters._LINTERS, "batchy", Linter(
        "batchy", "Batchy", "", [Rule("B1", "b")], never, default=False, batch=batch))
    report = benchmark.run(["batchy"], include_external=False, use_cache=False)
    assert calls == [124]
    row = next(b for b in report["bugs"] if b["key"] == "github-12")
    assert row["grades"]["selection"]["detected_by"] == ["batchy/B1"]


def test_benchmark_compares_the_port_with_the_recorded_original():
    report = benchmark.run(["lintq"], use_cache=False)
    ids = [c["id"] for c in report["configs"]]
    assert "external:lintq-codeql" in ids
    port, original = report["totals"]["selection"], report["totals"]["external:lintq-codeql"]
    for key in ("detected", "hit", "warned", "warnings_buggy", "warnings_fixed"):
        assert port[key] == original[key], key
