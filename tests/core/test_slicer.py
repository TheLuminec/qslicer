"""Backward and forward slices on a hand-built circuit (VV-03, VV-05).

Circuit (index: instruction):
    0: h  q0
    1: cx q0, q1
    2: x  q2
    3: measure q1 -> c0
    4: if (c0) x q2

Hand-derived edges with the baseline rules:
    DATA_QUBIT     0->1 (q0), 1->3 (q1), 2->4 (q2)
    MEASUREMENT    3->4
    DATA_CLASSICAL 3->4
"""

import networkx as nx
import pytest
from qiskit import QuantumCircuit

from qslicer.parsers.qiskitparser import QiskitParser
from qslicer.dependencies.dependency_rules import DependencyType as D
from qslicer.qdg import QDG
from qslicer.slicer import QuantumSlicer, SlicingCriterion

pytestmark = [pytest.mark.requirement("VV-03"), pytest.mark.requirement("VV-05")]


@pytest.fixture(scope="module")
def qdg():
    qc = QuantumCircuit(3, 1)
    qc.h(0)
    qc.cx(0, 1)
    qc.x(2)
    qc.measure(1, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(2)
    return QDG(QiskitParser(qc).program)


def backward(qdg, idx, deps=None):
    return QuantumSlicer.backward_slice(qdg, SlicingCriterion(node_idx=idx, dep_types=deps))


def forward(qdg, idx, deps=None):
    return QuantumSlicer.forward_slice(qdg, SlicingCriterion(node_idx=idx, dep_types=deps))


@pytest.mark.requirement("FR-12")
@pytest.mark.parametrize("idx, expected", [
    (0, {0}),
    (1, {0, 1}),
    (2, {2}),
    (3, {0, 1, 3}),
    (4, {0, 1, 2, 3, 4}),
])
def test_backward_slice(qdg, idx, expected):
    assert backward(qdg, idx) == expected


@pytest.mark.requirement("FR-13")
@pytest.mark.parametrize("idx, expected", [
    (0, {0, 1, 3, 4}),
    (2, {2, 4}),
    (3, {3, 4}),
    (4, {4}),
])
def test_forward_slice(qdg, idx, expected):
    assert forward(qdg, idx) == expected


@pytest.mark.requirement("FR-14")
def test_dep_type_filter_restricts_backward(qdg):
    assert backward(qdg, 4, {D.DATA_QUBIT}) == {2, 4}
    assert backward(qdg, 4, {D.MEASUREMENT}) == {3, 4}
    assert backward(qdg, 4, {D.MEASUREMENT, D.DATA_QUBIT}) == {0, 1, 2, 3, 4}


@pytest.mark.requirement("FR-14")
def test_dep_type_filter_restricts_forward(qdg):
    assert forward(qdg, 0, {D.DATA_QUBIT}) == {0, 1, 3}
    assert forward(qdg, 0, {D.ENTANGLEMENT}) == {0}


@pytest.mark.requirement("FR-15")
@pytest.mark.parametrize("deps", [None, {D.DATA_QUBIT}, {D.MEASUREMENT}, set()],
                         ids=["all", "qubit", "measurement", "none"])
def test_slice_contains_criterion_and_only_connected_nodes(qdg, deps):
    sub = nx.DiGraph()
    sub.add_nodes_from(qdg.graph.nodes)
    sub.add_edges_from(
        (u, v) for u, v, d in qdg.graph.edges(data=True)
        if deps is None or d["dependency"] in deps
    )
    for idx in qdg.graph.nodes:
        assert backward(qdg, idx, deps) == nx.ancestors(sub, idx) | {idx}
        assert forward(qdg, idx, deps) == nx.descendants(sub, idx) | {idx}


@pytest.mark.requirement("NFR-08")
def test_slices_are_deterministic(qdg):
    first = backward(qdg, 4)
    assert all(backward(qdg, 4) == first for _ in range(3))


def chop(qdg, source, target, deps=None):
    return QuantumSlicer.chop(qdg, source, target, deps)


@pytest.mark.parametrize("source, target, expected", [
    (0, 3, {0, 1, 3}),
    (1, 3, {1, 3}),
    (2, 3, set()),   # x q2 never reaches the measurement of q1
    (3, 0, set()),   # against the edges
])
def test_chop(qdg, source, target, expected):
    assert chop(qdg, source, target) == expected


def test_chop_follows_only_the_given_dep_types(qdg):
    assert chop(qdg, 0, 3, {D.DATA_QUBIT}) == {0, 1, 3}
    assert chop(qdg, 0, 3, {D.MEASUREMENT}) == set()


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_chop_from_a_node_to_itself_is_that_node(qdg, idx):
    assert chop(qdg, idx, idx) == {idx}


def test_chop_is_forward_slice_meet_backward_slice(qdg):
    for source in qdg.graph.nodes:
        for target in qdg.graph.nodes:
            assert chop(qdg, source, target) == forward(qdg, source) & backward(qdg, target)


# Without classical control:
#   0 h q0 | 1 cx q0,q1 | 2 measure q0 | 3 measure q1 | 4 x q0 | 5 measure q0 | 6 h q2
@pytest.fixture(scope="module")
def remeasured():
    qc = QuantumCircuit(3, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure(0, 0)
    qc.measure(1, 0)
    qc.x(0)
    qc.measure(0, 1)
    qc.h(2)
    return QiskitParser(qc).program


def test_measurements_of_lists_each_measured_qubit_in_order(remeasured):
    assert QuantumSlicer.measurements_of(remeasured) == {0: [2, 5], 1: [3]}


@pytest.mark.parametrize("qubit, expected", [(0, 5), (1, 3), (2, None), (7, None)])
def test_qubit_criterion_is_the_last_measurement(remeasured, qubit, expected):
    assert QuantumSlicer.qubit_criterion(remeasured, qubit) == expected


def test_chop_to_a_qubits_last_measurement(remeasured):
    graph = QDG(remeasured)
    target = QuantumSlicer.qubit_criterion(remeasured, 0)
    assert chop(graph, 1, target) == {1, 2, 4, 5}
    assert chop(graph, 3, target) == set()
    assert chop(graph, 6, target) == set()


def test_backward_slices_one_per_node(qdg):
    assert QuantumSlicer.backward_slices(qdg, [3, 1, 2]) == {3: {0, 1, 3}, 1: {0, 1}, 2: {2}}
    assert QuantumSlicer.backward_slices(qdg, [3], {D.MEASUREMENT}) == {3: {3}}
    assert QuantumSlicer.backward_slices(qdg, []) == {}
