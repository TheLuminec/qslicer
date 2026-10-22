"""Benchmarks: grade the selected lint rules on datasets of buggy and fixed programs.

A dataset is a folder with a ``manifest.json``, the programs it lists under
``programs/``, and recorded runs of other tools under ``external/``. Folders
under ``benchmarks/`` that have a manifest are found automatically; the first
is Bugs4Q (``benchmarks/bugs4q/``, real Qiskit bugs). A manifest looks like::

    {"name": "Bugs4Q", "description": "real Qiskit bugs",
     "upstream": "https://...", "commit": "...", "license": "Apache-2.0",
     "bugs": [{"key": "github-1", "buggy": "path/buggy.py", "fixed": "path/fixed.py",
               "source": "GitHub issue", "reference": "#5098", "url": "https://...",
               "type": "parameter"}, ...]}

Only ``bugs`` and each bug's ``key``, ``buggy`` and ``fixed`` (paths under
``programs/``) are required; anything else is shown when present.

Ground truth is the fix itself. The *fault lines* of a bug are the buggy
program's code lines the fix deletes or rewrites; comments, blank lines and
re-indentation do not count. A fix that only adds code is an *omission*: the
code lines on either side of the insertion stand in for the fault. A fix that
rewrites more than half the program is a *rewrite*, reported apart because
any warning in it lands on a "fault line".

A rule set is graded per bug:

* **warned** -- it reports anything on the buggy program;
* **hit** -- one of those warnings is on a fault line;
* **detected** -- a hit whose rule fires less often on the fixed program, so
  the fix is what silenced it. This is the headline number.

Warnings left on fixed programs are the false-alarm side: the fix resolved
the bug, so most of what is still reported there is noise.

Nothing is executed. Linters read the programs as text, or through the static
front ends, exactly as they do on the slicer page.
"""

import difflib
import glob
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import linters

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARKS_DIR = os.path.join(REPO_ROOT, "benchmarks")
DEFAULT_DATASET = "bugs4q"

VERSIONS = ("buggy", "fixed")
REWRITE_SHARE = 0.5


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------

class Dataset:
    """One manifest, with each bug's sources and ground truth attached."""

    def __init__(self, manifest_path: str):
        self.manifest_path = os.path.abspath(manifest_path)
        self.dir = os.path.dirname(self.manifest_path)
        self.id = os.path.basename(self.dir)
        self.programs_dir = os.path.join(self.dir, "programs")
        self.external_dir = os.path.join(self.dir, "external")
        with open(self.manifest_path, encoding="utf-8") as handle:
            self.manifest = json.load(handle)
        for bug in self.manifest["bugs"]:
            bug["sources"] = {version: self._read(bug[version]) for version in VERSIONS}
            bug["truth"] = ground_truth(bug["sources"]["buggy"], bug["sources"]["fixed"])

    def _read(self, rel: str) -> str:
        with open(os.path.join(self.programs_dir, rel), encoding="utf-8",
                  errors="replace") as handle:
            return handle.read()

    @property
    def name(self) -> str:
        return self.manifest.get("name") or self.id

    @property
    def bugs(self) -> List[Dict[str, Any]]:
        return self.manifest["bugs"]

    def get_bug(self, key: str) -> Optional[Dict[str, Any]]:
        return next((bug for bug in self.bugs if bug["key"] == key), None)


_DATASETS: Dict[str, Dataset] = {}


def dataset_ids() -> List[str]:
    """The folders under ``benchmarks/`` that hold a manifest, default first."""
    found = sorted(name for name in os.listdir(BENCHMARKS_DIR)
                   if os.path.isfile(os.path.join(BENCHMARKS_DIR, name, "manifest.json")))
    return sorted(found, key=lambda name: name != DEFAULT_DATASET)


def load(which: "Dataset | str | None" = None) -> Dataset:
    """A dataset by folder name under ``benchmarks/`` or by manifest path (cached).

    ``None`` is the default dataset. Raises ``KeyError`` for a name with no
    manifest. Only pass a path that came from the command line, never one from
    a web request: the web app checks names against ``dataset_ids()``.
    """
    if isinstance(which, Dataset):
        return which
    which = which or DEFAULT_DATASET
    path = which if which.endswith(".json") or os.sep in which or "/" in which \
        else os.path.join(BENCHMARKS_DIR, which, "manifest.json")
    if not os.path.isfile(path):
        raise KeyError(f"no benchmark manifest at {path}")
    path = os.path.abspath(path)
    if path not in _DATASETS:
        _DATASETS[path] = Dataset(path)
    return _DATASETS[path]


def bugs(dataset: "Dataset | str | None" = None) -> List[Dict[str, Any]]:
    return load(dataset).bugs


def get_bug(key: str, dataset: "Dataset | str | None" = None) -> Optional[Dict[str, Any]]:
    return load(dataset).get_bug(key)


def describe_bug(bug: Dict[str, Any], with_sources: bool = False) -> Dict[str, Any]:
    """A JSON-safe view of one bug, without its sources unless asked."""
    entry = {k: v for k, v in bug.items() if k not in ("sources", "truth")}
    entry["truth"] = dict(bug["truth"], lines=sorted(bug["truth"]["lines"]))
    if with_sources:
        entry["sources"] = dict(bug["sources"])
    return entry


def summary(dataset: "Dataset | str | None" = None) -> Dict[str, Any]:
    data = load(dataset)
    kinds: Dict[str, int] = {}
    for bug in data.bugs:
        kinds[bug["truth"]["kind"]] = kinds.get(bug["truth"]["kind"], 0) + 1
    manifest = data.manifest
    return {"id": data.id, "name": data.name,
            "description": manifest.get("description", ""),
            "upstream": manifest.get("upstream", ""), "commit": manifest.get("commit", ""),
            "license": manifest.get("license", ""), "bugs": len(data.bugs), "kinds": kinds,
            "sources": sorted({bug.get("source", "") for bug in data.bugs} - {""})}


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------

def _is_code(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def ground_truth(buggy: str, fixed: str) -> Dict[str, Any]:
    """The fault lines of a bug, from the diff between its two programs.

    Returns ``{"kind", "lines", "changed", "code_lines"}``. ``kind`` is
    ``edit``, ``rewrite``, ``omission`` or ``none`` (the programs do not
    differ in code); ``lines`` are 1-based buggy-program lines, the fault
    lines for an edit or rewrite and the lines around the insertion for an
    omission. Lines compare with surrounding whitespace stripped, so a line
    that was only re-indented is not a fault.
    """
    a = [line.strip() for line in buggy.splitlines()]
    b = [line.strip() for line in fixed.splitlines()]
    code_lines = sum(1 for line in a if _is_code(line))
    faults = set()
    insertions = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = {i + 1 for i in range(i1, i2) if _is_code(a[i])}
        added = any(_is_code(b[j]) for j in range(j1, j2))
        faults |= removed
        if added and not removed:
            insertions.append(i1)

    if faults:
        kind = "rewrite" if len(faults) > REWRITE_SHARE * max(code_lines, 1) else "edit"
        lines = faults
    elif insertions:
        kind = "omission"
        lines = set()
        for at in insertions:      # 0-based index of the buggy line the fix inserts before
            before = next((i + 1 for i in range(at - 1, -1, -1) if _is_code(a[i])), None)
            after = next((i + 1 for i in range(at, len(a)) if _is_code(a[i])), None)
            lines |= {line for line in (before, after) if line}
    else:
        kind, lines = "none", set()
    return {"kind": kind, "lines": lines, "changed": len(faults), "code_lines": code_lines}


# --------------------------------------------------------------------------
# External tools
# --------------------------------------------------------------------------

def external_results(dataset: "Dataset | str | None" = None) -> List[Dict[str, Any]]:
    """Recorded runs of other tools, from the dataset's ``external/*.json``.

    Each file is ``{"tool", "label", "version", "notes", "results"}`` where
    ``results`` maps a program's path (as in the manifest) to its warnings,
    ``[{"line", "code", "message"}]``. A program the file does not list had no
    warnings. ``import_sarif`` writes these from a SARIF log.
    """
    found = []
    for path in sorted(glob.glob(os.path.join(load(dataset).external_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("results"), dict):
            data.setdefault("tool", os.path.splitext(os.path.basename(path))[0])
            data.setdefault("label", data["tool"])
            found.append(data)
    return found


def import_sarif(sarif_path: str, tool: str, label: str = "", version: str = "",
                 notes: str = "", dataset: "Dataset | str | None" = None) -> Dict[str, Any]:
    """Turn a SARIF log (CodeQL, LintQ, ...) into an external-results record.

    Each result's file is matched to a program of the dataset by path suffix,
    so the tool can have been run over this repository's ``programs/`` folder
    or over a checkout of the upstream dataset. Results in other files are
    counted and dropped.
    """
    with open(sarif_path, encoding="utf-8") as handle:
        log = json.load(handle)
    paths = [bug[version_] for bug in bugs(dataset) for version_ in VERSIONS]
    results: Dict[str, List[Dict[str, Any]]] = {}
    unmatched = 0
    tool_version = version
    for run in log.get("runs", []):
        driver = run.get("tool", {}).get("driver", {})
        tool_version = tool_version or driver.get("semanticVersion") or driver.get("version", "")
        for result in run.get("results", []):
            locations = result.get("locations") or [{}]
            physical = locations[0].get("physicalLocation", {})
            uri = physical.get("artifactLocation", {}).get("uri", "")
            uri = uri.replace("\\", "/").split("?")[0]
            match = next((p for p in paths if uri.endswith(p)), None)
            if match is None:
                unmatched += 1
                continue
            results.setdefault(match, []).append({
                "line": physical.get("region", {}).get("startLine", 0),
                "code": result.get("ruleId") or result.get("rule", {}).get("id", "?"),
                "message": result.get("message", {}).get("text", ""),
            })
    return {"tool": tool, "label": label or tool, "version": tool_version, "notes": notes,
            "source": os.path.basename(sarif_path), "unmatched_results": unmatched,
            "results": results}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _rule_of(warning: Dict[str, Any]) -> str:
    return warning.get("rule") or warning.get("code", "?")


def grade_bug(bug: Dict[str, Any], buggy: List[Dict[str, Any]],
              fixed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How one rule set did on one bug, given its warnings on both programs."""
    truth = bug["truth"]
    targets = truth["lines"]
    hits = [w for w in buggy if w.get("line") in targets]
    fixed_counts: Dict[str, int] = {}
    for w in fixed:
        fixed_counts[_rule_of(w)] = fixed_counts.get(_rule_of(w), 0) + 1
    buggy_counts: Dict[str, int] = {}
    for w in buggy:
        buggy_counts[_rule_of(w)] = buggy_counts.get(_rule_of(w), 0) + 1
    detecting = sorted({_rule_of(w) for w in hits
                        if fixed_counts.get(_rule_of(w), 0) < buggy_counts[_rule_of(w)]})
    gradable = truth["kind"] != "none"
    return {
        "gradable": gradable,
        "warned": bool(buggy),
        "hit": gradable and bool(hits),
        "detected": gradable and bool(detecting),
        "detected_by": detecting if gradable else [],
        "hit_lines": sorted({w["line"] for w in hits}),
        "warnings": {"buggy": len(buggy), "fixed": len(fixed)},
    }


def _empty_totals() -> Dict[str, Any]:
    return {"bugs": 0, "gradable": 0, "detected": 0, "detected_no_rewrites": 0, "hit": 0,
            "warned": 0, "warnings_buggy": 0, "warnings_fixed": 0, "hit_warnings": 0,
            "fixed_programs_warned": 0}


def _add(totals: Dict[str, Any], bug: Dict[str, Any], grade: Dict[str, Any],
         buggy: List[Dict[str, Any]], fixed: List[Dict[str, Any]]) -> None:
    totals["bugs"] += 1
    totals["gradable"] += grade["gradable"]
    totals["detected"] += grade["detected"]
    totals["detected_no_rewrites"] += grade["detected"] and bug["truth"]["kind"] != "rewrite"
    totals["hit"] += grade["hit"]
    totals["warned"] += grade["warned"]
    totals["warnings_buggy"] += len(buggy)
    totals["warnings_fixed"] += len(fixed)
    totals["hit_warnings"] += sum(1 for w in buggy if w.get("line") in bug["truth"]["lines"])
    totals["fixed_programs_warned"] += bool(fixed)


def _finish(totals: Dict[str, Any]) -> Dict[str, Any]:
    gradable = totals["gradable"] or 1
    totals["detection_rate"] = round(totals["detected"] / gradable, 4)
    totals["hit_rate"] = round(totals["hit"] / gradable, 4)
    totals["warning_precision"] = (round(totals["hit_warnings"] / totals["warnings_buggy"], 4)
                                   if totals["warnings_buggy"] else None)
    return totals


def _compact(warning: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("code", "rule", "tool", "severity", "line", "column", "message")
    return {k: warning[k] for k in keep if k in warning}


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

_RESULTS: "OrderedDict[Tuple, Dict[str, Any]]" = OrderedDict()
_RESULTS_LIMIT = 16
# One run at a time: a run takes seconds, and the server has a handful of
# threads. A request that waits here usually finds its report cached.
_RUN_LOCK = threading.Lock()


def run(selection: Optional[Iterable[str]] = None, profile: str = "baseline",
        include_external: bool = True, use_cache: bool = True,
        dataset: "Dataset | str | None" = None) -> Dict[str, Any]:
    """Grade ``selection`` (rule ids, as on the slicer page) on every bug of a dataset.

    The report compares, side by side: the whole selection, each selected
    linter on its own (when more than one is selected), and every recorded
    external tool. Results are cached per (dataset, selection, profile).
    """
    data = load(dataset)
    chosen = linters.normalize_selection(selection)
    key = (data.manifest_path, tuple(chosen), profile, include_external)
    with _RUN_LOCK:
        if use_cache and key in _RESULTS:
            _RESULTS.move_to_end(key)
            return _RESULTS[key]
        report = _run(data, chosen, profile, include_external)
        _RESULTS[key] = report
        while len(_RESULTS) > _RESULTS_LIMIT:
            _RESULTS.popitem(last=False)
        return report


def _run(data: Dataset, chosen: List[str], profile: str,
         include_external: bool) -> Dict[str, Any]:
    started = time.monotonic()
    by_linter = linters.selection_by_linter(chosen)
    # Linters with a batch mode (an external tool) lint every program in one run.
    batched: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    programs = [(bug[v], bug["sources"][v]) for bug in data.bugs for v in VERSIONS]
    for linter_id in by_linter:
        linter = linters.get(linter_id)
        if linter is not None and linter.batch is not None and linter.available()[0]:
            try:
                batched[linter_id] = linter.batch(programs)
            except Exception as exc:
                crash = linters.crash_finding(linter, exc)
                batched[linter_id] = {path: [crash] for path, _ in programs}
    raw: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    front_end: Dict[str, Dict[str, Any]] = {}
    for bug in data.bugs:
        raw[bug["key"]] = {}
        for version in VERSIONS:
            context = linters.LintContext(bug["sources"][version], os.path.basename(bug[version]),
                                          "python-static", profile)
            pre = {lid: found.get(bug[version], []) for lid, found in batched.items()}
            raw[bug["key"]][version] = linters.run(context.source, context.filename,
                                                   context.kind, chosen, profile, context, pre)
            if context._parsed is not None:
                program, error = context._parsed
                front_end.setdefault(bug["key"], {})[version] = {
                    "ok": program is not None,
                    "instructions": len(program) if program is not None else 0,
                    "error": error or "",
                }

    configs = [{"id": "selection", "label": "Selected rules", "kind": "selection",
                "rules": chosen, "filter": None}]
    if len(by_linter) > 1:
        for linter_id, codes in by_linter.items():
            linter = linters.get(linter_id)
            configs.append({"id": linter_id, "label": linter.label if linter else linter_id,
                            "kind": "linter", "rules": [f"{linter_id}/{c}" for c in codes],
                            "filter": linter_id})
    externals = external_results(data) if include_external else []
    for ext in externals:
        configs.append({"id": f"external:{ext['tool']}", "label": ext["label"],
                        "kind": "external", "version": ext.get("version", ""),
                        "notes": ext.get("notes", ""), "rules": [], "filter": ext})

    per_bug = []
    totals = {config["id"]: _empty_totals() for config in configs}
    per_rule: Dict[str, Dict[str, Any]] = {}
    for bug in data.bugs:
        row = {"key": bug["key"], "source": bug.get("source", ""),
               "reference": bug.get("reference", ""), "url": bug.get("url", ""),
               "type": bug.get("type", ""), "paths": {v: bug[v] for v in VERSIONS},
               "truth": {"kind": bug["truth"]["kind"], "lines": sorted(bug["truth"]["lines"])},
               "front_end": front_end.get(bug["key"], {}), "grades": {},
               "warnings": {v: [_compact(w) for w in raw[bug["key"]][v]] for v in VERSIONS}}
        for config in configs:
            buggy, fixed = _warnings_for(config, bug, raw[bug["key"]])
            grade = grade_bug(bug, buggy, fixed)
            row["grades"][config["id"]] = grade
            _add(totals[config["id"]], bug, grade, buggy, fixed)
            if config["kind"] == "external":
                row.setdefault("external_warnings", {})[config["id"]] = {"buggy": buggy,
                                                                         "fixed": fixed}
        for rule_id in chosen:
            stats = per_rule.setdefault(rule_id, {"rule": rule_id, "buggy": 0, "fixed": 0,
                                                  "hits": 0, "detected": 0, "programs": 0})
            buggy = [w for w in raw[bug["key"]]["buggy"] if w.get("rule") == rule_id]
            fixed = [w for w in raw[bug["key"]]["fixed"] if w.get("rule") == rule_id]
            stats["buggy"] += len(buggy)
            stats["fixed"] += len(fixed)
            stats["programs"] += bool(buggy)
            stats["hits"] += sum(1 for w in buggy if w.get("line") in bug["truth"]["lines"])
            stats["detected"] += rule_id in row["grades"]["selection"]["detected_by"]
        per_bug.append(row)

    catalog = {r["id"]: r for entry in linters.catalog() for r in entry["rules"]}
    rules_report = []
    for rule_id in chosen:
        stats = per_rule.get(rule_id, {"rule": rule_id, "buggy": 0, "fixed": 0, "hits": 0,
                                       "detected": 0, "programs": 0})
        info = catalog.get(rule_id, {})
        rules_report.append(dict(stats, code=info.get("code", rule_id), title=info.get("title", ""),
                                 tool=rule_id.partition("/")[0]))

    read = [state for states in front_end.values() for state in states.values()]
    report = {
        "dataset": summary(data),
        "profile": profile,
        "selection": chosen,
        "linters": [{"id": lid, "label": (linters.get(lid).label if linters.get(lid) else lid),
                     "rules": len(codes)} for lid, codes in by_linter.items()],
        "configs": [{k: v for k, v in config.items() if k != "filter"} for config in configs],
        "totals": {cid: _finish(t) for cid, t in totals.items()},
        "rules": rules_report,
        "bugs": per_bug,
        "front_end": ({"attempted": len(read), "read": sum(1 for s in read if s["ok"])}
                      if read else None),
        "elapsed": round(time.monotonic() - started, 2),
    }
    return report


def _warnings_for(config: Dict[str, Any], bug: Dict[str, Any],
                  raw: Dict[str, List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]],
                                                                 List[Dict[str, Any]]]:
    if config["kind"] == "selection":
        return raw["buggy"], raw["fixed"]
    if config["kind"] == "linter":
        return ([w for w in raw["buggy"] if w.get("tool") == config["filter"]],
                [w for w in raw["fixed"] if w.get("tool") == config["filter"]])
    results = config["filter"]["results"]
    return (list(results.get(bug["buggy"], [])), list(results.get(bug["fixed"], [])))
