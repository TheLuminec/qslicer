"""Bridge between the web pages and the ``qslicer`` package.

Everything the web layer needs is converted to plain JSON-safe dicts here, so
the templates and the JavaScript never touch the analysis objects.

Nothing a user submits is ever executed. The front ends:

* **Python, static** (``language="qiskit-static"``) reads ``.py`` files
  without executing them: StaticQiskitParser interprets the source itself and
  refuses, with a line number, anything it cannot work out.
* **OpenQASM, static** (``language="qasm-static"``) interprets the reference
  parser's syntax tree itself instead of going through ``qiskit.qasm3``. Like
  the static Python route, it records where in the source every instruction
  came from (FR-07).
* **OpenQASM** (``language="qasm"``, not offered on the page) goes through
  ``qiskit.qasm3.load``, which parses the text and sends no location.

The route that executed a submitted ``.py`` file to reach its circuit has been
removed. Asking for it by name (``"qiskit"``, ``"python"``, ``"py"``) is
refused with ``execution_disabled`` rather than quietly rerouted, so a caller
relying on it finds out.
"""

import ast
import functools
import gc
import io
import json
import math
import os
import shutil
import tempfile
import threading
from collections import OrderedDict
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from typing import Any, Dict, List, Optional, Tuple

from qslicer import gates
from qslicer.lint import registry as linters

# Seconds a static parse may take before it is stopped.
PARSE_TIMEOUT = int(os.environ.get("QSLICER_PARSE_TIMEOUT", "20"))

# Extensions that go to the OpenQASM front end instead of the Python one.
QASM_SUFFIXES = (".qasm", ".qasm3")

# Imported lazily so the site still renders (with a clear banner) when qiskit or
# networkx are missing from the environment.
_IMPORT_ERROR: Optional[str] = None
try:
    from qslicer.dependencies.dependency_rules import DependencyType
    from qslicer.parsers.openqasmparser import OpenQASMParser
    from qslicer.parsers.staticopenqasmparser import StaticOpenQASMParser
    from qslicer.parsers.staticqiskitparser import StaticParseError, StaticQiskitParser
    from qslicer.qdg import QDG, get_rule_profile, list_rule_profiles
    from qslicer.slicer import QuantumSlicer, SlicingCriterion
except Exception as exc:  # pragma: no cover - depends on the local environment
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def backend_available() -> bool:
    return _IMPORT_ERROR is None


def backend_error() -> Optional[str]:
    return _IMPORT_ERROR


# --------------------------------------------------------------------------
# Analysis cache
#
# Slicing re-uses the QDG that the analyze call already built, so picking a new
# criterion never parses the submitted program again.
# --------------------------------------------------------------------------

_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_CACHE_LIMIT = 24
# Requests run on several threads (wsgi.py); the cache is changed under this.
_CACHE_LOCK = threading.RLock()

# Finished /api/analyze responses, encoded, are kept in their token's entry
# (under "responses", by response_key), so a repeated request is answered
# without linting or describing again -- and a response is only ever handed out
# while the QDG its token names is still here to slice. Evicting an entry drops
# its responses with it; beyond that, all of them together stay under this.
_RESPONSE_CACHE_BYTES = 64 * 1024 * 1024


def _cache_put(token: str, payload: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        previous = _CACHE.get(token)
        # Re-analyzing the same input (another lint selection, say) keeps the
        # responses already made for it: the token says they are still right.
        if previous is not None and previous.get("responses") and "responses" not in payload:
            payload["responses"] = previous["responses"]
        _CACHE[token] = payload
        _CACHE.move_to_end(token)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def _cache_get(token: str) -> Optional[Dict[str, Any]]:
    with _CACHE_LOCK:
        entry = _CACHE.get(token)
        if entry is not None:
            _CACHE.move_to_end(token)
        return entry


def response_key(source: str, filename: str = "", profile: str = "baseline",
                 language: Optional[str] = None, lint_rules: Optional[List[str]] = None,
                 bindings: Optional[List[Dict[str, Any]]] = None, circuit: int = 0) -> str:
    """What identifies an analyze request's response: everything it depends on.

    The lint selection is normalized (None and an explicit default selection
    are the same request) and the interpreter resolved, as analyze_source does.
    """
    material = json.dumps([
        source, filename, profile, source_kind(filename, language),
        linters.normalize_selection(lint_rules), bindings or [], circuit,
    ], sort_keys=True, default=repr)
    return sha256(material.encode("ascii")).hexdigest()


def cached_response(key: str) -> Optional[Dict[str, Any]]:
    """The stored response for ``response_key`` ``key``, or None.

    It is a dict with ``body`` (the encoded JSON) and ``gzip`` (the same,
    gzipped, or None). Finding one counts as using its token, which moves to
    the young end of the cache.
    """
    if _IMPORT_ERROR is not None:
        return None
    with _CACHE_LOCK:
        for token, entry in _CACHE.items():
            record = (entry.get("responses") or {}).get(key)
            if record is not None:
                _CACHE.move_to_end(token)
                return record
    return None


def store_response(key: str, token: str, body: bytes,
                   gzipped: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
    """Keep a successful analysis' encoded response with its token's QDG.

    Returns the stored record, or None when the token is no longer cached (the
    response would name a QDG that is gone) or the body alone is over the
    limit. Older responses are dropped, least recently used first, to keep
    the total under _RESPONSE_CACHE_BYTES.
    """
    record = {"body": body, "gzip": gzipped}
    size = _record_size(record)
    with _CACHE_LOCK:
        entry = _CACHE.get(token)
        if entry is None or size > _RESPONSE_CACHE_BYTES:
            return None
        responses = entry.setdefault("responses", OrderedDict())
        responses.pop(key, None)
        responses[key] = record
        total = sum(_record_size(r) for e in _CACHE.values()
                    for r in (e.get("responses") or {}).values())
        for other in list(_CACHE.values()):
            stored = other.get("responses") or {}
            for other_key in list(stored):
                if total <= _RESPONSE_CACHE_BYTES:
                    return record
                if stored[other_key] is not record:
                    total -= _record_size(stored.pop(other_key))
    return record


def _record_size(record: Dict[str, Any]) -> int:
    return len(record["body"]) + len(record.get("gzip") or b"")


# No full garbage collection while a request builds its analysis. The work
# allocates hundreds of thousands of objects that stay alive (instructions,
# graph edges, node dicts), so Python's default thresholds start a full
# collection every few hundred thousand allocations, and each one walks every
# cached parse and QDG as well: for Shor that was about a third of a warm
# analysis (0.22 s) and of a cold one (0.8 s). The young generations are still
# collected, so the static interpreter's short-lived cycles (350,000 objects
# for a cold Shor parse) are freed as before. Requests overlap on threads, so
# the thresholds are put back when the last of them is done.
_GC_LOCK = threading.Lock()
_GC_PAUSES = 0
_GC_SAVED: Tuple[int, ...] = ()
# Collections of the middle generation between two full ones, during a request
# (Python's default is 10).
_GC_FULL_EVERY = 1000


@contextmanager
def no_full_gc():
    global _GC_PAUSES, _GC_SAVED
    with _GC_LOCK:
        if _GC_PAUSES == 0:
            _GC_SAVED = gc.get_threshold()
            if len(_GC_SAVED) >= 3:
                gc.set_threshold(_GC_SAVED[0], _GC_SAVED[1], max(_GC_SAVED[2], _GC_FULL_EVERY))
        _GC_PAUSES += 1
    try:
        yield
    finally:
        with _GC_LOCK:
            _GC_PAUSES -= 1
            if _GC_PAUSES == 0:
                gc.set_threshold(*_GC_SAVED)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def _format_param(value: Any) -> str:
    """Render a gate parameter compactly, with a pi-fraction where it is close.

    Control-flow operations such as ``if_else`` carry whole sub-circuits as
    parameters, whose repr is a multi-line ASCII circuit drawing. Those are
    collapsed to something that fits on a node.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        if value is None:
            return "none"
        if hasattr(value, "num_qubits") and hasattr(value, "data"):
            return f"<circuit {len(value.data)} ops>"
        text = " ".join(str(value).split())
        return text if len(text) <= 24 else text[:21] + "..."
    return _format_number(number)


# Keyed by the float alone, which is all the text depends on. Large circuits
# repeat a few hundred angles thousands of times (Shor's QFTs), and the pi
# search below is most of the cost of describing them.
@functools.lru_cache(maxsize=4096)
def _format_number(number: float) -> str:
    if abs(number) < 1e-9:
        return "0"
    for numerator in range(1, 9):
        for denominator in (1, 2, 3, 4, 6, 8):
            candidate = numerator * math.pi / denominator
            for sign in (1, -1):
                if abs(number - sign * candidate) < 1e-6:
                    prefix = "-" if sign < 0 else ""
                    head = "pi" if numerator == 1 else f"{numerator}pi"
                    return f"{prefix}{head}" if denominator == 1 else f"{prefix}{head}/{denominator}"
    return f"{number:.4g}"


def _node_label(name: str, qubits: List[int], params: List[Any]) -> str:
    return _label_from(name, qubits, [_format_param(p) for p in params[:3]])


def _label_from(name: str, qubits: List[int], formatted: List[str]) -> str:
    """_node_label from parameters already formatted (only the first three are shown)."""
    label = name
    if formatted:
        label += "(" + ", ".join(formatted[:3]) + ")"
    if qubits:
        label += " q" + ",".join(str(q) for q in qubits)
    return label


def _short_label(name: str, qubits: List[int]) -> str:
    """The text drawn inside a node box: no parameters, and never long enough
    to stretch the layout out of usable range. ``qslicer.viz.model.short_label``
    is the same for the desktop viewer."""
    label = name if len(name) <= 12 else name[:11] + "…"
    if qubits:
        wires = "q" + ",".join(str(q) for q in qubits)
        if len(wires) > 10:
            wires = "q" + str(qubits[0]) + "+" + str(len(qubits) - 1)
        label += " " + wires
    return label


def _dependency_name(dep: Any) -> str:
    return getattr(dep, "name", str(dep))


def dependency_types() -> List[Dict[str, str]]:
    """The dependency kinds the graph legend can filter on."""
    descriptions = {
        "DATA_QUBIT": "A gate writes a wire that a later gate reads (def-use).",
        "MEASUREMENT": "A later operation is classically conditioned on this measurement.",
        "DATA_CLASSICAL": "Reaching definition on a classical bit.",
        "CONTROL_CLASSICAL": "An if_test decides whether the gates in its block run.",
        "ENTANGLEMENT": "Entangling gate to later operations on the entangled qubits.",
        "PHASE": "Phase-changing gate to later operations that can observe the phase.",
    }
    if _IMPORT_ERROR is not None:
        return [{"name": key, "description": value} for key, value in descriptions.items()]
    return [
        {"name": dep.name, "description": descriptions.get(dep.name, "")}
        for dep in DependencyType
    ]


def rule_profiles() -> List[str]:
    if _IMPORT_ERROR is not None:
        return ["baseline"]
    return sorted(list_rule_profiles().keys())


# Which dependency type each rule emits. The rules do not declare this
# themselves -- the type is baked into each ``build()`` -- so this is a best
# effort by rule name. Anything missing from here is still discovered from the
# edges a rule actually produced, so a new rule shows up either way; adding a
# line is only needed for a rule that can legitimately emit nothing.
_RULE_DEPENDENCIES = {
    "data_qubit": "DATA_QUBIT",
    "measurement": "MEASUREMENT",
    "data_classical": "DATA_CLASSICAL",
    "control_classical": "CONTROL_CLASSICAL",
    "entanglement": "ENTANGLEMENT",
    "phase": "PHASE",
}


def _offered_dependencies(rules: Any, edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The dependency types this analysis could have produced.

    Only these are offered as Follow chips, so a profile that does not run the
    phase rule does not offer to follow phase edges. The count says how many
    edges each one actually contributed: zero means the rule ran and found
    nothing, which is worth seeing rather than hiding.
    """
    descriptions = {item["name"]: item["description"] for item in dependency_types()}

    counts: Dict[str, int] = {}
    rules_by_dep: Dict[str, List[str]] = {}
    for edge in edges:
        dependency = edge["dependency"]
        counts[dependency] = counts.get(dependency, 0) + 1
        emitter = edge.get("rule") or ""
        if emitter and emitter not in rules_by_dep.setdefault(dependency, []):
            rules_by_dep[dependency].append(emitter)

    offered: Dict[str, List[str]] = {}
    for rule in rules:
        name = getattr(rule, "name", "")
        dependency = _RULE_DEPENDENCIES.get(name)
        if dependency:
            offered.setdefault(dependency, [])
            if name not in offered[dependency]:
                offered[dependency].append(name)

    # A rule this map has not heard of still earns a chip for whatever it emitted.
    for dependency, emitters in rules_by_dep.items():
        for emitter in emitters:
            offered.setdefault(dependency, [])
            if emitter not in offered[dependency]:
                offered[dependency].append(emitter)

    # Enum declaration order puts DATA_QUBIT last, which is backwards for a
    # reader: it is the rule that does most of the work. Order the chips the
    # way the info page introduces them instead.
    order = [
        "DATA_QUBIT", "MEASUREMENT", "DATA_CLASSICAL",
        "CONTROL_CLASSICAL", "ENTANGLEMENT", "PHASE",
    ]
    return [
        {
            "name": dependency,
            "description": descriptions.get(dependency, ""),
            "rules": offered[dependency],
            "count": counts.get(dependency, 0),
        }
        for dependency in sorted(offered, key=lambda d: order.index(d) if d in order else 99)
    ]


def _looks_like_qasm(source: str) -> bool:
    """True if the first meaningful line is an OPENQASM header."""
    for line in source.splitlines()[:20]:
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        return stripped.upper().startswith("OPENQASM")
    return False


def _note_interpreter_mismatch(error: Dict[str, Any], source: str, kind: str) -> None:
    """Add a line to an error when the text looks like the other language.

    Nothing switches interpreter on its own -- that is the point of the
    dropdown -- but a Python syntax error on an OpenQASM file deserves a more
    useful explanation than "invalid syntax".
    """
    looks_qasm = _looks_like_qasm(source)
    if kind not in QASM_KINDS and looks_qasm:
        error["hint"] = ("This looks like OpenQASM. Set Interpreter to OpenQASM 2/3 and "
                         "analyze again. " + error.get("hint", ""))
    elif kind in QASM_KINDS and not looks_qasm and source.strip():
        error["hint"] = ("This does not start with an OPENQASM header. If it is a Qiskit "
                         "program, set Interpreter to Qiskit (Python). " + error.get("hint", ""))


# --------------------------------------------------------------------------
# Main entry points
# --------------------------------------------------------------------------

def analyze_source(
    source: str,
    filename: str = "",
    profile: str = "baseline",
    language: Optional[str] = None,
    lint_rules: Optional[List[str]] = None,
    bindings: Optional[List[Dict[str, Any]]] = None,
    circuit: int = 0,
) -> Dict[str, Any]:
    """Lint, parse, build the QDG, and describe everything as JSON-safe data.

    ``language`` names the front end: ``"qiskit-static"``, ``"qasm-static"`` or
    ``"qasm"`` (see INTERPRETERS and _ALIASES; ``"qiskit"`` is refused). The page
    always passes one; without it the filename extension decides.
    ``lint_rules`` are the rule ids to lint with (see ``qslicer.lint.registry``);
    None means the default selection.

    ``bindings`` are values the user picked for things only a run could
    decide, as ``{"name", "line", "value"}`` with ``value`` a Python literal
    (see parse_bindings); a parse that stops on such a value says which in
    ``error.needs``. ``circuit`` picks one of the ``circuits`` a Python file
    builds; 0, the default, is the main one.

    Always returns a dict with an ``ok`` flag. Lint findings are returned even
    when the run fails, because they are usually what explains the failure.
    """
    language_error = unsupported_language(language)
    if language_error is not None:
        return {"ok": False, "diagnostics": [], "error": language_error}

    kind = source_kind(filename, language)
    filename = filename or default_filename(kind)

    try:
        parsed_bindings = parse_bindings(bindings)
    except ValueError as exc:
        parsed_bindings = None
        binding_error = str(exc)

    # The file is interpreted once, on first use: by a linter that reads the
    # circuit, or below. Both then see the same circuit -- the one picked, with
    # the user's values -- so a finding's node ids are the graph's.
    parsed: Dict[str, Any] = {}

    def parse_once() -> Tuple[Any, str, Optional["_ParseFailure"]]:
        if not parsed:
            try:
                program, captured = _parse_source(source, filename, kind, parsed_bindings, circuit)
                parsed["value"] = (program, captured, None)
            except _ParseFailure as failure:
                parsed["value"] = (None, failure.stdout, failure)
        return parsed["value"]

    def parse_for_linters() -> Tuple[Any, Optional[Exception]]:
        program, _, failure = parse_once()
        return program, (failure.cause or failure) if failure is not None else None

    # Likewise the dependence graph: built once, on first use, by the dependence
    # checks or below, with the rules of the profile asked for.
    rules: Any = None
    known_profile = True
    if _IMPORT_ERROR is None:
        try:
            rules = get_rule_profile(profile)
        except ValueError:
            rules = get_rule_profile("baseline")
            known_profile = False
    graphs: Dict[str, Any] = {}

    def qdg_once() -> Any:
        if "value" not in graphs:
            program, _, failure = parse_once()
            graphs["value"] = QDG(program, rules=rules) if failure is None else None
        return graphs["value"]

    selection = linters.normalize_selection(lint_rules)
    context = None
    # The static routes give the linters the circuit shown here (and its graph);
    # the qiskit.qasm3 route has no source locations, so its linters read the
    # file themselves.
    if kind in ("python-static", "qasm-static") and parsed_bindings is not None \
            and _IMPORT_ERROR is None:
        context = linters.LintContext(source, filename, kind, profile, parse=parse_for_linters,
                                      qdg=qdg_once)
    diagnostics = linters.run(source, filename, kind, selection, profile, context=context)
    # With QS000 deselected, a syntax error goes on to the parser, which
    # reports it in its own words.
    syntax = next((d for d in diagnostics if d.get("code") == "QS000"), None)
    # The quick checks read a Python AST, so they have nothing to say about
    # OpenQASM. Rather than report a syntax error on a perfectly good .qasm
    # file, say plainly that they did not run.
    if kind in QASM_KINDS and any(rule.startswith("quick/") for rule in selection):
        diagnostics = [{
            "code": "QS100",
            "severity": "info",
            "line": 0,
            "column": 0,
            "message": "Static checks skipped: they only read Python.",
            "hint": "OpenQASM is parsed rather than executed, so the checks that exist to catch "
                    "mistakes before execution have nothing to guard here. Errors in the QASM "
                    "itself are reported by the parser.",
        }] + diagnostics

    result: Dict[str, Any] = {
        "ok": False,
        "filename": filename,
        "profile": profile,
        "language": kind,
        "lint_rules": selection,
        "diagnostics": diagnostics,
        "stdout": "",
    }

    if _IMPORT_ERROR is not None:
        result["error"] = {
            "kind": "backend_unavailable",
            "message": "The analysis backend could not be imported.",
            "detail": _IMPORT_ERROR,
            "hint": "Install the project requirements: pip install -r requirements.txt",
        }
        return result

    if syntax is not None:
        result["error"] = {
            "kind": "syntax_error",
            "message": syntax["message"],
            "detail": f"line {syntax['line']}, column {syntax['column']}",
            "hint": syntax["hint"],
            "line": syntax["line"],
        }
        _note_interpreter_mismatch(result["error"], source, kind)
        return result

    if parsed_bindings is None:
        result["error"] = {
            "kind": "invalid_binding",
            "message": "One of the values you picked is not a Python literal.",
            "detail": binding_error,
            "hint": "Use a number, a string in quotes, True/False/None, or a list or tuple of those.",
            "line": 0,
        }
        return result

    program, captured, failure = parse_once()
    if failure is not None:
        result["error"] = failure.payload
        _note_interpreter_mismatch(result["error"], source, kind)
        result["stdout"] = failure.stdout
        return result

    result["stdout"] = captured
    # The circuits a Python file builds, for the page to switch between.
    result["circuits"] = getattr(program, "static_circuits", [])
    result["circuit"] = getattr(program, "static_circuit_index", 0)
    result["bindings"] = bindings or []
    notes = getattr(program, "static_notes", None)
    if notes:
        result["diagnostics"] = sorted(diagnostics + _static_diagnostics(notes),
                                       key=lambda d: d["line"])

    if len(program) == 0:
        result["error"] = {
            "kind": "empty_program",
            "message": "The circuit was found but contains no instructions.",
            "detail": f"{program!r}",
            "hint": "Add at least one gate before slicing.",
        }
        return result

    if not known_profile:
        result["profile"] = "baseline"

    qdg = qdg_once()

    # The front end is part of the key: the two OpenQASM routes must not share an entry.
    token = sha256(f"{filename}\x00{profile}\x00{kind}\x00{result['circuit']}\x00"
                   f"{sorted(parsed_bindings.items())!r}\x00{source}".encode("utf-8")).hexdigest()[:32]
    _cache_put(token, {"qdg": qdg, "program": program})

    result.update({
        "ok": True,
        "token": token,
        "program": {
            "num_qubits": program.num_qubits,
            "num_clbits": program.num_clbits,
            "num_instructions": len(program),
        },
        "route": _ROUTES[kind].format(timeout=PARSE_TIMEOUT),
        "nodes": _describe_nodes(program),
        "edges": _describe_edges(qdg),
        "rules": [rule.name for rule in rules],
    })
    # Each gate's reference card once, rather than a copy on every node.
    result["gates"] = _describe_gates(result["nodes"])
    result["stats"] = _describe_stats(result)
    # The Follow chips are built from this, so a rule set that never emits
    # phase edges never offers to follow them.
    result["dependency_types"] = _offered_dependencies(rules, result["edges"])
    result["insights"] = _describe_insights(program)
    attach_lint_nodes(result["diagnostics"], result["nodes"])
    return result


def _describe_insights(program: Any) -> Dict[str, Any]:
    """What the insight passes found (``qslicer.insights.summary.summarize``), with
    string keys as JSON has them: each qubit's measurements, mirrored gates,
    kickback, gates with no effect, predetermined and possibly correlated
    measurements, and which if_test each guarded node sits in. One of these
    failing must not cost the user the graph, so it degrades to nothing found."""
    try:
        from qslicer.insights.summary import summarize
        found = summarize(program)
    except Exception:       # noqa: BLE001 - labels are extras; the graph still stands
        return {"measurements": {}, "mirrors": [], "kickback": {}, "no_effect": {},
                "predetermined": {}, "correlations": {}, "guards": {}}
    return {key: ({str(k): v for k, v in value.items()} if isinstance(value, dict) else value)
            for key, value in found.items()}


def attach_lint_nodes(diagnostics: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> None:
    """Give each diagnostic ``nodes``: the graph nodes it is about, for the graph to mark.

    ``qslicer.viz.model.attach_lint_nodes`` applies the same rule for the desktop viewer.

    A linter that reads the rebuilt circuit names its instructions, whose
    indices are the node ids; those are kept when one of them really comes
    from the diagnostic's line (the linters parse the source themselves, so
    this guards against a different reading). Otherwise the diagnostic's
    ``lines`` (or its ``line``) pick every node whose source span covers them,
    as clicking a line number in the editor does. A diagnostic with no line
    (an empty file, a skipped linter) gets no nodes.
    """
    def covers(node: Dict[str, Any], line: int) -> bool:
        source = node.get("source") or {}
        return bool(source) and source["line"] <= line <= source.get("end_line", source["line"])

    # (id, first line, last line) of every node with a source span, made once,
    # on first need: a large circuit has thousands of nodes to scan per finding.
    spans: Optional[List[Tuple[int, int, int]]] = None

    def covering(lines: List[int]) -> List[int]:
        nonlocal spans
        if not lines:
            return []
        if spans is None:
            spans = [(node["id"], source["line"], source.get("end_line", source["line"]))
                     for node in nodes for source in [node.get("source")] if source]
        if len(lines) == 1:
            line = lines[0]
            return [idx for idx, first, last in spans if first <= line <= last]
        return [idx for idx, first, last in spans
                if any(first <= line <= last for line in lines)]

    for diagnostic in diagnostics:
        named = [n for n in diagnostic.get("nodes", []) if isinstance(n, int) and 0 <= n < len(nodes)]
        if named and any(covers(nodes[n], diagnostic.get("line") or 0) for n in named):
            diagnostic["nodes"] = named
            continue
        lines = diagnostic.get("lines") or ([diagnostic["line"]] if diagnostic.get("line") else [])
        diagnostic["nodes"] = covering(lines)


class _ParseFailure(Exception):
    def __init__(self, payload: Dict[str, Any], stdout: str = "",
                 cause: Optional[Exception] = None) -> None:
        super().__init__(payload.get("message", "parse failure"))
        self.payload = payload
        self.stdout = stdout
        # The front end's own exception, which the quick checks read (QS014).
        self.cause = cause


# The front ends the Interpreter dropdown offers. ``id`` is what the API takes
# as ``language``; ``suffix`` is what a pasted circuit is called when it has no
# file of its own. Both read the file without running it. The executing Qiskit
# route ("qiskit") and the qiskit.qasm3 route ("qasm") are no longer offered;
# API callers can still name them.
INTERPRETERS = [
    {
        "id": "qiskit-static",
        "label": "Qiskit (Python)",
        "suffix": ".py",
        "note": "Read without executing: the parser interprets the file itself to rebuild "
                "the circuit.",
    },
    {
        "id": "qasm-static",
        "label": "OpenQASM 2/3",
        "suffix": ".qasm",
        "note": "Parsed and interpreted without Qiskit; every instruction keeps its source line.",
    },
]

# The kinds of front end that read OpenQASM rather than Python.
QASM_KINDS = ("qasm", "qasm-static")

_ALIASES = {
    "qiskit-static": "python-static", "static": "python-static",
    "qasm": "qasm", "openqasm": "qasm", "qasm3": "qasm",
    "qasm-static": "qasm-static", "openqasm-static": "qasm-static",
}


# Names of the removed route that executed submitted Python. Refused outright,
# never mapped to another route.
_EXECUTING = frozenset({"qiskit", "python", "py"})


def unsupported_language(language: Any) -> Optional[Dict[str, Any]]:
    """The validation error for an interpreter name nothing handles, else None.

    None and the empty string mean "not chosen", which falls back to the
    filename extension.
    """
    if language is None or language == "":
        return None
    if isinstance(language, str) and language.strip().lower() in _EXECUTING:
        return {
            "kind": "execution_disabled",
            "stage": "validation",
            "message": "Executing submitted Python is disabled on this server.",
            "detail": f"The {language.strip()!r} interpreter ran the file to reach its "
                      "circuit, so it could run any code the file contains.",
            "hint": "Use language \"qiskit-static\", which reads the file without running it.",
            "line": 0,
        }
    if isinstance(language, str) and language.strip().lower() in _ALIASES:
        return None
    supported = ", ".join(option["id"] for option in INTERPRETERS)
    return {
        "kind": "unsupported_language",
        "stage": "validation",
        "message": f"{language!r} is not a supported interpreter.",
        "detail": f"Supported interpreters: {supported}.",
        "hint": "Pick one of the options in the Interpreter dropdown.",
        "line": 0,
    }


def interpreters() -> List[Dict[str, Any]]:
    """The dropdown's options, with the first marked as the default."""
    return [
        dict(option, default=(index == 0))
        for index, option in enumerate(INTERPRETERS)
    ]


def source_kind(filename: str = "", language: Optional[str] = None) -> str:
    """Which front end a submission goes to: ``"qasm"``, ``"qasm-static"`` or
    ``"python-static"``.

    ``language`` is the caller's explicit choice and wins outright -- the page
    always sends one, taken from the Interpreter dropdown. The extension is
    only a fallback for API callers that leave it out, and picks a static route;
    nothing here reads the file contents to guess.
    """
    if language:
        resolved = _ALIASES.get(str(language).strip().lower())
        if resolved:
            return resolved
    return ("qasm-static" if os.path.splitext(filename)[1].lower() in QASM_SUFFIXES
            else "python-static")


def default_filename(kind: str) -> str:
    """What to call a pasted circuit that has no file of its own."""
    return "pasted_circuit.qasm" if kind in QASM_KINDS else "pasted_circuit.py"


def parse_bindings(bindings: Any) -> Dict[Tuple[int, str], Any]:
    """``[{"name", "line", "value"}, ...]`` as the parser takes them: ``{(line, name): value}``.

    ``value`` is text the user typed, read as a Python literal (never run).
    Raises ValueError, naming the entry, for anything else.
    """
    out: Dict[Tuple[int, str], Any] = {}
    for entry in bindings or []:
        if not isinstance(entry, dict):
            raise ValueError("each binding must be an object with name, line and value")
        name, line, text = entry.get("name"), entry.get("line"), entry.get("value")
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"{name!r} is not a variable name")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError(f"the line of {name} must be a positive integer")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"no value given for {name}")
        if len(text) > 10_000:
            raise ValueError(f"the value of {name} is too long")
        try:
            out[(line, name)] = ast.literal_eval(text.strip())
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            raise ValueError(f"{name} = {text.strip()} is not a Python literal") from None
    return out


def _parse_source(source: str, filename: str, kind: str,
                  bindings: Optional[Dict[Tuple[int, str], Any]] = None,
                  circuit: int = 0) -> Tuple[Any, str]:
    """Turn submitted text into a QuantumProgram, by the route the caller chose."""
    if kind == "qasm":
        return _parse_qasm_source(source, filename)
    if kind == "qasm-static":
        return _parse_qasm_static_source(source)
    if kind == "python-static":
        return _parse_static_source(source, filename, bindings or {}, circuit)
    # There is deliberately no route that executes the submission.
    raise ValueError(f"no front end for {kind!r}")


# How each front end reached the circuit, as shown on the page.
_ROUTES = {
    "qasm": "OpenQASM, parsed without executing",
    "qasm-static": "OpenQASM, interpreted statically with source locations",
    "python-static": "Python, interpreted statically without executing",
}


# --------------------------------------------------------------------------
# OpenQASM: parsed, never executed
# --------------------------------------------------------------------------

def _parse_qasm_source(source: str, filename: str) -> Tuple[Any, str]:
    """Parse OpenQASM 3 text through the project's OpenQASM front end.

    ``qiskit.qasm3.load`` reads the grammar and builds a circuit; nothing in the
    submitted text is executed, so this route needs no subprocess and no
    timeout. It is the safe way to accept a circuit from someone else.
    """
    workdir = tempfile.mkdtemp(prefix="qslice_qasm_")
    safe_name = os.path.basename(filename) or "circuit.qasm"
    if not safe_name.endswith(QASM_SUFFIXES):
        safe_name = os.path.splitext(safe_name)[0] + ".qasm"
    path = os.path.join(workdir, safe_name)
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)

        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                program = OpenQASMParser().parse_file(path)
        except Exception as exc:
            raise _ParseFailure({
                "kind": "qasm_error",
                "message": "That OpenQASM file could not be parsed.",
                "detail": f"{type(exc).__name__}: {exc}".rstrip(": "),
                "hint": "The parser expects OpenQASM 3. A version 2 file usually needs its "
                        "header changed to OPENQASM 3.0 and its includes updated; "
                        "qiskit.qasm3.dumps(circuit) always emits something this accepts.",
            }, buffer.getvalue())
        return program, buffer.getvalue()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Python, static: interpreted, never executed
# --------------------------------------------------------------------------

# Parsed Python files, so switching between the circuits of one file does not
# interpret it again. Keyed by the source, its filename and the user's values.
_PARSES: "OrderedDict[str, Any]" = OrderedDict()
_PARSE_LIMIT = 6


def _parse_static_source(source: str, filename: str,
                         bindings: Optional[Dict[Tuple[int, str], Any]] = None,
                         circuit: int = 0) -> Tuple[Any, str]:
    """Rebuild the circuit with StaticQiskitParser, which never runs the file.

    Nothing is executed, so there is no worker process; the parser bounds its
    own work, in steps and in PARSE_TIMEOUT seconds. What it had to skip or approximate is attached
    to the program as ``static_notes``, which analyze_source reports as
    warning diagnostics. The circuits the file builds go on ``static_circuits``
    and the one this program is, on ``static_circuit_index``.
    """
    bindings = bindings or {}
    name = os.path.basename(filename) or "pasted_circuit.py"
    key = sha256(f"{name}\x00{sorted(bindings.items())!r}\x00{source}".encode("utf-8")).hexdigest()
    parser = _PARSES.get(key)
    if parser is not None:
        _PARSES.move_to_end(key)
        program = parser.select(circuit)
    else:
        parser = StaticQiskitParser(filename=name, time_limit=PARSE_TIMEOUT, bindings=bindings,
                                    circuit_index=circuit)
        parser.source = source
        try:
            program = parser.parse()
        except StaticParseError as exc:
            raise _ParseFailure(_static_error_payload(exc), cause=exc)
        except ValueError as exc:
            no_circuit = "No QuantumCircuit" in str(exc)
            hint = _HINTS["no_circuit"] if no_circuit else ""
            if no_circuit and bindings:
                hint = ("With the values you picked, the program never gets as far as building "
                        "a circuit (it may take a classical shortcut). Try a different value.")
            raise _ParseFailure({
                "kind": "no_circuit" if no_circuit else "static_error",
                "message": ("The program never builds a QuantumCircuit."
                            if no_circuit else "The static parser could not work out the circuit."),
                "detail": str(exc),
                "hint": hint,
            }, cause=exc)
        _PARSES[key] = parser
        while len(_PARSES) > _PARSE_LIMIT:
            _PARSES.popitem(last=False)
    program.static_notes = list(parser.notes)
    program.static_circuits = [dict(info) for info in parser.circuits]
    # Read from the request, not the shared parser, which another request may re-select.
    program.static_circuit_index = circuit if 0 <= circuit < len(parser.circuits) else 0
    return program, ""


def _static_error_payload(exc: Any) -> Dict[str, Any]:
    """The error for a StaticParseError; a ``needs_value`` one when the user can supply it."""
    needs = getattr(exc, "needs", None)
    if needs:
        what = ("the command-line argument" if needs["kind"] == "argument" else "the variable")
        return {
            "kind": "needs_value",
            "message": f"Pick a value for {what} `{needs['name']}` (line {needs['line']}).",
            # The error points at the line to change; where the parse stopped is detail.
            "detail": (f"line {exc.lineno}: {exc.message}" if exc.lineno else exc.message),
            "hint": f"`{needs['expression']}` only has a value when the program runs, and the "
                    "circuit depends on it. Whatever you pick is used where it is assigned, "
                    "as if a run had produced it.",
            "line": needs["line"],
            "needs": dict(needs),
        }
    return {
        "kind": "static_error",
        "message": "The static parser could not work out the circuit.",
        "detail": exc.message,
        "hint": "The file is read without running it, so the parser stops at anything "
                "only a real run could decide. Make that value a literal, or build the "
                "circuit without it.",
        "line": exc.lineno or 0,
    }


def _static_diagnostics(notes: List[Tuple[Any, str]]) -> List[Dict[str, Any]]:
    """The static parser's warnings, in the lint diagnostics format."""
    return [{
        "code": "QS200",
        "severity": "warning",
        "line": lineno or 0,
        "column": 0,
        "message": message[:1].upper() + message[1:] + ".",
        "hint": "The static parser skipped or approximated this rather than guess.",
    } for lineno, message in notes]


def _parse_qasm_static_source(source: str) -> Tuple[Any, str]:
    """Interpret OpenQASM 2/3 with StaticOpenQASMParser, which keeps source locations."""
    parser = StaticOpenQASMParser(time_limit=PARSE_TIMEOUT)
    parser.source = source
    try:
        program = parser.parse()
    except StaticParseError as exc:
        raise _ParseFailure({
            "kind": "qasm_error",
            "message": "That OpenQASM file could not be parsed.",
            "detail": exc.message,
            "hint": "The static OpenQASM route reads OpenQASM 2 and 3 itself; anything that "
                    "needs a run-time value in an unsupported shape is refused with its line.",
            "line": exc.lineno or 0,
        })
    except ValueError as exc:
        raise _ParseFailure({
            "kind": "backend_unavailable",
            "message": "The static OpenQASM parser is not available.",
            "detail": str(exc),
            "hint": _HINTS["backend_unavailable"],
        })
    program.static_notes = list(parser.notes)
    return program, ""


_HINTS = {
    "no_circuit": "Build the circuit at module level, or call the function that builds it "
                  "(from main() or an `if __name__ == \"__main__\":` block, which the parser "
                  "follows).",
    "backend_unavailable": "Install the project requirements: pip install -r requirements.txt",
}


def _describe_nodes(program: Any) -> List[Dict[str, Any]]:
    """One JSON-safe dict per instruction. The gate's reference card is not on
    the node: ``_describe_gates`` gives each name's once, as ``gates``."""
    nodes = []
    categories: Dict[str, str] = {}
    for idx in range(len(program)):
        instruction = program[idx]
        name = instruction.name
        qubits = list(instruction.qubits)
        clbits = list(instruction.clbits)
        category = categories.get(name)
        if category is None:
            category = categories[name] = gates.describe(name)["category"]
        params = [_format_param(p) for p in instruction.params]
        nodes.append({
            "id": idx,
            "name": name,
            "label": _label_from(name, qubits, params),
            "short": _short_label(name, qubits),
            "qubits": qubits,
            "clbits": clbits,
            "params": params,
            "condition_clbits": list(instruction.metadata.get("condition_clbits", []) or []),
            "lane": (sum(qubits) / len(qubits)) if qubits else 0.0,
            "category": category,
            "is_measurement": category == gates.MEASURE,
            **_describe_location(getattr(instruction, "location", None)),
        })
    return nodes


def _describe_gates(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``{name: gates.describe(name)}`` for every gate name the nodes use."""
    described: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        if node["name"] not in described:
            described[node["name"]] = gates.describe(node["name"])
    return described


def _describe_location(location: Any) -> Dict[str, Any]:
    """Where a node came from in the source (FR-07): ``line`` plus the full span.

    Both are None when the front end could not tell, as for the qiskit.qasm3
    route.
    """
    if location is None:
        return {"line": None, "source": None}
    return {"line": location.line, "source": location.to_dict()}


def _describe_edges(qdg: Any) -> List[Dict[str, Any]]:
    # In the order graph.edges(keys=True) gives, read straight from the
    # adjacency (networkx's _adj, when there is one) and with each dependency's
    # name worked out once: a third less time for Shor's 24,000 edges.
    names: Dict[Any, str] = {}
    adjacency = getattr(qdg.graph, "_adj", None) or qdg.graph.adj
    edges = []
    for source, targets in adjacency.items():
        for target, keyed in targets.items():
            for key, data in keyed.items():
                dependency = data.get("dependency")
                try:
                    name = names[dependency]
                except KeyError:
                    name = names[dependency] = _dependency_name(dependency)
                edges.append({
                    "source": source,
                    "target": target,
                    "key": key,
                    "dependency": name,
                    "rule": data.get("rule", ""),
                })
    return edges


def _describe_stats(result: Dict[str, Any]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for edge in result["edges"]:
        counts[edge["dependency"]] = counts.get(edge["dependency"], 0) + 1
    gate_counts: Dict[str, int] = {}
    for node in result["nodes"]:
        gate_counts[node["name"]] = gate_counts.get(node["name"], 0) + 1
    return {
        "edges_by_dependency": counts,
        "gate_counts": dict(sorted(gate_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "entangling_gates": sum(1 for n in result["nodes"]
                                if result["gates"][n["name"]]["entangling"]),
        "measurements": sum(1 for n in result["nodes"] if n["is_measurement"]),
    }


def _slice_error(kind: str, message: str, hint: str = "", **extra: Any) -> Dict[str, Any]:
    """A failed slice, shaped like an analysis error so the page can say which stage failed."""
    result = {
        "ok": False,
        "error": {"kind": kind, "stage": "slicing", "message": message,
                  "detail": "", "hint": hint, "line": 0},
    }
    result.update(extra)
    return result


MAX_SLICE_NODES = 16
CHOP_EMPTY_REASON = "This operation cannot affect that measurement."


def _is_int(value: Any) -> bool:
    # bool is a subclass of int, so JSON true/false would otherwise pass.
    return isinstance(value, int) and not isinstance(value, bool)


def slice_program(
    token: str,
    node_idx: Optional[int] = None,
    direction: str = "backward",
    dep_types: Optional[List[str]] = None,
    target_idx: Optional[int] = None,
    node_indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Run the real slicer over a cached QDG.

    ``dep_types`` is a list of DependencyType names; None or empty means follow
    every dependency, matching SlicingCriterion's default.

    ``direction`` is "backward", "forward" or "chop". A chop runs from
    ``node_idx`` (the operation) to ``target_idx`` (e.g. a measurement): the
    nodes on some dependence path between them. ``node_indices`` (backward
    only, 1 to MAX_SLICE_NODES of them) slices from several nodes at once and
    takes precedence over ``node_idx``.
    """
    if _IMPORT_ERROR is not None:
        return _slice_error("backend_unavailable", "The analysis backend is unavailable.")

    if node_indices is not None:
        if (not isinstance(node_indices, list) or not node_indices
                or len(node_indices) > MAX_SLICE_NODES
                or not all(_is_int(i) for i in node_indices)):
            return _slice_error(
                "invalid_request",
                f"node_indices must be a list of 1 to {MAX_SLICE_NODES} integers.")
        if direction != "backward":
            return _slice_error("invalid_request",
                                "node_indices can only be used with a backward slice.")
        node_indices = list(dict.fromkeys(node_indices))
        node_idx = node_indices[0]
    if not _is_int(node_idx):
        return _slice_error("invalid_request", "node_idx must be an integer.")
    if direction == "chop" and not _is_int(target_idx):
        return _slice_error("invalid_request", "A chop needs an integer target_idx.")
    if dep_types is not None and (
        not isinstance(dep_types, list) or not all(isinstance(n, str) for n in dep_types)
    ):
        return _slice_error("invalid_request", "dep_types must be a list of dependency names.")

    entry = _cache_get(token)
    if entry is None:
        return _slice_error(
            "stale_token",
            "This analysis is no longer cached on the server.",
            hint="Press Analyze again.",
            stale=True,
        )

    qdg = entry["qdg"]
    program = entry["program"]
    for idx in (node_indices or [node_idx]) + ([target_idx] if direction == "chop" else []):
        if not 0 <= idx < len(program):
            return _slice_error("out_of_range", f"Instruction {idx} is outside this program.")

    selected = None
    if dep_types:
        by_name = {dep.name: dep for dep in DependencyType}
        selected = {by_name[name] for name in dep_types if name in by_name}
        if not selected:
            selected = None

    extra: Dict[str, Any] = {}
    if node_indices is not None:
        slices = QuantumSlicer.backward_slices(qdg, node_indices, selected)
        indices = set().union(*slices.values())
        extra = {
            "node_indices": node_indices,
            "slices": [{"node_idx": i, "slice": sorted(slices[i]), "size": len(slices[i])}
                       for i in node_indices],
        }
    elif direction == "chop":
        indices = QuantumSlicer.chop(qdg, node_idx, target_idx, selected)
        extra = {"target_idx": target_idx}
        if not indices:
            extra["empty_reason"] = CHOP_EMPTY_REASON
    else:
        criterion = SlicingCriterion(node_idx=node_idx, dep_types=selected)
        if direction == "forward":
            indices = QuantumSlicer.forward_slice(qdg, criterion)
        else:
            indices = QuantumSlicer.backward_slice(qdg, criterion)

    result = {
        "ok": True,
        "direction": direction,
        "node_idx": node_idx,
        "dep_types": sorted(dep.name for dep in selected) if selected else None,
        "slice": sorted(indices),
        "size": len(indices),
        "total": len(program),
    }
    result.update(extra)
    return result
