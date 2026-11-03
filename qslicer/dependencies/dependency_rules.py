from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Iterable, FrozenSet, Dict
import networkx as nx

from ..ir.qprogram import QuantumProgram
from ..ir.qinstruction import QuantumInstruction

_MEASUREMENT_GATES = frozenset({"measure", "measure_all"})

_BASELINE_ENTANGLING_GATES = frozenset({
    "cx", "cy", "cz", "ch", "crx", "cry", "crz", "cp", "csx",
    "ccx", "c3x", "c4x", "dcx", "ecr", "iswap", "rzz", "rxx", "ryy", "cswap",
})

_PHASE_GATES = frozenset({
    "z", "t", "tdg", "s", "sdg", "rz", "ry", "rx", "p", "cp", "crz",
    "u1", "u2", "u3", "u",
})

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
