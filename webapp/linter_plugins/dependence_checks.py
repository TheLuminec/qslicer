"""Dependence checks: mistakes the QPDG can prove, as a linter add-on.

The slicer keeps every instruction that could change a measured bit. These
checks report the instructions that provably cannot, on the circuit the static
front end rebuilt (loops unrolled, helpers followed), so they see what the
quick checks' literal reading misses. They never execute the program; a file
the static parser cannot read gets no findings.

This module is also the reference for writing an add-on: define ``LINTER``,
give each rule a code, and return diagnostics from ``run``.
"""

from typing import Any, Dict, List, Set

from linters import Linter, LintContext, Rule

# Instructions that are bookkeeping rather than gates: never reported as dead.
_NOT_GATES = frozenset({"barrier", "delay", "snapshot", "measure", "measure_all"})
_PREPARING = frozenset({"barrier", "delay", "snapshot", "measure", "reset"})

RULES = [
    Rule("QD01", "Dead gate", "warning",
         "No measured bit depends on this gate, so removing it cannot change the output."),
    Rule("QD02", "Measurement overwritten", "warning",
         "A measurement writes a classical bit that a later measurement overwrites before "
         "anything reads it."),
    Rule("QD03", "Constant measurement", "warning",
         "A qubit is measured before any gate has acted on it, so the result is always 0."),
]


def _line(instruction) -> int:
    location = getattr(instruction, "location", None)
    return getattr(location, "line", 0) or 0


def _finding(code: str, line: int, message: str, hint: str) -> Dict[str, Any]:
    severity = next(rule.severity for rule in RULES if rule.code == code)
    return {"code": code, "severity": severity, "line": line, "column": 0,
            "message": message, "hint": hint}


def _dead_gates(program, qpdg) -> List[Dict[str, Any]]:
    from slicer import QuantumSlicer, SlicingCriterion

    measurements = [i for i in range(len(program)) if program[i].name == "measure"]
    if not measurements:
        return []
    live: Set[int] = set()
    for idx in measurements:
        if idx not in live:
            live |= QuantumSlicer.backward_slice(qpdg, SlicingCriterion(node_idx=idx))
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        if idx in live or instruction.name in _NOT_GATES or not _line(instruction):
            continue
        findings.append(_finding(
            "QD01", _line(instruction),
            f"{instruction.name} on qubit(s) {list(instruction.qubits)} cannot affect any "
            "measured bit.",
            "Either it is left over, or a measurement that should follow it is missing.",
        ))
    return findings


def _overwritten_measurements(program) -> List[Dict[str, Any]]:
    pending: Dict[int, Any] = {}     # clbit -> the measurement whose result nothing read yet
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        for clbit in instruction.metadata.get("condition_clbits", []) or []:
            pending.pop(clbit, None)
        if instruction.name != "measure":
            continue
        for clbit in instruction.clbits:
            earlier = pending.get(clbit)
            if earlier is not None and _line(instruction):
                findings.append(_finding(
                    "QD02", _line(instruction),
                    f"Classical bit {clbit} is overwritten here; the measurement at line "
                    f"{_line(earlier) or '?'} never reaches the output.",
                    "Measure into a different classical bit if both results are wanted.",
                ))
            pending[clbit] = instruction
    return findings


def _constant_measurements(program) -> List[Dict[str, Any]]:
    touched: Set[int] = set()
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        if instruction.name == "measure":
            untouched = [q for q in instruction.qubits if q not in touched]
            if untouched and _line(instruction):
                findings.append(_finding(
                    "QD03", _line(instruction),
                    f"Qubit(s) {untouched} are measured before any gate acts on them; the "
                    "result is always 0.",
                    "A preparation gate may be missing, or the wrong qubit is measured.",
                ))
        elif instruction.name == "reset":
            touched.difference_update(instruction.qubits)
        elif instruction.name not in _PREPARING:
            touched.update(instruction.qubits)
    return findings


def _merge(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One finding per (code, line): a gate inside a loop is reported once, with a count."""
    merged: Dict[tuple, Dict[str, Any]] = {}
    counts: Dict[tuple, int] = {}
    for finding in findings:
        key = (finding["code"], finding["line"])
        counts[key] = counts.get(key, 0) + 1
        merged.setdefault(key, finding)
    for key, finding in merged.items():
        if counts[key] > 1:
            finding["message"] += f" ({counts[key]} instances from this line)"
    return list(merged.values())


def run(context: LintContext) -> List[Dict[str, Any]]:
    program, _error = context.program()
    if program is None or len(program) == 0:
        return []
    from qpdg import QPDG, get_rule_profile

    try:
        rules = get_rule_profile(context.profile)
    except ValueError:
        rules = get_rule_profile("baseline")
    qpdg = QPDG(program, rules=rules)
    return _merge(_dead_gates(program, qpdg) + _overwritten_measurements(program)
                  + _constant_measurements(program))


LINTER = Linter(
    id="dependence",
    label="Dependence checks",
    description="Reads the circuit the static front end rebuilds and its dependence graph "
                "(with the selected QPDG rules). Finds gates no measured bit depends on.",
    rules=RULES,
    run=run,
    languages=("python", "qasm"),
    default=False,
)
