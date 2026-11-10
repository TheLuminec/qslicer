from typing import Dict, FrozenSet

import networkx as nx

from ..ir.qprogram import QuantumProgram
from .dependency_rules import DependencyRule, DependencyType
from .dependency_rules import (
    _BASELINE_ENTANGLING_GATES,
    _MEASUREMENT_GATES,
    _PHASE_GATES,
    _NO_DEPENDENCE,
    _condition_clbits,
    _guards,
    _severs_wire,
    ReachingDefs,
)

class DataQubitRule(DependencyRule):
    """Builds qubit data dependencies with def-use semantics.

    A barrier or delay is neither a use nor a definition, so the wires it spans
    stay separate. A reset that always runs is a definition only: nothing
    before it on the wire reaches anything after it.
    """

    name = "data_qubit"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        defs = ReachingDefs()

        for idx in range(len(program)):
            instruction = program[idx]
            if instruction.name in _NO_DEPENDENCE:
                continue
            defs.enter(_guards(instruction))
            severs = _severs_wire(instruction)
            for qubit in instruction.qubits:
                if not severs:
                    for source in defs.reaching(qubit):
                        self.add_dependency(graph, source, idx, DependencyType.DATA_QUBIT)
                defs.write(qubit, idx)


class EntanglementRule(DependencyRule):
    """Builds entanglement dependencies using a configurable gate set."""

    def __init__(
        self,
        entangling_gates: FrozenSet[str],
        collapse_on_measurement: bool = True,
        name: str = "entanglement",
    ) -> None:
        self.entangling_gates = entangling_gates
        self.collapse_on_measurement = collapse_on_measurement
        self.name = name

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        entangle_source: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]
            if instruction.name in _NO_DEPENDENCE:
                continue
            if _severs_wire(instruction):
                for qubit in instruction.qubits:
                    entangle_source.pop(qubit, None)
                continue

            if instruction.name in self.entangling_gates:
                for qubit in instruction.qubits:
                    if qubit in entangle_source:
                        self.add_dependency(
                            graph,
                            entangle_source[qubit],
                            idx,
                            DependencyType.ENTANGLEMENT,
                        )

                for qubit in instruction.qubits:
                    entangle_source[qubit] = idx
                continue

            for qubit in instruction.qubits:
                if qubit in entangle_source:
                    self.add_dependency(
                        graph,
                        entangle_source[qubit],
                        idx,
                        DependencyType.ENTANGLEMENT,
                    )

            if self.collapse_on_measurement and instruction.name in _MEASUREMENT_GATES:
                for qubit in instruction.qubits:
                    entangle_source.pop(qubit, None)


class MeasurementRule(DependencyRule):
    """Builds measurement-to-condition dependencies."""

    name = "measurement"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        measured = ReachingDefs()

        for idx in range(len(program)):
            instruction = program[idx]
            measured.enter(_guards(instruction))

            if instruction.name in _MEASUREMENT_GATES:
                for clbit in instruction.clbits:
                    measured.write(clbit, idx)

            for clbit in _condition_clbits(instruction):
                for source in measured.reaching(clbit):
                    self.add_dependency(graph, source, idx, DependencyType.MEASUREMENT)


class DataClassicalRule(DependencyRule):
    """Builds classical reaching-definition dependencies."""

    name = "data_classical"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        writes = ReachingDefs()

        for idx in range(len(program)):
            instruction = program[idx]
            writes.enter(_guards(instruction))

            for clbit in _condition_clbits(instruction):
                for source in writes.reaching(clbit):
                    self.add_dependency(graph, source, idx, DependencyType.DATA_CLASSICAL)

            for clbit in instruction.clbits:
                writes.write(clbit, idx)


class ControlClassicalRule(DependencyRule):
    """Builds control dependencies: an if_test's predicate to each instruction
    of its branches (a nested if_test's predicate included)."""

    name = "control_classical"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        for idx in range(len(program)):
            guards = _guards(program[idx])
            if guards:
                self.add_dependency(
                    graph, guards[-1][0], idx, DependencyType.CONTROL_CLASSICAL)


class PhaseRule(DependencyRule):
    """Experimental phase-dependence rule."""

    def __init__(self, phase_gates: FrozenSet[str] = _PHASE_GATES, name: str = "phase") -> None:
        self.phase_gates = phase_gates
        self.name = name

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        phase_source: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]
            if instruction.name in _NO_DEPENDENCE:
                continue
            if _severs_wire(instruction):
                for qubit in instruction.qubits:
                    phase_source.pop(qubit, None)
                continue

            for qubit in instruction.qubits:
                if qubit in phase_source and phase_source[qubit] != idx:
                    self.add_dependency(
                        graph,
                        phase_source[qubit],
                        idx,
                        DependencyType.PHASE,
                    )

            if instruction.name in self.phase_gates:
                for qubit in instruction.qubits:
                    phase_source[qubit] = idx
            elif instruction.name in _MEASUREMENT_GATES:
                for qubit in instruction.qubits:
                    phase_source.pop(qubit, None)
