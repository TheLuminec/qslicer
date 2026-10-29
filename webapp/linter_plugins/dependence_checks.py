"""Dependence checks: quantum mistakes in the circuit the static front end rebuilds.

The static parser rebuilds the circuit without running the program (loops
unrolled, helpers followed), so these checks see what a literal reading of the
source misses. Some use the dependence graph: the slicer keeps every
instruction that could change a measured bit, and QD01 reports those that
provably cannot. The others follow the rebuilt circuit: measurements that are
overwritten or constant, controls that are never set, a circuit that measures
nothing, idle qubits, gates that cancel. A file the static parser cannot read
gets no findings (the errors Qiskit raises while rebuilding it are the quick
checks' QS014).

Findings name the instructions they are about (``nodes``: indices into the
rebuilt circuit, which are the slicer's graph node ids), so the graph marks
exactly those, even inside a loop.

This module is also the reference for writing an add-on: define ``LINTER``,
give each rule a code, and return diagnostics from ``run``.
"""

from typing import Any, Dict, Iterable, List, Set

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
    Rule("QD05", "Control never set", "warning",
         "A controlled gate whose control qubit is still |0> when it runs, so the gate never "
         "acts: control and target swapped, or the gate preparing the control is missing or "
         "comes later."),
    Rule("QD06", "No measurement", "warning",
         "The circuit never measures anything (helpers and loops included), and its state is "
         "not read another way (Statevector, Operator, an Estimator)."),
    Rule("QD07", "Idle qubit", "info",
         "A declared qubit that no instruction of the rebuilt circuit ever touches."),
    Rule("QD08", "Cancelling pair", "info",
         "Two identical self-inverse gates back to back on the same wires (loops and helpers "
         "included) cancel; a barrier in between keeps them apart."),
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

# QD07: instructions that span qubits without using them.
_NOT_A_USE = frozenset({"barrier", "delay"})


def _line(instruction) -> int:
    location = getattr(instruction, "location", None)
    return getattr(location, "line", 0) or 0


def _finding(code: str, line: int, message: str, hint: str,
             nodes: Iterable[int] = ()) -> Dict[str, Any]:
    """A finding; ``nodes`` are the instructions it is about (indices into the rebuilt
    circuit, which are also the slicer's graph node ids), for the graph to mark."""
    severity = next(rule.severity for rule in RULES if rule.code == code)
    finding = {"code": code, "severity": severity, "line": line, "column": 0,
               "message": message, "hint": hint}
    if nodes:
        finding["nodes"] = list(nodes)
    return finding


def _dead_gates(program, qdg) -> List[Dict[str, Any]]:
    from slicer import QuantumSlicer, SlicingCriterion

    measurements = [i for i in range(len(program)) if program[i].name == "measure"]
    if not measurements:
        return []
    live: Set[int] = set()
    for idx in measurements:
        if idx not in live:
            live |= QuantumSlicer.backward_slice(qdg, SlicingCriterion(node_idx=idx))
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
            nodes=[idx],
        ))
    return findings


def _overwritten_measurements(program) -> List[Dict[str, Any]]:
    pending: Dict[int, int] = {}     # clbit -> the measurement whose result nothing read yet
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
                    f"{_line(program[earlier]) or '?'} never reaches the output.",
                    "Measure into a different classical bit if both results are wanted.",
                    nodes=[earlier, idx],
                ))
            pending[clbit] = idx
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
                    nodes=[idx],
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
    idle: Dict[int, List[tuple]] = {}   # line -> (gate, unset controls, index) per idle instance
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
            idle.setdefault(line, []).append((name, unset, idx))
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
        name, unset, _ = instances[0]
        findings.append(_finding(
            "QD05", line,
            f"{name} never acts: its control qubit(s) {unset} are always |0> here"
            + (f" ({len(instances)} instances from this line, none of which act)."
               if len(instances) > 1 else "."),
            "Control and target may be swapped, or the gate that should put the control "
            "into superposition (h) or |1> (x) is missing or comes after this one.",
            nodes=[idx for _, _, idx in instances],
        ))
    return findings


def _operation_names(program) -> Set[str]:
    """Every instruction name, those inside control-flow bodies included."""
    names: Set[str] = set()

    def walk(circuit) -> None:
        for item in circuit.data:
            names.add(item.operation.name)
            for param in item.operation.params:
                if hasattr(param, "data"):
                    walk(param)

    for idx in range(len(program)):
        names.add(program[idx].name)
        for param in program[idx].params or []:
            if hasattr(param, "data"):
                walk(param)
    return names


def _no_measurement(program, source: str) -> List[Dict[str, Any]]:
    """QD06: the rebuilt circuit never measures, and nothing reads its state instead."""
    import lint
    if len(program) == 0 or "measure" in _operation_names(program) \
            or lint.measurement_excused(source):
        return []
    line = lint.circuit_line(source) or _line(program[0])
    return [_finding(
        "QD06", line, "This circuit never measures anything.",
        "Sampling it returns nothing useful. Add measure_all() (or measure the qubits you "
        "want), or read its state with Statevector if that is what you meant. Slicing still "
        "works on the gate graph, but a backward slice usually starts from a measurement.",
    )]


def _idle_qubits(program, source: str) -> List[Dict[str, Any]]:
    """QD07: declared qubits no instruction touches (barriers and delays do not count)."""
    import lint
    used: Set[int] = set()
    for idx in range(len(program)):
        if program[idx].name not in _NOT_A_USE:
            used.update(program[idx].qubits)
    idle = sorted(set(range(program.num_qubits)) - used)
    if not idle or len(program) == 0:
        return []
    line = lint.circuit_line(source) or _line(program[0])
    return [_finding(
        "QD07", line, f"Qubit(s) {idle} are declared but never used.",
        "They show up in the graph as empty wires. Shrink the register if they are left over "
        "from an earlier draft, or check whether a gate meant for them names another qubit.",
    )]


def _call_chain(instruction) -> tuple:
    """The lines of the calls that led to an instruction (empty at module level)."""
    return tuple(getattr(getattr(instruction, "location", None), "via", None) or ())


def _cancelling_pairs(program, source: str) -> List[Dict[str, Any]]:
    """QD08: a self-inverse gate followed, on its wires, by the very same gate.

    The next instruction to touch any of the first gate's qubits or bits must be
    the same gate on the same qubits in the same order, with the same
    parameters, and neither may be classically conditioned. Anything else on
    those wires in between, a barrier included, keeps them apart.

    Both gates must also come from the same code: the same chain of calls (the
    location's ``via``). A gate the caller applies and one the helper it calls
    applies, or two calls of one helper, only cancel for the input the program
    happens to build, as when a test prepares x(0) and the solution's own x(0)
    undoes it; the helper is not wrong for other inputs. For the same reason,
    gates on a circuit a function receives (``_lines_given_a_circuit``: an
    oracle's compute/uncompute meets nothing in between only on some inputs)
    are not judged, as for QD05.
    """
    from gates import SELF_INVERSE

    given = _lines_given_a_circuit(source)
    findings = []
    paired: Set[int] = set()
    for first in range(len(program)):
        a = program[first]
        if first in paired or a.name not in SELF_INVERSE or a.metadata.get("condition") is not None:
            continue
        wires = set(a.qubits)
        bits = set(a.clbits)
        for second in range(first + 1, len(program)):
            b = program[second]
            if not (wires & set(b.qubits) or bits & set(b.clbits)):
                continue
            if (b.name == a.name and list(b.qubits) == list(a.qubits)
                    and list(b.params or []) == list(a.params or [])
                    and b.metadata.get("condition") is None and _line(b)
                    and _call_chain(a) == _call_chain(b)
                    and _line(a) not in given and _line(b) not in given):
                paired.add(second)
                where = f"line {_line(a)}" if _line(a) != _line(b) else "this same line"
                finding = _finding(
                    "QD08", _line(b),
                    f"{b.name} on qubit(s) {list(b.qubits)} directly follows the identical gate "
                    f"from {where}; the pair cancels.",
                    "The entanglement analyzer reads this as an uncomputation. If that is what "
                    "you meant, fine -- if not, one of the two is probably a typo. A barrier "
                    "between them marks a deliberate pair.",
                    nodes=[first, second],
                )
                finding["lines"] = sorted({_line(a), _line(b)})
                findings.append(finding)
            break
    return findings


def _merge(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One finding per (code, line): a gate inside a loop is reported once, with a count."""
    merged: Dict[tuple, Dict[str, Any]] = {}
    counts: Dict[tuple, int] = {}
    for finding in findings:
        key = (finding["code"], finding["line"])
        counts[key] = counts.get(key, 0) + 1
        if key in merged:
            nodes = merged[key].setdefault("nodes", [])
            nodes.extend(n for n in finding.get("nodes", []) if n not in nodes)
        else:
            merged[key] = finding
    for key, finding in merged.items():
        if counts[key] > 1:
            finding["message"] += f" ({counts[key]} instances from this line)"
    return list(merged.values())


def run(context: LintContext) -> List[Dict[str, Any]]:
    program, _error = context.program()
    if program is None or len(program) == 0:
        return []
    from qdg import QDG, get_rule_profile

    try:
        rules = get_rule_profile(context.profile)
    except ValueError:
        rules = get_rule_profile("baseline")
    qdg = QDG(program, rules=rules)
    return _merge(_dead_gates(program, qdg) + _overwritten_measurements(program)
                  + _constant_measurements(program) + _unset_controls(program, context.source)
                  + _no_measurement(program, context.source)
                  + _idle_qubits(program, context.source) + _cancelling_pairs(program, context.source))


LINTER = Linter(
    id="dependence",
    label="Dependence checks",
    description="Quantum mistakes in the circuit the static front end rebuilds (loops "
                "unrolled, helpers followed) and its dependence graph: dead gates, overwritten "
                "or constant measurements, controls never set, missing measurements, idle "
                "qubits, cancelling pairs.",
    rules=RULES,
    run=run,
    languages=("python", "qasm"),
    default=True,
)
