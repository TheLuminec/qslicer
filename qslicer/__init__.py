"""Static slicing and linting for quantum programs written in Qiskit or OpenQASM.

The front ends read a program without running it and build a
``QuantumProgram``; ``QDG`` turns that into a dependence graph, and
``backward_slice`` / ``forward_slice`` walk it. ``lint_source`` and
``lint_file`` run the registered linters.

    import qslicer

    program = qslicer.parse_file("bell.py")
    print(qslicer.backward_slice(program, len(program) - 1))
    for finding in qslicer.lint_file("bell.py"):
        print(finding["line"], finding["code"], finding["message"])
"""

import os
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from .dependencies.dependency_rules import DependencyRule, DependencyType
from .ir.qinstruction import QuantumInstruction, SourceLocation
from .ir.qprogram import QuantumProgram
from .parsers.staticopenqasmparser import StaticOpenQASMParser
from .parsers.staticqiskitparser import StaticParseError, StaticQiskitParser
from .qdg import QDG, get_rule_profile, list_rule_profiles
from .slicer import QuantumSlicer, SlicingCriterion

__version__ = "0.1.0"

__all__ = [
    "DependencyRule", "DependencyType", "QDG", "QuantumInstruction", "QuantumProgram",
    "QuantumSlicer", "SlicingCriterion", "SourceLocation", "StaticOpenQASMParser",
    "StaticParseError", "StaticQiskitParser", "backward_slice", "chop", "example_path",
    "forward_slice", "get_rule_profile", "language_of", "lint_file", "lint_source",
    "list_rule_profiles", "parse", "parse_file",
]

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")

_QASM_SUFFIXES = (".qasm", ".qasm2", ".qasm3")


def language_of(filename: str) -> str:
    """``"qasm"`` for an OpenQASM file name, ``"python"`` for anything else."""
    return "qasm" if filename.lower().endswith(_QASM_SUFFIXES) else "python"


def _parser(language: str, filename: str, time_limit: Optional[float]):
    if language == "qasm":
        return StaticOpenQASMParser(time_limit=time_limit)
    if language == "python":
        return StaticQiskitParser(filename=os.path.basename(filename) or "<source>",
                                  time_limit=time_limit)
    raise ValueError(f"unknown language {language!r}; use 'python' or 'qasm'")


def parse(source: str, filename: str = "", language: Optional[str] = None,
          time_limit: Optional[float] = None) -> QuantumProgram:
    """Build the IR of ``source`` without executing it.

    ``language`` is ``"python"`` (Qiskit) or ``"qasm"`` (OpenQASM 2/3); left
    out, it follows ``filename``'s extension. Raises ``StaticParseError`` when
    the circuit cannot be determined statically.
    """
    parser = _parser(language or language_of(filename), filename, time_limit)
    parser.source = source
    return parser.parse()


def parse_file(path: str, language: Optional[str] = None,
               time_limit: Optional[float] = None) -> QuantumProgram:
    """Read ``path`` and build its IR without executing it; see ``parse``."""
    with open(path, encoding="utf-8-sig") as handle:
        return parse(handle.read(), path, language, time_limit)


def _graph(target: Union[QuantumProgram, QDG], profile: str) -> QDG:
    return target if isinstance(target, QDG) else QDG(target, get_rule_profile(profile))


def backward_slice(target: Union[QuantumProgram, QDG], node_idx: int,
                   dep_types: Optional[Iterable[DependencyType]] = None,
                   profile: str = "baseline") -> Set[int]:
    """Indices of the instructions ``node_idx`` depends on, itself included.

    ``target`` is a program (its QDG is built with the ``profile`` rules) or a
    QDG already built. ``dep_types`` restricts which edges are followed.
    """
    criterion = SlicingCriterion(node_idx, dep_types=set(dep_types) if dep_types else None)
    return QuantumSlicer.backward_slice(_graph(target, profile), criterion)


def forward_slice(target: Union[QuantumProgram, QDG], node_idx: int,
                  dep_types: Optional[Iterable[DependencyType]] = None,
                  profile: str = "baseline") -> Set[int]:
    """Indices of the instructions ``node_idx`` can affect, itself included."""
    criterion = SlicingCriterion(node_idx, dep_types=set(dep_types) if dep_types else None)
    return QuantumSlicer.forward_slice(_graph(target, profile), criterion)


def chop(target: Union[QuantumProgram, QDG], source_idx: int, target_idx: int,
         dep_types: Optional[Iterable[DependencyType]] = None,
         profile: str = "baseline") -> Set[int]:
    """Indices of the instructions through which ``source_idx`` can affect
    ``target_idx``: its forward slice intersected with the target's backward
    slice. Empty when it cannot affect the target at all."""
    return QuantumSlicer.chop(_graph(target, profile), source_idx, target_idx,
                              set(dep_types) if dep_types else None)


def lint_source(source: str, filename: str = "", language: Optional[str] = None,
                select: Optional[Iterable[str]] = None,
                profile: str = "baseline") -> List[Dict[str, Any]]:
    """Run the linters over ``source``; findings ordered by line.

    ``select`` names rules (``"quick/QS002"``) or whole linters (``"lintq"``);
    left out, every linter's default rules run. Each finding is a dict with
    code, severity, line, column, message, hint, tool and rule.
    """
    from .lint import registry
    kind = f"{language or language_of(filename)}-static"
    return registry.run(source, filename, kind, select, profile=profile)


def lint_file(path: str, language: Optional[str] = None,
              select: Optional[Iterable[str]] = None,
              profile: str = "baseline") -> List[Dict[str, Any]]:
    """Read ``path`` and lint it; see ``lint_source``."""
    with open(path, encoding="utf-8-sig") as handle:
        return lint_source(handle.read(), path, language, select, profile)


def example_path(name: str) -> str:
    """Path of a bundled example circuit, such as ``"bell_pair.py"``."""
    path = os.path.join(EXAMPLES_DIR, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no bundled example {name!r} in {EXAMPLES_DIR}")
    return path
