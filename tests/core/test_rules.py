"""Each dependency rule, run on its own over small circuits (VV-03)."""

import networkx as nx
import pytest
from qiskit import QuantumCircuit

from qslicer.parsers.qiskitparser import QiskitParser
from qslicer.dependencies.dependency_rules import DependencyRule, DependencyType
from qslicer.dependencies.rules import (
    ControlClassicalRule,
    DataClassicalRule,
    DataQubitRule,
    EntanglementRule,
    MeasurementRule,
    PhaseRule,
    _BASELINE_ENTANGLING_GATES,
)

pytestmark = pytest.mark.requirement("VV-03")


def run(rule, qc):
    program = QiskitParser(qc).program
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(range(len(program)))
    rule.build(program, graph)
    return graph


def edges(graph):
    return {(u, v, d["dependency"]) for u, v, d in graph.edges(data=True)}


def conditional_circuit():
    """0 h q0; 1 measure q0->c0; 2 measure q1->c0 (overwrites c0); 3 if(c0) x q1."""
    qc = QuantumCircuit(2, 1)
    qc.h(0)
    qc.measure(0, 0)
    qc.measure(1, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(1)
    return qc


@pytest.mark.requirement("FR-09")
def test_data_qubit_rule_links_consecutive_uses_per_wire():
    qc = QuantumCircuit(3)
    qc.h(0)          # 0
    qc.x(1)          # 1
    qc.cx(0, 1)      # 2
    qc.z(0)          # 3
    qc.y(2)          # 4  alone on its wire
    graph = run(DataQubitRule(), qc)
    D = DependencyType.DATA_QUBIT
    assert edges(graph) == {(0, 2, D), (1, 2, D), (2, 3, D)}


@pytest.mark.requirement("FR-09")
def test_measurement_rule_links_measure_to_if_test():
    graph = run(MeasurementRule(), conditional_circuit())
    # Only the latest measurement into c0 feeds the condition.
    assert edges(graph) == {(2, 3, DependencyType.MEASUREMENT)}


@pytest.mark.requirement("FR-09")
def test_data_classical_rule_uses_reaching_definition():
    graph = run(DataClassicalRule(), conditional_circuit())
    assert edges(graph) == {(2, 3, DependencyType.DATA_CLASSICAL)}


@pytest.mark.requirement("FR-09")
def test_classical_rules_emit_nothing_without_a_condition():
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.measure(0, 0)
    assert edges(run(MeasurementRule(), qc)) == set()
    assert edges(run(DataClassicalRule(), qc)) == set()


@pytest.mark.requirement("FR-11")
def test_control_classical_rule_runs():
    # Placeholder rule today: it must run cleanly, and any edges it does emit
    # must be labelled CONTROL_CLASSICAL.
    graph = run(ControlClassicalRule(), conditional_circuit())
    assert all(d == DependencyType.CONTROL_CLASSICAL for _, _, d in edges(graph))


@pytest.mark.requirement("FR-10")
def test_entanglement_rule_smoke():
    qc = QuantumCircuit(2, 1)
    qc.h(0)
    qc.cx(0, 1)
    qc.x(1)
    qc.measure(1, 0)
    graph = run(EntanglementRule(_BASELINE_ENTANGLING_GATES), qc)
    assert all(d == DependencyType.ENTANGLEMENT for _, _, d in edges(graph))
    assert (1, 2, DependencyType.ENTANGLEMENT) in edges(graph)


@pytest.mark.requirement("FR-10")
def test_phase_rule_smoke():
    qc = QuantumCircuit(1)
    qc.t(0)
    qc.h(0)
    graph = run(PhaseRule(), qc)
    assert edges(graph) == {(0, 1, DependencyType.PHASE)}


@pytest.mark.requirement("FR-08")
@pytest.mark.parametrize("rule", [
    DataQubitRule(), MeasurementRule(), DataClassicalRule(), ControlClassicalRule(),
    EntanglementRule(_BASELINE_ENTANGLING_GATES), PhaseRule(),
], ids=lambda r: r.name)
def test_every_edge_carries_a_dependency_type(rule):
    assert isinstance(rule, DependencyRule)
    qc = conditional_circuit()
    qc.cx(0, 1)
    qc.t(1)
    graph = run(rule, qc)
    for _, _, data in graph.edges(data=True):
        assert isinstance(data["dependency"], DependencyType)
        assert data["rule"] == rule.name


@pytest.mark.requirement("FR-09")
def test_barriers_do_not_link_the_wires_they_span():
    qc = QuantumCircuit(2)
    qc.h(0)          # 0
    qc.x(1)          # 1
    qc.barrier()     # 2
    qc.z(0)          # 3
    qc.y(1)          # 4
    D = DependencyType.DATA_QUBIT
    assert edges(run(DataQubitRule(), qc)) == {(0, 3, D), (1, 4, D)}


@pytest.mark.requirement("FR-09")
def test_a_reset_cuts_its_wire_off_from_what_came_before():
    qc = QuantumCircuit(2)
    qc.h(0)          # 0
    qc.cx(0, 1)      # 1
    qc.reset(0)      # 2  nothing before it reaches past it on q0
    qc.x(0)          # 3
    qc.z(1)          # 4  q1 still carries what the cx did
    D = DependencyType.DATA_QUBIT
    assert edges(run(DataQubitRule(), qc)) == {(0, 1, D), (2, 3, D), (1, 4, D)}
