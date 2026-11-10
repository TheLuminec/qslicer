"""Slices through /api/slice: hand-derived expectations and the FR-15 invariant.

FR-15 is checked independently of the slicer: the returned edges are loaded
into a networkx MultiDiGraph, filtered to the chosen dependency types, and the
slice must be exactly the criterion plus every node with a path to it
(backward) or from it (forward).
"""

import itertools

import networkx as nx
import pytest

from webapp_helpers import post_slice

ALL_DEPS = ["DATA_QUBIT", "MEASUREMENT", "DATA_CLASSICAL", "CONTROL_CLASSICAL"]


def slice_of(client, result, node_idx, direction="backward", dep_types=None):
    payload = {"token": result["token"], "node_idx": node_idx, "direction": direction}
    if dep_types is not None:
        payload["dep_types"] = dep_types
    response, body = post_slice(client, **payload)
    assert response.status_code == 200, body
    assert body["ok"] is True
    assert body["size"] == len(body["slice"])
    assert body["total"] == len(result["nodes"])
    assert body["slice"] == sorted(body["slice"])
    return body["slice"]


def expected_by_reachability(result, node_idx, direction, dep_types):
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(range(len(result["nodes"])))
    for edge in result["edges"]:
        if dep_types is None or edge["dependency"] in dep_types:
            graph.add_edge(edge["source"], edge["target"])
    related = nx.ancestors(graph, node_idx) if direction == "backward" \
        else nx.descendants(graph, node_idx)
    return sorted(related | {node_idx})


def dep_choices(result):
    offered = [d["name"] for d in result["dependency_types"]]
    choices = [None] + [[name] for name in offered]
    choices += [list(pair) for pair in itertools.combinations(offered, 2)]
    return choices


def assert_fr15(client, result):
    for node_idx in range(len(result["nodes"])):
        for direction in ("backward", "forward"):
            for deps in dep_choices(result):
                got = slice_of(client, result, node_idx, direction, deps)
                assert node_idx in got
                assert got == expected_by_reachability(result, node_idx, direction, deps), \
                    (node_idx, direction, deps)


# ----------------------------------------------- GHZ in OpenQASM (fast) --
#   0 h q0 | 1 cx q0,q1 | 2 cx q0,q2 | 3 measure q0 | 4 measure q1 | 5 measure q2

@pytest.mark.requirement("FR-12")
@pytest.mark.requirement("VV-05")
@pytest.mark.parametrize("node_idx, expected", [
    (0, [0]),
    (3, [0, 1, 2, 3]),
    (4, [0, 1, 4]),
    (5, [0, 1, 2, 5]),
])
def test_ghz_backward_slices(client, ghz_qasm_result, node_idx, expected):
    assert slice_of(client, ghz_qasm_result, node_idx) == expected


@pytest.mark.requirement("FR-13")
@pytest.mark.requirement("VV-05")
@pytest.mark.parametrize("node_idx, expected", [
    (0, [0, 1, 2, 3, 4, 5]),
    (1, [1, 2, 3, 4, 5]),
    (2, [2, 3, 5]),
    (4, [4]),
])
def test_ghz_forward_slices(client, ghz_qasm_result, node_idx, expected):
    assert slice_of(client, ghz_qasm_result, node_idx, "forward") == expected


@pytest.mark.requirement("FR-14")
def test_ghz_measurement_only_slice_is_just_the_criterion(client, ghz_qasm_result):
    # No classical feed-forward in GHZ, so no measurement edges to follow.
    assert slice_of(client, ghz_qasm_result, 5, dep_types=["MEASUREMENT"]) == [5]


@pytest.mark.requirement("FR-15")
@pytest.mark.requirement("VV-05")
def test_ghz_slices_match_reachability(client, ghz_qasm_result):
    assert_fr15(client, ghz_qasm_result)


@pytest.mark.requirement("NFR-08")
@pytest.mark.requirement("VV-08")
def test_same_qasm_input_twice_is_identical(client, ghz_qasm_result):
    from webapp_helpers import GHZ_QASM, post_analyze

    runs = [post_analyze(client, source=GHZ_QASM, filename="ghz.qasm", language="qasm")[1]
            for _ in range(3)]
    for run in runs:
        for key in ("token", "nodes", "edges", "stats", "dependency_types"):
            assert run[key] == ghz_qasm_result[key], key
    for idx in range(6):
        for direction in ("backward", "forward"):
            slices = {tuple(slice_of(client, run, idx, direction)) for run in runs}
            assert len(slices) == 1


# --------------------------------------------- Bell pair (Python, slow) --
#   0 h q0 | 1 cx q0,q1 | 2 measure q0 | 3 measure q1

@pytest.mark.slow
@pytest.mark.requirement("FR-12")
@pytest.mark.requirement("FR-13")
@pytest.mark.requirement("VV-05")
def test_bell_pair_hand_derived_slices(client, bell_result):
    assert slice_of(client, bell_result, 2) == [0, 1, 2]
    assert slice_of(client, bell_result, 3) == [0, 1, 3]
    assert slice_of(client, bell_result, 0, "forward") == [0, 1, 2, 3]
    assert slice_of(client, bell_result, 1, "forward") == [1, 2, 3]
    assert slice_of(client, bell_result, 2, "forward") == [2]


@pytest.mark.slow
@pytest.mark.requirement("FR-15")
def test_bell_pair_slices_match_reachability(client, bell_result):
    assert_fr15(client, bell_result)


# ---------------------------------------- teleportation (Python, slow) --
#   0 ry q0 | 1 rz q0 | 2 h q1 | 3 cx q1,q2 | 4 cx q0,q1 | 5 h q0
#   6 measure q0->c0 | 7 measure q1->c1 | 8 if(c1) x q2 | 9 if(c0) z q2 | 10 measure q2

@pytest.mark.slow
@pytest.mark.requirement("FR-12")
@pytest.mark.requirement("VV-05")
def test_teleportation_backward_from_final_measurement_is_everything(client, teleport_result):
    assert slice_of(client, teleport_result, 10) == list(range(11))


@pytest.mark.slow
@pytest.mark.requirement("FR-14")
@pytest.mark.requirement("VV-05")
def test_teleportation_measurement_only_from_final_measurement(client, teleport_result):
    assert slice_of(client, teleport_result, 10, dep_types=["MEASUREMENT"]) == [10]


@pytest.mark.slow
@pytest.mark.requirement("FR-14")
@pytest.mark.requirement("VV-05")
def test_teleportation_measurement_only_from_first_correction(client, teleport_result):
    # #8 is conditioned on c1 alone, so only the measurement into c1 (#7) reaches it.
    assert slice_of(client, teleport_result, 8, dep_types=["MEASUREMENT"]) == [7, 8]
    # #9 is conditioned on c0, written by the measurement at #6.
    assert slice_of(client, teleport_result, 9, dep_types=["MEASUREMENT"]) == [6, 9]


@pytest.mark.slow
@pytest.mark.requirement("FR-13")
@pytest.mark.requirement("VV-05")
def test_teleportation_forward_from_bell_cnot(client, teleport_result):
    assert slice_of(client, teleport_result, 3, "forward") == list(range(3, 11))


@pytest.mark.slow
@pytest.mark.requirement("FR-14")
def test_teleportation_data_qubit_only_skips_the_classical_link(client, teleport_result):
    # Bob's wire q2 is reached from Alice's measurements only via classical bits.
    got = slice_of(client, teleport_result, 10, dep_types=["DATA_QUBIT"])
    assert 6 not in got and 7 not in got
    assert {2, 3, 8, 9, 10} <= set(got)


@pytest.mark.slow
@pytest.mark.requirement("FR-15")
@pytest.mark.requirement("VV-05")
def test_teleportation_slices_match_reachability(client, teleport_result):
    assert_fr15(client, teleport_result)


@pytest.mark.slow
@pytest.mark.requirement("FR-07")
def test_teleportation_condition_clbits(teleport_result):
    assert teleport_result["nodes"][8]["condition_clbits"] == [1]
    assert teleport_result["nodes"][9]["condition_clbits"] == [0]


# ------------------------------------------------ chop and multi-selection --
# GHZ edges: 0->1, 1->2, 1->4, 2->3, 2->5 (all DATA_QUBIT).

def post_ok(client, result, **payload):
    response, body = post_slice(client, token=result["token"], **payload)
    assert response.status_code == 200, body
    assert body["ok"] is True
    assert body["size"] == len(body["slice"])
    assert body["total"] == len(result["nodes"])
    return body


@pytest.mark.parametrize("source, target, expected", [
    (1, 3, [1, 2, 3]),
    (0, 4, [0, 1, 4]),
    (2, 2, [2]),
])
def test_ghz_chop(client, ghz_qasm_result, source, target, expected):
    body = post_ok(client, ghz_qasm_result, node_idx=source, target_idx=target,
                   direction="chop")
    assert body["direction"] == "chop"
    assert body["node_idx"] == source and body["target_idx"] == target
    assert body["slice"] == expected
    assert "empty_reason" not in body


@pytest.mark.parametrize("source, target, deps", [
    (4, 5, None),             # different wires after the split
    (3, 0, None),             # against the edges
    (0, 4, ["MEASUREMENT"]),  # a path exists, but not through this dependency type
])
def test_ghz_empty_chop_says_why(client, ghz_qasm_result, source, target, deps):
    payload = dict(node_idx=source, target_idx=target, direction="chop")
    if deps is not None:
        payload["dep_types"] = deps
    body = post_ok(client, ghz_qasm_result, **payload)
    assert body["slice"] == [] and body["size"] == 0
    assert body["empty_reason"] == "This operation cannot affect that measurement."


def test_ghz_chop_matches_forward_meet_backward(client, ghz_qasm_result):
    n = len(ghz_qasm_result["nodes"])
    for source in range(n):
        for target in range(n):
            got = post_ok(client, ghz_qasm_result, node_idx=source, target_idx=target,
                          direction="chop")["slice"]
            fwd = set(expected_by_reachability(ghz_qasm_result, source, "forward", None))
            bwd = set(expected_by_reachability(ghz_qasm_result, target, "backward", None))
            assert got == sorted(fwd & bwd), (source, target)


def test_ghz_multi_selection_unions_in_given_order(client, ghz_qasm_result):
    body = post_ok(client, ghz_qasm_result, node_indices=[5, 4, 5])
    assert body["direction"] == "backward"
    assert body["node_idx"] == 5
    assert body["node_indices"] == [5, 4]
    assert body["slices"] == [
        {"node_idx": 5, "slice": [0, 1, 2, 5], "size": 4},
        {"node_idx": 4, "slice": [0, 1, 4], "size": 3},
    ]
    assert body["slice"] == [0, 1, 2, 4, 5]


def test_ghz_multi_selection_takes_precedence_and_filters(client, ghz_qasm_result):
    body = post_ok(client, ghz_qasm_result, node_idx=0, node_indices=[3],
                   dep_types=["MEASUREMENT"])
    assert body["node_idx"] == 3 and body["slice"] == [3]
    assert body["slices"] == [{"node_idx": 3, "slice": [3], "size": 1}]


def test_plain_slice_has_no_new_fields(client, ghz_qasm_result):
    body = post_ok(client, ghz_qasm_result, node_idx=4)
    assert set(body) == {"ok", "direction", "node_idx", "dep_types", "slice", "size", "total"}


@pytest.mark.parametrize("payload, message", [
    ({"node_idx": 0, "direction": "chop"}, "target_idx"),
    ({"node_idx": 0, "direction": "chop", "target_idx": "3"}, "target_idx"),
    ({"node_idx": 0, "direction": "chop", "target_idx": True}, "target_idx"),
    ({"node_indices": []}, "node_indices"),
    ({"node_indices": list(range(6)) * 3}, "node_indices"),
    ({"node_indices": [0, True]}, "node_indices"),
    ({"node_indices": [0, 1.0]}, "node_indices"),
    ({"node_indices": 3}, "node_indices"),
    ({"node_indices": [0], "direction": "forward"}, "backward"),
    ({"node_indices": [0], "direction": "chop", "target_idx": 3}, "backward"),
])
def test_chop_and_multi_reject_malformed_input(client, ghz_qasm_result, payload, message):
    response, body = post_slice(client, token=ghz_qasm_result["token"], **payload)
    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error"]["kind"] == "invalid_request"
    assert body["error"]["stage"] == "slicing"
    assert message in body["error"]["message"]


@pytest.mark.parametrize("payload", [
    {"node_idx": 0, "direction": "chop", "target_idx": 6},
    {"node_idx": 0, "direction": "chop", "target_idx": -1},
    {"node_idx": 9, "direction": "chop", "target_idx": 3},
    {"node_indices": [0, 6]},
    {"node_indices": [-1]},
])
def test_chop_and_multi_out_of_range(client, ghz_qasm_result, payload):
    response, body = post_slice(client, token=ghz_qasm_result["token"], **payload)
    assert response.status_code == 409
    assert body["ok"] is False
    assert body["error"]["kind"] == "out_of_range"
    assert body["error"]["stage"] == "slicing"
    assert "outside this program" in body["error"]["message"]
    assert "slice" not in body


def test_multi_selection_still_needs_a_token(client):
    response, body = post_slice(client, node_indices=[0])
    assert response.status_code == 400 and body["error"]["kind"] == "invalid_request"


def test_analysis_rejects_malformed_chop_and_multi(ghz_qasm_result):
    import analysis

    token = ghz_qasm_result["token"]
    for kwargs in ({"direction": "chop", "node_idx": 0},
                   {"node_indices": []},
                   {"node_indices": [0], "direction": "forward"}):
        result = analysis.slice_program(token, **kwargs)
        assert result["ok"] is False and result["error"]["kind"] == "invalid_request"
