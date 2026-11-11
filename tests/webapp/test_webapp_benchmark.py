"""The Benchmark page, /api/benchmark, /api/linters, and lint_rules on /api/analyze."""

import pytest

from webapp_helpers import GHZ_QASM, post_analyze


def test_linters_endpoint_lists_rules_and_the_default(client):
    body = client.get("/api/linters").get_json()
    ids = [entry["id"] for entry in body["linters"]]
    assert "quick" in ids and "dependence" in ids
    linters_on = {rule.partition("/")[0] for rule in body["default"]}
    assert linters_on == {"quick", "dependence"}
    assert body["plugin_errors"] == []


def test_dataset_endpoint(client):
    body = client.get("/api/benchmark").get_json()
    assert body["dataset"]["bugs"] == len(body["bugs"]) == 62
    assert "sources" not in body["bugs"][0]
    assert isinstance(body["bugs"][0]["truth"]["lines"], list)


def test_bug_endpoint_has_both_sources(client):
    body = client.get("/api/benchmark/bugs/github-12").get_json()
    assert set(body["sources"]) == {"buggy", "fixed"}
    assert body["sources"]["buggy"] != body["sources"]["fixed"]
    assert client.get("/api/benchmark/bugs/nope").status_code == 404


def test_run_grades_the_requested_rules(client):
    response = client.post("/api/benchmark", json={"lint_rules": ["quick/QS009"]})
    body = response.get_json()
    assert response.status_code == 200 and body["ok"]
    assert body["selection"] == ["quick/QS009"]
    assert body["totals"]["selection"]["gradable"] == 62
    assert len(body["bugs"]) == 62


def test_run_without_rules_uses_the_default(client):
    body = client.post("/api/benchmark", json={}).get_json()
    assert body["ok"] and body["selection"] == client.get("/api/linters").get_json()["default"]


@pytest.mark.parametrize("payload, message", [
    ({"lint_rules": "quick"}, "list of rule ids"),
    ({"lint_rules": [1]}, "list of rule ids"),
    ({"lint_rules": []}, "at least one rule"),
    ({"lint_rules": ["unknown/rule"]}, "at least one rule"),
    ({"profile": "nope"}, "Unknown rule profile"),
])
def test_run_rejects_bad_requests(client, payload, message):
    response = client.post("/api/benchmark", json=payload)
    assert response.status_code == 400
    assert message in response.get_json()["error"]["message"]


def test_analyze_honours_lint_rules(client):
    _, body = post_analyze(client, source=GHZ_QASM, filename="ghz.qasm", language="qasm-static",
                           lint_rules=["dependence"])
    assert body["ok"], body
    assert body["lint_rules"] == ["dependence/QD01", "dependence/QD02", "dependence/QD03",
                                  "dependence/QD05", "dependence/QD06", "dependence/QD07",
                                  "dependence/QD08", "dependence/QD09", "dependence/QD10",
                                  "dependence/QD11", "dependence/QD12"]
    # No quick checks were selected, so there is nothing to say about skipping them.
    assert all(d["code"] != "QS100" for d in body["diagnostics"])


def test_analyze_quick_checks_on_qasm_still_say_they_skipped(client):
    _, body = post_analyze(client, source=GHZ_QASM, filename="ghz.qasm", language="qasm-static")
    assert [d["code"] for d in body["diagnostics"]][:1] == ["QS100"]


def test_analyze_rejects_bad_lint_rules(client):
    response, body = post_analyze(client, source=GHZ_QASM, filename="ghz.qasm", language="qasm",
                                  lint_rules="quick")
    assert response.status_code == 400 and "lint_rules" in body["error"]["message"]


def test_analyze_with_a_rule_off_does_not_report_it(client):
    source = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\nqc.h(0)\n"
    _, on = post_analyze(client, source=source, filename="a.py", language="qiskit-static")
    _, off = post_analyze(client, source=source, filename="a.py", language="qiskit-static",
                          lint_rules=["quick/QS002"])
    assert "QD06" in [d["code"] for d in on["diagnostics"]]
    assert "QD06" not in [d["code"] for d in off["diagnostics"]]
    assert all(d.get("tool") == "quick" for d in on["diagnostics"] if d["code"].startswith("QS0"))


def test_benchmark_page_links_the_picker_and_dataset(client):
    body = client.get("/benchmark").get_data(as_text=True)
    # The rules are picked in a dropdown; the Lint page explains them.
    assert 'id="lint-dropdown"' in body and 'id="lint-picker"' in body and 'href="/lint"' in body
    assert "lintrules.js" in body and "benchmark.js" in body
    assert "62 real Qiskit bugs" in body
    assert "Z-928/Bugs4Q-NA" in body


def test_lint_page_has_the_rules(client):
    body = client.get("/lint").get_data(as_text=True)
    assert 'id="lint-picker"' in body
    # Programs are linted on the Slicer page; neither page offers a rule profile.
    assert 'id="lint-source"' not in body and 'id="profile-select"' not in body
    assert 'id="profile-select"' not in client.get("/slicer").get_data(as_text=True)
    assert "lintrules.js" in body and "lint.js" in body


def test_slicer_page_has_the_lint_menu(client):
    body = client.get("/slicer").get_data(as_text=True)
    assert 'id="lint-dropdown"' in body and "lintrules.js" in body


def test_benchmark_endpoints_reject_unknown_datasets(client):
    assert client.get("/benchmark?dataset=nope").status_code == 404
    assert client.get("/api/benchmark?dataset=nope").status_code == 404
    assert client.get("/api/benchmark/bugs/github-12?dataset=nope").status_code == 404
    # A path is never a dataset name, even one that exists.
    assert client.get("/api/benchmark?dataset=benchmarks/bugs4q/manifest.json").status_code == 404
    response = client.post("/api/benchmark", json={"dataset": "nope"})
    assert response.status_code == 400 and "Unknown benchmark" in response.get_json()["error"]["message"]


def test_benchmark_endpoints_take_a_dataset_name(client):
    body = client.get("/api/benchmark?dataset=bugs4q").get_json()
    assert body["dataset"]["id"] == "bugs4q" and len(body["bugs"]) == 62
    assert client.get("/api/benchmark/bugs/github-12?dataset=bugs4q").status_code == 200


def test_benchmark_is_off_unless_enabled(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "BENCHMARK", False)
    assert client.get("/benchmark").status_code == 404
    assert client.get("/api/benchmark").status_code == 404
    assert client.get("/api/benchmark/bugs/github-12").status_code == 404
    assert client.post("/api/benchmark", json={"lint_rules": ["quick/QS009"]}).status_code == 404
    # Nothing on the other pages points at it.
    for path in ("/", "/slicer", "/lint", "/quantum", "/team"):
        body = client.get(path).get_data(as_text=True)
        assert "/benchmark" not in body and "Benchmark" not in body, path


def test_benchmark_is_linked_when_enabled(client):
    for path in ("/slicer", "/lint"):
        assert 'href="/benchmark"' in client.get(path).get_data(as_text=True), path


def test_benchmark_flag_comes_from_the_environment():
    import os
    import subprocess
    import sys

    webapp = os.path.dirname(os.path.abspath(sys.modules["app"].__file__))
    code = "import app; print(app.app.config['BENCHMARK'])"
    for value, expected in (("1", "True"), ("", "False"), ("0", "False")):
        env = dict(os.environ, QSLICER_BENCHMARK=value)
        out = subprocess.run([sys.executable, "-c", code], cwd=webapp, env=env,
                             capture_output=True, text=True, check=True).stdout
        assert out.strip() == expected, value
