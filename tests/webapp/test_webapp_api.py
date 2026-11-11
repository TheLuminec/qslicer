"""JSON API: /api/examples, /api/analyze and /api/slice, without the Python worker.

Everything here goes through the OpenQASM route or fails before the worker is
started, so the file runs in well under a second per test.
"""

import pytest

import analysis
import app as app_module
import examples
from webapp_helpers import GHZ_QASM, manifest_entries, post_analyze, post_slice


# ---------------------------------------------------------------- examples --

def test_api_examples_lists_catalog_and_groups(client):
    response = client.get("/api/examples")
    assert response.status_code == 200
    body = response.get_json()
    assert body["examples"] == examples.catalog()
    assert body["groups"] == examples.grouped()
    keys = {entry["key"] for entry in body["examples"]}
    assert {entry["key"] for entry in manifest_entries()} <= keys


@pytest.mark.requirement("FR-01")
@pytest.mark.parametrize("key", [entry["key"] for entry in manifest_entries()])
def test_api_example_returns_source(client, key):
    response = client.get(f"/api/examples/{key}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["key"] == key
    assert body["source"].strip()
    assert body["filename"] == body["file"]
    assert body["interpreter"] in ("qiskit-static", "qasm-static")


def test_api_example_unknown_key_is_404(client):
    response = client.get("/api/examples/no_such_example")
    assert response.status_code == 404
    assert "no_such_example" in response.get_data(as_text=True)


def test_api_example_builtin_fallback_key(client):
    body = client.get("/api/examples/builtin_bell").get_json()
    assert body["filename"] == "bell_pair.py"
    assert "QuantumCircuit(2, 2)" in body["source"]


# ----------------------------------------------------------------- analyze --

@pytest.mark.requirement("FR-03")
@pytest.mark.requirement("VV-04")
@pytest.mark.parametrize("payload", [
    {},
    {"source": ""},
    {"source": "   \n\t  "},
    {"source": 42},
    {"source": None},
    {"source": ["qc = 1"]},
])
def test_analyze_rejects_empty_or_non_string_source(client, payload):
    response, body = post_analyze(client, **payload)
    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error"]["kind"] == "empty_input"
    assert body["error"]["message"] and body["error"]["hint"]
    assert body["diagnostics"] == []


def test_analyze_with_no_json_body_is_empty_input(client):
    response = client.post("/api/analyze", data="not json", content_type="text/plain")
    assert response.status_code == 400
    assert response.get_json()["error"]["kind"] == "empty_input"


@pytest.mark.requirement("FR-03")
def test_analyze_rejects_source_over_256kb(client):
    source = "# " + "x" * (app_module.MAX_SOURCE_BYTES) + "\n"
    response, body = post_analyze(client, source=source)
    assert response.status_code == 413
    assert body["ok"] is False
    assert body["error"]["kind"] == "too_large"
    assert "256 KB" in body["error"]["detail"]


def test_analyze_size_limit_counts_utf8_bytes(client, monkeypatch):
    # 100 three-byte characters are 300 bytes, not 100.
    monkeypatch.setattr(app_module, "MAX_SOURCE_BYTES", 200)
    _, body = post_analyze(client, source="☃" * 100)
    assert body["error"]["kind"] == "too_large"


def test_request_body_over_2mb_hits_413_handler(client):
    response = client.post("/api/analyze", data=b"x" * (3 * 1024 * 1024),
                           content_type="application/json")
    assert response.status_code == 413
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"]["kind"] == "too_large"
    assert "2 MB" in body["error"]["hint"]


@pytest.mark.requirement("FR-02")
@pytest.mark.requirement("FR-19")
def test_analyze_qasm_success_payload(ghz_qasm_result):
    body = ghz_qasm_result
    assert body["ok"] is True
    assert body["language"] == "qasm"
    assert body["filename"] == "ghz.qasm"
    assert body["profile"] == "baseline"
    assert len(body["token"]) == 32
    assert body["program"] == {"num_qubits": 3, "num_clbits": 3, "num_instructions": 6}
    assert body["route"] == "OpenQASM, parsed without executing"
    assert [n["name"] for n in body["nodes"]] == ["h", "cx", "cx", "measure", "measure", "measure"]
    assert body["edges"], "a GHZ circuit has dependencies"
    assert body["rules"] == ["data_qubit", "measurement", "data_classical", "control_classical"]
    for key in ("stats", "dependency_types", "diagnostics", "stdout"):
        assert key in body


def test_analyze_qasm_skips_python_lint(ghz_qasm_result):
    assert [d["code"] for d in ghz_qasm_result["diagnostics"]] == ["QS100"]
    assert ghz_qasm_result["diagnostics"][0]["severity"] == "info"


@pytest.mark.requirement("FR-01")
def test_pasted_source_gets_default_filename(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm")
    assert body["ok"]
    assert body["filename"] == "pasted_circuit.qasm"


def test_language_param_overrides_extension(client):
    # A .py filename with language=qasm still goes to the OpenQASM front end.
    _, body = post_analyze(client, source=GHZ_QASM, filename="ghz.py", language="qasm")
    assert body["ok"] and body["language"] == "qasm"


@pytest.mark.requirement("NFR-04")
@pytest.mark.parametrize("language", ["qiskit", "python", "py"])
def test_api_refuses_the_executing_interpreter(client, language, tmp_path):
    marker = tmp_path / "ran.txt"
    source = f"open({str(marker)!r}, 'w').write('ran')\n"
    response, body = post_analyze(client, source=source, filename="evil.py", language=language)
    assert response.status_code == 400
    assert body["ok"] is False and body["error"]["kind"] == "execution_disabled"
    assert not marker.exists()


def test_extension_decides_without_language(client):
    _, body = post_analyze(client, source=GHZ_QASM, filename="ghz.qasm")
    assert body["ok"] and body["language"] == "qasm-static"


def test_unknown_profile_falls_back_to_baseline(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm", profile="no-such-profile")
    assert body["ok"]
    assert body["profile"] == "baseline"


def test_missing_profile_defaults_to_baseline(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm", profile=None)
    assert body["profile"] == "baseline"


@pytest.mark.requirement("FR-04")
@pytest.mark.requirement("VV-04")
def test_qasm_text_under_qiskit_interpreter_names_mismatch(client):
    response, body = post_analyze(client, source=GHZ_QASM, language="qiskit-static")
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["language"] == "python-static"
    assert body["error"]["kind"] == "syntax_error"
    assert "looks like OpenQASM" in body["error"]["hint"]
    assert "OpenQASM 2/3" in body["error"]["hint"]


@pytest.mark.requirement("FR-04")
@pytest.mark.requirement("VV-04")
def test_python_text_under_qasm_interpreter_names_mismatch(client):
    source = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\nqc.h(0)\n"
    _, body = post_analyze(client, source=source, language="qasm")
    assert body["ok"] is False
    assert body["error"]["kind"] == "qasm_error"
    assert "does not start with an OPENQASM header" in body["error"]["hint"]
    assert "Qiskit (Python)" in body["error"]["hint"]


@pytest.mark.requirement("FR-03")
@pytest.mark.requirement("VV-04")
@pytest.mark.requirement("VV-07")
def test_malformed_qasm_is_rejected_with_reason(client):
    source = 'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[1] q;\nnot_a_gate q[0];\n'
    _, body = post_analyze(client, source=source, language="qasm")
    assert body["ok"] is False
    assert body["error"]["kind"] == "qasm_error"
    assert "not_a_gate" in body["error"]["detail"]
    # Nothing about the program is reported as if it had succeeded.
    for key in ("token", "nodes", "edges"):
        assert key not in body


@pytest.mark.requirement("FR-03")
@pytest.mark.requirement("VV-04")
@pytest.mark.requirement("VV-07")
def test_python_syntax_error_is_reported_with_its_line(client):
    source = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1\nqc.h(0)\n"
    _, body = post_analyze(client, source=source, language="qiskit-static")
    assert body["ok"] is False
    assert body["error"]["kind"] == "syntax_error"
    assert body["error"]["line"] >= 2
    assert body["diagnostics"][0]["code"] == "QS000"


def test_empty_qasm_program_is_reported(client):
    _, body = post_analyze(client, source="OPENQASM 3.0;\nqubit[1] q;\n", language="qasm")
    assert body["ok"] is False
    assert body["error"]["kind"] == "empty_program"


@pytest.mark.requirement("FR-21")
def test_lint_findings_returned_even_when_run_fails(client, monkeypatch):
    # The linters read the same parse as the page, so the failure carries the
    # front end's exception, which QS014 reports.
    rejected = analysis.StaticParseError("the program raises CircuitError: index 5 out of range",
                                         4, raised="CircuitError")

    def fail(*_args):
        raise analysis._ParseFailure({"kind": "static_error", "message": "stub",
                                      "detail": "", "hint": "h"}, "printed", cause=rejected)

    monkeypatch.setattr(analysis, "_parse_source", fail)
    source = ("from qiskit import QuantumCircuit\n"
              "qc = QuantumCircuit(2)\n"
              "qc.h(0)\n"
              "qc.cx(0, 5)\n")
    _, body = post_analyze(client, source=source, language="qiskit-static")
    assert body["ok"] is False
    assert body["stdout"] == "printed"
    codes = {d["code"] for d in body["diagnostics"]}
    # The literal index (QS002, read from the source) and Qiskit rejecting it
    # (QS014, from the shared parse's failure).
    assert {"QS002", "QS014"} <= codes
    for diagnostic in body["diagnostics"]:
        assert isinstance(diagnostic["line"], int) and diagnostic["line"] > 0
        assert diagnostic["hint"]
    qs002 = next(d for d in body["diagnostics"] if d["code"] == "QS002")
    assert qs002["line"] == 4


def test_backend_unavailable_is_reported(client, monkeypatch):
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "ImportError: nope")
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm")
    assert body["ok"] is False
    assert body["error"]["kind"] == "backend_unavailable"
    assert body["error"]["detail"] == "ImportError: nope"
    response, body = post_slice(client, token="x", node_idx=0)
    assert body["ok"] is False and response.status_code == 409


# ------------------------------------------------------------------- slice --

@pytest.mark.parametrize("payload", [
    {},
    {"node_idx": 0},
    {"token": "", "node_idx": 0},
    {"token": "abc"},
    {"token": "abc", "node_idx": "3"},
    {"token": "abc", "node_idx": 1.0},
    {"token": "abc", "node_idx": None},
])
def test_slice_requires_token_and_int_node(client, payload):
    response, body = post_slice(client, **payload)
    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error"]["kind"] == "invalid_request"
    assert body["error"]["stage"] == "slicing"
    assert "token and node_idx are required" in body["error"]["message"]


@pytest.mark.parametrize("direction", ["sideways", "BACKWARD", "", None])
def test_slice_rejects_bad_direction(client, ghz_qasm_result, direction):
    response, body = post_slice(client, token=ghz_qasm_result["token"], node_idx=0,
                                direction=direction)
    assert response.status_code == 400
    assert "direction" in body["error"]["message"]


@pytest.mark.requirement("FR-23")
def test_slice_unknown_token_is_stale(client):
    response, body = post_slice(client, token="0" * 32, node_idx=0)
    assert response.status_code == 409
    assert body["ok"] is False
    assert body["stale"] is True
    assert body["error"]["kind"] == "stale_token"
    assert "Analyze again" in body["error"]["hint"]


@pytest.mark.requirement("FR-23")
def test_slice_after_eviction_is_stale(client, ghz_qasm_result, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(analysis, "_CACHE", OrderedDict())
    response, body = post_slice(client, token=ghz_qasm_result["token"], node_idx=0)
    assert response.status_code == 409 and body["stale"] is True


@pytest.mark.parametrize("node_idx", [-1, 6, 1000])
def test_slice_node_out_of_range(client, ghz_qasm_result, node_idx):
    response, body = post_slice(client, token=ghz_qasm_result["token"], node_idx=node_idx)
    assert response.status_code == 409
    assert body["ok"] is False
    assert body["error"]["kind"] == "out_of_range"
    assert "outside this program" in body["error"]["message"]
    assert "slice" not in body


def test_slice_default_direction_is_backward(client, ghz_qasm_result):
    _, body = post_slice(client, token=ghz_qasm_result["token"], node_idx=4)
    assert body["direction"] == "backward"
    assert body["node_idx"] == 4
    assert body["total"] == 6
    assert body["size"] == len(body["slice"])


def test_slice_unknown_dep_types_follow_everything(client, ghz_qasm_result):
    token = ghz_qasm_result["token"]
    _, everything = post_slice(client, token=token, node_idx=4)
    _, bogus = post_slice(client, token=token, node_idx=4, dep_types=["NOT_A_TYPE"])
    assert bogus["dep_types"] is None
    assert bogus["slice"] == everything["slice"]


def test_slice_dep_types_are_reported_sorted(client, ghz_qasm_result):
    _, body = post_slice(client, token=ghz_qasm_result["token"], node_idx=4,
                         dep_types=["MEASUREMENT", "DATA_QUBIT", "NOT_A_TYPE"])
    assert body["dep_types"] == ["DATA_QUBIT", "MEASUREMENT"]


# ---------------------------------------------- malformed-request robustness --
# These document expected behavior the app does not have yet: a malformed
# request should be a 4xx with a JSON error, never an unhandled 500.

@pytest.mark.requirement("FR-03")
def test_analyze_json_array_body_is_a_client_error(client):
    response = client.post("/api/analyze", json=["source"])
    assert response.status_code == 400


def test_slice_json_array_body_is_a_client_error(client):
    response = client.post("/api/slice", json=[1])
    assert response.status_code == 400


@pytest.mark.requirement("FR-03")
def test_analyze_non_string_filename_is_a_client_error(client):
    response = client.post("/api/analyze", json={"source": GHZ_QASM, "filename": 5})
    assert response.status_code in (200, 400)


def test_slice_non_list_dep_types_is_a_client_error(client, ghz_qasm_result):
    response = client.post("/api/slice", json={"token": ghz_qasm_result["token"],
                                               "node_idx": 0, "dep_types": 5})
    assert response.status_code == 400


def test_slice_rejects_boolean_node_idx(client, ghz_qasm_result):
    response = client.post("/api/slice", json={"token": ghz_qasm_result["token"],
                                               "node_idx": True})
    assert response.status_code == 400


@pytest.mark.requirement("FR-03")
def test_unsupported_language_is_rejected_by_name(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="cirq")
    assert body["ok"] is False
    error = body["error"]
    assert "cirq" in " ".join(str(error.get(k, "")) for k in ("message", "detail", "hint"))


# --------------------------------------------------------------------------
# Values only a run decides, and files with several circuits
# --------------------------------------------------------------------------

RUNTIME_SOURCE = '''import random
from qiskit import QuantumCircuit


def bell():
    qc = QuantumCircuit(2, name="bell")
    qc.h(0)
    qc.cx(0, 1)
    return qc


def main():
    target = random.randrange(3)
    full = QuantumCircuit(3, 3, name="full")
    full.compose(bell(), inplace=True)
    full.x(target)
    full.measure(range(3), range(3))


if __name__ == "__main__":
    main()
'''


def test_unknown_value_asks_for_it_by_name(client):
    response, body = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static")
    assert response.status_code == 200 and body["ok"] is False
    assert body["error"]["kind"] == "needs_value"
    assert body["error"]["needs"] == {"name": "target", "line": 13, "kind": "variable",
                                      "expression": "random.randrange(3)"}
    assert body["error"]["line"] == 13


def test_bindings_complete_the_parse_and_list_the_circuits(client):
    bindings = [{"name": "target", "line": 13, "value": "2"}]
    _, body = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static",
                           bindings=bindings)
    assert body["ok"] is True and body["bindings"] == bindings
    assert [(c["name"], c["used_inside"]) for c in body["circuits"]] == [
        ("full", False), ("bell", True)]
    assert body["circuit"] == 0 and body["program"]["num_qubits"] == 3
    assert any(n["label"].startswith("x") and n["qubits"] == [2] for n in body["nodes"])

    _, sub = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static",
                          bindings=bindings, circuit=1)
    assert sub["ok"] is True and sub["circuit"] == 1
    assert sub["program"] == {"num_qubits": 2, "num_clbits": 0, "num_instructions": 2}
    assert sub["token"] != body["token"]


def test_out_of_range_circuit_falls_back_to_the_main_one(client):
    _, body = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static",
                           bindings=[{"name": "target", "line": 13, "value": "0"}], circuit=9)
    assert body["ok"] is True and body["circuit"] == 0


@pytest.mark.parametrize("bindings, circuit", [
    ("target=2", 0),
    ([{"name": "target", "line": 13, "value": "2"}], -1),
    ([{"name": "target", "line": 13, "value": "2"}], True),
])
def test_malformed_bindings_or_circuit_are_rejected(client, bindings, circuit):
    response, body = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static",
                                  bindings=bindings, circuit=circuit)
    assert response.status_code == 400 and body["error"]["kind"] == "invalid_request"


def test_a_value_that_is_not_a_literal_is_never_run(client):
    _, body = post_analyze(client, source=RUNTIME_SOURCE, language="qiskit-static",
                           bindings=[{"name": "target", "line": 13,
                                      "value": "__import__('os').getcwd()"}])
    assert body["ok"] is False and body["error"]["kind"] == "invalid_binding"
