from enum import Enum, auto
import networkx as nx
from typing import Dict, List, Any, Optional, Set

from qinstruction import QuantumInstruction
from qprogram import QuantumProgram


class DependencyType(Enum):
    ENTANGLEMENT = auto()
    MEASUREMENT = auto()
    DATA_CLASSICAL = auto()
    CONTROL_CLASSICAL = auto()
    PHASE = auto()
    DATA_QUBIT = auto()


# Multi-qubit gates that create or propagate quantum entanglement.
# swap and barrier are intentionally excluded: SWAP transfers state without
# creating quantum correlations; barrier is a scheduling hint only.
_ENTANGLING_GATES = frozenset({
    'cx', 'cy', 'cz', 'ch', 'crx', 'cry', 'crz', 'cp', 'csx',
    'ccx', 'c3x', 'c4x', 'dcx', 'ecr', 'iswap', 'rzz', 'rxx', 'ryy', 'cswap',
})

# Gates that apply a phase to qubit state, influencing interference effects.
_PHASE_GATES = frozenset({
    'z', 't', 'tdg', 's', 'sdg', 'rz', 'ry', 'rx', 'p', 'cp', 'crz',
    'u1', 'u2', 'u3', 'u',
})


class QPDGNode:
    """Wrapper for a QuantumInstruction to serve as a graph node."""

    def __init__(self, idx: int, instruction: QuantumInstruction):
        self.idx = idx
        self.instruction = instruction

    def __repr__(self) -> str:
        return f"Node({self.idx}: {self.instruction.name})"

    def __hash__(self) -> int:
        return hash(self.idx)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QPDGNode):
            return NotImplemented
        return self.idx == other.idx


class QPDG:
    """Quantum Program Dependence Graph (QPDG) constructor and container."""

    def __init__(self, program: QuantumProgram) -> None:
        self.program = program
        self.graph = nx.MultiDiGraph()
        self.nodes: Dict[int, QPDGNode] = {}
        self.build_graph()

    def build_graph(self) -> None:
        """Constructs the nodes and edges for the QPDG."""
        for idx in range(len(self.program)):
            node = QPDGNode(idx, self.program[idx])
            self.nodes[idx] = node
            self.graph.add_node(idx, data=node)

        self._build_data_qubit_dependencies()
        self._build_entanglement_dependencies()
        self._build_measurement_dependencies()
        self._build_phase_dependencies()
        self._build_data_classical_dependencies()
        self._build_control_classical_dependencies()

    def _build_data_qubit_dependencies(self) -> None:
        """
        Builds qubit data dependencies (def-use chain).

        Every gate is simultaneously a use and a def of each qubit it touches —
        the no-cloning theorem enforces single-definition (SSA) semantics on qubits.
        An edge (u → v, DATA_QUBIT) means v operates on the qubit state produced by u
        with no intermediate operation on that qubit in between.
        """
        last_def: Dict[int, int] = {}  # qubit index -> last instruction index that touched it
        for idx in range(len(self.program)):
            instr = self.program[idx]
            for q in instr.qubits:
                if q in last_def:
                    self.graph.add_edge(last_def[q], idx, dependency=DependencyType.DATA_QUBIT)
                last_def[q] = idx

    def _build_entanglement_dependencies(self) -> None:
        """
        Builds entanglement dependencies.

        An edge (u → v, ENTANGLEMENT) means u is a gate that created or propagated
        entanglement on a qubit, and v is a later operation on that entangled qubit.

        Only gates in _ENTANGLING_GATES create entanglement. SWAP is explicitly excluded
        because it transfers separable state without creating quantum correlations.
        Measurement collapses the entangled state, clearing the entanglement source
        for the measured qubit.
        """
        entangle_source: Dict[int, int] = {}  # qubit index -> last entangling gate index

        for idx in range(len(self.program)):
            instr = self.program[idx]

            if instr.name in _ENTANGLING_GATES:
                # Draw edges from any prior entanglement sources on these qubits
                for q in instr.qubits:
                    if q in entangle_source:
                        self.graph.add_edge(
                            entangle_source[q], idx,
                            dependency=DependencyType.ENTANGLEMENT
                        )
                # Mark all qubits of this gate as entangled by it
                for q in instr.qubits:
                    entangle_source[q] = idx
            else:
                # Non-entangling gate: draw edge if qubit is currently entangled
                for q in instr.qubits:
                    if q in entangle_source:
                        self.graph.add_edge(
                            entangle_source[q], idx,
                            dependency=DependencyType.ENTANGLEMENT
                        )
                # Measurement collapses entanglement for the measured qubit
                if instr.name in ('measure', 'measure_all'):
                    for q in instr.qubits:
                        entangle_source.pop(q, None)

    def _build_measurement_dependencies(self) -> None:
        """
        Builds measurement dependencies.

        An edge (u → v, MEASUREMENT) means u is a measurement that wrote to a
        classical bit c, and v is a classically-conditioned gate whose execution
        depends on the value of c (i.e., v used c_if(c, ...)).

        Requires the parser to populate metadata['condition_clbits'] with the
        global clbit indices that the instruction is conditioned on.
        """
        last_measured: Dict[int, int] = {}  # clbit index -> last measurement instruction index

        for idx in range(len(self.program)):
            instr = self.program[idx]

            if instr.name in ('measure', 'measure_all'):
                for c in instr.clbits:
                    last_measured[c] = idx

            for c in instr.metadata.get('condition_clbits', []):
                if c in last_measured:
                    self.graph.add_edge(last_measured[c], idx, dependency=DependencyType.MEASUREMENT)

    def _build_phase_dependencies(self) -> None:
        """
        Builds phase dependencies.

        An edge (u → v, PHASE) means u is a phase gate on qubit q and v is the
        immediate next operation on q. Phase gates modify the relative phase of
        qubit state and govern interference behavior in algorithms like QFT and
        Grover's search. The edge is drawn to the next gate regardless of its
        type, then the phase source is cleared — a gate that observes a phase
        effect becomes the new baseline for that qubit wire.
        """
        last_phase: Dict[int, int] = {}  # qubit index -> last phase gate index

        for idx in range(len(self.program)):
            instr = self.program[idx]
            # Draw PHASE edge from last phase gate to this instruction, then clear
            for q in instr.qubits:
                if q in last_phase:
                    self.graph.add_edge(
                        last_phase[q], idx,
                        dependency=DependencyType.PHASE
                    )
                    last_phase.pop(q)
            # If this instruction is a phase gate, record it as the new source
            if instr.name in _PHASE_GATES:
                for q in instr.qubits:
                    last_phase[q] = idx

    def _build_data_classical_dependencies(self) -> None:
        """
        Builds classical data dependencies (reaching definitions on classical bits).

        An edge (u → v, DATA_CLASSICAL) means u writes to classical bit c (via
        measurement) and v reads c (via a classical condition), with no re-write
        of c between them.

        Requires the parser to populate metadata['condition_clbits'].
        """
        last_write: Dict[int, int] = {}  # clbit index -> last instruction that wrote to it

        for idx in range(len(self.program)):
            instr = self.program[idx]
            # Read: instruction conditioned on classical bits
            for c in instr.metadata.get('condition_clbits', []):
                if c in last_write:
                    self.graph.add_edge(
                        last_write[c], idx,
                        dependency=DependencyType.DATA_CLASSICAL
                    )
            # Write: measurement stores result to classical bits
            for c in instr.clbits:
                last_write[c] = idx

    def _build_control_classical_dependencies(self) -> None:
        """
        Builds classical control dependencies.

        An edge (u → v, CONTROL_CLASSICAL) means v's execution is controlled by
        the outcome of branch u, determined via post-dominator tree analysis of
        the classical control flow graph.

        TODO: Requires CFG construction and post-dominator tree analysis. Sprint 4.
        """
        pass
