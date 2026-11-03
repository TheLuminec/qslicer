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

import io
import os
import shutil
import tempfile
from collections import OrderedDict
from contextlib import redirect_stderr, redirect_stdout
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
# criterion never re-executes the submitted program.
# --------------------------------------------------------------------------

_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_CACHE_LIMIT = 24


def _cache_put(token: str, payload: Dict[str, Any]) -> None:
    _CACHE[token] = payload
    _CACHE.move_to_end(token)
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)


def _cache_get(token: str) -> Optional[Dict[str, Any]]:
    entry = _CACHE.get(token)
    if entry is not None:
        _CACHE.move_to_end(token)
    return entry


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

    import math

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
    label = name
    if params:
        label += "(" + ", ".join(_format_param(p) for p in params[:3]) + ")"
    if qubits:
        label += " q" + ",".join(str(q) for q in qubits)
    return label


def _short_label(name: str, qubits: List[int]) -> str:
    """The text drawn inside a node box: no parameters, and never long enough
    to stretch the layout out of usable range."""
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
        "CONTROL_CLASSICAL": "Control dependence. Rule is a placeholder today.",
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
) -> Dict[str, Any]:
    """Lint, parse, build the QDG, and describe everything as JSON-safe data.

    ``language`` names the front end: ``"qiskit"``, ``"qiskit-static"``, ``"qasm"``
    or ``"qasm-static"`` (see INTERPRETERS). The page
    always passes one; without it the filename extension decides.
    ``lint_rules`` are the rule ids to lint with (see ``linters.py``); None
    means the default selection.

    Always returns a dict with an ``ok`` flag. Lint findings are returned even
    when the run fails, because they are usually what explains the failure.
    """
    language_error = unsupported_language(language)
    if language_error is not None:
        return {"ok": False, "diagnostics": [], "error": language_error}

    kind = source_kind(filename, language)
    filename = filename or default_filename(kind)

    selection = linters.normalize_selection(lint_rules)
    diagnostics = linters.run(source, filename, kind, selection, profile)
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

    try:
        program, captured = _parse_source(source, filename, kind)
    except _ParseFailure as failure:
        result["error"] = failure.payload
        _note_interpreter_mismatch(result["error"], source, kind)
        result["stdout"] = failure.stdout
        return result

    result["stdout"] = captured
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

    try:
        rules = get_rule_profile(profile)
    except ValueError:
        rules = get_rule_profile("baseline")
        result["profile"] = "baseline"

    qdg = QDG(program, rules=rules)

    # The front end is part of the key: the two Python routes must not share an entry.
    token = sha256(f"{filename}\x00{profile}\x00{kind}\x00{source}".encode("utf-8")).hexdigest()[:32]
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
    result["stats"] = _describe_stats(result)
    # The Follow chips are built from this, so a rule set that never emits
    # phase edges never offers to follow them.
    result["dependency_types"] = _offered_dependencies(rules, result["edges"])
    attach_lint_nodes(result["diagnostics"], result["nodes"])
    return result


def attach_lint_nodes(diagnostics: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> None:
    """Give each diagnostic ``nodes``: the graph nodes it is about, for the graph to mark.

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

    for diagnostic in diagnostics:
        named = [n for n in diagnostic.get("nodes", []) if isinstance(n, int) and 0 <= n < len(nodes)]
        if named and any(covers(nodes[n], diagnostic.get("line") or 0) for n in named):
            diagnostic["nodes"] = named
            continue
        lines = diagnostic.get("lines") or ([diagnostic["line"]] if diagnostic.get("line") else [])
        diagnostic["nodes"] = [node["id"] for node in nodes
                               if any(covers(node, line) for line in lines)]


class _ParseFailure(Exception):
    def __init__(self, payload: Dict[str, Any], stdout: str = "") -> None:
        super().__init__(payload.get("message", "parse failure"))
        self.payload = payload
        self.stdout = stdout


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


def _parse_source(source: str, filename: str, kind: str) -> Tuple[Any, str]:
    """Turn submitted text into a QuantumProgram, by the route the caller chose."""
    if kind == "qasm":
        return _parse_qasm_source(source, filename)
    if kind == "qasm-static":
        return _parse_qasm_static_source(source)
    if kind == "python-static":
        return _parse_static_source(source, filename)
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

def _parse_static_source(source: str, filename: str) -> Tuple[Any, str]:
    """Rebuild the circuit with StaticQiskitParser, which never runs the file.

    Nothing is executed, so there is no worker process; the parser bounds its
    own work, in steps and in PARSE_TIMEOUT seconds. What it had to skip or approximate is attached
    to the program as ``static_notes``, which analyze_source reports as
    warning diagnostics.
    """
    parser = StaticQiskitParser(filename=os.path.basename(filename) or "pasted_circuit.py",
                                time_limit=PARSE_TIMEOUT)
    parser.source = source
    try:
        program = parser.parse()
    except StaticParseError as exc:
        raise _ParseFailure({
            "kind": "static_error",
            "message": "The static parser could not work out the circuit.",
            "detail": exc.message,
            "hint": "The file is read without running it, so the parser stops at anything "
                    "only a real run could decide. Make that value a literal, or build the "
                    "circuit without it.",
            "line": exc.lineno or 0,
        })
    except ValueError as exc:
        no_circuit = "No QuantumCircuit" in str(exc)
        raise _ParseFailure({
            "kind": "no_circuit" if no_circuit else "static_error",
            "message": ("No QuantumCircuit was left in the file's module namespace."
                        if no_circuit else "The static parser could not work out the circuit."),
            "detail": str(exc),
            "hint": _HINTS["no_circuit"] if no_circuit else "",
        })
    program.static_notes = list(parser.notes)
    return program, ""


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
    "no_circuit": "Assign the circuit to a module-level variable. A circuit built inside a "
                  "function and never returned into a global is invisible to the parser.",
    "backend_unavailable": "Install the project requirements: pip install -r requirements.txt",
}


def _describe_nodes(program: Any) -> List[Dict[str, Any]]:
    nodes = []
    for idx in range(len(program)):
        instruction = program[idx]
        qubits = list(instruction.qubits)
        clbits = list(instruction.clbits)
        info = gates.describe(instruction.name)
        nodes.append({
            "id": idx,
            "name": instruction.name,
            "label": _node_label(instruction.name, qubits, instruction.params),
            "short": _short_label(instruction.name, qubits),
            "qubits": qubits,
            "clbits": clbits,
            "params": [_format_param(p) for p in instruction.params],
            "condition_clbits": list(instruction.metadata.get("condition_clbits", []) or []),
            "lane": (sum(qubits) / len(qubits)) if qubits else 0.0,
            "gate": info,
            "category": info["category"],
            "is_measurement": info["category"] == gates.MEASURE,
            **_describe_location(getattr(instruction, "location", None)),
        })
    return nodes


def _describe_location(location: Any) -> Dict[str, Any]:
    """Where a node came from in the source (FR-07): ``line`` plus the full span.

    Both are None when the front end could not tell, as for the executing
    Python route and the qiskit.qasm3 one.
    """
    if location is None:
        return {"line": None, "source": None}
    return {"line": location.line, "source": location.to_dict()}


def _describe_edges(qdg: Any) -> List[Dict[str, Any]]:
    edges = []
    for source, target, key, data in qdg.graph.edges(keys=True, data=True):
        edges.append({
            "source": source,
            "target": target,
            "key": key,
            "dependency": _dependency_name(data.get("dependency")),
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
        "entangling_gates": sum(1 for n in result["nodes"] if n["gate"]["entangling"]),
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


def slice_program(
    token: str,
    node_idx: int,
    direction: str = "backward",
    dep_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the real slicer over a cached QDG.

    ``dep_types`` is a list of DependencyType names; None or empty means follow
    every dependency, matching SlicingCriterion's default.
    """
    if _IMPORT_ERROR is not None:
        return _slice_error("backend_unavailable", "The analysis backend is unavailable.")

    if isinstance(node_idx, bool) or not isinstance(node_idx, int):
        return _slice_error("invalid_request", "node_idx must be an integer.")
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
    if not 0 <= node_idx < len(program):
        return _slice_error("out_of_range", f"Instruction {node_idx} is outside this program.")

    selected = None
    if dep_types:
        by_name = {dep.name: dep for dep in DependencyType}
        selected = {by_name[name] for name in dep_types if name in by_name}
        if not selected:
            selected = None

    criterion = SlicingCriterion(node_idx=node_idx, dep_types=selected)
    if direction == "forward":
        indices = QuantumSlicer.forward_slice(qdg, criterion)
    else:
        indices = QuantumSlicer.backward_slice(qdg, criterion)

    return {
        "ok": True,
        "direction": direction,
        "node_idx": node_idx,
        "dep_types": sorted(dep.name for dep in selected) if selected else None,
        "slice": sorted(indices),
        "size": len(indices),
        "total": len(program),
    }
