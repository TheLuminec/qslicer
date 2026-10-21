"""LintQ, as two linters: the port, and the original where CodeQL is installed.

* ``lintq`` runs ``scripts/Linters/lintq_port.py``: LintQ's ten queries
  reimplemented on the syntax tree. Milliseconds, nothing to install, and on
  every program in tests/lintq/ it gives exactly the original's warnings.
* ``lintq-codeql`` runs LintQ's CodeQL queries themselves through
  ``scripts/Linters/codeql_lintq.py`` when QSLICER_CODEQL and QSLICER_LINTQ
  are set. Tens of seconds per run, so it is never on by default; the
  benchmark runs it once over every program instead of once per program.

Both report LintQ's queries under the same codes (LQ01-LQ10), so a benchmark
of the two compares them rule by rule.
"""

import hashlib
import os
import shutil
import tempfile
from typing import Any, Dict, List, Tuple

from linters import Linter, LintContext, Rule
from Linters import codeql_lintq, lintq_port

_DESCRIPTIONS = {
    "LQ01": "Two measurements of a qubit with no operation on it in between.",
    "LQ02": "An unconditional gate right after a measurement of its qubit.",
    "LQ03": "measure_all() with default arguments on a circuit that already has classical bits.",
    "LQ04": "A conditional gate with no measurement before it in its circuit.",
    "LQ05": "compose() without inplace=True whose result is neither stored nor returned.",
    "LQ06": "An operation added to a circuit transpiled at optimization level 3.",
    "LQ07": "A circuit measures a qubit no gate acted on, so the bit is constant.",
    "LQ08": "A circuit none of whose qubits is ever used.",
    "LQ09": "More qubits than classical bits, and no measure_all().",
    "LQ10": "The deprecated iden() is called.",
}


def _rules():
    rules = []
    for code, (lintq_id, precision, title) in lintq_port.RULES.items():
        rules.append(Rule(code, title, "warning",
                          f"{_DESCRIPTIONS[code]} LintQ {lintq_id}, {precision} precision."))
    return rules


def _run_port(context: LintContext) -> List[Dict[str, Any]]:
    return lintq_port.check_source(context.source, context.filename)


def _run_original(context: LintContext) -> List[Dict[str, Any]]:
    return codeql_lintq.check_source(context.source, context.filename)


_BATCH_CACHE: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}


def _batch_original(programs: List[Tuple[str, str]]) -> Dict[str, List[Dict[str, Any]]]:
    """One CodeQL run over many programs: ``[(path, source)]`` -> {path: findings}."""
    digest = hashlib.sha256()
    for path, source in programs:
        digest.update(path.encode() + b"\0" + source.encode("utf-8", "replace") + b"\0")
    key = digest.hexdigest()
    if key in _BATCH_CACHE:
        return _BATCH_CACHE[key]
    work = tempfile.mkdtemp(prefix="qslicer-lintq-batch-")
    try:
        for path, source in programs:
            target = os.path.join(work, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(source)
        warnings = codeql_lintq.analyze_directory(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found: Dict[str, List[Dict[str, Any]]] = {path: [] for path, _ in programs}
    for w in warnings:
        if w["path"] in found and w["code"] in lintq_port.RULES:
            lintq_id, precision, _ = lintq_port.RULES[w["code"]]
            found[w["path"]].append({
                "code": w["code"], "severity": "warning", "line": w["line"],
                "column": w["column"], "message": w["message"],
                "hint": f"LintQ {lintq_id} ({precision} precision), reported by CodeQL.",
            })
    _BATCH_CACHE[key] = found
    return found


LINTERS = [
    Linter(
        id="lintq",
        label="LintQ",
        description="LintQ's ten analyses (Paltenghi and Pradel, FSE 2024), reimplemented on the "
                    "syntax tree. Gives exactly the original's warnings on LintQ's tests and "
                    "Bugs4Q, in milliseconds and without CodeQL.",
        rules=_rules() + [Rule(lintq_port.TOO_LARGE, "Program too large", "info",
                               "LintQ skipped a program with very many quantum operations.")],
        run=_run_port,
        languages=("python",),
        default=False,
    ),
    Linter(
        id="lintq-codeql",
        label="LintQ (original, CodeQL)",
        description="The original LintQ queries, run by CodeQL. Takes tens of seconds per run.",
        rules=_rules(),
        run=_run_original,
        languages=("python",),
        default=False,
        available=codeql_lintq.available,
        batch=_batch_original,
    ),
]
