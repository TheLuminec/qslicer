"""
What can be proved about each qubit, gate by gate, without simulating.

One forward pass over a program keeps two things per qubit:

* its **state**, while that is a known single-qubit Pauli eigenstate: ``"+Z"``
  (|0>), ``"-Z"`` (|1>), ``"+X"`` (|+>), ``"-X"`` (|->), ``"+Y"``, ``"-Y"``.
  ``None`` means "not known", which is where every qubit ends up once it may be
  entangled or has been through a gate this pass does not follow;
* its **group**: the qubits and recorded measurements whose outcomes may be
  correlated with it. Groups only ever say "may": they are an
  over-approximation, never a proof of entanglement.

From those it reports, per instruction:

* ``kickback``: a controlled gate whose target is an eigenstate of the gate it
  applies, so the target is left alone and the phase lands on the controls;
* ``no_effect``: a controlled gate that provably does nothing (a control known
  to be |0>, or a target the gate leaves exactly as it is);
* ``predetermined``: a measurement whose outcome is known before running;
* ``correlation(idx)``: for a measurement, the other measurements it may be
  correlated with and the unmeasured qubits it may still be entangled with.

The rules a multi-qubit gate follows (a control known to be |0> switches the
gate off, controls known to be |1> leave the bare gate on the target, a
diagonal gate ignores operands known to be |0> or |1>, a swap exchanges the
wires) are what keep a qubit in a known basis state out of a group it cannot
really join. Everything else groups all the qubits it touches.
"""

from typing import Dict, List, NamedTuple, Optional, Set

from ..ir.qprogram import QuantumProgram

__all__ = ["Correlation", "QubitStates", "track_states"]

# Single-qubit Clifford gates as maps of a qubit's stabilizer axis.
_CLIFFORD = {
    "h": {"X": "+Z", "Z": "+X", "Y": "-Y"},
    "x": {"X": "+X", "Y": "-Y", "Z": "-Z"},
    "y": {"X": "-X", "Y": "+Y", "Z": "-Z"},
    "z": {"X": "-X", "Y": "-Y", "Z": "+Z"},
    "s": {"X": "+Y", "Y": "-X", "Z": "+Z"},
    "sdg": {"X": "-Y", "Y": "+X", "Z": "+Z"},
    "id": {"X": "+X", "Y": "+Y", "Z": "+Z"},
    "i": {"X": "+X", "Y": "+Y", "Z": "+Z"},
}
# Gates that keep the eigenstates of one axis (up to a phase) and nothing else
# this pass can follow: diagonal ones keep Z, x-rotations keep X, and so on.
_KEEPS_AXIS = {
    "t": "Z", "tdg": "Z", "p": "Z", "rz": "Z", "u1": "Z",
    "sx": "X", "sxdg": "X", "rx": "X",
    "ry": "Y",
}
# Of those, the ones whose eigenvalues on that axis differ, so a control picks
# up a phase when the target is such an eigenstate (kickback).
_AXIS_OF = {"x": "X", "y": "Y", "sx": "X", "sxdg": "X", "rx": "X", "ry": "Y"}

# Controlled gates that are not diagonal: how many leading qubits are controls
# (None: all but the last) and the gate the target gets.
_CONTROLLED = {
    "cx": (1, "x"), "ccx": (2, "x"), "c3x": (3, "x"), "c4x": (4, "x"),
    "mcx": (None, "x"), "mcx_gray": (None, "x"), "mcx_recursive": (None, "x"),
    "mcx_vchain": (None, "x"),
    "cy": (1, "y"), "ch": (1, "h"), "csx": (1, "sx"),
    "crx": (1, "rx"), "cry": (1, "ry"), "mcrx": (None, "rx"), "mcry": (None, "ry"),
    "cu": (1, None), "cu3": (1, None),
}
# Diagonal gates that only put a phase on |1...1>: every operand acts as a
# control, and the name is the gate left on a single remaining operand.
_PHASE_ON_ALL_ONES = {
    "cz": "z", "ccz": "z", "cs": "s", "csdg": "sdg",
    "cp": "p", "cu1": "p", "mcp": "p", "mcphase": "p",
}
# Diagonal gates that act whatever the operands are, so only operands known to
# be |0> or |1> drop out (leaving a phase gate on the rest).
_DIAGONAL_ROTATIONS = frozenset({"rzz", "crz", "mcrz"})

_TRANSPARENT = frozenset({"barrier", "delay", "global_phase"})
_RESETS = frozenset({"reset"})
# Prepare a joint state on their qubits; initialize resets them first.
_PREPARES = {"initialize": True, "state_preparation": False}


class Correlation(NamedTuple):
    """What a measurement's outcome may be tied to."""

    measurements: List[int]     # other measurement nodes it may be correlated with
    qubits: List[int]           # the qubits those measurements read
    unmeasured: List[int]       # qubits never measured that it may still be entangled with
    predetermined: Optional[int]  # 0 or 1 when the outcome is known before running


class _Group:
    __slots__ = ("qubits", "events")

    def __init__(self) -> None:
        self.qubits: Set[int] = set()
        self.events: Set[int] = set()


class QubitStates:
    """The result of ``track_states``; see the module docstring."""

    def __init__(self) -> None:
        self.kickback: Dict[int, List[int]] = {}
        self.no_effect: Dict[int, str] = {}
        self.predetermined: Dict[int, int] = {}
        self.final_states: Dict[int, Optional[str]] = {}
        # Qubits that may be correlated with each other at the end, as a partition.
        self.final_groups: List[List[int]] = []
        self._event_group: Dict[int, _Group] = {}
        self._event_qubit: Dict[int, int] = {}
        self._measures: Set[int] = set()

    def correlation(self, idx: int) -> Optional[Correlation]:
        """What measurement ``idx`` may be correlated with; None if ``idx`` is
        not a measurement this pass followed."""
        if idx in self.predetermined:
            return Correlation([], [], [], self.predetermined[idx])
        group = self._event_group.get(idx)
        if group is None:
            return None
        own = self._event_qubit[idx]
        others = sorted(e for e in group.events if e != idx and e in self._measures)
        measured = {self._event_qubit[e] for e in group.events if e in self._event_qubit}
        return Correlation(
            others,
            sorted({self._event_qubit[e] for e in others} - {own}),
            sorted(q for q in group.qubits if q not in measured),
            None,
        )


class _Tracker:
    def __init__(self, program: QuantumProgram) -> None:
        self.program = program
        self.state: Dict[int, Optional[str]] = {}
        self.group: Dict[int, _Group] = {}
        self.event_group: Dict[int, _Group] = {}
        self.bit_event: Dict[int, Optional[int]] = {}
        self.result = QubitStates()
        self.merged = False

    # -- groups -------------------------------------------------------------

    def group_of(self, qubit: int) -> _Group:
        group = self.group.get(qubit)
        if group is None:
            group = self.group[qubit] = _Group()
            group.qubits.add(qubit)
        return group

    def union(self, a: _Group, b: _Group) -> _Group:
        if a is b:
            return a
        if len(a.qubits) + len(a.events) < len(b.qubits) + len(b.events):
            a, b = b, a
        for qubit in b.qubits:
            self.group[qubit] = a
        for event in b.events:
            self.event_group[event] = a
        a.qubits |= b.qubits
        a.events |= b.events
        return a

    def merge(self, qubits: List[int]) -> None:
        """These qubits may now be correlated; none of them is in a known state."""
        if not qubits:
            return
        group = self.group_of(qubits[0])
        for qubit in qubits[1:]:
            group = self.union(group, self.group_of(qubit))
        for qubit in qubits:
            self.state[qubit] = None
        self.merged = True

    def leave(self, qubit: int) -> None:
        """Take a qubit out of its group (its earlier measurements stay in it)."""
        group = self.group.pop(qubit, None)
        if group is not None:
            group.qubits.discard(qubit)

    def add_event(self, idx: int, group: _Group) -> None:
        group.events.add(idx)
        self.event_group[idx] = group

    # -- single-qubit gates -------------------------------------------------

    def get(self, qubit: int) -> Optional[str]:
        return self.state.get(qubit, "+Z")

    def single(self, qubit: int, name: Optional[str]) -> None:
        current = self.get(qubit)
        if current is None:
            return
        table = _CLIFFORD.get(name)
        if table is not None:
            mapped = table[current[1]]
            flip = (current[0] == "-") != (mapped[0] == "-")
            self.state[qubit] = ("-" if flip else "+") + mapped[1]
        elif _KEEPS_AXIS.get(name) != current[1]:
            self.state[qubit] = None

    # -- multi-qubit gates --------------------------------------------------

    def controlled(self, idx: int, controls: List[int], target: int,
                   base: Optional[str]) -> None:
        states = [self.get(c) for c in controls]
        if "+Z" in states:
            self.result.no_effect[idx] = "a control is |0>, so the gate never acts"
            return
        open_controls = [c for c, s in zip(controls, states) if s != "-Z"]
        if not open_controls:
            self.single(target, base)       # every control is |1>: the bare gate
            return
        current = self.get(target)
        axis = _AXIS_OF.get(base)
        if current is not None and axis == current[1]:
            # The target is an eigenstate of the gate, so it stays as it is.
            if base in ("x", "y", "sx", "sxdg") and current[0] == "+":
                self.result.no_effect[idx] = "the gate leaves its target's state as it is"
                return
            self.result.kickback[idx] = list(open_controls)
            self.phase_on(open_controls, "z" if base in ("x", "y") else "p")
            return
        self.merge(open_controls + [target])

    def phase_on(self, qubits: List[int], residual: str) -> None:
        """A diagonal gate over ``qubits``: a phase gate on one, grouping on more."""
        if len(qubits) == 1:
            self.single(qubits[0], residual)
        elif qubits:
            self.merge(qubits)

    def phase_on_all_ones(self, idx: int, qubits: List[int], residual: str) -> None:
        states = [self.get(q) for q in qubits]
        if "+Z" in states:
            self.result.no_effect[idx] = "an operand is |0>, so no phase is applied"
            return
        self.phase_on([q for q, s in zip(qubits, states) if s != "-Z"], residual)

    def diagonal_rotation(self, idx: int, name: str, qubits: List[int]) -> None:
        if name != "rzz":
            controls = qubits[:-1]
            states = [self.get(c) for c in controls]
            if "+Z" in states:
                self.result.no_effect[idx] = "a control is |0>, so the gate never acts"
                return
            qubits = [c for c, s in zip(controls, states) if s != "-Z"] + qubits[-1:]
        rest = [q for q in qubits if (self.get(q) or "  ")[1] != "Z"]
        self.phase_on(rest, "p")

    def swap(self, a: int, b: int) -> None:
        self.state[a], self.state[b] = self.get(b), self.get(a)
        group_a, group_b = self.group.pop(a, None), self.group.pop(b, None)
        for group, old, new in ((group_a, a, b), (group_b, b, a)):
            if group is not None:
                group.qubits.discard(old)
        for group, new in ((group_a, b), (group_b, a)):
            if group is not None:
                group.qubits.add(new)
                self.group[new] = group

    # -- one instruction ----------------------------------------------------

    def gate(self, idx: int, name: str, qubits: List[int]) -> None:
        if name in _CONTROLLED and len(qubits) >= 2:
            count, base = _CONTROLLED[name]
            count = len(qubits) - 1 if count is None else count
            if count == len(qubits) - 1:
                self.controlled(idx, qubits[:-1], qubits[-1], base)
                return
        if name in _PHASE_ON_ALL_ONES and len(qubits) >= 2:
            self.phase_on_all_ones(idx, qubits, _PHASE_ON_ALL_ONES[name])
        elif name in _DIAGONAL_ROTATIONS and len(qubits) >= 2:
            self.diagonal_rotation(idx, name, qubits)
        elif name == "swap" and len(qubits) == 2:
            self.swap(*qubits)
        elif name == "cswap" and len(qubits) == 3:
            control = self.get(qubits[0])
            if control == "+Z":
                self.result.no_effect[idx] = "a control is |0>, so the gate never acts"
            elif control == "-Z":
                self.swap(qubits[1], qubits[2])
            elif self.get(qubits[1]) is None or self.get(qubits[1]) != self.get(qubits[2]):
                self.merge(qubits)
        elif len(qubits) == 1:
            self.single(qubits[0], name)
        else:
            self.merge(qubits)

    def condition_events(self, instruction) -> List[int]:
        """The recorded measurements an instruction's classical conditions read."""
        bits = list(instruction.metadata.get("condition_clbits") or [])
        for predicate, _ in instruction.metadata.get("guards") or ():
            bits += self.program[predicate].metadata.get("condition_clbits") or []
        events = []
        for bit in bits:
            event = self.bit_event.get(bit)
            if event is not None and event not in events:
                events.append(event)
        return events

    def step(self, idx: int) -> None:
        instruction = self.program[idx]
        name, qubits = instruction.name, list(instruction.qubits)
        if name in _TRANSPARENT:
            return
        conditional = instruction.metadata.get("condition") is not None
        opaque = any(hasattr(p, "data") for p in instruction.params or [])
        before = {q: self.get(q) for q in qubits}
        self.merged = False
        event = None

        if opaque:
            self.merge(qubits)
            for qubit in qubits:
                self.state[qubit] = None
            if instruction.clbits:
                event = idx
        elif name == "measure":
            for qubit in qubits:
                current = self.get(qubit)
                if current in ("+Z", "-Z") and not conditional:
                    self.result.predetermined[idx] = 0 if current == "+Z" else 1
                else:
                    event = idx
                    self.result._event_qubit[idx] = qubit
                    self.result._measures.add(idx)
                    if current not in ("+Z", "-Z"):
                        self.state[qubit] = None
        elif name in _RESETS:
            for qubit in qubits:
                if not conditional:
                    self.leave(qubit)
                self.state[qubit] = "+Z"
        elif name in _PREPARES:
            if _PREPARES[name] and not conditional:
                for qubit in qubits:
                    self.leave(qubit)
            self.merge(qubits)
            for qubit in qubits:
                self.state[qubit] = None
        elif name == "store" or not qubits:
            if instruction.clbits and name != "if_else":
                event = idx
        else:
            self.gate(idx, name, qubits)

        if conditional:
            self.result.no_effect.pop(idx, None)
            for qubit in qubits:
                if self.get(qubit) != before[qubit]:
                    self.state[qubit] = None
        changed = self.merged or any(self.get(q) != before[q] for q in qubits)

        sources = self.condition_events(instruction)
        if event is not None:
            group = self.group_of(qubits[0]) if qubits else _Group()
            self.add_event(event, group)
            for source in sources:
                group = self.union(group, self.event_group[source])
        elif sources and changed:
            group = self.group_of(qubits[0])
            for qubit in qubits[1:]:
                group = self.union(group, self.group_of(qubit))
            for source in sources:
                group = self.union(group, self.event_group[source])
        for bit in instruction.clbits:
            self.bit_event[bit] = event


def track_states(program: QuantumProgram) -> QubitStates:
    """Follow every qubit of ``program`` through it; see the module docstring."""
    tracker = _Tracker(program)
    for idx in range(len(program)):
        tracker.step(idx)
    result = tracker.result
    result._event_group = tracker.event_group
    result.final_states = {q: tracker.get(q) for q in range(program.num_qubits)}
    seen: Dict[int, List[int]] = {}
    for qubit in range(program.num_qubits):
        seen.setdefault(id(tracker.group_of(qubit)), []).append(qubit)
    result.final_groups = sorted(seen.values())
    return result
