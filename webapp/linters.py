"""The linters the site can run, and which of their rules a request selected.

Every linter registers itself here with the rules it can report. The slicer
page and the Bugs4Q benchmark both go through ``run`` with the same
*selection* -- a list of rule ids such as ``"quick/QS002"`` -- so a benchmark
grades exactly the rules a user has switched on, across every linter at once.

Built in:

* ``quick`` -- the AST checks in ``lint.py``.

Add-ons are modules in ``webapp/linter_plugins/`` (loaded automatically) or
named in ``QSLICER_LINTER_PLUGINS`` (comma-separated importable modules, for
linters kept outside this repository). An add-on defines ``LINTER``, a
``Linter``, or ``LINTERS``, a list of them; see
``linter_plugins/dependence_checks.py`` for a complete one and
``linter_plugins/lintq.py`` for one that wraps an external tool.
Its ``run`` gets a ``LintContext`` and returns diagnostics in the shape
``lint.py`` uses: code, severity, line, column, message, hint.
"""

import importlib
import os
import pkgutil
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import lint

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

PYTHON_KINDS = ("python", "python-static")
QASM_KINDS = ("qasm", "qasm-static")

# Seconds a linter's static parse may take. The parse never executes the
# program, but a polling loop over values it cannot know can still spin.
STATIC_PARSE_TIME_LIMIT = float(os.environ.get("QSLICER_LINT_PARSE_LIMIT", "5"))


@dataclass(frozen=True)
class Rule:
    code: str
    title: str
    severity: str = "warning"
    description: str = ""


@dataclass
class Linter:
    """One linter: an id, the rules it can report, and the function that runs it.

    ``languages`` are the source kinds it reads (``"python"`` covers both Python
    routes, ``"qasm"`` both OpenQASM ones). ``default`` says whether its rules
    are selected when a request names none. ``available`` is a callable so a
    linter that needs an external tool can say at request time whether it is
    there; it returns ``(ok, reason)``. ``batch``, for a linter that is slow
    to start (an external tool), lints many programs in one go:
    ``[(path, source)] -> {path: findings}``; the benchmark uses it.
    """

    id: str
    label: str
    description: str
    rules: List[Rule]
    run: Callable[["LintContext"], List[Dict[str, Any]]]
    languages: Tuple[str, ...] = ("python",)
    default: bool = True
    available: Callable[[], Tuple[bool, str]] = field(default=lambda: (True, ""))
    batch: Optional[Callable[[List[Tuple[str, str]]], Dict[str, List[Dict[str, Any]]]]] = None

    def reads(self, kind: str) -> bool:
        family = "qasm" if kind in QASM_KINDS else "python"
        return family in self.languages

    def rule_ids(self) -> List[str]:
        return [f"{self.id}/{rule.code}" for rule in self.rules]


class LintContext:
    """What a linter is given: the source, and the static IR on request.

    ``program()`` parses the source without executing it (the static Qiskit
    or OpenQASM front end), at most once per context however many linters ask,
    and returns ``(program, error)`` with exactly one of them set.
    """

    def __init__(self, source: str, filename: str = "", kind: str = "python-static",
                 profile: str = "baseline") -> None:
        self.source = source
        self.filename = filename or ("circuit.qasm" if kind in QASM_KINDS else "circuit.py")
        self.kind = kind
        self.profile = profile
        self._parsed: Optional[Tuple[Any, Optional[str]]] = None

    def program(self) -> Tuple[Any, Optional[str]]:
        if self._parsed is None:
            self._parsed = _static_parse(self.source, self.filename, self.kind)
        return self._parsed


def _static_parse(source: str, filename: str, kind: str) -> Tuple[Any, Optional[str]]:
    try:
        if kind in QASM_KINDS:
            from Parsers.staticopenqasmparser import StaticOpenQASMParser
            parser = StaticOpenQASMParser(time_limit=STATIC_PARSE_TIME_LIMIT)
        else:
            from Parsers.staticqiskitparser import StaticQiskitParser
            parser = StaticQiskitParser(filename=os.path.basename(filename),
                                        time_limit=STATIC_PARSE_TIME_LIMIT)
        parser.source = source
        return parser.parse(), None
    except Exception as exc:  # any front-end failure means "no IR", never a crash
        return None, str(exc) or type(exc).__name__


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_LINTERS: Dict[str, Linter] = {}
_PLUGIN_ERRORS: List[str] = []
_LOADED = False


def register(linter: Linter) -> Linter:
    if "/" in linter.id or not linter.id:
        raise ValueError(f"bad linter id {linter.id!r}")
    _LINTERS[linter.id] = linter
    return linter


def _load_plugins() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    names = []
    try:
        import linter_plugins
        names += [f"linter_plugins.{m.name}" for m in pkgutil.iter_modules(linter_plugins.__path__)
                  if not m.name.startswith("_")]
    except ImportError:
        pass
    names += [n.strip() for n in os.environ.get("QSLICER_LINTER_PLUGINS", "").split(",")
              if n.strip()]
    for name in names:
        try:
            module = importlib.import_module(name)
            found = getattr(module, "LINTERS", None) or [getattr(module, "LINTER", None)]
            if found and all(isinstance(linter, Linter) for linter in found):
                for linter in found:
                    register(linter)
            else:
                _PLUGIN_ERRORS.append(f"{name}: defines no LINTER or LINTERS")
        except Exception as exc:  # a broken add-on must not take the site down
            _PLUGIN_ERRORS.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"WARNING: linter add-on {name} failed to load: {exc}", file=sys.stderr)


def all_linters() -> List[Linter]:
    _load_plugins()
    return list(_LINTERS.values())


def get(linter_id: str) -> Optional[Linter]:
    _load_plugins()
    return _LINTERS.get(linter_id)


def plugin_errors() -> List[str]:
    _load_plugins()
    return list(_PLUGIN_ERRORS)


def catalog() -> List[Dict[str, Any]]:
    """Every linter and its rules, JSON-safe, for the rule pickers."""
    entries = []
    for linter in all_linters():
        ok, reason = linter.available()
        entries.append({
            "id": linter.id,
            "label": linter.label,
            "description": linter.description,
            "languages": list(linter.languages),
            "default": linter.default,
            "batch": linter.batch is not None,
            "available": ok,
            "unavailable_reason": reason,
            "rules": [{"id": f"{linter.id}/{r.code}", "code": r.code, "title": r.title,
                       "severity": r.severity, "description": r.description}
                      for r in linter.rules],
        })
    return entries


def default_selection() -> List[str]:
    return [rule_id for linter in all_linters() if linter.default and linter.available()[0]
            for rule_id in linter.rule_ids()]


def normalize_selection(selection: Optional[Iterable[str]]) -> List[str]:
    """Known rule ids from ``selection``, in catalog order; the default for None.

    A bare linter id (``"quick"``) selects all of its rules. Unknown ids are
    dropped, so a selection saved in a browser survives a rule being removed.
    """
    if selection is None:
        return default_selection()
    wanted = set(selection)
    chosen = []
    for linter in all_linters():
        for rule_id in linter.rule_ids():
            if linter.id in wanted or rule_id in wanted:
                chosen.append(rule_id)
    return chosen


def selection_by_linter(selection: Iterable[str]) -> Dict[str, List[str]]:
    """``{"quick": ["QS002", ...]}`` for a normalized selection."""
    grouped: Dict[str, List[str]] = {}
    for rule_id in selection:
        linter_id, _, code = rule_id.partition("/")
        grouped.setdefault(linter_id, []).append(code)
    return grouped


def crash_finding(linter: Linter, exc: Exception) -> Dict[str, Any]:
    """The one diagnostic a linter that raised reports instead."""
    return {"code": f"{linter.id.upper()}-CRASH", "severity": "error", "line": 0, "column": 0,
            "message": f"{linter.label} crashed: {exc}",
            "hint": "This is a bug in the linter, not in the program."}


def tag(linter: Linter, produced: Iterable[Dict[str, Any]],
        codes: Iterable[str]) -> List[Dict[str, Any]]:
    """The findings among ``produced`` whose code was selected, marked with their linter."""
    wanted = set(codes) | {f"{linter.id.upper()}-CRASH"}
    return [dict(f, tool=linter.id, rule=f"{linter.id}/{f['code']}")
            for f in produced if f.get("code") in wanted]


def run(source: str, filename: str = "", kind: str = "python-static",
        selection: Optional[Iterable[str]] = None, profile: str = "baseline",
        context: Optional[LintContext] = None,
        precomputed: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> List[Dict[str, Any]]:
    """Run the selected rules over ``source``; diagnostics ordered by line.

    Each diagnostic gains ``tool`` (the linter's id) and ``rule`` (its rule id).
    A linter that raises reports one ``error`` diagnostic instead of failing
    the whole run. ``precomputed`` maps a linter id to findings already made
    for this program (by its ``batch``), which are used instead of running it.
    """
    context = context or LintContext(source, filename, kind, profile)
    findings: List[Dict[str, Any]] = []
    for linter_id, codes in selection_by_linter(normalize_selection(selection)).items():
        linter = get(linter_id)
        if linter is None or not linter.reads(kind):
            continue
        if precomputed is not None and linter_id in precomputed:
            findings += tag(linter, precomputed[linter_id], codes)
            continue
        if not linter.available()[0]:
            continue
        try:
            produced = linter.run(context)
        except Exception as exc:
            produced = [crash_finding(linter, exc)]
        findings += tag(linter, produced, codes)
    findings.sort(key=lambda f: (f.get("line") or 0, f.get("column") or 0, f.get("code", "")))
    return findings


# --------------------------------------------------------------------------
# Built-in: the quick checks
# --------------------------------------------------------------------------

register(Linter(
    id="quick",
    label="Quick checks",
    description="Reads the Python syntax tree only, so it sees literal arguments and nothing "
                "computed. Never runs or interprets the program.",
    rules=[Rule(code, *info) for code, info in lint.RULES.items()],
    run=lambda context: lint.check_source(context.source),
    languages=("python",),
))
