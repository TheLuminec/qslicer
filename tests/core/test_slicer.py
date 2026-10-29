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

from Parsers.qiskitparser import QiskitParser
from Dependencies.dependency_rules import DependencyType as D
from qdg import QDG
from slicer import QuantumSlicer, SlicingCriterion

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
