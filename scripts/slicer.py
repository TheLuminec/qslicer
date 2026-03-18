from dataclasses import dataclass, field
from typing import List, Optional, Set

from qpdg import QPDG, DependencyType


@dataclass
class SlicingCriterion:
    """
    Defines the starting point and scope for a quantum program slice.

    Attributes:
        node_idx:  Index of the instruction node to slice from/to.
        qubits:    Qubit indices of interest (reserved for qubit-filtered slicing,
                   not yet consumed by the slicer algorithms).
        dep_types: Set of DependencyType values to follow during traversal.
                   None means follow all dependency types.
    """
    node_idx: int
    qubits: List[int] = field(default_factory=list)
    dep_types: Optional[Set[DependencyType]] = None


class QuantumSlicer:
    """
    Computes backward and forward slices over a QPDG.

    A backward slice from node n contains every instruction that n transitively
    depends on — i.e., all instructions whose output could affect the state
    observed at n.

    A forward slice from node n contains every instruction that is transitively
    affected by n — i.e., all instructions that may consume state produced by n.

    Both slices include the criterion node itself.
    """

    @staticmethod
    def backward_slice(qpdg: QPDG, criterion: SlicingCriterion) -> Set[int]:
        """
        BFS backward through incoming edges from criterion.node_idx.
        Returns the set of instruction indices in the backward slice.
        """
        visited: Set[int] = set()
        queue = [criterion.node_idx]
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            for pred, _, edge_data in qpdg.graph.in_edges(current, data=True):
                dep = edge_data.get('dependency')
                if criterion.dep_types is None or dep in criterion.dep_types:
                    if pred not in visited:
                        queue.append(pred)
        return visited

    @staticmethod
    def forward_slice(qpdg: QPDG, criterion: SlicingCriterion) -> Set[int]:
        """
        BFS forward through outgoing edges from criterion.node_idx.
        Returns the set of instruction indices in the forward slice.
        """
        visited: Set[int] = set()
        queue = [criterion.node_idx]
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            for _, succ, edge_data in qpdg.graph.out_edges(current, data=True):
                dep = edge_data.get('dependency')
                if criterion.dep_types is None or dep in criterion.dep_types:
                    if succ not in visited:
                        queue.append(succ)
        return visited
