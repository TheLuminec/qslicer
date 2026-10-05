"""analysis.py units: routing, helpers, cache, payload shapes, error stages (fast)."""

import math
import os
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import analysis
import gates
from webapp_helpers import GHZ_QASM


# --------------------------------------------------------------- routing --

@pytest.mark.parametrize("filename, language, expected", [
    ("", None, "python"),
    ("circuit.py", None, "python"),
    ("circuit.qasm", None, "qasm"),
    ("circuit.QASM", None, "qasm"),
    ("circuit.qasm3", None, "qasm"),
    ("circuit.txt", None, "python"),
    ("circuit.py", "qasm", "qasm"),
    ("circuit.qasm", "qiskit", "python"),
    ("", "openqasm", "qasm"),
    ("", " QASM3 ", "qasm"),
    ("", "py", "python"),
    ("", "Python", "python"),
    ("circuit.qasm", "unknown-lang", "qasm"),   # unknown language: extension decides
    ("circuit.py", "", "python"),
    ("circuit.py", "qiskit-static", "python-static"),
    ("", "static", "python-static"),
])
def test_source_kind(filename, language, expected):
    assert analysis.source_kind(filename, language) == expected


def test_default_filename():
    assert analysis.default_filename("qasm") == "pasted_circuit.qasm"
    assert analysis.default_filename("python") == "pasted_circuit.py"


@pytest.mark.parametrize("source, expected", [
    ("OPENQASM 3.0;\nqubit q;", True),
    ("openqasm 3;\n", True),
    ("// comment\n\n   OPENQASM 2.0;", True),
    ("# hash comment\nOPENQASM 3.0;", True),
    ("from qiskit import QuantumCircuit\nOPENQASM 3.0;", False),
    ("", False),
    ("\n" * 25 + "OPENQASM 3.0;", False),   # only the first 20 lines are read
    ("   \n\t\nqubit q;", False),
])
def test_looks_like_qasm(source, expected):
    assert analysis._looks_like_qasm(source) is expected


def test_note_interpreter_mismatch_python_on_qasm():
    error = {"hint": "original"}
    analysis._note_interpreter_mismatch(error, GHZ_QASM, "python")
    assert error["hint"].startswith("This looks like OpenQASM.")
    assert error["hint"].endswith("original")


def test_note_interpreter_mismatch_qasm_on_python():
    error = {}
    analysis._note_interpreter_mismatch(error, "import qiskit\n", "qasm")
    assert "does not start with an OPENQASM header" in error["hint"]


@pytest.mark.parametrize("source, kind", [
    ("import qiskit\n", "python"),   # matches: python on python
    (GHZ_QASM, "qasm"),              # matches: qasm on qasm
    ("   ", "qasm"),                 # blank: nothing to say
])
def test_note_interpreter_mismatch_silent_when_consistent(source, kind):
    error = {"hint": "h"}
    analysis._note_interpreter_mismatch(error, source, kind)
    assert error == {"hint": "h"}


# ------------------------------------------------------------- listings --

def test_interpreters_first_is_default():
    options = analysis.interpreters()
    assert [o["id"] for o in options] == ["qiskit", "qiskit-static", "qasm"]
    assert [o["default"] for o in options] == [True, False, False]
    for option in options:
        assert set(option) == {"id", "label", "suffix", "note", "default"}
    # Calling it does not mutate the module constant.
    assert "default" not in analysis.INTERPRETERS[0]


def test_rule_profiles():
    profiles = analysis.rule_profiles()
    assert "baseline" in profiles
    assert profiles == sorted(profiles)


def test_rule_profiles_without_backend(monkeypatch):
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "x")
    assert analysis.rule_profiles() == ["baseline"]


def test_dependency_types_cover_the_enum():
    from Dependencies.dependency_rules import DependencyType

    listed = analysis.dependency_types()
    assert [d["name"] for d in listed] == [d.name for d in DependencyType]
    for item in listed:
        assert set(item) == {"name", "description"}
        assert item["description"], item["name"]


def test_dependency_types_without_backend(monkeypatch):
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "x")
    names = [d["name"] for d in analysis.dependency_types()]
    assert names == ["DATA_QUBIT", "MEASUREMENT", "DATA_CLASSICAL",
                     "CONTROL_CLASSICAL", "ENTANGLEMENT", "PHASE"]


def test_offered_dependencies_order_counts_and_unknown_rules():
    rules = [SimpleNamespace(name="phase"), SimpleNamespace(name="data_qubit"),
             SimpleNamespace(name="mystery")]
    edges = [
        {"dependency": "DATA_QUBIT", "rule": "data_qubit"},
        {"dependency": "DATA_QUBIT", "rule": "data_qubit"},
        {"dependency": "CUSTOM", "rule": "mystery"},
    ]
    offered = analysis._offered_dependencies(rules, edges)
    assert [o["name"] for o in offered] == ["DATA_QUBIT", "PHASE", "CUSTOM"]
    by_name = {o["name"]: o for o in offered}
    assert by_name["DATA_QUBIT"]["count"] == 2
    assert by_name["PHASE"]["count"] == 0          # ran, found nothing: still offered
    assert by_name["CUSTOM"]["rules"] == ["mystery"]


def test_offered_dependencies_omit_rules_that_did_not_run(ghz_qasm_result):
    names = [d["name"] for d in ghz_qasm_result["dependency_types"]]
    assert "ENTANGLEMENT" not in names and "PHASE" not in names
    assert names[0] == "DATA_QUBIT"


# --------------------------------------------------------- formatting --

@pytest.mark.parametrize("value, expected", [
    (0, "0"),
    (1e-12, "0"),
    (math.pi, "pi"),
    (-math.pi, "-pi"),
    (math.pi / 2, "pi/2"),
    (-math.pi / 4, "-pi/4"),
    (3 * math.pi / 4, "3pi/4"),
    (2 * math.pi, "2pi"),
    (0.123456, "0.1235"),
    (None, "none"),
    ("theta", "theta"),
    ("x" * 40, "x" * 21 + "..."),
    ("a\n   b", "a b"),
])
def test_format_param(value, expected):
    assert analysis._format_param(value) == expected


def test_format_param_collapses_circuits():
    from qiskit import QuantumCircuit

    body = QuantumCircuit(1)
    body.x(0)
    body.z(0)
    assert analysis._format_param(body) == "<circuit 2 ops>"


def test_node_labels():
    assert analysis._node_label("rx", [0], [math.pi / 2]) == "rx(pi/2) q0"
    assert analysis._node_label("u", [1], [1, 2, 3, 4]).count(",") == 2   # at most 3 params
    assert analysis._node_label("barrier", [], []) == "barrier"
    assert analysis._short_label("cx", [0, 1]) == "cx q0,1"
    assert analysis._short_label("a_very_long_gate_name", [0]) == "a_very_long… q0"
    assert analysis._short_label("barrier", list(range(12))) == "barrier q0+11"


# ----------------------------------------------------------- payloads --

NODE_KEYS = {"id", "name", "label", "short", "qubits", "clbits", "params",
             "condition_clbits", "lane", "gate", "category", "is_measurement"}


@pytest.mark.requirement("FR-07")
def test_node_shape(ghz_qasm_result):
    for idx, node in enumerate(ghz_qasm_result["nodes"]):
        assert NODE_KEYS <= set(node)
        assert node["id"] == idx
        assert node["gate"] == gates.describe(node["name"])
        assert node["category"] == node["gate"]["category"]
        assert node["is_measurement"] == (node["name"] == "measure")
        assert node["lane"] == sum(node["qubits"]) / len(node["qubits"])
    cx = ghz_qasm_result["nodes"][2]
    assert cx["qubits"] == [0, 2] and cx["clbits"] == [] and cx["params"] == []
    assert ghz_qasm_result["nodes"][4]["clbits"] == [1]


def test_edge_shape(ghz_qasm_result):
    nodes = len(ghz_qasm_result["nodes"])
    for edge in ghz_qasm_result["edges"]:
        assert set(edge) == {"source", "target", "key", "dependency", "rule"}
        assert 0 <= edge["source"] < edge["target"] < nodes   # edges run forward in time
        assert edge["dependency"] in {d["name"] for d in analysis.dependency_types()}
        assert edge["rule"]


def test_entanglement_shape(ghz_qasm_result):
    ent = ghz_qasm_result["entanglement"]
    assert set(ent) == {"groups", "num_groups", "entangled_groups"}
    assert ent["num_groups"] == len(ent["groups"])
    assert ent["entangled_groups"] == sum(g["kind"] == "entangled" for g in ent["groups"])
    keys = {"id", "kind", "depth", "qubits", "parents", "active", "measured", "origin",
            "computed", "uncomputed", "measurements"}
    for gid, group in enumerate(ent["groups"]):
        assert set(group) == keys
        assert group["id"] == gid
        assert group["qubits"] == sorted(group["qubits"])
        for field in ("computed", "uncomputed", "measurements"):
            assert all(0 <= i < 6 for i in group[field])
    # One GHZ group spanning all three qubits, measured.
    entangled = [g for g in ent["groups"] if g["kind"] == "entangled"]
    assert any(g["qubits"] == [0, 1, 2] and g["measured"] for g in entangled)


def test_stats_shape(ghz_qasm_result):
    stats = ghz_qasm_result["stats"]
    assert stats["gate_counts"] == {"measure": 3, "cx": 2, "h": 1}
    # Ordered by count, then name, in the dict analysis builds. (Flask's jsonify
    # sorts keys, so the order does not survive the HTTP response; no client
    # code relies on it.)
    direct = analysis.analyze_source(GHZ_QASM, "ghz.qasm", language="qasm")["stats"]
    assert list(direct["gate_counts"]) == ["measure", "cx", "h"]
    assert stats["entangling_gates"] == 2
    assert stats["measurements"] == 3
    assert sum(stats["edges_by_dependency"].values()) == len(ghz_qasm_result["edges"])


def test_dependency_type_counts_match_edges(ghz_qasm_result):
    for item in ghz_qasm_result["dependency_types"]:
        actual = sum(e["dependency"] == item["name"] for e in ghz_qasm_result["edges"])
        assert item["count"] == actual


# ---------------------------------------------------------------- cache --

def test_cache_is_lru_and_bounded(monkeypatch):
    monkeypatch.setattr(analysis, "_CACHE", OrderedDict())
    monkeypatch.setattr(analysis, "_CACHE_LIMIT", 3)
    for token in "abc":
        analysis._cache_put(token, {"v": token})
    assert analysis._cache_get("a") == {"v": "a"}    # touch a: b is now oldest
    analysis._cache_put("d", {"v": "d"})
    assert list(analysis._CACHE) == ["c", "a", "d"]
    assert analysis._cache_get("b") is None
    analysis._cache_put("c", {"v": "c2"})            # re-put moves to the end
    assert list(analysis._CACHE) == ["a", "d", "c"]


def test_real_cache_limit_value():
    # Recorded so a change is deliberate; wsgi.py's single-worker advice relies on it.
    import importlib.util

    spec = importlib.util.find_spec("analysis")
    with open(spec.origin, encoding="utf-8") as handle:
        assert "_CACHE_LIMIT = 24" in handle.read()


def test_eviction_makes_a_token_stale(monkeypatch):
    monkeypatch.setattr(analysis, "_CACHE", OrderedDict())
    monkeypatch.setattr(analysis, "_CACHE_LIMIT", 1)
    first = analysis.analyze_source(GHZ_QASM, "a.qasm", language="qasm")
    second = analysis.analyze_source(GHZ_QASM, "b.qasm", language="qasm")
    assert first["token"] != second["token"]
    assert analysis.slice_program(first["token"], 0)["stale"] is True
    assert analysis.slice_program(second["token"], 0)["ok"] is True


@pytest.mark.requirement("NFR-08")
def test_token_is_deterministic_in_filename_profile_and_source():
    a = analysis.analyze_source(GHZ_QASM, "g.qasm", language="qasm")["token"]
    assert analysis.analyze_source(GHZ_QASM, "g.qasm", language="qasm")["token"] == a
    assert analysis.analyze_source(GHZ_QASM, "h.qasm", language="qasm")["token"] != a
    assert analysis.analyze_source(GHZ_QASM + "\n", "g.qasm", language="qasm")["token"] != a


def test_slice_program_without_backend(monkeypatch):
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "x")
    result = analysis.slice_program("t", 0)
    assert result["ok"] is False
    assert result["error"]["kind"] == "backend_unavailable"
    assert result["error"]["stage"] == "slicing"


# --------------------------------------------------------- QASM route --

@pytest.mark.requirement("FR-06")
@pytest.mark.requirement("NFR-04")
def test_qasm_route_never_starts_a_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("OpenQASM must not start a subprocess")

    monkeypatch.setattr(analysis.subprocess, "run", forbidden)
    monkeypatch.setattr(analysis.subprocess, "Popen", forbidden)
    result = analysis.analyze_source(GHZ_QASM, "ghz.qasm", language="qasm")
    assert result["ok"]


@pytest.mark.requirement("FR-06")
@pytest.mark.requirement("NFR-04")
def test_python_submitted_as_qasm_is_not_executed(tmp_path):
    marker = tmp_path / "executed.txt"
    source = (f"open({str(marker)!r}, 'w').write('ran')\n"
              "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\nqc.h(0)\n")
    result = analysis.analyze_source(source, "evil.qasm", language="qasm")
    assert result["ok"] is False
    assert result["error"]["kind"] == "qasm_error"
    assert not marker.exists()


def test_qasm_scratch_directory_is_removed(monkeypatch):
    made = []
    real = analysis.tempfile.mkdtemp

    def tracking(*args, **kwargs):
        path = real(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(analysis.tempfile, "mkdtemp", tracking)
    analysis.analyze_source(GHZ_QASM, "../../escape.qasm", language="qasm")
    analysis.analyze_source("garbage", "x.qasm", language="qasm")
    assert made and not any(os.path.exists(p) for p in made)


# ------------------------------------------------------- Python route --
# The worker is replaced with a stub here; the real worker is exercised in
# test_webapp_python_route.py (slow).

def _fake_run_writing(payload_json):
    def fake_run(cmd, cwd, capture_output, text, timeout):
        _, worker, path, workdir = cmd
        assert worker.endswith("parse_worker.py")
        assert cwd == workdir and os.path.dirname(path) == workdir
        if payload_json is not None:
            with open(os.path.join(workdir, "result.json"), "w", encoding="utf-8") as h:
                h.write(payload_json)
        return SimpleNamespace(returncode=3, stdout="", stderr="line1\nKilled\n")
    return fake_run


PY_SOURCE = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\nqc.h(0)\nqc.measure_all()\n"


def test_worker_without_result_file_is_worker_crashed(monkeypatch):
    monkeypatch.setattr(analysis.subprocess, "run", _fake_run_writing(None))
    result = analysis.analyze_source(PY_SOURCE, "c.py")
    assert result["ok"] is False
    assert result["error"]["kind"] == "worker_crashed"
    assert "code 3" in result["error"]["message"]
    assert result["error"]["traceback"] == ["line1", "Killed"]
    assert result["error"]["hint"] == analysis._HINTS["worker_crashed"]


def test_worker_reported_error_is_passed_through(monkeypatch):
    payload = ('{"ok": false, "stdout": "hello", "error": {"kind": "no_circuit", '
               '"message": "m", "detail": "d", "traceback": null}}')
    monkeypatch.setattr(analysis.subprocess, "run", _fake_run_writing(payload))
    result = analysis.analyze_source(PY_SOURCE, "c.py")
    assert result["error"]["kind"] == "no_circuit"
    assert result["error"]["hint"] == analysis._HINTS["no_circuit"]
    assert result["stdout"] == "hello"


def test_worker_timeout_is_reported(monkeypatch):
    def timeout(*args, **kwargs):
        raise analysis.subprocess.TimeoutExpired(cmd="x", timeout=kwargs["timeout"])

    monkeypatch.setattr(analysis.subprocess, "run", timeout)
    monkeypatch.setattr(analysis, "PARSE_TIMEOUT", 7)
    result = analysis.analyze_source(PY_SOURCE, "c.py")
    assert result["error"]["kind"] == "timeout"
    assert "7 seconds" in result["error"]["message"]
    assert "QSLICER_PARSE_TIMEOUT" in result["error"]["hint"]


def test_worker_that_cannot_start_is_reported(monkeypatch):
    def oserror(*args, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(analysis.subprocess, "run", oserror)
    result = analysis.analyze_source(PY_SOURCE, "c.py")
    assert result["error"]["kind"] == "worker_failed"
    assert "no such interpreter" in result["error"]["detail"]


def test_python_filename_is_sanitized(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["path"] = cmd[2]
        seen["workdir"] = cmd[3]
        raise OSError("stop here")

    monkeypatch.setattr(analysis.subprocess, "run", fake_run)
    analysis.analyze_source(PY_SOURCE, "../../outside/evil", language="qiskit")
    assert os.path.dirname(seen["path"]) == seen["workdir"]
    assert os.path.basename(seen["path"]) == "evil.py"
    assert not os.path.exists(seen["workdir"])      # scratch dir cleaned up


def test_read_worker_result_with_corrupt_json(tmp_path):
    (tmp_path / "result.json").write_text("{not json", encoding="utf-8")
    completed = SimpleNamespace(returncode=1, stdout="out", stderr="")
    payload = analysis._read_worker_result(str(tmp_path), completed)
    assert payload["error"]["kind"] == "worker_crashed"
    assert payload["error"]["traceback"] == ["out"]


@pytest.mark.requirement("NFR-07")
def test_python_source_reaches_worker_unchanged(monkeypatch):
    source = "# -*- coding: utf-8 -*-\nname = 'qubit ☃ é'\n\tx = 1  \n\n"
    seen = {}

    def fake_run(cmd, **kwargs):
        with open(cmd[2], "r", encoding="utf-8", newline="") as handle:
            raw = handle.read()
        with open(cmd[2], "r", encoding="utf-8") as handle:
            seen["text"] = handle.read()       # what Python's own reader would see
        seen["raw_len"] = len(raw)
        raise OSError("stop")

    monkeypatch.setattr(analysis.subprocess, "run", fake_run)
    monkeypatch.setattr(analysis.lint, "check_source", lambda s: [])
    analysis.analyze_source(source, "c.py")
    assert seen["text"] == source


@pytest.mark.requirement("NFR-07")
def test_qasm_source_reaches_parser_unchanged(monkeypatch):
    seen = {}
    real = analysis.OpenQASMParser.parse_file

    def spy(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            seen["text"] = handle.read()
        return real(self, path)

    monkeypatch.setattr(analysis.OpenQASMParser, "parse_file", spy)
    source = GHZ_QASM.replace("// ", "") + "// trailing é comment\n"
    analysis.analyze_source(source, "g.qasm", language="qasm")
    assert seen["text"] == source


@pytest.mark.requirement("NFR-07")
def test_crlf_source_keeps_its_line_numbers():
    source = ('OPENQASM 3.0;\r\ninclude "stdgates.inc";\r\nqubit[1] q;\r\nh q[0];\r\n'
              'bogus q[0];\r\n')
    result = analysis.analyze_source(source, "crlf.qasm", language="qasm")
    assert result["error"]["kind"] == "qasm_error"
    assert '"5,' in result["error"]["detail"], result["error"]["detail"]


# -------------------------------------------------------- error stages --

# NFR-12: every error states which stage failed. The kind is the stage marker.
STAGE_OF_KIND = {
    "empty_input": "validation",
    "too_large": "upload",
    "syntax_error": "validation",
    "qasm_error": "parsing",
    "empty_program": "parsing",
    "no_circuit": "execution",
    "execution_error": "execution",
    "timeout": "execution",
    "worker_crashed": "execution",
    "worker_failed": "execution",
    "backend_unavailable": "parsing",
}


@pytest.mark.requirement("NFR-12")
@pytest.mark.parametrize("source, language", [
    (GHZ_QASM, "qiskit"),
    ("from qiskit import QuantumCircuit\nqc = QuantumCircuit(1\n", "qiskit"),
    ("OPENQASM 3.0;\nqubit[1] q;\nfoo q[0];\n", "qasm"),
    ("OPENQASM 3.0;\nqubit[1] q;\n", "qasm"),
])
def test_analysis_errors_name_their_stage(source, language):
    result = analysis.analyze_source(source, language=language)
    assert result["ok"] is False
    error = result["error"]
    assert error["kind"] in STAGE_OF_KIND
    assert error["message"] and error["hint"]
    for key in ("token", "nodes", "edges", "program"):
        assert key not in result


@pytest.mark.requirement("NFR-12")
def test_syntax_error_carries_the_source_line():
    source = "from qiskit import QuantumCircuit\n\nqc = QuantumCircuit(1\n"
    error = analysis.analyze_source(source)["error"]
    assert error["kind"] == "syntax_error"
    assert isinstance(error["line"], int) and error["line"] >= 3
    assert f"line {error['line']}" in error["detail"]


@pytest.mark.requirement("NFR-12")
def test_slice_errors_name_their_stage():
    result = analysis.slice_program("0" * 32, 0)
    assert isinstance(result["error"], dict)
    assert result["error"].get("kind")
