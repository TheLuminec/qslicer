"""QPDG construction: nodes, default rules, edge labels."""

import pytest
from qiskit import QuantumCircuit

from Parsers.qiskitparser import QiskitParser
from Dependencies.dependency_rules import DependencyType
from Dependencies.rules import (
    ControlClassicalRule, DataClassicalRule, DataQubitRule, MeasurementRule,
)
from qpdg import QPDG, QPDGNode, get_rule_profile, list_rule_profiles


def sample_program():
    qc = QuantumCircuit(3, 1)
    qc.h(0)
    qc.cx(0, 1)
    qc.x(2)
    qc.measure(1, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(2)
    return QiskitParser(qc).program


@pytest.mark.requirement("FR-08")
def test_every_instruction_is_a_node():
    program = sample_program()
    qpdg = QPDG(program)
    assert sorted(qpdg.graph.nodes) == list(range(len(program)))
    for idx in range(len(program)):
        node = qpdg.graph.nodes[idx]["data"]
        assert isinstance(node, QPDGNode)
        assert node.instruction is program[idx]


@pytest.mark.requirement("FR-08")
def test_every_edge_has_a_dependency_label():
    qpdg = QPDG(sample_program())
    assert qpdg.graph.number_of_edges() > 0
    for _, _, data in qpdg.graph.edges(data=True):
        assert isinstance(data["dependency"], DependencyType)


@pytest.mark.requirement("FR-09")
def test_default_graph_has_qubit_measurement_and_classical_edges():
    qpdg = QPDG(sample_program())
    kinds = {d["dependency"] for _, _, d in qpdg.graph.edges(data=True)}
    assert {DependencyType.DATA_QUBIT, DependencyType.MEASUREMENT,
            DependencyType.DATA_CLASSICAL} <= kinds


def test_default_rule_profile():
    assert "baseline" in list_rule_profiles()
    rules = get_rule_profile("baseline")
    assert [type(r) for r in rules] == [
        DataQubitRule, MeasurementRule, DataClassicalRule, ControlClassicalRule,
    ]
    assert [type(r) for r in QPDG(sample_program()).rules] == [type(r) for r in rules]


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        get_rule_profile("no-such-profile")


@pytest.mark.requirement("NFR-09")
def test_custom_rule_set_only_emits_its_edges():
    qpdg = QPDG(sample_program(), rules=[DataQubitRule()])
    kinds = {d["dependency"] for _, _, d in qpdg.graph.edges(data=True)}
    assert kinds == {DependencyType.DATA_QUBIT}


@pytest.mark.requirement("NFR-08")
@pytest.mark.requirement("VV-08")
def test_graph_is_deterministic():
    def snapshot():
        g = QPDG(sample_program()).graph
        return sorted((u, v, d["dependency"].name, d["rule"]) for u, v, d in g.edges(data=True))
    first = snapshot()
    assert all(snapshot() == first for _ in range(3))
