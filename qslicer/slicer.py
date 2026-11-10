from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .ir.qprogram import QuantumProgram
from .qdg import QDG
from .dependencies.dependency_rules import DependencyType


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
    Computes backward and forward slices over a QDG.

    A backward slice from node n contains every instruction that n transitively
    depends on — i.e., all instructions whose output could affect the state
    observed at n.

    A forward slice from node n contains every instruction that is transitively
    affected by n — i.e., all instructions that may consume state produced by n.

    Both slices include the criterion node itself.

    A chop between a source s and a target t is the set of instructions on some
    dependence path from s to t: what s can affect that also affects t.
    """

    @staticmethod
    def backward_slice(qdg: QDG, criterion: SlicingCriterion) -> Set[int]:
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
            for pred, _, edge_data in qdg.graph.in_edges(current, data=True):
                dep = edge_data.get('dependency')
                if criterion.dep_types is None or dep in criterion.dep_types:
                    if pred not in visited:
                        queue.append(pred)
        return visited

    @staticmethod
    def forward_slice(qdg: QDG, criterion: SlicingCriterion) -> Set[int]:
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
            for _, succ, edge_data in qdg.graph.out_edges(current, data=True):
                dep = edge_data.get('dependency')
                if criterion.dep_types is None or dep in criterion.dep_types:
                    if succ not in visited:
                        queue.append(succ)
        return visited

    @staticmethod
    def chop(
        qdg: QDG,
        source_idx: int,
        target_idx: int,
        dep_types: Optional[Set[DependencyType]] = None,
    ) -> Set[int]:
        """
        Instructions on some dependence path from source_idx to target_idx.

        The intersection of the forward slice from source_idx and the backward
        slice from target_idx, following only dep_types (None = all). Empty when
        no such path exists, so neither endpoint is included then; {source_idx}
        when source_idx == target_idx.
        """
        forward = QuantumSlicer.forward_slice(
            qdg, SlicingCriterion(node_idx=source_idx, dep_types=dep_types))
        if target_idx not in forward:
            return set()
        backward = QuantumSlicer.backward_slice(
            qdg, SlicingCriterion(node_idx=target_idx, dep_types=dep_types))
        return forward & backward

    @staticmethod
    def measurements_of(program: QuantumProgram) -> Dict[int, List[int]]:
        """
        For each measured qubit, the indices of the ``measure`` instructions on
        it, in program order. Qubits never measured are left out.
        """
        found: Dict[int, List[int]] = {}
        for idx in range(len(program)):
            instruction = program[idx]
            if instruction.name != "measure":
                continue
            for qubit in instruction.qubits:
                indices = found.setdefault(qubit, [])
                if not indices or indices[-1] != idx:
                    indices.append(idx)
        return found

    @staticmethod
    def qubit_criterion(program: QuantumProgram, qubit: int) -> Optional[int]:
        """
        The node to slice from to slice on ``qubit``: its last measurement, or
        None if it is never measured.
        """
        indices = QuantumSlicer.measurements_of(program).get(qubit)
        return indices[-1] if indices else None

    @staticmethod
    def backward_slices(
        qdg: QDG,
        node_indices: Iterable[int],
        dep_types: Optional[Set[DependencyType]] = None,
    ) -> Dict[int, Set[int]]:
        """
        One backward slice per node in node_indices, keyed by node index,
        following only dep_types (None = all).
        """
        return {
            idx: QuantumSlicer.backward_slice(
                qdg, SlicingCriterion(node_idx=idx, dep_types=dep_types))
            for idx in node_indices
        }
