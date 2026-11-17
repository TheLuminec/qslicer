"""Run one analysis in a child process with hard limits, for a public server.

The static parsers never execute a submission, and they bound their own Python
loops (steps, instructions, ``QSLICER_PARSE_TIMEOUT``). What they cannot bound is
work outside those loops -- a regular expression, building the dependence
graph, a big-integer operation, one huge allocation -- and much of it holds the
GIL, so in the web server's own process one bad request could stall every
thread or exhaust its memory.

So ``/api/analyze`` hands the whole of :func:`analysis.analyze_source` to a
short-lived child process:

* address space capped at its starting size plus ``QSLICER_SANDBOX_MEM_MB``
  (default 768), so an oversized allocation fails in the child with MemoryError;
* CPU time capped (``RLIMIT_CPU``), and a wall-clock deadline
  (``QSLICER_SANDBOX_TIMEOUT``, default the parse timeout plus 15 s) after which
  the child is killed;
* at most ``QSLICER_SANDBOX_JOBS`` (default 2) children at once; a request that
  cannot get a slot within the deadline is told the server is busy.

The child sends back the JSON-safe result and the cache entry (the program and
its QDG) for :func:`analysis._cache_put`, so ``/api/slice`` works exactly as
before. Children come from a ``forkserver`` that has ``analysis`` imported
already, so each one starts in milliseconds without re-importing Qiskit.

``QSLICER_SANDBOX=0`` turns this off (analysis runs in the request thread, as on
a laptop); it is on by default.
"""

from __future__ import annotations

import multiprocessing
import os
import pickle
import resource
import signal
import threading
from typing import Any, Dict, Optional

import analysis

ENABLED = os.environ.get("QSLICER_SANDBOX", "1").strip() not in ("0", "false", "no", "")
MEM_MB = int(os.environ.get("QSLICER_SANDBOX_MEM_MB", "768"))
WALL_SECONDS = float(os.environ.get("QSLICER_SANDBOX_TIMEOUT", str(analysis.PARSE_TIMEOUT + 15)))
JOBS = max(1, int(os.environ.get("QSLICER_SANDBOX_JOBS", "2")))
# A result larger than this is refused rather than unpickled into the server.
MAX_RESULT_BYTES = 256 * 1024 * 1024

_SLOTS = threading.BoundedSemaphore(JOBS)
_CTX_LOCK = threading.Lock()
_CTX: Optional[Any] = None


def _context():
    global _CTX
    with _CTX_LOCK:
        if _CTX is None:
            ctx = multiprocessing.get_context("forkserver")
            ctx.set_forkserver_preload(["analysis"])
            _CTX = ctx
        return _CTX


def _vm_size_bytes() -> int:
    """This process's current virtual size, from /proc (0 if unavailable)."""
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _limit_self(mem_mb: int, cpu_seconds: int) -> None:
    base = _vm_size_bytes()
    if base and mem_mb > 0:
        cap = base + mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    cpu = max(1, int(cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))


def _child(conn, kwargs: Dict[str, Any], mem_mb: int, cpu_seconds: int) -> None:
    try:
        _limit_self(mem_mb, cpu_seconds)
        result = analysis.analyze_source(**kwargs)
        entry = analysis._cache_get(result["token"]) if result.get("ok") else None
        conn.send_bytes(pickle.dumps(("ok", result, entry), protocol=pickle.HIGHEST_PROTOCOL))
    except MemoryError:
        try:
            conn.send_bytes(pickle.dumps(("memory", None, None)))
        except BaseException:
            pass
    except RecursionError:
        conn.send_bytes(pickle.dumps(("recursion", None, None)))
    except BaseException as exc:  # reported, never re-raised into the forkserver
        try:
            conn.send_bytes(pickle.dumps(("error", f"{type(exc).__name__}", None)))
        except BaseException:
            pass
    finally:
        conn.close()


def _failure(kind: str, message: str, hint: str) -> Dict[str, Any]:
    return {"ok": False, "diagnostics": [],
            "error": {"kind": kind, "stage": "analysis", "message": message,
                      "detail": "", "hint": hint, "line": 0}}


_TOO_BIG = _failure(
    "too_expensive", "That program needs more memory than this server allows for one analysis.",
    "Slice a smaller circuit, or run qslicer locally (pip install qslicer) for large ones.")
_TOO_SLOW = _failure(
    "timeout", "The analysis took too long and was stopped.",
    "Slice a smaller circuit, or run qslicer locally (pip install qslicer) for large ones.")
_BUSY = _failure(
    "busy", "The server is busy with other analyses. Try again in a moment.", "")
_CRASHED = _failure(
    "analysis_failed", "The analysis stopped unexpectedly.",
    "Try a smaller or simpler circuit; if it keeps happening, report the input.")
_TOO_DEEP = _failure(
    "too_deep", "The program nests too deeply to analyse.", "Flatten the deepest expression or block.")


def analyze(source: str, **kwargs: Any) -> Dict[str, Any]:
    """:func:`analysis.analyze_source` with the limits above; same return shape."""
    if not ENABLED:
        return analysis.analyze_source(source, **kwargs)
    if not _SLOTS.acquire(timeout=WALL_SECONDS):
        return _BUSY
    try:
        ctx = _context()
        receiver, sender = ctx.Pipe(duplex=False)
        child = ctx.Process(target=_child,
                            args=(sender, dict(kwargs, source=source), MEM_MB, WALL_SECONDS + 5),
                            daemon=True)
        child.start()
        sender.close()
        try:
            if not receiver.poll(WALL_SECONDS):
                return _TOO_SLOW
            try:
                data = receiver.recv_bytes(maxlength=MAX_RESULT_BYTES)
            except (EOFError, OSError):
                # Died without a word: classify by how it ended.
                child.join(5)
                if child.exitcode == -signal.SIGKILL:
                    return _TOO_BIG          # the kernel's OOM killer
                if child.exitcode == -signal.SIGXCPU:
                    return _TOO_SLOW         # RLIMIT_CPU
                return _CRASHED
        finally:
            receiver.close()
            if child.is_alive():
                child.kill()
            child.join(5)
        status, result, entry = pickle.loads(data)
        if status == "memory":
            return _TOO_BIG
        if status == "recursion":
            return _TOO_DEEP
        if status != "ok":
            return _CRASHED
        if result.get("ok") and entry is not None:
            analysis._cache_put(result["token"], entry)
        return result
    finally:
        _SLOTS.release()
