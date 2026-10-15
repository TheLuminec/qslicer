"""Executes one submitted circuit file, in a process of its own.

    python parse_worker.py <circuit.py> <work_dir>

Writes two things into ``work_dir``:

    result.json   {ok, stdout, error: {kind, message, detail, traceback}}
    circuit.qpy   the QuantumCircuit, on success

The point of the separate process is the timeout the parent puts on it: running
the submitted file is unavoidable with the Qiskit front end (that is how the
parser gets at the QuantumCircuit), but a circuit that loops forever now kills a
child process instead of wedging the server. The parent also gets to set the
working directory and, if you want to go further, the user the child runs as.

This is a containment measure, not a sandbox. It stops accidents. It does not
stop someone who is trying. See webapp/README.md.
"""

import json
import os
import sys
import traceback

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(WEBAPP_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

for path in (WEBAPP_DIR, SCRIPTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv):
    if len(argv) != 3:
        print("usage: parse_worker.py <circuit.py> <work_dir>", file=sys.stderr)
        return 2

    circuit_path, work_dir = argv[1], argv[2]
    result_path = os.path.join(work_dir, "result.json")
    qpy_path = os.path.join(work_dir, "circuit.qpy")

    program_output = []
    payload = {"ok": False, "stdout": "", "error": None}

    try:
        import qiskit.qpy
        from Parsers.qiskitparser import QiskitParser
        import Parsers.qiskitparser as qiskitparser_module
        from capture_io import CaptureIO
    except Exception as exc:
        payload["error"] = {
            "kind": "backend_unavailable",
            "message": "The analysis backend could not be imported in the worker.",
            "detail": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc().splitlines()[-8:],
        }
        _write(result_path, payload)
        return 1

    original_io = getattr(qiskitparser_module, "io", None)
    try:
        qiskitparser_module.io = CaptureIO(program_output)
        parser = QiskitParser()
        parser.parse_file(circuit_path)          # runs the submitted file
        circuit = parser.source

        with open(qpy_path, "wb") as handle:
            qiskit.qpy.dump(circuit, handle)

        payload["ok"] = True
    except ValueError as exc:
        message = str(exc)
        if "No QuantumCircuit assignment found" in message:
            payload["error"] = {
                "kind": "no_circuit",
                "message": "The file ran, but no QuantumCircuit was left in its namespace.",
                "detail": message,
                "traceback": None,
            }
        else:
            payload["error"] = {
                "kind": "execution_error",
                "message": "The circuit file raised an error while it was being executed.",
                "detail": message,
                "traceback": _clean_traceback(circuit_path),
            }
    except BaseException as exc:   # includes MemoryError and RecursionError
        payload["error"] = {
            "kind": "execution_error",
            "message": f"{type(exc).__name__} while running the circuit file.",
            "detail": str(exc),
            "traceback": _clean_traceback(circuit_path),
        }
    finally:
        if original_io is not None:
            qiskitparser_module.io = original_io

    payload["stdout"] = "\n".join(text for text in program_output if text.strip())
    _write(result_path, payload)
    return 0 if payload["ok"] else 1


MAX_TRACEBACK_LINES = 20


def _user_traceback(exc, circuit_path):
    """The traceback of the exception the submitted file raised, from its frames on.

    The parser re-raises the program's exception wrapped in a ValueError, so the
    interesting frames sit in an earlier exception of the chain. Returns None
    when no exception in the chain passed through the submitted file.
    """
    chain = []
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    for candidate in reversed(chain):          # innermost (original) first
        stack = traceback.extract_tb(candidate.__traceback__)
        starts = [i for i, frame in enumerate(stack) if frame.filename == circuit_path]
        if starts:
            lines = ["Traceback (most recent call last):"]
            for frame in traceback.format_list(stack[starts[0]:]):
                lines.extend(frame.rstrip("\n").splitlines())
            for text in traceback.format_exception_only(type(candidate), candidate):
                lines.extend(text.rstrip("\n").splitlines())
            return lines
    return None


def _clean_traceback(circuit_path):
    """The traceback from the submitted file's frames on, with absolute paths taken out.

    Falls back to the tail of the full traceback when the error did not pass
    through the submitted file. Long tracebacks keep their first frame (the
    user's line) and their tail.
    """
    display = os.path.basename(circuit_path)
    lines = _user_traceback(sys.exc_info()[1], circuit_path)
    if lines is None:
        lines = traceback.format_exc().splitlines()[-12:]
    elif len(lines) > MAX_TRACEBACK_LINES:
        lines = lines[:3] + ["  ..."] + lines[-(MAX_TRACEBACK_LINES - 4):]
    return [
        line.replace(circuit_path, display)
            .replace(REPO_ROOT + os.sep, "")
            .replace(REPO_ROOT, "")
        for line in lines
    ]


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
