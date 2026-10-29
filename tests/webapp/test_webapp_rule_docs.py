"""rule_docs.py and /api/linters/rules/<id>: every rule explained, every example real."""

import pytest

import linters
import rule_docs

RULE_IDS = [rule["id"] for linter in linters.catalog() for rule in linter["rules"]]
CODES = {rule_id.partition("/")[2] for rule_id in RULE_IDS}


def test_every_rule_is_documented_and_no_entry_is_stale():
    assert CODES == set(rule_docs.DOCS)
    for code, entry in rule_docs.DOCS.items():
        assert entry["explain"], code
        assert (entry["example"] is None) == (entry["fixed"] is None), code


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_example_fires_and_fix_does_not(client, rule_id):
    body = client.get(f"/api/linters/rules/{rule_id}").get_json()
    assert body["id"] == rule_id and body["explain"]
    if body["example"] is None:
        assert body["findings"] is None
        return
    found = body["findings"]
    assert found["example"], f"{rule_id} does not fire on its own example"
    assert found["fixed"] == [], f"{rule_id} still fires on its fixed example"
    lines = body["example"].splitlines()
    assert all(0 < finding["line"] <= len(lines) for finding in found["example"])


def test_codeql_rules_are_shown_with_the_port(client):
    body = client.get("/api/linters/rules/lintq-codeql/LQ02").get_json()
    assert body["run_with"] == "lintq" and body["findings"]["example"]


def test_unknown_rule_is_404(client):
    assert client.get("/api/linters/rules/quick/QS999").status_code == 404
