"""Requirements the web app does not implement yet: one strict xfail each.

Each test states the expected behavior; when a gap is closed the xfail turns
into an XPASS failure, which is the cue to remove the marker.
"""

import pytest

import analysis
from webapp_helpers import GHZ_QASM, post_analyze


@pytest.mark.requirement("FR-07")
def test_ir_fields_present_in_nodes(ghz_qasm_result):
    for node in ghz_qasm_result["nodes"]:
        for key in ("name", "qubits", "clbits", "params"):
            assert key in node


@pytest.mark.requirement("FR-07")
@pytest.mark.xfail(strict=True, reason="FR-07: neither the IR nor the node payload records a "
                   "source location (line) for each instruction")
def test_nodes_carry_source_location(ghz_qasm_result):
    lines = [node.get("line") for node in ghz_qasm_result["nodes"]]
    # GHZ_QASM: the six instructions are on lines 5-10.
    assert lines == [5, 6, 7, 8, 9, 10]


@pytest.mark.requirement("FR-10")
@pytest.mark.xfail(strict=True, reason="FR-10: there is only the 'baseline' rule profile, with "
                   "the entanglement and phase rules commented out in scripts/qpdg.py, and no "
                   "per-analysis toggle; the API cannot enable them")
def test_entanglement_and_phase_rules_can_be_enabled_per_analysis(client):
    offered = set()
    for profile in analysis.rule_profiles():
        _, body = post_analyze(client, source=GHZ_QASM, language="qasm", profile=profile)
        offered |= {d["name"] for d in body["dependency_types"]}
    assert {"ENTANGLEMENT", "PHASE"} <= offered


@pytest.mark.requirement("FR-16")
@pytest.mark.requirement("VV-06")
@pytest.mark.xfail(strict=True, reason="FR-16: an entangling gate uncomputed before the "
                   "criterion's measurement is still included in the backward slice")
def test_uncomputed_entangling_gate_is_excluded(client):
    # 0 h q0 | 1 cx q0,q1 | 2 cx q0,q1 (uncomputes 1) | 3 measure q1
    source = ('OPENQASM 3.0;\ninclude "stdgates.inc";\nbit[2] c;\nqubit[2] q;\n'
              'h q[0];\ncx q[0], q[1];\ncx q[0], q[1];\nc[1] = measure q[1];\n')
    _, body = post_analyze(client, source=source, language="qasm")
    assert body["ok"], body
    sliced = client.post("/api/slice", json={"token": body["token"], "node_idx": 3}).get_json()
    assert 1 not in sliced["slice"] and 2 not in sliced["slice"]


@pytest.mark.requirement("FR-22")
@pytest.mark.xfail(strict=True, reason="FR-22: the slicer exports the graph as PNG only; "
                   "there is no JSON export of the slice or graph")
def test_slicer_offers_json_export(client):
    import re

    page = client.get("/slicer").get_data(as_text=True)
    # The PNG control is id="download-png"; a JSON one would sit beside it.
    assert 'id="download-png"' in page
    assert re.search(r'id="(download|export)-json"', page)
