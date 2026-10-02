"""parse_worker.py (run in-process) and capture_io.py.

``parse_worker.main`` is called directly with a scratch directory: Qiskit is
already imported in the test process, so this is fast. The real subprocess
path, including the timeout, is in test_webapp_python_route.py.
"""

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

import capture_io
import parse_worker
from webapp_helpers import BELL_PY, WEBAPP_DIR


def run_worker(tmp_path, source, name="circuit.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    code = parse_worker.main(["parse_worker.py", str(path), str(tmp_path)])
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    return code, result


# ------------------------------------------------------------- capture_io --

def test_captured_buffer_hands_contents_to_sink_on_close():
    sink = []
    buffer = capture_io.CaptureIO(sink).StringIO()
    assert isinstance(buffer, capture_io.CapturedBuffer)
    assert isinstance(buffer, io.StringIO)
    buffer.write("hello")
    buffer.close()
    assert sink == ["hello"]
    buffer.close()                      # second close is harmless
    assert sink == ["hello"]


def test_capture_io_ignores_constructor_arguments():
    sink = []
    buffer = capture_io.CaptureIO(sink).StringIO("initial", newline="\n")
    assert buffer.getvalue() == ""
    buffer.close()
    assert sink == [""]


def test_capture_io_falls_through_to_io():
    stand_in = capture_io.CaptureIO([])
    assert stand_in.BytesIO is io.BytesIO
    assert stand_in.TextIOWrapper is io.TextIOWrapper
    with pytest.raises(AttributeError):
        stand_in.no_such_attribute


def test_capture_io_works_as_a_with_block_and_redirect_target():
    sink = []
    stand_in = capture_io.CaptureIO(sink)
    with stand_in.StringIO() as buf, redirect_stdout(buf):
        print("from the program")
    assert sink == ["from the program\n"]


# ----------------------------------------------------------- parse_worker --

def test_worker_usage_error_in_process():
    assert parse_worker.main(["parse_worker.py"]) == 2
    assert parse_worker.main(["parse_worker.py", "a", "b", "c"]) == 2


def test_worker_usage_error_as_subprocess():
    completed = subprocess.run([sys.executable, os.path.join(WEBAPP_DIR, "parse_worker.py")],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2
    assert "usage: parse_worker.py" in completed.stderr


def test_worker_success_writes_result_and_qpy(tmp_path):
    source = BELL_PY + "print('drawn circuit')\n"
    code, result = run_worker(tmp_path, source)
    assert code == 0
    assert result == {"ok": True, "stdout": "drawn circuit\n", "error": None}
    import qiskit.qpy

    with open(tmp_path / "circuit.qpy", "rb") as handle:
        circuit = qiskit.qpy.load(handle)[0]
    assert [i.operation.name for i in circuit.data] == ["h", "cx", "measure", "measure"]


def test_worker_restores_parser_io_module(tmp_path):
    import Parsers.qiskitparser as module

    before = module.io
    run_worker(tmp_path, BELL_PY)
    assert module.io is before
    run_worker(tmp_path, "raise RuntimeError('x')\n")
    assert module.io is before


def test_worker_reports_no_circuit(tmp_path):
    code, result = run_worker(tmp_path, "x = 1\nprint('ran')\n")
    assert code == 1
    assert result["ok"] is False
    assert result["error"]["kind"] == "no_circuit"
    assert "No QuantumCircuit" in result["error"]["detail"]
    assert result["stdout"] == "ran\n"
    assert not (tmp_path / "circuit.qpy").exists()


@pytest.mark.requirement("VV-07")
def test_worker_reports_python_exception(tmp_path):
    code, result = run_worker(tmp_path, BELL_PY + "raise RuntimeError('boom here')\n",
                              name="mine.py")
    assert code == 1
    error = result["error"]
    assert error["kind"] == "execution_error"
    assert "boom here" in error["detail"]
    assert isinstance(error["traceback"], list) and error["traceback"]
    joined = "\n".join(error["traceback"])
    # Absolute paths are taken out of what is shown to the user.
    assert str(tmp_path) not in joined
    assert parse_worker.REPO_ROOT not in joined


@pytest.mark.requirement("VV-07")
def test_worker_reports_system_exit(tmp_path):
    code, result = run_worker(tmp_path, "import sys\nsys.exit(4)\n")
    assert code == 1
    assert result["error"]["kind"] == "execution_error"
    assert result["error"]["message"].startswith("SystemExit")


@pytest.mark.requirement("NFR-12")
def test_worker_traceback_names_the_failing_source_line(tmp_path):
    source = BELL_PY + "raise RuntimeError('boom')\n"     # line 8
    _, result = run_worker(tmp_path, source, name="mine.py")
    joined = "\n".join(result["error"]["traceback"])
    assert 'File "mine.py", line 8' in joined


def test_worker_reports_backend_unavailable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "Parsers.qiskitparser", None)
    code, result = run_worker(tmp_path, BELL_PY)
    assert code == 1
    assert result["error"]["kind"] == "backend_unavailable"
    assert "ImportError" in result["error"]["detail"] or "ModuleNotFound" in result["error"]["detail"]


def test_clean_traceback_strips_paths():
    circuit_path = os.path.join(parse_worker.REPO_ROOT, "tmp", "abc", "c.py")
    try:
        exec(compile("raise ValueError('x')", circuit_path, "exec"))
    except ValueError:
        lines = parse_worker._clean_traceback(circuit_path)
    joined = "\n".join(lines)
    assert 'File "c.py"' in joined
    assert parse_worker.REPO_ROOT not in joined
    assert len(lines) <= 12
