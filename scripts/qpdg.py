from typing import Dict, Optional, Sequence, Tuple
import networkx as nx

from qinstruction import QuantumInstruction
from qprogram import QuantumProgram
from Dependencies.dependency_rules import DependencyRule
from Dependencies.rules import (
    DataQubitRule,
    EntanglementRule,
    MeasurementRule,
    DataClassicalRule,
    ControlClassicalRule,
    PhaseRule,
    _BASELINE_ENTANGLING_GATES,
)



def _baseline_rules() -> Tuple[DependencyRule, ...]:
    return (
        DataQubitRule(),
        EntanglementRule(_BASELINE_ENTANGLING_GATES),
        MeasurementRule(),
        DataClassicalRule(),
        ControlClassicalRule(),
    )


def list_rule_profiles() -> Dict[str, Tuple[DependencyRule, ...]]:
    """Return a mapping of profile names to rule sets."""
    return {
        "baseline": _baseline_rules(),
        # Future profiles can be added here.
    }

def get_rule_profile(name: str) -> Tuple[DependencyRule, ...]:
    """Lookup a rule profile by name, raising an error if not found."""
    profiles = list_rule_profiles()
    if name not in profiles:
        raise ValueError(f"Unknown rule profile '{name}'. Available profiles: {list(profiles.keys())}")
    return profiles[name]

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

    def __init__(
        self,
        program: QuantumProgram,
        rules: Optional[Sequence[DependencyRule]] = None,
    ) -> None:
        self.program = program
        self.graph = nx.MultiDiGraph()
        self.nodes: Dict[int, QPDGNode] = {}
        self.rules = tuple(rules) if rules is not None else _baseline_rules()
        self.build_graph()

    def build_graph(self) -> None:
        """Construct the graph nodes once and let each rule emit dependencies."""
        self.graph.clear()
        self.nodes = {}

        for idx in range(len(self.program)):
            node = QPDGNode(idx, self.program[idx])
            self.nodes[idx] = node
            self.graph.add_node(idx, data=node)

        for rule in self.rules:
            rule.build(self.program, self.graph)
