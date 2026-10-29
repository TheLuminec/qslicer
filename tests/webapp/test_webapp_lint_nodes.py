"""Lint findings carry the graph nodes they are about, so the slicer can mark them."""

import analysis
from webapp_helpers import post_analyze

HEADER = "from qiskit import QuantumCircuit\n"


def analyze(client, body, rules):
    _, data = post_analyze(client, source=HEADER + body, filename="c.py",
                           language="qiskit-static", lint_rules=rules)
    assert data["ok"], data
    return data


def by_code(data, code):
    return [d for d in data["diagnostics"] if d["code"] == code]


def names(data, ids):
    return [(data["nodes"][i]["name"], data["nodes"][i]["line"]) for i in ids]


def test_cancelling_pair_marks_both_gates(client):
    data = analyze(client, "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.x(1)\nqc.x(1)\n"
                           "qc.measure([0, 1], [0, 1])\n", ["dependence/QD08"])
    (pair,) = by_code(data, "QD08")
    assert names(data, pair["nodes"]) == [("x", 4), ("x", 5)]


def test_overwritten_measurement_marks_both_measurements(client):
    data = analyze(client, "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.cx(0, 1)\n"
                           "for q in range(2):\n    qc.measure(q, 0)\n", ["dependence/QD02"])
    (finding,) = by_code(data, "QD02")
    assert [data["nodes"][i]["name"] for i in finding["nodes"]] == ["measure", "measure"]
    assert finding["nodes"][0] < finding["nodes"][1]


def test_control_never_set_marks_only_the_gates_that_never_act(client):
    data = analyze(client, "qc = QuantumCircuit(3, 3)\nqc.h(0)\nfor i in range(2):\n"
                           "    qc.cx(i + 1, i)\nqc.measure(range(3), range(3))\n",
                   ["dependence/QD05"])
    (finding,) = by_code(data, "QD05")
    assert names(data, finding["nodes"]) == [("cx", 5), ("cx", 5)]


def test_dead_gate_marks_that_instance(client):
    data = analyze(client, "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\nqc.x(0)\n",
                   ["dependence/QD01"])
    (finding,) = by_code(data, "QD01")
    assert names(data, finding["nodes"]) == [("x", 5)]


def test_findings_without_a_node_get_none(client):
    data = analyze(client, "qc = QuantumCircuit(3)\nqc.h(0)\n",
                   ["dependence/QD06", "dependence/QD07"])
    assert by_code(data, "QD06") and by_code(data, "QD07")
    for finding in data["diagnostics"]:
        assert finding["nodes"] == []          # the constructor line made no instruction


def test_node_ids_that_do_not_match_the_line_fall_back_to_the_line():
    nodes = [{"id": 0, "source": {"line": 3, "end_line": 3}},
             {"id": 1, "source": {"line": 4, "end_line": 4}}]
    diagnostics = [{"code": "X", "line": 4, "nodes": [0]},      # names a node from line 3
                   {"code": "Y", "line": 3, "nodes": [0, 9]},   # 9 is out of range
                   {"code": "Z", "line": 0}]
    analysis.attach_lint_nodes(diagnostics, nodes)
    assert [d["nodes"] for d in diagnostics] == [[1], [0], []]
