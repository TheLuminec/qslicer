from Dependencies.dependency_rules import *
from Dependencies.dependency_rules import (
    _BASELINE_ENTANGLING_GATES,
    _MEASUREMENT_GATES,
    _PHASE_GATES,
    _condition_clbits
)

class DataQubitRule(DependencyRule):
    """Builds qubit data dependencies with def-use semantics."""

    name = "data_qubit"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        last_def: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]
            for qubit in instruction.qubits:
                if qubit in last_def:
                    self.add_dependency(
                        graph,
                        last_def[qubit],
                        idx,
                        DependencyType.DATA_QUBIT,
                    )
                last_def[qubit] = idx


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
        last_measured: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]

            if instruction.name in _MEASUREMENT_GATES:
                for clbit in instruction.clbits:
                    last_measured[clbit] = idx

            for clbit in _condition_clbits(instruction):
                if clbit in last_measured:
                    self.add_dependency(
                        graph,
                        last_measured[clbit],
                        idx,
                        DependencyType.MEASUREMENT,
                    )


class DataClassicalRule(DependencyRule):
    """Builds classical reaching-definition dependencies."""

    name = "data_classical"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        last_write: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]

            for clbit in _condition_clbits(instruction):
                if clbit in last_write:
                    self.add_dependency(
                        graph,
                        last_write[clbit],
                        idx,
                        DependencyType.DATA_CLASSICAL,
                    )

            for clbit in instruction.clbits:
                last_write[clbit] = idx


class ControlClassicalRule(DependencyRule):
    """Placeholder for future control-dependence analysis."""

    name = "control_classical"

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        return


class PhaseRule(DependencyRule):
    """Experimental phase-dependence rule."""

    def __init__(self, phase_gates: FrozenSet[str] = _PHASE_GATES, name: str = "phase") -> None:
        self.phase_gates = phase_gates
        self.name = name

    def build(self, program: QuantumProgram, graph: nx.MultiDiGraph) -> None:
        phase_source: Dict[int, int] = {}

        for idx in range(len(program)):
            instruction = program[idx]

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
