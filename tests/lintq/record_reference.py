"""Record what the original LintQ reports, for the port's conformance test.

    QSLICER_CODEQL=... QSLICER_LINTQ=... python tests/lintq/record_reference.py

Runs LintQ's CodeQL queries (``LintQ-all.qls``) once over three sets of
programs and writes ``tests/lintq/reference.json``:

* ``tests/lintq/programs/lintq/``: LintQ's own test programs (MIT, see the
  LICENSE.md there);
* ``tests/lintq/programs/probes/``: programs written to exercise each query's
  edge cases, never used to tune the port;
* ``webapp/examples/`` and ``benchmarks/bugs4q/programs/``.

It also writes ``benchmarks/bugs4q/external/lintq-codeql.json``, so every
run on Bugs4Q compares against the original even where CodeQL is not
installed. Paths are repository-relative. Takes about a minute (two the first
time, while CodeQL compiles the queries).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from Linters import codeql_lintq  # noqa: E402

TREES = ["tests/lintq/programs", "webapp/examples", "benchmarks/bugs4q/programs"]
REFERENCE = os.path.join(ROOT, "tests", "lintq", "reference.json")
EXTERNAL = os.path.join(ROOT, "benchmarks", "bugs4q", "external", "lintq-codeql.json")


def _version(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    ok, reason = codeql_lintq.available()
    if not ok:
        print(reason, file=sys.stderr)
        return 1
    cfg = codeql_lintq.settings()
    staging = tempfile.mkdtemp(prefix="lintq-reference-")
    try:
        files = []
        for tree in TREES:
            for dirpath, _dirs, names in os.walk(os.path.join(ROOT, tree)):
                for name in names:
                    if name.endswith(".py"):
                        src = os.path.join(dirpath, name)
                        rel = os.path.relpath(src, ROOT).replace(os.sep, "/")
                        dst = os.path.join(staging, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copyfile(src, dst)
                        files.append(rel)
        warnings = codeql_lintq.analyze_directory(staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    results = {rel: [] for rel in sorted(files)}
    for w in warnings:
        if w["path"] in results:
            results[w["path"]].append([w["lintq_id"], w["line"], w["column"], w["message"]])
    for found in results.values():
        found.sort()
    codeql_version = _version([cfg["codeql"], "version", "--format=terse"])
    lintq_commit = _version(["git", "-C", cfg["lintq"], "rev-parse", "HEAD"])
    reference = {
        "tool": "LintQ", "suite": codeql_lintq.SUITE, "lintq_commit": lintq_commit,
        "codeql_version": codeql_version, "files": len(results),
        "warnings": sum(len(v) for v in results.values()), "results": results,
    }
    with open(REFERENCE, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(reference, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    prefix = "benchmarks/bugs4q/programs/"
    external = {
        "tool": "lintq-codeql",
        "label": "LintQ (original, recorded)",
        "version": f"CodeQL {codeql_version}, LintQ {lintq_commit[:7]}",
        "notes": f"{codeql_lintq.SUITE}, recorded by tests/lintq/record_reference.py",
        "results": {
            rel[len(prefix):]: [{"line": line, "code": codeql_lintq.CODE_OF.get(lid, lid),
                                 "message": message}
                                for lid, line, _col, message in found]
            for rel, found in results.items() if rel.startswith(prefix) and found
        },
    }
    os.makedirs(os.path.dirname(EXTERNAL), exist_ok=True)
    with open(EXTERNAL, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(external, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print(f"{reference['warnings']} warnings in {len(results)} files -> {REFERENCE}")
    print(f"Bugs4Q part -> {EXTERNAL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
