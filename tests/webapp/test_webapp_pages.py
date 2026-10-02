"""Server-rendered pages: every route renders, nav links, page content, 404, banner, wsgi."""

import html
import re

import pytest

import analysis
import examples
import gates
from team import COURSE, MEMBERS, MISSION, PROJECT_BLURB, VISION

PAGES = {
    "/": "index",
    "/slicer": "slicer",
    "/quantum": "quantum",
    "/team": "team",
}
NAV = [("/", "Home"), ("/slicer", "Slicer"), ("/quantum", "Quantum Info"), ("/team", "Team")]


def _text(response):
    return html.unescape(response.get_data(as_text=True))


@pytest.mark.parametrize("path", list(PAGES))
def test_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    body = _text(response)
    assert "<title>" in body and "Quantum Program Slicer" in body
    assert f'class="page-{PAGES[path]}"' in body


@pytest.mark.requirement("FR-24")
@pytest.mark.parametrize("path", list(PAGES))
def test_every_page_has_full_nav(client, path):
    body = _text(client.get(path))
    nav = re.search(r'<nav class="site-nav".*?</nav>', body, re.S)
    assert nav, "main nav missing"
    for href, label in NAV:
        assert re.search(rf'<a href="{re.escape(href)}"[^>]*>{re.escape(label)}</a>', nav.group(0)), \
            (href, label)


@pytest.mark.requirement("FR-24")
@pytest.mark.parametrize("path", list(PAGES))
def test_current_page_is_marked_active(client, path):
    body = _text(client.get(path))
    active = re.findall(r'<a href="([^"]+)"\s+class="active"\s+aria-current="page"', body)
    assert active == [path]


@pytest.mark.requirement("FR-24")
def test_about_and_slicer_link_each_other(client):
    # The "About" content lives on /quantum (and the landing page); both reach the slicer.
    assert 'href="/slicer"' in _text(client.get("/quantum"))
    assert 'href="/quantum"' in _text(client.get("/slicer"))
    assert 'href="/slicer"' in _text(client.get("/"))


@pytest.mark.requirement("FR-25")
def test_quantum_page_describes_slicing_and_the_tool(client):
    body = _text(client.get("/quantum"))
    for heading in ("What slicing means", "The dependency types", "How our slicer works",
                    "Walkthrough: teleportation", "Gate reference"):
        assert heading in body
    lowered = body.lower()
    for term in ("backward slice", "forward slice", "qpdg", "entanglement", "measurement"):
        assert term in lowered, term


@pytest.mark.requirement("FR-25")
def test_quantum_page_lists_every_glossary_gate(client):
    body = _text(client.get("/quantum"))
    for entry in gates.glossary():
        assert entry["title"] in body, entry["name"]


@pytest.mark.requirement("FR-01")
def test_slicer_page_offers_all_three_input_methods(client):
    body = _text(client.get("/slicer"))
    assert re.search(r'<input id="file-input" type="file"[^>]*accept="[^"]*\.py[^"]*\.qasm', body)
    assert '<textarea id="code"' in body
    assert '<select id="example-select">' in body
    for entry in examples.catalog():
        assert f'<option value="{entry["key"]}"' in body, entry["key"]
    for group in examples.grouped():
        assert f'<optgroup label="{group["title"]}">' in body


@pytest.mark.requirement("FR-02")
def test_slicer_page_offers_both_interpreters_and_profiles(client):
    body = _text(client.get("/slicer"))
    for option in analysis.interpreters():
        assert f'<option value="{option["id"]}"' in body
    assert re.search(r'<option value="qiskit"\s+selected', body), "Qiskit should be the default"
    for profile in analysis.rule_profiles():
        assert f'<option value="{profile}">' in body


@pytest.mark.requirement("FR-21")
def test_slicer_page_has_diagnostics_panel(client):
    body = _text(client.get("/slicer"))
    assert 'id="diagnostics"' in body and "<h3>Diagnostics</h3>" in body


def test_team_page_shows_members_and_charter(client):
    body = _text(client.get("/team"))
    for member in MEMBERS:
        assert member["name"] in body
        assert member["role"] in body
    assert VISION in body and MISSION in body
    assert COURSE["name"] in body or COURSE["institution"] in body
    assert PROJECT_BLURB[:60] in body


def test_index_page_content(client):
    body = _text(client.get("/"))
    assert "See how a quantum program actually fits together." in body
    assert "Ready to look at a circuit?" in body


def test_unknown_page_returns_html_404(client):
    response = client.get("/definitely-not-a-page")
    assert response.status_code == 404
    body = _text(response)
    assert "That page is not here." in body
    assert '<p class="error-code">404</p>' in body
    # The error page still carries the nav and links back.
    assert 'href="/slicer"' in body and 'href="/"' in body


def test_wrong_method_is_405(client):
    assert client.get("/api/analyze").status_code == 405
    assert client.get("/api/slice").status_code == 405
    assert client.post("/api/examples").status_code == 405


def test_static_assets_are_served(client):
    for path in ("/static/css/style.css", "/static/js/slicer.js", "/static/js/graph.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        response.close()


def test_no_backend_banner_when_backend_imports(client):
    assert analysis.backend_available()
    assert analysis.backend_error() is None
    assert "Analysis backend unavailable" not in _text(client.get("/"))


def test_backend_banner_when_backend_missing(client, monkeypatch):
    monkeypatch.setattr(analysis, "_IMPORT_ERROR", "ImportError: no qiskit here")
    body = _text(client.get("/slicer"))
    assert "Analysis backend unavailable" in body
    assert "ImportError: no qiskit here" in body


def test_wsgi_exposes_the_same_application():
    import app
    import wsgi

    assert wsgi.application is app.app
    assert not wsgi.application.debug
