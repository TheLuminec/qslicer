"""Fixtures for the web app tests.

The parent ``tests/conftest.py`` puts ``webapp/`` on sys.path,
so ``import app`` / ``import analysis`` resolve exactly as they do at runtime.

Every route parses in-process and runs nothing. Tests that need a full
analysis of the examples share results through the session-scoped fixtures
here.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

import analysis
import app as app_module

from webapp_helpers import GHZ_QASM, manifest_entries, post_analyze  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _roomy_cache():
    """Keep every analysis of the session in the token cache.

    The real cache holds 24 entries; the example sweep alone produces 24, so
    without this a later slice request could find its token evicted depending
    on test order. The eviction behavior itself is tested separately with a
    deliberately small limit.
    """
    mp = pytest.MonkeyPatch()
    mp.setattr(analysis, "_CACHE_LIMIT", 10_000)
    yield
    mp.undo()


@pytest.fixture(scope="session")
def flask_app():
    app_module.app.config["TESTING"] = True
    # Off unless asked for (--benchmark); on here so its pages and API are tested.
    app_module.app.config["BENCHMARK"] = True
    return app_module.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture(scope="session")
def session_client(flask_app):
    return flask_app.test_client()


@pytest.fixture(scope="session")
def ghz_qasm_result(session_client):
    response, body = post_analyze(session_client, source=GHZ_QASM, filename="ghz.qasm",
                                  language="qasm")
    assert response.status_code == 200, body
    assert body["ok"], body
    return body


@pytest.fixture(scope="session")
def example_results(flask_app):
    """Every manifest example analyzed once through /api/analyze.

    They run on a small thread pool. Returns {key: (status, body)}.
    """
    keys = [entry["key"] for entry in manifest_entries()]

    def run(key):
        client = flask_app.test_client()
        entry = client.get(f"/api/examples/{key}").get_json()
        response = client.post("/api/analyze", json={
            "source": entry["source"],
            "filename": entry["filename"],
            "language": entry["interpreter"],
            "profile": "baseline",
        })
        return key, (response.status_code, response.get_json(), entry)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(run, keys))


@pytest.fixture(scope="session")
def bell_result(example_results):
    status, body, _ = example_results["bell_pair"]
    assert status == 200 and body["ok"], body
    return body


@pytest.fixture(scope="session")
def teleport_result(example_results):
    status, body, _ = example_results["quantum_teleportation"]
    assert status == 200 and body["ok"], body
    return body
