"""Entanglement-group approximation (FR-17, FR-18) and the FR-16 gap."""

import pytest
from qiskit import QuantumCircuit

from Parsers.qiskitparser import QiskitParser
from entanglement import EntanglementAnalyzer, EntanglementGraph
from qpdg import QPDG
from slicer import QuantumSlicer, SlicingCriterion


def analyze(qc):
    analyzer = EntanglementAnalyzer(QiskitParser(qc).program)
    analyzer.analyze()
    return analyzer


@pytest.mark.requirement("FR-17")
def test_each_qubit_starts_in_its_own_active_group():
    groups = analyze(QuantumCircuit(3)).get_groups()
    assert [g.qubits for g in groups] == [{0}, {1}, {2}]
    assert all(g.active and g.kind == "init" for g in groups)


@pytest.mark.requirement("FR-18")
def test_entangling_gate_merges_two_groups():
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.cx(0, 1)
    analyzer = analyze(qc)
    groups = analyzer.get_groups()
    merged = groups[-1]
    assert merged.kind == "entangled"
    assert merged.qubits == {0, 1}
    assert merged.parent_groups == {0, 1}
    assert merged.active
    assert not groups[0].active and not groups[1].active
    assert groups[2].active            # q2 untouched
    graph = EntanglementGraph(analyzer).graph
    assert set(graph.predecessors(len(groups) - 1)) == {0, 1}


@pytest.mark.requirement("FR-17")
def test_measurement_closes_group():
    qc = QuantumCircuit(2, 1)
    qc.cx(0, 1)
    qc.measure(0, 0)
    groups = analyze(qc).get_groups()
    entangled = next(g for g in groups if g.kind == "entangled")
    assert not entangled.active
    assert entangled.measurement_applied
    measured = [g for g in groups if g.kind == "measurement"]
    assert len(measured) == 1 and measured[0].measurement_applied


@pytest.mark.requirement("FR-17")
def test_repeated_single_qubit_gate_is_uncomputed():
    qc = QuantumCircuit(1)
    qc.h(0)
    qc.h(0)
    group = analyze(qc).get_groups()[0]
    assert [i.name for i in group.uncomputed_instructions] == ["h"]
    assert group.computed_instructions == []


@pytest.mark.requirement("FR-16")
@pytest.mark.requirement("VV-06")
@pytest.mark.xfail(reason="FR-16 not implemented: the slicer does not use the "
                          "entanglement analysis, so an uncomputed cx stays in the slice",
                   strict=True)
def test_uncomputed_entangling_gate_excluded_from_backward_slice():
    qc = QuantumCircuit(2, 1)
    qc.h(0)           # 0
    qc.cx(0, 1)       # 1  entangle
    qc.cx(0, 1)       # 2  uncompute
    qc.measure(1, 0)  # 3  criterion: q1 is back to |0>
    qpdg = QPDG(QiskitParser(qc).program)
    sliced = QuantumSlicer.backward_slice(qpdg, SlicingCriterion(node_idx=3))
    assert 1 not in sliced and 2 not in sliced
