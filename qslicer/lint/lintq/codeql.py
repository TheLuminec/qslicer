"""Run the original LintQ (CodeQL queries) on Python sources.

LintQ ships as CodeQL query packs. This adapter builds a CodeQL database for
a file or a directory, runs LintQ's ``LintQ-all.qls`` suite on it, and returns
the warnings with the same codes as the port (``lintq_port.RULES``), so the
two can be compared rule by rule. Nothing is executed: CodeQL's extractor
only reads the sources.

It is used when three settings are present:

    QSLICER_CODEQL          the CodeQL CLI executable (LintQ was built for 2.11.x)
    QSLICER_LINTQ           a checkout of https://github.com/sola-st/LintQ, after
                            ``codeql pack install`` in qlint/codeql/src and lib
    QSLICER_CODEQL_PYTHON   optional: the Python CodeQL's extractor runs on
                            (default: this interpreter)

CodeQL 2.11's extractor imports ``imp``, which Python 3.12 removed; on 3.12
or later a one-function stand-in from ``codeql_support/`` goes on its path.
A first run compiles LintQ's queries (a minute or two); later runs reuse them.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SUPPORT = os.path.join(HERE, "codeql_support")
SUITE = "LintQ-all.qls"

from .port import CODE_OF, RULES


def settings() -> Dict[str, str]:
    return {
        "codeql": os.environ.get("QSLICER_CODEQL", ""),
        "lintq": os.environ.get("QSLICER_LINTQ", ""),
        "python": os.environ.get("QSLICER_CODEQL_PYTHON", "") or sys.executable,
    }


def available() -> Tuple[bool, str]:
    """Whether the original LintQ can run here, and if not, why."""
    cfg = settings()
    if not cfg["codeql"]:
        return False, "Set QSLICER_CODEQL to the CodeQL CLI and QSLICER_LINTQ to a LintQ checkout."
    if not (os.path.isfile(cfg["codeql"]) or shutil.which(cfg["codeql"])):
        return False, f"QSLICER_CODEQL points at {cfg['codeql']}, which does not exist."
    if not cfg["lintq"] or not os.path.isfile(os.path.join(cfg["lintq"], SUITE)):
        return False, "Set QSLICER_LINTQ to a checkout of https://github.com/sola-st/LintQ."
    return True, ""


def _python_needs_imp_shim(python: str) -> bool:
    if python == sys.executable:
        return sys.version_info >= (3, 12)
    try:
        out = subprocess.run([python, "-c", "import sys; print(sys.version_info >= (3, 12))"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() == "True"
    except (OSError, subprocess.SubprocessError):
        return False


def _run(cmd, cwd=None, env=None, timeout=900):
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                            timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} {cmd[1]} {cmd[2]} failed: "
                           + " | ".join(tail))
    return result


def analyze_directory(source_root: str, threads: int = 0,
                      timeout: int = 900) -> List[Dict[str, Any]]:
    """LintQ's warnings for every Python file under ``source_root``.

    Each warning is ``{path, line, column, lintq_id, code, message}``, ``path``
    relative to ``source_root`` with forward slashes.
    """
    ok, reason = available()
    if not ok:
        raise RuntimeError(reason)
    cfg = settings()
    work = tempfile.mkdtemp(prefix="qslicer-codeql-")
    try:
        db = os.path.join(work, "db")
        sarif = os.path.join(work, "results.sarif")
        env = dict(os.environ)
        if _python_needs_imp_shim(cfg["python"]):
            env["PYTHONPATH"] = SUPPORT + os.pathsep + env.get("PYTHONPATH", "")
        _run([cfg["codeql"], "database", "init", "--language=python",
              f"--source-root={source_root}", db], timeout=timeout)
        _run([cfg["codeql"], "database", "trace-command", db, "--", cfg["python"],
              os.path.join(SUPPORT, "extract.py")], cwd=source_root, env=env, timeout=timeout)
        _run([cfg["codeql"], "database", "finalize", db], timeout=timeout)
        _run([cfg["codeql"], "database", "analyze", db, SUITE,
              f"--additional-packs={cfg['lintq']}", "--format=sarifv2.1.0",
              f"--output={sarif}", f"--threads={threads}"], cwd=cfg["lintq"], timeout=timeout)
        return read_sarif(sarif)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read_sarif(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        log = json.load(handle)
    found = []
    for run in log.get("runs", []):
        for result in run.get("results", []):
            physical = (result.get("locations") or [{}])[0].get("physicalLocation", {})
            region = physical.get("region", {})
            lintq_id = result.get("ruleId", "")
            found.append({
                "path": physical.get("artifactLocation", {}).get("uri", "").replace("\\", "/"),
                "line": region.get("startLine", 0),
                "column": region.get("startColumn", 0),
                "lintq_id": lintq_id,
                "code": CODE_OF.get(lintq_id, lintq_id),
                "message": " ".join(result.get("message", {}).get("text", "").split()),
            })
    return found


def check_source(source: str, filename: str = "program.py") -> List[Dict[str, Any]]:
    """Run the original LintQ on one program: tens of seconds, so never by default."""
    work = tempfile.mkdtemp(prefix="qslicer-lintq-src-")
    try:
        name = os.path.basename(filename) or "program.py"
        if not name.endswith(".py"):
            name += ".py"
        with open(os.path.join(work, name), "w", encoding="utf-8") as handle:
            handle.write(source)
        warnings = analyze_directory(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    diagnostics = []
    for w in warnings:
        if w["code"] not in RULES:
            continue
        lintq_id, precision, _title = RULES[w["code"]]
        diagnostics.append({
            "code": w["code"], "severity": "warning", "line": w["line"], "column": w["column"],
            "message": w["message"], "hint": f"LintQ {lintq_id} ({precision} precision), "
                                             "reported by the original CodeQL queries.",
            "lintq_id": lintq_id,
        })
    return diagnostics


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m qslicer.lint.lintq.codeql DIR [OUT.json]``: record LintQ's warnings for a directory."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(main.__doc__)
        return 2
    warnings = analyze_directory(os.path.abspath(argv[0]))
    text = json.dumps(warnings, indent=1)
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    print(f"{len(warnings)} warnings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
