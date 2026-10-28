"""Dependence checks: mistakes the QPDG can prove, as a linter add-on.

The slicer keeps every instruction that could change a measured bit. These
checks report the instructions that provably cannot, on the circuit the static
front end rebuilt (loops unrolled, helpers followed), so they see what the
quick checks' literal reading misses. They never execute the program; a file
the static parser cannot read gets no findings, except QD04 when the reason is
that Qiskit itself would reject an operation (a qubit index out of range, the
same qubit twice): the parser raises Qiskit's errors where Qiskit would.

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
    Rule("QD04", "Qiskit rejects an operation", "error",
         "Rebuilding the circuit hits an error Qiskit raises when the program runs: a qubit "
         "index out of range, the same qubit twice, the wrong number of arguments."),
    Rule("QD05", "Control never set", "warning",
         "A controlled gate whose control qubit is still |0> when it runs, so the gate never "
         "acts: control and target swapped, or the gate preparing the control is missing or "
         "comes later."),
]

# QD05: how many leading qubits of a controlled gate are controls.
_CONTROLS = {
    "cx": 1, "cy": 1, "cz": 1, "ch": 1, "cs": 1, "csdg": 1, "csx": 1, "cp": 1, "cu1": 1,
    "crx": 1, "cry": 1, "crz": 1, "cu": 1, "cu3": 1, "cswap": 1,
    "ccx": 2, "ccz": 2, "rccx": 2, "c3x": 3, "c3sx": 3, "rc3x": 3, "c4x": 4,
}
_ALL_BUT_LAST = frozenset({"mcx", "mcp", "mcphase", "mcrx", "mcry", "mcrz", "mcx_gray",
                           "mcx_recursive", "mcx_vchain"})
# Instructions after which a qubit that was |0> still is: diagonal gates, and the
# bookkeeping that does not change a state (a measured |0> stays |0>).
_KEEP_ZERO = frozenset({"z", "s", "sdg", "t", "tdg", "p", "rz", "u1", "id", "i", "barrier",
                        "delay", "measure", "global_phase", "cz", "cp", "cu1", "ccz", "cs",
                        "csdg", "crz", "mcp", "mcphase", "mcrz", "rzz"})

# Errors the static parser raises where Qiskit would. Other exceptions it reports
# (NameError, TypeError, ...) can come from what it cannot model, so they are not
# findings: on the benchmarks they also appear on fixed programs.
_QISKIT_ERRORS = frozenset({"CircuitError", "QiskitError"})


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


def _controls(instruction) -> List[int]:
    if instruction.name in _CONTROLS:
        return list(instruction.qubits[:_CONTROLS[instruction.name]])
    if instruction.name in _ALL_BUT_LAST:
        return list(instruction.qubits[:-1])
    return []


def _lines_given_a_circuit(source: str) -> Set[int]:
    """Lines of gate calls on a circuit that a function receives as a parameter.

    Such a function (an oracle, a QFT, an adder) is written for any input; the
    rebuilt circuit only shows it run on one, where a control may well be |0>.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: Set[int] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in func.args.posonlyargs + func.args.args + func.args.kwonlyargs}
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in params):
                found.add(node.lineno)
    return found


def _unset_controls(program, source: str) -> List[Dict[str, Any]]:
    """Controlled gates run while a control qubit is provably still |0>.

    Every qubit starts in |0>. It stays there through diagonal gates and
    measurements, returns there on reset, and leaves for good on anything
    else: another gate, a classically conditioned one, a control-flow block, an
    instruction the rule does not know. A gate written with ``ctrl_state`` is
    skipped, since the IR does not record which controls are open, and so is a
    gate on a circuit a function receives (see ``_lines_given_a_circuit``).
    """
    lines = source.splitlines()
    given = _lines_given_a_circuit(source)
    zero: Set[int] = set(q for i in range(len(program)) for q in program[i].qubits)
    idle: Dict[int, List[tuple]] = {}         # line -> (gate, unset controls) per idle instance
    acting: Set[int] = set()                  # lines with at least one gate that can act
    for idx in range(len(program)):
        instruction = program[idx]
        qubits = list(instruction.qubits)
        name = instruction.name
        conditioned = instruction.metadata.get("condition") is not None
        line = _line(instruction)
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        unset = [q for q in _controls(instruction) if q in zero]
        if unset and not conditioned and "ctrl_state" not in text:
            idle.setdefault(line, []).append((name, unset))
            continue                          # the gate does nothing, so no state changes
        if _controls(instruction):
            acting.add(line)
        if name == "reset":
            zero.update(qubits)
        elif conditioned or name not in _KEEP_ZERO:
            zero.difference_update(qubits)
    # A line is reported only when no gate it produced could act: a loop whose
    # other iterations do act is general code meeting one particular input.
    findings = []
    for line, instances in idle.items():
        if not line or line in acting or line in given:
            continue
        name, unset = instances[0]
        findings.append(_finding(
            "QD05", line,
            f"{name} never acts: its control qubit(s) {unset} are always |0> here"
            + (f" ({len(instances)} instances from this line, none of which act)."
               if len(instances) > 1 else "."),
            "Control and target may be swapped, or the gate that should put the control "
            "into superposition (h) or |1> (x) is missing or comes after this one.",
        ))
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


def _rejected_operation(failure) -> List[Dict[str, Any]]:
    raised = getattr(failure, "raised", None)
    line = getattr(failure, "lineno", None)
    if raised not in _QISKIT_ERRORS or not line:
        return []
    detail = getattr(failure, "message", str(failure)).split(f"{raised}: ", 1)[-1]
    return [_finding(
        "QD04", line, f"Qiskit raises {raised} here: {detail}",
        "The program stops at this line when it runs. Check the qubit and bit indices against "
        "the circuit's size, and the number of qubits the gate takes.",
    )]


def run(context: LintContext) -> List[Dict[str, Any]]:
    program, _error = context.program()
    if program is None:
        return _rejected_operation(context.failure())
    if len(program) == 0:
        return []
    from qpdg import QPDG, get_rule_profile

    try:
        rules = get_rule_profile(context.profile)
    except ValueError:
        rules = get_rule_profile("baseline")
    qpdg = QPDG(program, rules=rules)
    return _merge(_dead_gates(program, qpdg) + _overwritten_measurements(program)
                  + _constant_measurements(program) + _unset_controls(program, context.source))


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
