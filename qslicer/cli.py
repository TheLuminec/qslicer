"""The ``qslicer`` command: lint, list and slice Qiskit or OpenQASM files.

    qslicer lint circuit.py [more.py ...] [--select quick lintq] [--format json]
    qslicer list circuit.py
    qslicer slice circuit.py --backward 12
    qslicer slice circuit.py --forward --line 8 --deps DATA_QUBIT MEASUREMENT
    qslicer slice circuit.py --qubit 2            (from qubit 2's last measurement)
    qslicer slice circuit.py 5 --chop-qubit 2     (how instruction 5 reaches it)
    qslicer rules
    qslicer viz circuit.py           (needs matplotlib: pip install qslicer[viz];
                                      qslicer viz --help for its options)

Nothing is executed: every command reads the files with the static front ends.
``lint`` exits 1 when a finding at or above ``--fail-on`` is reported, so it can
gate CI or a pre-commit hook.
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import (DependencyType, QuantumProgram, QuantumSlicer, StaticParseError, __version__,
               backward_slice, chop, forward_slice, lint_file, parse_file)

_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


def _label(instruction) -> str:
    text = instruction.name
    if instruction.qubits:
        text += " q" + ",".join(str(q) for q in instruction.qubits)
    if instruction.clbits:
        text += " -> c" + ",".join(str(c) for c in instruction.clbits)
    return text


def _line(instruction) -> Optional[int]:
    return instruction.location.line if instruction.location is not None else None


def _describe(program: QuantumProgram, indices) -> List[Dict[str, Any]]:
    return [{"index": i, "name": program[i].name, "qubits": list(program[i].qubits),
             "clbits": list(program[i].clbits), "line": _line(program[i]),
             "label": _label(program[i])} for i in sorted(indices)]


def _print_instructions(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        line = row["line"] if row["line"] is not None else "-"
        print(f"{row['index']:>5}  line {line:>4}  {row['label']}")


def _parse(path: str, language: Optional[str]) -> QuantumProgram:
    try:
        return parse_file(path, language)
    except StaticParseError as exc:
        raise SystemExit(f"{path}: cannot determine the circuit statically: {exc}")
    except OSError as exc:
        raise SystemExit(f"{path}: {exc.strerror or exc}")


def _cmd_lint(args) -> int:
    threshold = _SEVERITY_RANK.get(args.fail_on)
    failed = False
    report = {}
    for path in args.files:
        try:
            findings = lint_file(path, args.language, args.select or None, args.profile)
        except OSError as exc:
            raise SystemExit(f"{path}: {exc.strerror or exc}")
        report[path] = findings
        if threshold is not None and any(
                _SEVERITY_RANK.get(f.get("severity"), 0) >= threshold for f in findings):
            failed = True
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        total = 0
        for path, findings in report.items():
            for f in findings:
                total += 1
                # Columns are 1-based already; 0 means the linter gave none.
                print(f"{path}:{f.get('line') or 0}:{f.get('column') or 1}: "
                      f"{f.get('severity', 'warning')} {f['code']} {f['message']}")
                if args.hints and f.get("hint"):
                    print(f"    hint: {f['hint']}")
        print(f"{total} finding{'s' if total != 1 else ''} in {len(report)} "
              f"file{'s' if len(report) != 1 else ''}", file=sys.stderr)
    return 1 if failed else 0


def _cmd_list(args) -> int:
    program = _parse(args.file, args.language)
    rows = _describe(program, range(len(program)))
    if args.format == "json":
        print(json.dumps({"num_qubits": program.num_qubits, "num_clbits": program.num_clbits,
                          "instructions": rows}, indent=2))
    else:
        print(f"{program.num_qubits} qubits, {program.num_clbits} clbits, "
              f"{len(program)} instructions")
        _print_instructions(rows)
    return 0


def _measurement_of(program: QuantumProgram, qubit: int) -> int:
    """The last measurement of ``qubit``: what slicing on a qubit slices from."""
    if not 0 <= qubit < program.num_qubits:
        raise SystemExit(f"qubit {qubit} is outside 0..{program.num_qubits - 1}")
    node = QuantumSlicer.qubit_criterion(program, qubit)
    if node is None:
        raise SystemExit(f"q{qubit} is never measured, so there is no measurement to slice from")
    return node


def _criterion(program: QuantumProgram, args) -> int:
    if args.qubit is not None:
        if args.index is not None or args.line is not None:
            raise SystemExit("give --qubit, an instruction index or --line, not several")
        if args.direction == "forward":
            raise SystemExit("--qubit slices backward from the qubit's last measurement")
        return _measurement_of(program, args.qubit)
    if args.line is None:
        if args.index is None:
            raise SystemExit("give an instruction index or --line")
        if not 0 <= args.index < len(program):
            raise SystemExit(f"index {args.index} is outside 0..{len(program) - 1}")
        return args.index
    on_line = [i for i in range(len(program)) if _line(program[i]) == args.line]
    if not on_line:
        raise SystemExit(f"no instruction comes from line {args.line}; "
                         f"`qslicer list {args.file}` shows which lines do")
    # The last instruction on the line for a backward slice, the first for a forward one.
    return on_line[0] if args.direction == "forward" else on_line[-1]


def _cmd_slice(args) -> int:
    program = _parse(args.file, args.language)
    if len(program) == 0:
        raise SystemExit(f"{args.file}: the circuit has no instructions")
    node = _criterion(program, args)
    try:
        deps = [DependencyType[name.upper()] for name in args.deps or []]
    except KeyError as exc:
        raise SystemExit(f"unknown dependency type {exc.args[0]}; choose from "
                         + ", ".join(t.name for t in DependencyType))
    target = None
    if args.chop is not None and args.chop_qubit is not None:
        raise SystemExit("give --chop or --chop-qubit, not both")
    if args.chop_qubit is not None:
        target = _measurement_of(program, args.chop_qubit)
    elif args.chop is not None:
        if not 0 <= args.chop < len(program):
            raise SystemExit(f"index {args.chop} is outside 0..{len(program) - 1}")
        target = args.chop
    if target is not None:
        if args.direction == "forward" or args.qubit is not None:
            raise SystemExit("a chop runs from an instruction (index or --line) to "
                             "--chop or --chop-qubit")
        rows = _describe(program, chop(program, node, target, deps or None, args.profile))
        if args.format == "json":
            print(json.dumps({"criterion": node, "target": target, "direction": "chop",
                              "slice": rows}, indent=2))
        elif not rows:
            print(f"instruction {node} ({_label(program[node])}) cannot affect "
                  f"instruction {target} ({_label(program[target])})")
        else:
            print(f"chop from instruction {node} ({_label(program[node])}) to instruction "
                  f"{target} ({_label(program[target])}): {len(rows)} of {len(program)} "
                  "instructions")
            _print_instructions(rows)
        return 0
    walk = forward_slice if args.direction == "forward" else backward_slice
    rows = _describe(program, walk(program, node, deps or None, args.profile))
    if args.format == "json":
        print(json.dumps({"criterion": node, "direction": args.direction, "slice": rows},
                         indent=2))
    else:
        print(f"{args.direction} slice from instruction {node} "
              f"({_label(program[node])}): {len(rows)} of {len(program)} instructions")
        _print_instructions(rows)
    return 0


def _cmd_rules(args) -> int:
    from .lint import registry
    catalog = registry.catalog()
    if args.format == "json":
        print(json.dumps(catalog, indent=2))
        return 0
    for linter in catalog:
        state = "on by default" if linter["default"] else "off by default"
        if not linter["available"]:
            state = f"unavailable: {linter['unavailable_reason']}"
        print(f"{linter['id']}  {linter['label']} ({state})")
        for rule in linter["rules"]:
            print(f"    {rule['id']:<22} {rule['severity']:<8} {rule['title']}")
    return 0


def _cmd_viz(argv: Sequence[str]) -> int:
    try:
        from .viz import visualizer
    except ImportError as exc:
        raise SystemExit(f"qslicer viz needs matplotlib ({exc}); pip install qslicer[viz]")
    return visualizer.main(list(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qslicer", description="Static slicing and linting for Qiskit and OpenQASM programs.")
    parser.add_argument("--version", action="version", version=f"qslicer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, many=False):
        if many:
            p.add_argument("files", nargs="+", help="Qiskit (.py) or OpenQASM (.qasm) files")
        else:
            p.add_argument("file", help="a Qiskit (.py) or OpenQASM (.qasm) file")
        p.add_argument("--language", choices=("python", "qasm"),
                       help="override the language the file extension implies")
        p.add_argument("--format", choices=("text", "json"), default="text")

    p = sub.add_parser("lint", help="report likely bugs")
    common(p, many=True)
    p.add_argument("--select", nargs="+", metavar="RULE",
                   help="rule ids (quick/QS002) or linter ids (lintq); default: each linter's defaults")
    p.add_argument("--fail-on", choices=("info", "warning", "error", "never"), default="warning",
                   help="lowest severity that makes the exit status 1 (default: warning)")
    p.add_argument("--hints", action="store_true", help="print each finding's hint")
    p.add_argument("--profile", default="baseline", help="dependency rule profile")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("list", help="print the instructions the static parser found")
    common(p)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("slice", help="backward or forward slice from one instruction, "
                                     "or a chop between two")
    common(p)
    p.add_argument("index", nargs="?", type=int, help="instruction index (see `qslicer list`)")
    p.add_argument("--line", type=int, help="slice from the instruction on this source line")
    p.add_argument("--qubit", type=int, metavar="Q",
                   help="slice backward from the last measurement of this qubit")
    p.add_argument("--chop", type=int, metavar="INDEX",
                   help="keep only what lies between the instruction and this later one "
                        "(forward slice of the first, backward slice of the second)")
    p.add_argument("--chop-qubit", type=int, metavar="Q",
                   help="like --chop, to the last measurement of this qubit")
    direction = p.add_mutually_exclusive_group()
    direction.add_argument("--backward", dest="direction", action="store_const",
                           const="backward", help="what the instruction depends on (default)")
    direction.add_argument("--forward", dest="direction", action="store_const",
                           const="forward", help="what the instruction can affect")
    p.add_argument("--deps", nargs="+", metavar="TYPE",
                   help="follow only these dependency types, e.g. DATA_QUBIT MEASUREMENT")
    p.add_argument("--profile", default="baseline", help="dependency rule profile")
    p.set_defaults(func=_cmd_slice, direction="backward")

    # Only listed here; main() hands its arguments straight to the visualizer.
    sub.add_parser("viz", help="explore the dependence graph, its slices and the lint "
                               "findings in a window (qslicer viz --help)")

    p = sub.add_parser("rules", help="list the linters and their rules")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=_cmd_rules)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["viz"]:
        return _cmd_viz(argv[1:])
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
