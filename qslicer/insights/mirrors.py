"""
Mirrored gates: a gate and a later gate that is its inverse on the same qubits.

This finds the compute/uncompute shape ``U ... V ... U†`` so the UI can draw a
link between the two ends. It is a visual aid only: it proves nothing about the
two gates cancelling, since whatever sits between them is not examined.

Matching is bracket matching per wire. Each qubit keeps a stack of unmatched
instruction indices; a gate closes the nearest open inverse on its first
qubit's stack (within ``max_span`` entries) when that partner is still open on
every one of its qubits. Arcs therefore never cross on a wire.
"""

import numbers
from typing import Any, Dict, List, NamedTuple, Optional, Set

from qslicer.gates import SELF_INVERSE
from qslicer.ir.qprogram import QuantumProgram

__all__ = ["MirrorPair", "find_mirror_pairs", "is_inverse"]


class MirrorPair(NamedTuple):
    """Two mirrored instructions, by index, and how deeply other pairs nest inside."""

    first: int
    second: int
    depth: int


_TRANSPARENT = frozenset({"barrier", "delay", "global_phase"})

_BLOCKING_NAMES = frozenset({
    "measure", "reset", "initialize", "state_preparation",
    "if_else", "while_loop", "for_loop", "switch_case", "store", "c_if",
})

_DAGGER_PAIRS = frozenset({
    ("s", "sdg"), ("sdg", "s"),
    ("t", "tdg"), ("tdg", "t"),
    ("sx", "sxdg"), ("sxdg", "sx"),
})

_ONE_ANGLE = frozenset({
    "rx", "ry", "rz", "p", "u1", "crx", "cry", "crz", "cp", "cu1",
    "rxx", "ryy", "rzz", "rzx", "mcp", "mcphase", "mcrx", "mcry", "mcrz",
})

_ANGLE_TOLERANCE = 1e-9


def _is_blocker(inst) -> bool:
    """Whether ``inst`` ends every open mirror on the wires it touches."""
    if inst.name in _BLOCKING_NAMES or not inst.qubits:
        return True
    meta = inst.metadata or {}
    if meta.get("condition") is not None:
        return True
    if meta.get("condition_clbits") or meta.get("guards"):
        return True
    return any(hasattr(param, "data") for param in (inst.params or ()))


def _params_equal(a: List[Any], b: List[Any]) -> bool:
    """List equality, falling back to ``repr`` when ``==`` is not a plain bool."""
    try:
        result = a == b
        if isinstance(result, bool):
            return result
    except Exception:
        pass
    try:
        return repr(a) == repr(b)
    except Exception:
        return False


def _real(value: Any) -> Optional[float]:
    """``value`` as a float when it is a concrete real number, else None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        return float(value)
    except Exception:
        return None


def is_inverse(p, g) -> bool:
    """Whether instruction ``g`` undoes instruction ``p`` by name and params (qubits not checked)."""
    p_params, g_params = list(p.params or []), list(g.params or [])
    if p.name == g.name and p.name in SELF_INVERSE:
        return _params_equal(p_params, g_params)
    if (p.name, g.name) in _DAGGER_PAIRS:
        return not p_params and not g_params
    if p.name == g.name and p.name in _ONE_ANGLE:
        if len(p_params) != 1 or len(g_params) != 1:
            return False
        a, b = _real(p_params[0]), _real(g_params[0])
        return a is not None and b is not None and abs(a + b) <= _ANGLE_TOLERANCE
    return False


def find_mirror_pairs(program: QuantumProgram, max_span: int = 64) -> List[MirrorPair]:
    """
    Every mirrored pair in ``program``, sorted by the first instruction's index.

    ``max_span`` bounds how many open entries on the first qubit's stack are
    searched for a partner, which keeps the walk linear. ``depth`` is 0 for a
    pair with no other pair strictly inside it on any of its wires, otherwise
    one more than the deepest pair inside.
    """
    # Per qubit: the stack of open indices, the same indices as a set, and for
    # each open index the deepest pair closed above it while it was open (-1:
    # none). That depth is kept on the top entry only and handed down on pop.
    stacks: Dict[int, List[int]] = {}
    members: Dict[int, Set[int]] = {}
    inner: Dict[int, Dict[int, int]] = {}
    pairs: List[MirrorPair] = []

    def clear(q: int) -> None:
        stacks.pop(q, None)
        members.pop(q, None)
        inner.pop(q, None)

    for index in range(len(program)):
        inst = program[index]
        if inst.name in _TRANSPARENT:
            continue
        qubits = list(inst.qubits)
        if _is_blocker(inst):
            for q in qubits:
                clear(q)
            continue

        partner = None
        stack = stacks.get(qubits[0], ())
        lowest = max(len(stack) - max_span, 0)
        for pos in range(len(stack) - 1, lowest - 1, -1):
            candidate = program[stack[pos]]
            if list(candidate.qubits) == qubits and is_inverse(candidate, inst):
                partner = stack[pos]
                break
        if partner is not None and any(partner not in members.get(q, ()) for q in qubits):
            partner = None

        if partner is None:
            for q in qubits:
                stacks.setdefault(q, []).append(index)
                members.setdefault(q, set()).add(index)
                inner.setdefault(q, {})[index] = -1
            continue

        deepest = -1
        for q in qubits:
            stack, member, depths = stacks[q], members[q], inner[q]
            while True:
                top = stack.pop()
                member.discard(top)
                deepest = max(deepest, depths.pop(top))
                if top == partner:
                    break
        depth = deepest + 1
        for q in qubits:
            stack = stacks[q]
            if stack:
                depths = inner[q]
                depths[stack[-1]] = max(depths[stack[-1]], depth)
        pairs.append(MirrorPair(partner, index, depth))

    pairs.sort(key=lambda pair: pair.first)
    return pairs
