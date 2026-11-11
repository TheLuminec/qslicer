from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Any, Dict, Hashable, Iterable, List, Tuple
import networkx as nx

from ..ir.qprogram import QuantumProgram
from ..ir.qinstruction import QuantumInstruction
from .. import gates

_MEASUREMENT_GATES = frozenset({"measure", "measure_all"})

_BASELINE_ENTANGLING_GATES = frozenset({
    "cx", "cy", "cz", "ch", "crx", "cry", "crz", "cp", "csx",
    "ccx", "c3x", "c4x", "dcx", "ecr", "iswap", "rzz", "rxx", "ryy", "cswap",
})

# Diagonal in the computational basis: they change phases only. The one list is
# qslicer.gates.PHASE, which the gate cards and the "phase only" label also use.
_PHASE_GATES = gates.PHASE

# Instructions that span qubits without reading or writing them: they take part
# in no dependence, so wires a barrier spans stay separate in a slice.
_NO_DEPENDENCE = frozenset({"barrier", "delay"})

# Instructions that put a qubit into a fixed state whatever came before, so
# nothing earlier on the wire reaches past them (initialize resets first).
_WIRE_RESETS = frozenset({"reset", "initialize"})

class DependencyType(Enum):
    ENTANGLEMENT = auto()
    MEASUREMENT = auto()
    DATA_CLASSICAL = auto()
    CONTROL_CLASSICAL = auto()
    PHASE = auto()
    DATA_QUBIT = auto()


class DependencyRule(ABC):
    """Rule interface for emitting dependency edges into a QDG."""

    name = "dependency_rule"

    @abstractmethod
    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        """Populate dependency edges for this rule."""

    def add_dependency(
        self,
        graph: nx.MultiDiGraph,
        source_idx: int,
        target_idx: int,
        dependency: DependencyType,
    ) -> None:
        """Add an edge with both dependency type and rule provenance."""
        graph.add_edge(
            source_idx,
            target_idx,
            dependency=dependency,
            rule=self.name,
        )

def _condition_clbits(instruction: QuantumInstruction) -> Iterable[int]:
    """Return the classical bits read by a classically-conditioned instruction."""
    return instruction.metadata.get("condition_clbits", [])


def _severs_wire(instruction: QuantumInstruction) -> bool:
    """Whether an instruction cuts its wires off from what came before: a reset
    that always runs. One under a classical condition only may run, so the
    earlier state can still get past it."""
    return (instruction.name in _WIRE_RESETS
            and instruction.metadata.get("condition") is None
            and not instruction.metadata.get("guards"))



def _guards(instruction: QuantumInstruction) -> Tuple[Tuple[int, int], ...]:
    """The ``(predicate index, branch)`` pairs an instruction sits under,
    outermost first: empty outside every if_test or switch block."""
    return tuple(instruction.metadata.get("guards") or ())


class ReachingDefs:
    """Which instructions may have last written each wire or bit, branch by branch.

    Outside a conditional a write replaces what was there. Inside one it does
    so for the rest of its branch only: the next branch starts again from what
    reached the block, and after the block every branch's writes reach. What
    reached the block still reaches past it too, unless the branches cover
    every case (the predicate's ``arms``: 2 for an if_test, the number of
    cases of a switch with a default) and each of them wrote.

    Call ``enter`` with each instruction's guards before reading or writing.
    """

    def __init__(self, program: QuantumProgram = None) -> None:
        self._program = program
        self._defs: Dict[Hashable, Tuple[int, ...]] = {}
        # One frame per open block: [predicate, branch, saved, done, written].
        # ``saved`` is what reached the block for every key written inside it,
        # ``written`` the keys the current branch wrote, and ``done`` what each
        # finished branch left for the keys it wrote.
        self._frames: List[List[Any]] = []

    def _arms(self, predicate: int) -> Any:
        if self._program is None:
            return 2
        return self._program[predicate].metadata.get("arms", 2)

    def enter(self, guards: Tuple[Tuple[int, int], ...]) -> None:
        """Open, switch and close blocks so the open ones are exactly ``guards``."""
        frames = self._frames
        if not frames and not guards:
            return
        keep = 0
        while (keep < len(frames) and keep < len(guards)
               and frames[keep][0] == guards[keep][0]
               and frames[keep][1] == guards[keep][1]):
            keep += 1
        switching = (keep < len(frames) and keep < len(guards)
                     and frames[keep][0] == guards[keep][0])
        while len(frames) > keep + (1 if switching else 0):
            self._close()
        if switching:
            self._finish_branch(frames[keep])
            frames[keep][1] = guards[keep][1]
            keep += 1
        for predicate, branch in guards[keep:]:
            frames.append([predicate, branch, {}, [], set()])

    def _finish_branch(self, frame: List[Any]) -> None:
        saved, written = frame[2], frame[4]
        frame[3].append({key: self._defs.get(key, ()) for key in written})
        for key in written:
            self._defs[key] = saved[key]
        frame[4] = set()

    def _close(self) -> None:
        frame = self._frames.pop()
        self._finish_branch(frame)
        predicate, _, saved, done, _ = frame
        arms = self._arms(predicate)
        covered = arms is not None and len(done) >= arms
        parent = self._frames[-1] if self._frames else None
        for key, before in saved.items():
            reaching = () if covered else before
            for branch in done:
                reaching += branch.get(key, before)
            if parent is not None:
                parent[2].setdefault(key, before)
                parent[4].add(key)
            self._defs[key] = tuple(dict.fromkeys(reaching))

    def reaching(self, key: Hashable) -> Tuple[int, ...]:
        """The instructions whose write of ``key`` may be the latest."""
        return self._defs.get(key, ())

    def write(self, key: Hashable, idx: int) -> None:
        """Record that instruction ``idx`` writes ``key``."""
        if self._frames:
            frame = self._frames[-1]
            frame[2].setdefault(key, self._defs.get(key, ()))
            frame[4].add(key)
        self._defs[key] = (idx,)
