"""Backward and forward slices on a hand-built circuit (VV-03, VV-05).

Circuit (index: instruction):
    0: h  q0
    1: cx q0, q1
    2: x  q2
    3: measure q1 -> c0
    4: if (c0)      the predicate: reads c0, on no qubit
    5:   x q2       its true body

Hand-derived edges with the baseline rules:
    DATA_QUBIT        0->1 (q0), 1->3 (q1), 2->5 (q2)
    MEASUREMENT       3->4
    DATA_CLASSICAL    3->4
    CONTROL_CLASSICAL 4->5
"""

import networkx as nx
import pytest
from qiskit import QuantumCircuit

from qslicer.parsers.qiskitparser import QiskitParser
from qslicer.dependencies.dependency_rules import DependencyType as D
from qslicer.dependence_graph import DependenceGraph
from qslicer.slicer import QuantumSlicer, SlicingCriterion

pytestmark = [pytest.mark.requirement("VV-03"), pytest.mark.requirement("VV-05")]


@pytest.fixture(scope="module")
def dg():
    qc = QuantumCircuit(3, 1)
    qc.h(0)
    qc.cx(0, 1)
    qc.x(2)
    qc.measure(1, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(2)
    return DependenceGraph(QiskitParser(qc).program)


def backward(dg, idx, deps=None):
    return QuantumSlicer.backward_slice(dg, SlicingCriterion(node_idx=idx, dep_types=deps))


def forward(dg, idx, deps=None):
    return QuantumSlicer.forward_slice(dg, SlicingCriterion(node_idx=idx, dep_types=deps))


@pytest.mark.requirement("FR-12")
@pytest.mark.parametrize("idx, expected", [
    (0, {0}),
    (1, {0, 1}),
    (2, {2}),
    (3, {0, 1, 3}),
    (4, {0, 1, 3, 4}),
    (5, {0, 1, 2, 3, 4, 5}),
])
def test_backward_slice(dg, idx, expected):
    assert backward(dg, idx) == expected


@pytest.mark.requirement("FR-13")
@pytest.mark.parametrize("idx, expected", [
    (0, {0, 1, 3, 4, 5}),
    (2, {2, 5}),
    (3, {3, 4, 5}),
    (4, {4, 5}),
    (5, {5}),
])
def test_forward_slice(dg, idx, expected):
    assert forward(dg, idx) == expected


@pytest.mark.requirement("FR-14")
def test_dep_type_filter_restricts_backward(dg):
    assert backward(dg, 5, {D.DATA_QUBIT}) == {2, 5}
    assert backward(dg, 4, {D.MEASUREMENT}) == {3, 4}
    # The body gate reaches the measurement only through its predicate.
    assert backward(dg, 5, {D.MEASUREMENT, D.DATA_QUBIT}) == {2, 5}
    assert backward(dg, 5, {D.MEASUREMENT, D.CONTROL_CLASSICAL}) == {3, 4, 5}
    assert backward(dg, 5, {D.MEASUREMENT, D.DATA_QUBIT, D.CONTROL_CLASSICAL}) == {
        0, 1, 2, 3, 4, 5}


@pytest.mark.requirement("FR-14")
def test_dep_type_filter_restricts_forward(dg):
    assert forward(dg, 0, {D.DATA_QUBIT}) == {0, 1, 3}
    assert forward(dg, 0, {D.ENTANGLEMENT}) == {0}


@pytest.mark.requirement("FR-15")
@pytest.mark.parametrize("deps", [None, {D.DATA_QUBIT}, {D.MEASUREMENT}, set()],
                         ids=["all", "qubit", "measurement", "none"])
def test_slice_contains_criterion_and_only_connected_nodes(dg, deps):
    sub = nx.DiGraph()
    sub.add_nodes_from(dg.graph.nodes)
    sub.add_edges_from(
        (u, v) for u, v, d in dg.graph.edges(data=True)
        if deps is None or d["dependency"] in deps
    )
    for idx in dg.graph.nodes:
        assert backward(dg, idx, deps) == nx.ancestors(sub, idx) | {idx}
        assert forward(dg, idx, deps) == nx.descendants(sub, idx) | {idx}


@pytest.mark.requirement("NFR-08")
def test_slices_are_deterministic(dg):
    first = backward(dg, 5)
    assert all(backward(dg, 5) == first for _ in range(3))


def chop(dg, source, target, deps=None):
    return QuantumSlicer.chop(dg, source, target, deps)


@pytest.mark.parametrize("source, target, expected", [
    (0, 3, {0, 1, 3}),
    (1, 3, {1, 3}),
    (2, 3, set()),   # x q2 never reaches the measurement of q1
    (3, 0, set()),   # against the edges
])
def test_chop(dg, source, target, expected):
    assert chop(dg, source, target) == expected


def test_chop_follows_only_the_given_dep_types(dg):
    assert chop(dg, 0, 3, {D.DATA_QUBIT}) == {0, 1, 3}
    assert chop(dg, 0, 3, {D.MEASUREMENT}) == set()


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_chop_from_a_node_to_itself_is_that_node(dg, idx):
    assert chop(dg, idx, idx) == {idx}


def test_chop_is_forward_slice_meet_backward_slice(dg):
    for source in dg.graph.nodes:
        for target in dg.graph.nodes:
            assert chop(dg, source, target) == forward(dg, source) & backward(dg, target)


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
    graph = DependenceGraph(remeasured)
    target = QuantumSlicer.qubit_criterion(remeasured, 0)
    assert chop(graph, 1, target) == {1, 2, 4, 5}
    assert chop(graph, 3, target) == set()
    assert chop(graph, 6, target) == set()


def test_backward_slices_one_per_node(dg):
    assert QuantumSlicer.backward_slices(dg, [3, 1, 2]) == {3: {0, 1, 3}, 1: {0, 1}, 2: {2}}
    assert QuantumSlicer.backward_slices(dg, [3], {D.MEASUREMENT}) == {3: {3}}
    assert QuantumSlicer.backward_slices(dg, []) == {}
