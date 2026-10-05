"""FR-07: graph nodes link back to the source that produced them."""

import pytest

import analysis
from webapp_helpers import BELL_PY, GHZ_QASM, post_analyze, post_slice


@pytest.mark.requirement("FR-07")
def test_static_qasm_nodes_carry_source_location(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm-static")
    assert body["ok"], body
    # GHZ_QASM: the six instructions are on lines 5-10.
    assert [node["line"] for node in body["nodes"]] == [5, 6, 7, 8, 9, 10]
    assert body["nodes"][1]["source"] == {
        "line": 6, "column": 0, "end_line": 6, "end_column": 14, "via": []}


@pytest.mark.requirement("FR-07")
def test_static_python_nodes_carry_source_location(client):
    _, body = post_analyze(client, source=BELL_PY, language="qiskit-static")
    assert body["ok"], body
    assert [node["line"] for node in body["nodes"]] == [4, 5, 6, 7]


@pytest.mark.requirement("FR-07")
def test_location_records_the_call_path(client):
    source = (BELL_PY + "def flip(circuit):\n    circuit.x(1)\n\nflip(qc)\n")
    _, body = post_analyze(client, source=source, language="qiskit-static")
    flip = body["nodes"][-1]
    assert (flip["name"], flip["line"], flip["source"]["via"]) == ("x", 9, [11])


@pytest.mark.requirement("FR-07")
@pytest.mark.parametrize("language, source", [("qiskit", BELL_PY), ("qasm", GHZ_QASM)])
def test_routes_without_locations_send_none(client, language, source):
    _, body = post_analyze(client, source=source, language=language)
    assert body["ok"]
    assert all(node["line"] is None and node["source"] is None for node in body["nodes"])


@pytest.mark.requirement("FR-07")
def test_static_qasm_matches_the_qiskit_qasm_route(client):
    _, imported = post_analyze(client, source=GHZ_QASM, language="qasm")
    _, static = post_analyze(client, source=GHZ_QASM, language="qasm-static")
    shape = lambda body: [(n["name"], n["qubits"], n["clbits"]) for n in body["nodes"]]
    assert shape(static) == shape(imported)
    assert static["edges"] == imported["edges"]
    assert static["token"] != imported["token"]
    assert "source locations" in static["route"]


@pytest.mark.requirement("FR-07")
def test_slice_ids_map_to_source_lines(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm-static")
    response, sliced = post_slice(client, token=body["token"], node_idx=4)
    assert response.status_code == 200 and sliced["ok"]
    lines = sorted({body["nodes"][i]["line"] for i in sliced["slice"]})
    assert lines == [5, 6, 9]       # h, the cx onto q1, and the measurement of q1


def test_static_qasm_errors_point_at_their_line(client):
    source = GHZ_QASM + "if (q[0]) { x q[1]; }\n"
    _, body = post_analyze(client, source=source, language="qasm-static")
    assert not body["ok"]
    assert body["error"]["kind"] == "qasm_error"
    assert body["error"]["line"] == 11


def test_static_qasm_skips_python_lint(client):
    _, body = post_analyze(client, source=GHZ_QASM, language="qasm-static")
    assert [d["code"] for d in body["diagnostics"]] == ["QS100"]


def test_qasm_static_default_filename():
    assert analysis.default_filename("qasm-static") == "pasted_circuit.qasm"
