"""The Qiskit (Python, static) route: same graph as the executing route, nothing run."""

import pytest

from webapp_helpers import BELL_PY, post_analyze, post_slice


def shape(body):
    return [(n["name"], n["qubits"], n["clbits"]) for n in body["nodes"]]


def test_static_route_matches_the_executing_route(client):
    _, executed = post_analyze(client, source=BELL_PY, language="qiskit")
    _, static = post_analyze(client, source=BELL_PY, language="qiskit-static")
    assert executed["ok"] and static["ok"]
    assert static["language"] == "python-static"
    assert "statically" in static["route"]
    assert shape(static) == shape(executed)
    assert static["edges"] == executed["edges"]


def test_routes_do_not_share_a_cache_token(client):
    _, executed = post_analyze(client, source=BELL_PY, language="qiskit")
    _, static = post_analyze(client, source=BELL_PY, language="qiskit-static")
    assert executed["token"] != static["token"]
    response, sliced = post_slice(client, token=static["token"], node_idx=3)
    assert response.status_code == 200 and sliced["ok"]


def test_static_route_never_executes_the_file(client, tmp_path):
    marker = tmp_path / "ran.txt"
    source = BELL_PY + f"open({str(marker)!r}, 'w').write('ran')\n"
    _, body = post_analyze(client, source=source, language="qiskit-static")
    assert body["ok"]
    assert not marker.exists()


def test_static_warnings_become_diagnostics(client):
    source = BELL_PY + "import random\nfor shot in range(random.randint(1, 5)):\n    print(shot)\n"
    _, body = post_analyze(client, source=source, language="qiskit-static")
    assert body["ok"]
    found = [d for d in body["diagnostics"] if d["code"] == "QS200"]
    assert found and found[0]["severity"] == "warning" and found[0]["line"] == 9


def test_static_refusal_is_reported_with_its_line(client):
    source = BELL_PY + "import random\nqc.x(random.randint(0, 1))\n"
    _, body = post_analyze(client, source=source, language="qiskit-static")
    assert not body["ok"]
    assert body["error"]["kind"] == "static_error"
    assert body["error"]["line"] == 9
    assert "Qiskit (Python)" in body["error"]["hint"]


def test_static_route_without_circuit_is_reported(client):
    _, body = post_analyze(client, source="x = 1\n", language="qiskit-static")
    assert not body["ok"]
    assert body["error"]["kind"] == "no_circuit"


@pytest.mark.parametrize("language", ["qiskit-static", "static"])
def test_static_language_ids_are_accepted(client, language):
    response, body = post_analyze(client, source=BELL_PY, language=language)
    assert response.status_code == 200 and body["ok"]
