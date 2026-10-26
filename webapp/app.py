"""Flask mock-up for the quantum program slicer.

Run it from the repository root:

    python webapp/app.py

then open http://127.0.0.1:5000/.

Pages
    /          landing
    /slicer    upload or paste a circuit, then explore its graph
    /quantum   background on quantum programs and on how the slicer works
    /benchmark grade the selected lint rules on the Bugs4Q bugs
    /team      who built it

JSON API
    GET  /api/examples          list the bundled example circuits
    GET  /api/examples/<key>    fetch one example's source
    POST /api/analyze           {source, filename, profile, language} -> graph payload
    POST /api/slice             {token, node_idx, direction, dep_types} -> slice
    GET  /api/linters           every linter, its rules, and the default selection
    GET  /api/benchmark         the Bugs4Q dataset: summary and bug list
    GET  /api/benchmark/bugs/<key>  one bug with both programs' sources
    POST /api/benchmark         {lint_rules, profile} -> graded report
"""

import os
import sys

# webapp/ is added to sys.path so the sibling modules import cleanly whether the
# app is started from the repository root or from inside webapp/.
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from flask import Flask, abort, jsonify, render_template, request  # noqa: E402

import analysis  # noqa: E402
import benchmark  # noqa: E402
import examples  # noqa: E402
import gates  # noqa: E402
import linters  # noqa: E402
from team import (  # noqa: E402
    COURSE, MEMBERS, MISSION, PROJECT_BLURB, VISION,
)

MAX_SOURCE_BYTES = 256 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


@app.context_processor
def inject_globals():
    """Values every template needs for the nav bar and the backend banner."""
    return {
        "nav_items": [
            ("index", "Home"),
            ("slicer", "Slicer"),
            ("benchmark", "Benchmark"),
            ("quantum", "Quantum Info"),
            ("team", "Team"),
        ],
        "backend_ok": analysis.backend_available(),
        "backend_error": analysis.backend_error(),
    }


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", page="index")


@app.route("/slicer")
def slicer():
    return render_template(
        "slicer.html",
        page="slicer",
        example_groups=examples.grouped(),
        profiles=analysis.rule_profiles(),
        interpreters=analysis.interpreters(),
    )


# The endpoint is named for the page; the function is not, so it does not
# shadow the benchmark module.
@app.route("/benchmark", endpoint="benchmark")
def benchmark_page():
    return render_template(
        "benchmark.html",
        page="benchmark",
        dataset=benchmark.summary(),
        profiles=analysis.rule_profiles(),
    )


@app.route("/quantum")
def quantum():
    return render_template("quantum.html", page="quantum", glossary=gates.glossary())


@app.route("/team")
def team():
    return render_template(
        "team.html",
        page="team",
        members=MEMBERS,
        course=COURSE,
        blurb=PROJECT_BLURB,
        vision=VISION,
        mission=MISSION,
    )


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------

@app.get("/api/examples")
def api_examples():
    return jsonify({"examples": examples.catalog(), "groups": examples.grouped()})


@app.get("/api/examples/<key>")
def api_example(key):
    entry = examples.load(key)
    if entry is None:
        abort(404, description=f"No example named {key!r}.")
    return jsonify(entry)


def _lint_rules(payload):
    """The ``lint_rules`` of a request: None (use the default) or a list of ids.

    Raises ValueError for anything else.
    """
    rules = payload.get("lint_rules")
    if rules is not None and (not isinstance(rules, list)
                              or not all(isinstance(r, str) for r in rules)):
        raise ValueError("lint_rules must be a list of rule ids.")
    return rules


def _bad_request(message: str, stage: str = "validation", **extra):
    """A 400 for a request body the API cannot use, in the usual error shape."""
    body = {
        "ok": False,
        "diagnostics": [],
        "error": {"kind": "invalid_request", "stage": stage, "message": message,
                  "detail": "", "hint": "", "line": 0},
    }
    body.update(extra)
    return jsonify(body), 400


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _bad_request("The request body must be a JSON object.")
    source = payload.get("source", "")
    filename = payload.get("filename") or ""
    profile = payload.get("profile") or "baseline"
    # Which front end to use. The page always sends this, from the Interpreter
    # dropdown; without it the filename extension decides.
    language = payload.get("language")

    if not isinstance(filename, str):
        return _bad_request("filename must be a string.")
    if not isinstance(profile, str):
        return _bad_request("profile must be a string.")
    try:
        lint_rules = _lint_rules(payload)
    except ValueError as exc:
        return _bad_request(str(exc))
    language_error = analysis.unsupported_language(language)
    if language_error is not None:
        return jsonify({"ok": False, "diagnostics": [], "error": language_error}), 400

    if not isinstance(source, str) or not source.strip():
        return jsonify({
            "ok": False,
            "diagnostics": [],
            "error": {
                "kind": "empty_input",
                "message": "There is nothing to analyze yet.",
                "detail": "",
                "hint": "Paste a circuit, drop a .py file on the editor, or load an example.",
            },
        }), 400

    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return jsonify({
            "ok": False,
            "diagnostics": [],
            "error": {
                "kind": "too_large",
                "message": "That file is larger than the slicer will accept.",
                "detail": f"Limit is {MAX_SOURCE_BYTES // 1024} KB.",
                "hint": "Trim the circuit down to the part you want to slice.",
            },
        }), 413

    return jsonify(analysis.analyze_source(
        source, filename=filename, profile=profile, language=language, lint_rules=lint_rules,
    ))


@app.post("/api/slice")
def api_slice():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _bad_request("The request body must be a JSON object.", stage="slicing")
    token = payload.get("token")
    node_idx = payload.get("node_idx")
    direction = payload.get("direction", "backward")
    dep_types = payload.get("dep_types")

    # bool is a subclass of int, so JSON true/false would otherwise pass.
    if (not token or not isinstance(token, str)
            or isinstance(node_idx, bool) or not isinstance(node_idx, int)):
        return _bad_request("token and node_idx are required.", stage="slicing")
    if direction not in ("backward", "forward"):
        return _bad_request("direction must be backward or forward.", stage="slicing")
    if dep_types is not None and (
        not isinstance(dep_types, list) or not all(isinstance(n, str) for n in dep_types)
    ):
        return _bad_request("dep_types must be a list of dependency names.", stage="slicing")

    result = analysis.slice_program(token, node_idx, direction=direction, dep_types=dep_types)
    return jsonify(result), (200 if result.get("ok") else 409)


@app.get("/api/linters")
def api_linters():
    return jsonify({"linters": linters.catalog(), "default": linters.default_selection(),
                    "plugin_errors": linters.plugin_errors()})


@app.get("/api/benchmark")
def api_benchmark_dataset():
    return jsonify({"dataset": benchmark.summary(),
                    "bugs": [benchmark.describe_bug(bug) for bug in benchmark.bugs()],
                    "external": [{"tool": e["tool"], "label": e["label"],
                                  "version": e.get("version", "")}
                                 for e in benchmark.external_results()]})


@app.get("/api/benchmark/bugs/<key>")
def api_benchmark_bug(key):
    bug = benchmark.get_bug(key)
    if bug is None:
        abort(404, description=f"No Bugs4Q bug named {key!r}.")
    return jsonify(benchmark.describe_bug(bug, with_sources=True))


@app.post("/api/benchmark")
def api_benchmark_run():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _bad_request("The request body must be a JSON object.", stage="benchmark")
    profile = payload.get("profile") or "baseline"
    if not isinstance(profile, str) or profile not in analysis.rule_profiles():
        return _bad_request(f"Unknown rule profile {profile!r}.", stage="benchmark")
    try:
        lint_rules = _lint_rules(payload)
    except ValueError as exc:
        return _bad_request(str(exc), stage="benchmark")
    chosen = linters.normalize_selection(lint_rules)
    if not chosen:
        return _bad_request("Select at least one rule to benchmark.", stage="benchmark")
    report = benchmark.run(chosen, profile=profile)
    return jsonify(dict(report, ok=True))


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", page="", code=404,
                           message=getattr(error, "description", "Page not found.")), 404


@app.errorhandler(413)
def too_large(error):
    return jsonify({
        "ok": False,
        "error": {
            "kind": "too_large",
            "message": "That upload is too large.",
            "detail": "",
            "hint": "Keep circuit files under 2 MB.",
        },
    }), 413


if __name__ == "__main__":
    if not analysis.backend_available():
        print("WARNING: the analysis backend failed to import:", analysis.backend_error())
        print("         The site will run, but analysis requests will return an error.")
    print(f"NOTE: a submitted .py file is EXECUTED in a worker process, with a "
          f"{analysis.PARSE_TIMEOUT}s timeout,")
    print("      because that is how QiskitParser reaches the QuantumCircuit. That bounds "
          "accidents,")
    print("      not attacks: run this on localhost only. A .qasm file is parsed without "
          "executing.")
    app.run(debug=True, host="127.0.0.1", port=5000)
