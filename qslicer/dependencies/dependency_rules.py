from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Iterable, FrozenSet, Dict
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
