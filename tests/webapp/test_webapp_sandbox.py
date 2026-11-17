"""Production defaults for a public server: the limited child process and the closed qasm route.

conftest.py turns both off for the rest of the suite (it patches analysis
internals in-process); these tests turn them back on.
"""

import pytest

import analysis
import app as app_module
import sandbox

from webapp_helpers import GHZ_QASM, post_analyze, post_slice

pytestmark = pytest.mark.skipif(not sandbox.SUPPORTED, reason="the sandbox needs Unix rlimits and a forkserver")


@pytest.fixture
def boxed(monkeypatch):
    monkeypatch.setattr(sandbox, "ENABLED", True)
    return sandbox


def test_analysis_runs_in_a_child_and_slicing_still_works(boxed, client):
    response, body = post_analyze(client, source=GHZ_QASM, filename="ghz.qasm",
                                  language="qasm-static")
    assert response.status_code == 200 and body["ok"], body
    # The child's program and QDG were handed back, so the token resolves here.
    response, sliced = post_slice(client, token=body["token"], node_idx=len(body["nodes"]) - 1,
                                  direction="backward")
    assert response.status_code == 200 and sliced["ok"], sliced


def _rss_mb():
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) // 1024 for line in status if line.startswith("VmRSS:"))


def test_a_memory_bomb_is_stopped_in_the_child(boxed, monkeypatch):
    # 1.5 GB of strings: about 1.46 GB of RSS when it ran in the server process.
    monkeypatch.setattr(sandbox, "MEM_MB", 256)
    before = _rss_mb()
    result = sandbox.analyze('xs = ["a" * 1000000 for i in range(1500)]\n',
                             filename="bomb.py", language="qiskit-static")
    assert _rss_mb() - before < 100, "the allocation reached the server process"
    assert result["ok"] is False
    error = result["error"]
    # The child's address-space cap fired: either the sandbox saw MemoryError,
    # or the parser caught it first and reported the program as too large.
    assert (error["kind"] == "too_expensive"
            or (error["kind"] == "static_error" and "too large" in error["detail"])), error


def test_a_slow_analysis_is_killed_at_the_deadline(boxed, monkeypatch):
    # The unknown-name lint check looks each imported name up outside the
    # parser's own time limit; 20,000 of them is work only the deadline stops.
    monkeypatch.setattr(sandbox, "WALL_SECONDS", 1.0)
    names = ", ".join(f"n{i}" for i in range(20000))
    result = sandbox.analyze(f"from qiskit import {names}\n",
                             filename="slow.py", language="qiskit-static")
    assert result["ok"] is False and result["error"]["kind"] == "timeout", result


def test_deep_nesting_is_an_error_not_a_crash():
    source = "x = 1" + " + 1" * 60000 + "\n"
    with pytest.raises(analysis._ParseFailure) as failure:
        analysis._parse_static_source(source, "deep.py")
    assert failure.value.payload["kind"] == "too_deep"


@pytest.mark.parametrize("name", ["qasm", "openqasm", " QASM3 "])
def test_qiskit_qasm3_route_is_off_by_default(monkeypatch, name):
    monkeypatch.setattr(analysis, "QASM_IMPORT_ROUTE", False)
    assert analysis.source_kind("c.qasm", name) == "qasm-static"


def test_api_errors_are_json_without_a_traceback():
    with app_module.app.test_request_context("/api/analyze"):
        response, status = app_module.internal_error(RuntimeError("secret detail"))
    assert status == 500
    body = response.get_json()
    assert body["error"]["kind"] == "internal_error"
    assert "secret detail" not in response.get_data(as_text=True)
