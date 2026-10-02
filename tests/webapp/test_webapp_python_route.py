"""The Qiskit (Python) route end to end: the real parse_worker.py subprocess.

Every test here starts at least one worker process (a fresh interpreter that
imports Qiskit), so the whole module is marked slow.
"""

import os
import subprocess
import sys

import pytest

import analysis
from webapp_helpers import BELL_PY, GHZ_QASM, ROOT, WEBAPP_DIR, post_analyze, post_slice

pytestmark = pytest.mark.slow

INFINITE_LOOP = """from qiskit import QuantumCircuit
qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)
while True:
    pass
"""


@pytest.mark.requirement("FR-02")
@pytest.mark.requirement("FR-05")
@pytest.mark.requirement("FR-19")
@pytest.mark.requirement("VV-04")
def test_python_bell_pair_succeeds(bell_result):
    assert bell_result["ok"] is True
    assert bell_result["language"] == "python"
    assert bell_result["route"].startswith("Python, executed in a worker process")
    assert bell_result["program"] == {"num_qubits": 2, "num_clbits": 2, "num_instructions": 4}
    assert [n["name"] for n in bell_result["nodes"]] == ["h", "cx", "measure", "measure"]
    assert bell_result["edges"]


@pytest.mark.requirement("NFR-04")
def test_python_runs_in_a_separate_process_in_a_scratch_directory(client):
    source = ("import os\nfrom qiskit import QuantumCircuit\n"
              "print('PID', os.getpid())\nprint('CWD', os.getcwd())\n"
              "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\n")
    _, body = post_analyze(client, source=source, filename="pid.py", language="qiskit")
    assert body["ok"], body
    lines = dict(line.split(" ", 1) for line in body["stdout"].splitlines() if " " in line)
    assert int(lines["PID"]) != os.getpid()
    cwd = os.path.normcase(os.path.realpath(lines["CWD"]))
    assert not cwd.startswith(os.path.normcase(os.path.realpath(ROOT)))
    assert not os.path.exists(lines["CWD"]), "scratch directory should be removed afterwards"


@pytest.mark.requirement("NFR-04")
def test_python_side_effects_do_not_touch_the_server_process(client):
    source = ("import builtins\nbuiltins.QSLICER_POISON = True\n"
              "from qiskit import QuantumCircuit\nqc = QuantumCircuit(1, 1)\nqc.measure(0, 0)\n")
    _, body = post_analyze(client, source=source, language="qiskit")
    assert body["ok"], body
    import builtins

    assert not hasattr(builtins, "QSLICER_POISON")


@pytest.mark.requirement("VV-07")
@pytest.mark.requirement("FR-23")
@pytest.mark.requirement("NFR-12")
def test_python_exception_is_reported(client):
    source = BELL_PY + "raise RuntimeError('deliberate failure')\n"
    response, body = post_analyze(client, source=source, filename="bad.py", language="qiskit")
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["kind"] == "execution_error"
    assert "deliberate failure" in body["error"]["detail"]
    assert body["error"]["hint"] and body["error"]["traceback"]
    for key in ("token", "nodes", "edges"):
        assert key not in body


@pytest.mark.requirement("VV-07")
def test_python_without_circuit_is_reported(client):
    _, body = post_analyze(client, source="from qiskit import QuantumCircuit\nx = 1\n",
                           language="qiskit")
    assert body["ok"] is False
    assert body["error"]["kind"] == "no_circuit"
    assert "module-level variable" in body["error"]["hint"]


@pytest.mark.requirement("VV-07")
def test_worker_hard_crash_is_reported(client):
    source = "import os\nfrom qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\nos._exit(7)\n"
    _, body = post_analyze(client, source=source, language="qiskit")
    assert body["ok"] is False
    assert body["error"]["kind"] == "worker_crashed"
    assert "code 7" in body["error"]["message"]


@pytest.mark.requirement("VV-07")
@pytest.mark.requirement("NFR-06")
@pytest.mark.requirement("FR-23")
def test_timeout_is_reported_and_server_keeps_serving(client, monkeypatch, ghz_qasm_result):
    monkeypatch.setattr(analysis, "PARSE_TIMEOUT", 2)
    _, body = post_analyze(client, source=INFINITE_LOOP, filename="loop.py", language="qiskit")
    assert body["ok"] is False
    assert body["error"]["kind"] == "timeout"
    assert "2 seconds" in body["error"]["message"]
    assert "token" not in body and "nodes" not in body

    # The next requests are unaffected: a fresh analysis and an old token both work.
    _, ok = post_analyze(client, source=GHZ_QASM, language="qasm")
    assert ok["ok"]
    response, sliced = post_slice(client, token=ghz_qasm_result["token"], node_idx=4)
    assert response.status_code == 200 and sliced["ok"]


@pytest.mark.requirement("NFR-06")
def test_failures_do_not_affect_a_following_python_request(client):
    _, bad = post_analyze(client, source="import os\nos._exit(1)\nQuantumCircuit\n",
                          language="qiskit")
    assert bad["ok"] is False
    _, good = post_analyze(client, source=BELL_PY, filename="after_failure.py",
                           language="qiskit")
    assert good["ok"], good


def test_parse_timeout_comes_from_environment():
    env = dict(os.environ, QSLICER_PARSE_TIMEOUT="3")
    completed = subprocess.run(
        [sys.executable, "-c", "import analysis; print(analysis.PARSE_TIMEOUT)"],
        cwd=WEBAPP_DIR, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.stdout.strip().splitlines()[-1] == "3"


def test_default_parse_timeout_is_20s_without_environment(monkeypatch):
    env = {k: v for k, v in os.environ.items() if k != "QSLICER_PARSE_TIMEOUT"}
    completed = subprocess.run(
        [sys.executable, "-c", "import analysis; print(analysis.PARSE_TIMEOUT)"],
        cwd=WEBAPP_DIR, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.stdout.strip().splitlines()[-1] == "20"


@pytest.mark.requirement("NFR-07")
def test_python_source_reaches_the_program_unchanged(client):
    text = "café ☃ π\ttab  trailing  "
    source = ("from qiskit import QuantumCircuit\n"
              f"TEXT = {text!r}\n"
              "print(ascii(TEXT))\n"
              "print(len(open(__file__, encoding='utf-8').read().splitlines()))\n"
              "qc = QuantumCircuit(1, 1)\nqc.measure(0, 0)\n")
    _, body = post_analyze(client, source=source, language="qiskit")
    assert body["ok"], body
    printed = body["stdout"].splitlines()
    assert printed[0] == ascii(text)
    assert int(printed[1]) == len(source.splitlines())


@pytest.mark.requirement("NFR-08")
@pytest.mark.requirement("VV-08")
def test_same_python_input_twice_is_identical(client, bell_result):
    _, again = post_analyze(client, source=BELL_PY, filename="bell_pair_repeat.py",
                            language="qiskit")
    _, third = post_analyze(client, source=BELL_PY, filename="bell_pair_repeat.py",
                            language="qiskit")
    assert again["ok"] and third["ok"]
    for key in ("nodes", "edges", "entanglement", "stats", "dependency_types", "token"):
        assert again[key] == third[key], key
    # The bundled example is the same circuit: same graph, different token (filename differs).
    assert [n["name"] for n in again["nodes"]] == [n["name"] for n in bell_result["nodes"]]
    for idx in range(len(again["nodes"])):
        for direction in ("backward", "forward"):
            _, a = post_slice(client, token=again["token"], node_idx=idx, direction=direction)
            _, b = post_slice(client, token=third["token"], node_idx=idx, direction=direction)
            assert a == b


@pytest.mark.requirement("FR-03")
@pytest.mark.requirement("VV-04")
def test_python_with_empty_circuit_is_reported(client):
    source = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\n"
    _, body = post_analyze(client, source=source, language="qiskit")
    assert body["ok"] is False
    assert body["error"]["kind"] == "empty_program"
