"""What /api/analyze costs: one dependence graph, finished responses reused, gzip.

None of this may change what a response says. A repeated request gets the very
bytes the first one did, and only while the token in them still slices.
"""

import gzip
from collections import OrderedDict

import pytest

import analysis
import app as app_module
from qslicer import qdg as qdg_module
from qslicer.lint import registry as linters
from webapp_helpers import GHZ_QASM, post_analyze, post_slice

# Big enough to be gzipped, with a gate after the last measurement (a QD01 finding).
WIDE_PY = """from qiskit import QuantumCircuit

qc = QuantumCircuit(4, 4)
for layer in range(6):
    for q in range(4):
        qc.rx(0.25 * (layer + 1), q)
    for q in range(3):
        qc.cx(q, q + 1)
qc.measure(range(4), range(4))
qc.h(0)
"""

GHZ_DEAD_QASM = GHZ_QASM + "x q[0];\n"


@pytest.fixture
def fresh_cache(monkeypatch):
    """An empty token cache, so the session's earlier requests cannot answer this test's."""
    monkeypatch.setattr(analysis, "_CACHE", OrderedDict())


def analyze(client, headers=None, **payload):
    payload.setdefault("source", WIDE_PY)
    payload.setdefault("filename", "wide.py")
    payload.setdefault("language", "qiskit-static")
    return client.post("/api/analyze", json=payload, headers=headers or {})


def count_qdgs(monkeypatch):
    built = []
    original = qdg_module.QDG.__init__

    def counting(self, *args, **kwargs):
        built.append(self)
        original(self, *args, **kwargs)

    monkeypatch.setattr(qdg_module.QDG, "__init__", counting)
    return built


# ------------------------------------------------------------ one QDG --

@pytest.mark.parametrize("source, filename, language", [
    (WIDE_PY, "wide.py", "qiskit-static"),
    (GHZ_DEAD_QASM, "ghz.qasm", "qasm-static"),
])
def test_analysis_builds_one_dependence_graph(client, fresh_cache, monkeypatch,
                                              source, filename, language):
    built = count_qdgs(monkeypatch)
    _, body = post_analyze(client, source=source, filename=filename, language=language)
    assert body["ok"], body
    # The dependence checks ran on it (QD01 needs the graph) and the slicer keeps it.
    assert "QD01" in {d["code"] for d in body["diagnostics"]}
    assert len(built) == 1
    assert analysis._CACHE[body["token"]]["qdg"] is built[0]


@pytest.mark.parametrize("source, filename, kind", [
    (WIDE_PY, "wide.py", "python-static"),
    (GHZ_DEAD_QASM, "ghz.qasm", "qasm-static"),
])
def test_shared_graph_gives_the_findings_a_lint_run_of_its_own_gives(client, fresh_cache,
                                                                     source, filename, kind):
    language = "qiskit-static" if kind == "python-static" else "qasm-static"
    _, body = post_analyze(client, source=source, filename=filename, language=language,
                           lint_rules=["dependence"])
    alone = linters.run(source, filename, kind, ["dependence"])
    assert alone
    assert ([(d["code"], d["line"], d["message"]) for d in body["diagnostics"]]
            == [(d["code"], d["line"], d["message"]) for d in alone])


def test_cli_style_lint_run_still_builds_its_own_graph(monkeypatch):
    built = count_qdgs(monkeypatch)
    found = linters.run(WIDE_PY, "wide.py", "python-static", ["dependence/QD01"])
    assert [d["code"] for d in found] == ["QD01"] and len(built) == 1


# ------------------------------------------------------ response cache --

def test_identical_request_is_answered_from_the_cache(client, fresh_cache, monkeypatch):
    first = analyze(client)
    assert first.headers["X-Analysis-Cache"] == "miss"
    monkeypatch.setattr(analysis, "analyze_source",
                        lambda *a, **k: pytest.fail("analyzed again"))
    again = analyze(client)
    assert again.headers["X-Analysis-Cache"] == "hit"
    assert again.get_data() == first.get_data()
    # The token in the stored response still slices.
    response, sliced = post_slice(client, token=again.get_json()["token"], node_idx=0)
    assert response.status_code == 200 and sliced["ok"]


def test_a_hit_counts_as_using_the_token(client, fresh_cache):
    token = analyze(client).get_json()["token"]
    other = analyze(client, source=WIDE_PY + "qc.x(1)\n").get_json()["token"]
    assert list(analysis._CACHE) == [token, other]
    assert analyze(client).headers["X-Analysis-Cache"] == "hit"
    assert list(analysis._CACHE) == [other, token]


def test_cache_key_is_the_whole_request(client, fresh_cache):
    first = analyze(client)
    token = first.get_json()["token"]
    # The default selection, spelled out, is the same request as leaving it out.
    spelled = analyze(client, lint_rules=linters.default_selection())
    assert spelled.headers["X-Analysis-Cache"] == "hit"
    # Another selection is another response, for the same program and token...
    quick = analyze(client, lint_rules=["quick"])
    assert quick.headers["X-Analysis-Cache"] == "miss"
    assert quick.get_json()["token"] == token
    assert quick.get_json()["lint_rules"] != first.get_json()["lint_rules"]
    # ...and both are kept with it.
    assert analyze(client).headers["X-Analysis-Cache"] == "hit"
    assert analyze(client, lint_rules=["quick"]).headers["X-Analysis-Cache"] == "hit"
    assert len(analysis._CACHE[token]["responses"]) == 2
    for change in ({"filename": "other.py"}, {"profile": "no-such-profile"},
                   {"source": WIDE_PY + "\n"}, {"circuit": 1},
                   {"bindings": [{"name": "n", "line": 1, "value": "1"}]}):
        assert analyze(client, **change).headers["X-Analysis-Cache"] == "miss", change


def test_cached_response_never_outlives_its_token(client, fresh_cache, monkeypatch):
    monkeypatch.setattr(analysis, "_CACHE_LIMIT", 1)
    first = analyze(client)
    analyze(client, source=GHZ_QASM, filename="ghz.qasm", language="qasm-static")   # evicts it
    assert first.get_json()["token"] not in analysis._CACHE
    again = analyze(client)
    assert again.headers["X-Analysis-Cache"] == "miss"
    assert again.get_data() == first.get_data()
    response, _ = post_slice(client, token=again.get_json()["token"], node_idx=0)
    assert response.status_code == 200


def test_a_dropped_token_cache_drops_its_responses(client, fresh_cache, monkeypatch):
    analyze(client)
    monkeypatch.setattr(analysis, "_CACHE", OrderedDict())    # as after a restart
    again = analyze(client)
    assert again.headers["X-Analysis-Cache"] == "miss"
    response, _ = post_slice(client, token=again.get_json()["token"], node_idx=0)
    assert response.status_code == 200


def test_failed_analyses_are_not_cached(client, fresh_cache):
    for _ in range(2):
        response = analyze(client, source="OPENQASM 3.0;\nqubit[1] q;\nnot_a_gate q[0];\n",
                           filename="bad.qasm", language="qasm-static")
        assert response.get_json()["ok"] is False
        assert response.headers["X-Analysis-Cache"] == "miss"


def test_responses_stay_under_the_byte_limit(client, fresh_cache, monkeypatch):
    first = analyze(client)
    token = first.get_json()["token"]
    record = next(iter(analysis._CACHE[token]["responses"].values()))
    one = len(record["body"]) + len(record["gzip"] or b"")
    monkeypatch.setattr(analysis, "_RESPONSE_CACHE_BYTES", one + 100)
    second = analyze(client, lint_rules=["quick"])            # same token, another response
    assert second.headers["X-Analysis-Cache"] == "miss"
    stored = analysis._CACHE[token]["responses"]
    assert len(stored) == 1                                  # the older one made room
    assert analyze(client, lint_rules=["quick"]).headers["X-Analysis-Cache"] == "hit"
    assert analyze(client).headers["X-Analysis-Cache"] == "miss"
    # A body over the limit on its own is not kept at all.
    monkeypatch.setattr(analysis, "_RESPONSE_CACHE_BYTES", 10)
    assert analysis.store_response("k", token, b"x" * 11) is None
    assert analysis.cached_response("k") is None


def test_a_response_is_only_stored_with_a_cached_token(fresh_cache):
    assert analysis.store_response("k", "no-such-token", b"{}") is None
    assert analysis.cached_response("k") is None


def test_no_cached_answers_without_the_backend(client, fresh_cache, monkeypatch):
    analyze(client)
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "ImportError: nope")
    body = analyze(client).get_json()
    assert body["ok"] is False and body["error"]["kind"] == "backend_unavailable"


# ----------------------------------------------------------------- gzip --

GZIP = {"Accept-Encoding": "gzip, deflate, br"}


@pytest.mark.parametrize("first_gzipped", [True, False])
def test_analyze_is_gzipped_for_a_client_that_accepts_it(client, fresh_cache, first_gzipped):
    # Either way round: compressed on a miss, and the stored copy on a hit.
    if first_gzipped:
        packed, plain = analyze(client, headers=GZIP), analyze(client)
    else:
        plain, packed = analyze(client), analyze(client, headers=GZIP)
    assert len(plain.get_data()) >= app_module.GZIP_MIN_BYTES
    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    for response in (plain, packed):
        assert "Accept-Encoding" in response.headers["Vary"]
    assert int(packed.headers["Content-Length"]) == len(packed.get_data())
    assert len(packed.get_data()) < len(plain.get_data()) // 3
    assert gzip.decompress(packed.get_data()) == plain.get_data()


@pytest.mark.parametrize("header", ["gzip;q=0", "identity", "br", ""])
def test_no_gzip_unless_accepted(client, fresh_cache, header):
    response = analyze(client, headers={"Accept-Encoding": header})
    assert "Content-Encoding" not in response.headers
    assert response.get_json()["ok"]


def test_wildcard_accepts_gzip(client, fresh_cache):
    assert analyze(client, headers={"Accept-Encoding": "*"}).headers["Content-Encoding"] == "gzip"


def test_small_responses_are_not_compressed(client, fresh_cache):
    token = analyze(client).get_json()["token"]
    response = client.post("/api/slice", json={"token": token, "node_idx": 0}, headers=GZIP)
    assert len(response.get_data()) < app_module.GZIP_MIN_BYTES
    assert "Content-Encoding" not in response.headers
    assert response.get_json()["ok"]


@pytest.mark.parametrize("path", ["/api/linters", "/api/examples", "/api/benchmark"])
def test_other_large_api_responses_are_gzipped(client, path):
    plain = client.get(path)
    packed = client.get(path, headers=GZIP)
    assert len(plain.get_data()) >= app_module.GZIP_MIN_BYTES
    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in packed.headers["Vary"]
    assert gzip.decompress(packed.get_data()) == plain.get_data()


def test_pages_are_left_alone(client):
    response = client.get("/slicer", headers=GZIP)
    assert response.status_code == 200 and "Content-Encoding" not in response.headers
