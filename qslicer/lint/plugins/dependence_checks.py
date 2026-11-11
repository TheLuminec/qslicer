"""Dependence checks: quantum mistakes in the circuit the static front end rebuilds.

The static parser rebuilds the circuit without running the program (loops
unrolled, helpers followed), so these checks see what a literal reading of the
source misses. Some use the dependence graph: the slicer keeps every
instruction that could change a measured bit, and QD01 reports those that
provably cannot. The others follow the rebuilt circuit: measurements that are
overwritten or constant, controls that are never set, a circuit that measures
nothing, idle qubits, gates that cancel, conditions no measurement sets, gates
that leave their qubit unchanged, a measured circuit whose state is read,
classical bits nothing writes. A file the static parser cannot read
gets no findings (the errors Qiskit raises while rebuilding it are the quick
checks' QS014).

Findings name the instructions they are about (``nodes``: indices into the
rebuilt circuit, which are the slicer's graph node ids), so the graph marks
exactly those, even inside a loop.

This module is also the reference for writing an add-on: define ``LINTER``,
give each rule a code, and return diagnostics from ``run``.
"""

import functools
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set

from ..registry import Linter, LintContext, Rule

# Instructions that are bookkeeping rather than gates: never reported as dead.
_NOT_GATES = frozenset({"barrier", "delay", "snapshot", "measure", "measure_all"})
_PREPARING = frozenset({"barrier", "delay", "snapshot", "measure", "reset"})

RULES = [
    Rule("QD01", "Dead gate", "warning",
         "No classical bit the circuit writes (by a measurement, a store or a block that "
         "measures inside) depends on this gate, so removing it cannot change the output."),
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
    Rule("QD09", "Condition never set", "warning",
         "An if_test (or other classical condition) reads only bits no earlier measurement "
         "wrote: they are still 0, so the branch is decided before the program runs."),
    Rule("QD10", "Gate has no effect", "warning",
         "A gate that leaves its qubit's state unchanged, because its own code just put the "
         "qubit in an eigenstate of it: x after h (x leaves |+> as it is), y on |+i>."),
    Rule("QD11", "State read from a circuit that measures", "error",
         "A circuit measures (or resets) and is then given to something that reads its quantum "
         "state: Statevector(qc) and Operator(qc) raise an error; a statevector simulator "
         "returns the collapsed state (a warning)."),
    Rule("QD12", "Classical bits never written", "warning",
         "A circuit that measures declares classical bits that no measurement writes and no "
         "condition reads: a warning when measure_all() added bits of its own instead, "
         "information otherwise."),
]

# QD10: single-qubit Clifford gates as maps of a qubit's stabilizer, one of
# "+Z" (|0>), "-Z" (|1>), "+X" (|+>), "-X" (|->), "+Y" (|+i>), "-Y" (|-i>).
_CLIFFORD = {
    "h": {"X": "+Z", "Z": "+X", "Y": "-Y"},
    "x": {"X": "+X", "Y": "-Y", "Z": "-Z"},
    "y": {"X": "-X", "Y": "+Y", "Z": "-Z"},
    "z": {"X": "-X", "Y": "-Y", "Z": "+Z"},
    "s": {"X": "+Y", "Y": "-X", "Z": "+Z"},
    "sdg": {"X": "-Y", "Y": "+X", "Z": "+Z"},
    "id": {"X": "+X", "Y": "+Y", "Z": "+Z"},
}
# Gates (non-diagonal ones only) and the axis whose eigenstates they leave alone.
_FIXES = {"x": "X", "sx": "X", "sxdg": "X", "rx": "X", "y": "Y", "ry": "Y"}
# Diagonal gates: they keep a Z eigenstate as it is.
_DIAGONAL = frozenset({"z", "s", "sdg", "t", "tdg", "p", "rz", "u1", "id", "i"})
_KET = {"+Z": "|0>", "-Z": "|1>", "+X": "|+>", "-X": "|->", "+Y": "|+i>", "-Y": "|-i>"}

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


def _writes_a_bit(instruction) -> bool:
    """Whether an instruction writes a classical bit: a measurement, a store, or a
    control-flow block whose body (at any depth) writes one. A block lists its
    condition bits among its clbits, so its body is what tells."""
    bodies = [p for p in instruction.params if hasattr(p, "data")]
    if not bodies:
        return bool(instruction.clbits)
    return any(_body_writes(body) for body in bodies)


def _body_writes(circuit) -> bool:
    for inner in circuit.data:
        bodies = [p for p in inner.operation.params if hasattr(p, "data")]
        if bodies:
            if any(_body_writes(body) for body in bodies):
                return True
        elif inner.clbits:
            return True
    return False


def _live_nodes(qdg, criteria: Iterable[int]) -> Set[int]:
    """The union of the backward slices from every node in ``criteria``, following
    every dependency type: the criteria and everything they transitively depend
    on, found in one walk over the graph instead of one slice per criterion."""
    graph = qdg.graph
    live: Set[int] = set(criteria)
    stack = list(live)
    while stack:
        for pred in graph.predecessors(stack.pop()):
            if pred not in live:
                live.add(pred)
                stack.append(pred)
    return live


def _dead_gates(program, qdg) -> List[Dict[str, Any]]:
    # The program's output is every classical bit it writes, so every writer is a
    # criterion: measurements, stores, and blocks that measure inside.
    writers = [i for i in range(len(program)) if _writes_a_bit(program[i])]
    if not any(program[i].name == "measure" for i in writers):
        return []
    live = _live_nodes(qdg, writers)
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        if idx in live or instruction.name in _NOT_GATES or not _line(instruction):
            continue
        # A dead if_test block is reported once, at the block: not again for
        # each gate inside it.
        if any(predicate not in live
               for predicate, _ in instruction.metadata.get("guards") or ()):
            continue
        what = ("This if_test block" if instruction.name == "if_else"
                else f"{instruction.name} on qubit(s) {list(instruction.qubits)}")
        findings.append(_finding(
            "QD01", _line(instruction),
            f"{what} cannot affect any classical bit the circuit writes.",
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
        # A measurement inside an if_test only may run, so the earlier result
        # can still reach the output.
        conditional = instruction.metadata.get("condition") is not None
        for clbit in instruction.clbits:
            earlier = pending.get(clbit)
            if earlier is not None and _line(instruction) and not conditional:
                findings.append(_finding(
                    "QD02", _line(instruction),
                    f"Classical bit {clbit} is overwritten here; the measurement at line "
                    f"{_line(program[earlier]) or '?'} never reaches the output.",
                    "Measure into a different classical bit if both results are wanted.",
                    nodes=[earlier, idx],
                ))
            pending[clbit] = idx
    return findings


def _constant_measurements(program, source: str) -> List[Dict[str, Any]]:
    """QD03: a measurement of a qubit no gate has acted on yet. Not judged inside a
    function that receives its circuit: there the caller prepares the qubit, and the
    rebuilt circuit only shows one caller (often a test passing |0>)."""
    given = _lines_given_a_circuit(source)
    touched: Set[int] = set()
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        if instruction.name == "measure":
            untouched = [q for q in instruction.qubits if q not in touched]
            if untouched and _line(instruction) and _line(instruction) not in given:
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


@functools.lru_cache(maxsize=8)
def _lines_given_a_circuit(source: str) -> FrozenSet[int]:
    """Lines of gate calls on a circuit that a function receives as a parameter.

    Such a function (an oracle, a QFT, an adder) is written for any input; the
    rebuilt circuit only shows it run on one, where a control may well be |0>.
    Three rules ask, so a source is read once (hence the frozenset).
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    found: Set[int] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in func.args.posonlyargs + func.args.args + func.args.kwonlyargs}
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in params):
                found.add(node.lineno)
    return frozenset(found)


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
    """QD06: the rebuilt circuit never measures, and nothing reads its state instead.

    Reported where the samples are read (the first get_counts() after the circuit is
    sampled, else the sampling call), with the circuit's line in ``lines``; at the
    circuit when nothing samples it. A read inside a function QS016 reports is left
    to QS016, which names the error Aer raises there.
    """
    from .. import quick_checks as lint
    if len(program) == 0 or "measure" in _operation_names(program) \
            or lint.measurement_excused(source):
        return []
    line = lint.circuit_line(source) or _line(program[0])
    read = lint.sampled_read_line(source)
    if read and any(first <= read <= last for first, last in lint.unmeasured_counts_functions(source)):
        return []
    finding = _finding(
        "QD06", read or line,
        (f"The circuit built at line {line} never measures anything, so sampling it gives no "
         "counts." if read and line and read != line else "This circuit never measures anything."),
        "Sampling it returns nothing useful. Add measure_all() (or measure the qubits you "
        "want), or read its state with Statevector if that is what you meant. Slicing still "
        "works on the gate graph, but a backward slice usually starts from a measurement.",
    )
    if read and line and read != line:
        finding["lines"] = sorted({read, line})
    return [finding]


def _idle_qubits(program, source: str) -> List[Dict[str, Any]]:
    """QD07: declared qubits no instruction touches (barriers and delays do not count)."""
    from .. import quick_checks as lint
    used: Set[int] = set()
    for idx in range(len(program)):
        if program[idx].name not in _NOT_A_USE:
            used.update(program[idx].qubits)
    idle = sorted(set(range(program.num_qubits)) - used)
    if not idle or len(program) == 0:
        return []
    line = lint.circuit_line(source) or _line(program[0])
    finding = _finding(
        "QD07", line, f"Qubit(s) {idle} are declared but never used.",
        "They show up in the graph as empty wires, marked in red. Shrink the register if they "
        "are left over from an earlier draft, or check whether a gate meant for them names "
        "another qubit.",
    )
    finding["qubits"] = idle                   # the graph marks these wires
    return [finding]


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
    from ...gates import SELF_INVERSE

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
                    "This reads as an uncomputation. If that is what "
                    "you meant, fine -- if not, one of the two is probably a typo. A barrier "
                    "between them marks a deliberate pair.",
                    nodes=[first, second],
                )
                finding["lines"] = sorted({_line(a), _line(b)})
                findings.append(finding)
            break
    return findings


def _conditions_never_set(program) -> List[Dict[str, Any]]:
    """QD09: a condition none of whose bits an earlier measurement (or store) wrote.

    Classical bits start at 0, so such a branch always goes the same way; usually
    the measurement is missing, comes after the if_test, or writes another bit.
    A control-flow block may write bits inside it, so its bits count as written
    afterwards.
    """
    written: Set[int] = set()
    findings = []
    for idx in range(len(program)):
        instruction = program[idx]
        bits = list(instruction.metadata.get("condition_clbits") or [])
        if bits and _line(instruction) and not any(bit in written for bit in bits):
            findings.append(_finding(
                "QD09", _line(instruction),
                f"This condition reads classical bit(s) {bits}, which no earlier measurement "
                "wrote: they are still 0, so the branch never changes.",
                "Measure into those bits before the if_test, or check that the measurement "
                "writes the bit the condition reads.",
                nodes=[idx],
            ))
        if instruction.name in ("measure", "store") or bits:
            written.update(instruction.clbits)
    return findings


def _no_effect_gates(program) -> List[Dict[str, Any]]:
    """QD10: a non-diagonal gate on a qubit already in one of its eigenstates.

    Each qubit's state is followed while it is a Pauli eigenstate: from |0>
    through single-qubit Clifford gates, back to |0> on reset, lost on anything
    else (a multi-qubit or conditioned gate, a measurement, an unknown gate).
    A gate is reported when it leaves that state as it is, the gate that put
    the qubit there came from the same code (the same call chain), and the
    qubit is not about to be used as a control (x ... x around a control is a
    pattern). Like QD05, a line is reported only when none of its gates act.
    """
    state: Dict[int, Optional[str]] = {}
    setter: Dict[int, Optional[int]] = {}
    idle: Dict[int, List[tuple]] = {}
    acting: Set[int] = set()

    def next_use_is_control(start: int, qubit: int) -> bool:
        for later in range(start + 1, len(program)):
            if qubit in program[later].qubits:
                return qubit in _controls(program[later])
        return False

    for idx in range(len(program)):
        instruction = program[idx]
        name, qubits = instruction.name, list(instruction.qubits)
        conditioned = instruction.metadata.get("condition") is not None
        if len(qubits) == 1 and not conditioned and name not in ("barrier", "delay"):
            q = qubits[0]
            current = state.get(q, "+Z")
            source = setter.get(q)
            axis = _FIXES.get(name)
            if (current and axis and current[1] == axis and source is not None
                    and _call_chain(program[source]) == _call_chain(instruction)
                    and not next_use_is_control(idx, q)):
                idle.setdefault(_line(instruction), []).append((name, q, current, source, idx))
                continue                       # no effect: the state stays
            acting.add(_line(instruction))
            if name == "reset":
                state[q], setter[q] = "+Z", idx
            elif name in _CLIFFORD and current:
                state[q], setter[q] = _apply(name, current), idx
            elif not (name in _DIAGONAL and current and current[1] == "Z"):
                state[q], setter[q] = None, None
        elif name not in ("barrier", "delay"):
            for q in qubits:
                state[q], setter[q] = None, None
    findings = []
    for line, instances in idle.items():
        if not line or line in acting:
            continue
        name, q, current, source, _ = instances[0]
        findings.append(_finding(
            "QD10", line,
            f"{name} on qubit {q} has no effect: the {program[source].name} at line "
            f"{_line(program[source])} left it in {_KET[current]}, which {name} does not change.",
            "A different gate was probably meant (z turns |+> into |->), or the two are in the "
            "wrong order (x before h).",
            nodes=[source] + [i for *_, i in instances],
        ))
    return findings


def _apply(gate: str, stabilizer: str) -> str:
    """A single-qubit Clifford gate's action on a Pauli stabilizer such as "-X"."""
    sign, axis = stabilizer[0], stabilizer[1]
    mapped = _CLIFFORD[gate][axis]
    flip = (sign == "-") != (mapped[0] == "-")
    return ("-" if flip else "+") + mapped[1]


# QD11: quantum_info calls that read the state or operator of the circuit they are
# given, and what Qiskit raises on a measure or reset in it. Every one raises on a
# measure; Operators raise on a reset too, Statevector applies it as a random collapse,
# and a density matrix (which DensityMatrix, evolve and from_instruction may be) applies
# it exactly, so a reset is only judged for the first two.
_STATE_CALLS = frozenset({"Statevector", "DensityMatrix", "Operator", "from_instruction",
                          "from_circuit", "evolve"})
_RAISES = {
    ("measure", "Operator"): "Cannot apply operation with classical bits: measure",
    ("measure", "from_circuit"): "Cannot apply operation with classical bits: measure",
    ("reset", "Operator"): "Cannot apply Operation: reset",
    ("reset", "from_circuit"): "Cannot apply Operation: reset",
}
_RAISES_ON_MEASURE = "Cannot apply instruction with classical bits: measure"
_COLLAPSES_ON_RESET = frozenset({"Statevector"})
# Old-style backends that return a simulated state: execute(qc, statevector backend).
_STATE_BACKENDS = ("statevector_simulator", "unitary_simulator", "StatevectorSimulator",
                   "UnitarySimulator")
_STATE_GETTERS = frozenset({"get_statevector", "get_unitary"})
# What says a circuit is also sampled, so its measurements are wanted.
_COUNT_READS = frozenset({"get_counts", "get_memory", "get_probabilities"})
_SAMPLER_CALLS = frozenset({"Sampler", "SamplerV2", "StatevectorSampler", "BackendSamplerV2"})
_SAVES = frozenset({"save_statevector", "save_unitary", "save_density_matrix"})


@functools.lru_cache(maxsize=8)
def _state_reads(source: str) -> Optional[Dict[str, Any]]:
    """What QD11 needs from the source, read once: who reads which circuit's state.

    ``readers``: ``(line, circuit name, reader, function)`` for each call given a
    circuit variable that reads its state (``function`` is the (first, last) lines of
    the function the call is in, None at module level). ``receivers``: line -> the
    variables measured or reset on that line, with their function. ``stores``: name
    -> the lines that rebind it. ``excused``: circuits that are also sampled for
    counts or have their final measurements removed, or every circuit when the file
    saves a simulator's state itself.
    """
    import ast
    from .. import quick_checks as lint
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    functions = [(fn.lineno, fn.end_lineno or fn.lineno) for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def scope(line: int):
        inside = [span for span in functions if span[0] <= line <= span[1]]
        return max(inside) if inside else None          # the innermost one

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    callees = {lint._callee_name(call.func) for call in calls}
    texts = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | callees
    state_backend = any(b in names or any(b in text for text in texts) for b in _STATE_BACKENDS)
    reads_counts = bool(callees & _COUNT_READS)
    readers, receivers, stores, excused, sampled = [], {}, {}, set(), set()
    for call in calls:
        callee = lint._callee_name(call.func)
        owner = call.func.value.id if (isinstance(call.func, ast.Attribute)
                                       and isinstance(call.func.value, ast.Name)) else ""
        if owner and callee in ("measure", "measure_all", "measure_active", "reset"):
            receivers.setdefault(call.lineno, set()).add((owner, scope(call.lineno)))
        if owner and callee == "remove_final_measurements":
            excused.add(owner)
        given = [arg.id for arg in call.args if isinstance(arg, ast.Name) and arg.id != owner]
        if callee in _STATE_CALLS and given:
            readers += [(call.lineno, name, callee, scope(call.lineno)) for name in given[:1]]
        everything = [n.id for n in lint._flat_names(list(call.args)
                                                    + [k.value for k in call.keywords])]
        if callee in _SAMPLER_CALLS or (callee == "run" and "sampler" in owner.lower()):
            sampled.update(everything)
        if callee in ("execute", "run") and "estimator" not in owner.lower():
            if reads_counts:
                sampled.update(everything)
            elif state_backend and callees & _STATE_GETTERS and given:
                readers.append((call.lineno, given[0], "simulator", scope(call.lineno)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores.setdefault(node.id, []).append(node.lineno)
    if callees & _SAVES:
        excused.add("*")
    return {"readers": readers, "receivers": receivers, "stores": stores,
            "excused": excused | sampled}


def _measured_state_reads(program, source: str) -> List[Dict[str, Any]]:
    """QD11: a measure or reset in a circuit whose state is then read.

    The instruction must be written on the circuit variable (qc.measure_all(), not in a
    helper handed it) in the same function as the call that reads that variable, on an
    earlier line, with no rebinding in between. Not judged when the circuit is also
    sampled for counts (its measurements are wanted, and the state may be read before
    them), has its final measurements removed, or the file saves a simulator's state
    itself (save_statevector). Statevector(qc), Operator(qc) and evolve raise on a
    measurement (Operator on a reset too): errors. A statevector simulator, or a reset
    under Statevector, returns a collapsed state instead: warnings.
    """
    reads = _state_reads(source)
    if not reads or "*" in reads["excused"]:
        return []
    by_line: Dict[int, List[int]] = {}
    for idx in range(len(program)):
        instruction = program[idx]
        if (instruction.name in ("measure", "reset") and _line(instruction)
                and instruction.metadata.get("condition") is None
                and not instruction.metadata.get("guards")):
            by_line.setdefault(_line(instruction), []).append(idx)
    findings = []
    for line, indices in sorted(by_line.items()):
        kind = program[indices[0]].name
        later = sorted(
            (read_line, name, reader) for read_line, name, reader, where in reads["readers"]
            if read_line > line and (name, where) in reads["receivers"].get(line, set())
            and name not in reads["excused"]
            and not any(line < store <= read_line for store in reads["stores"].get(name, []))
            and (kind == "measure" or reader == "simulator" or (kind, reader) in _RAISES
                 or reader in _COLLAPSES_ON_RESET))
        if not later:
            continue
        read_line, name, reader = later[0]
        raised = _RAISES.get((kind, reader), _RAISES_ON_MEASURE if kind == "measure" else None)
        if reader == "simulator":
            message = (f"The statevector simulator run at line {read_line} applies this {kind}, so "
                       f"the state it returns is collapsed (one random outcome), not the state "
                       f"{name} prepares.")
            severity = "warning"
        elif raised:
            message = (f"{reader}({name}) at line {read_line} reads the state of {name}, which has "
                       f"this {kind}: Qiskit raises QiskitError '{raised}'.")
            severity = "error"
        else:
            message = (f"{reader}({name}) at line {read_line} applies this reset as a random "
                       "collapse, so the state it gives changes from run to run.")
            severity = "warning"
        finding = _finding(
            "QD11", line, message,
            f"Read the state of the circuit without its measurements "
            f"({name}.remove_final_measurements(inplace=False)), or read it before measuring; "
            "sample counts instead if the measurement is what you want.",
            nodes=indices,
        )
        finding["severity"] = severity
        finding["lines"] = [line, read_line]
        findings.append(finding)
    return findings


@functools.lru_cache(maxsize=8)
def _measure_all_lines(source: str) -> FrozenSet[int]:
    """Lines of measure_all() calls that add classical bits of their own (all but
    measure_all(add_bits=False))."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    found: Set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "measure_all"):
            add_bits = next((k.value for k in node.keywords if k.arg == "add_bits"), None)
            if not (isinstance(add_bits, ast.Constant) and add_bits.value is False):
                found.add(node.lineno)
    return frozenset(found)


def _unwritten_clbits(program, source: str) -> List[Dict[str, Any]]:
    """QD12: declared classical bits that no instruction writes or reads, in a circuit
    that measures. Any instruction naming a bit (a measurement, a store, a block,
    whatever it is) counts as using it, and so does a condition.

    When measure_all() added bits of its own, the declared ones are where the results
    were meant to go: a warning at the measure_all(). Otherwise the bits are only spare
    (a register sized for a device, say): information, at the circuit. Not judged when
    a measurement is made by a function handed its circuit: the caller declared the
    bits (``_lines_given_a_circuit``).
    """
    from .. import quick_checks as lint
    measures = [i for i in range(len(program)) if program[i].name == "measure"]
    if not measures or not program.num_clbits:
        return []
    given = _lines_given_a_circuit(source)
    if any(_line(program[i]) in given for i in measures):
        return []
    used: Set[int] = set()
    for idx in range(len(program)):
        used.update(program[idx].clbits)
        used.update(program[idx].metadata.get("condition_clbits") or [])
    unwritten = sorted(set(range(program.num_clbits)) - used)
    if not unwritten:
        return []
    circuit = lint.circuit_line(source) or _line(program[measures[0]])
    added = [i for i in measures if _line(program[i]) in _measure_all_lines(source)]
    if added:
        line = _line(program[added[0]])
        bits = sorted({c for i in added for c in program[i].clbits})
        finding = _finding(
            "QD12", line,
            f"measure_all() adds its own {len(bits)} classical bit(s) and measures into those, "
            f"so the declared classical bit(s) {unwritten} are never written and stay 0.",
            "Pass add_bits=False to measure into the bits the circuit declares, or use "
            "measure(qc.qubits, qc.clbits); or declare the circuit without classical bits.",
            nodes=added,
        )
        finding["lines"] = sorted({line, circuit})
        return [finding]
    finding = _finding(
        "QD12", circuit,
        f"Classical bit(s) {unwritten} are declared, but nothing writes them: they read 0 in "
        "every result.",
        "Shrink the classical register if they are spare, or check that every qubit you want "
        "is measured into its own bit.",
    )
    finding["severity"] = "info"
    return [finding]


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
    # The context builds the graph under its rule profile, or hands over the one
    # the web page built for the same program.
    qdg = context.qdg()
    return _merge(_dead_gates(program, qdg) + _overwritten_measurements(program)
                  + _constant_measurements(program, context.source) + _unset_controls(program, context.source)
                  + _no_measurement(program, context.source)
                  + _idle_qubits(program, context.source) + _cancelling_pairs(program, context.source)
                  + _conditions_never_set(program) + _no_effect_gates(program)
                  + _measured_state_reads(program, context.source)
                  + _unwritten_clbits(program, context.source))


LINTER = Linter(
    id="dependence",
    label="Dependence checks",
    description="Quantum mistakes in the circuit the static front end rebuilds (loops "
                "unrolled, helpers followed) and its dependence graph: dead gates, overwritten "
                "or constant measurements, controls never set, missing measurements, idle "
                "qubits, cancelling pairs, conditions never set, gates with no effect, states "
                "read from circuits that measure, classical bits never written.",
    rules=RULES,
    run=run,
    languages=("python", "qasm"),
    default=True,
)
