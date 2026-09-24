"""Flask mock-up for the quantum program slicer.

Run it from the repository root:

    python webapp/app.py

then open http://127.0.0.1:5000/.

Pages
    /          landing
    /slicer    upload or paste a circuit, then explore its graph
    /quantum   background on quantum programs and on how the slicer works
    /team      who built it

JSON API
    GET  /api/examples          list the bundled example circuits
    GET  /api/examples/<key>    fetch one example's source
    POST /api/analyze           {source, filename, profile, language} -> graph payload
    POST /api/slice             {token, node_idx, direction, dep_types} -> slice
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
import examples  # noqa: E402
import gates  # noqa: E402
from team import (  # noqa: E402
    ADVISOR, CLIENT, CONSTRAINTS, COURSE, MEMBERS, MISSION, PROJECT_BLURB,
    STANDARDS, SUCCESS_CRITERIA, VISION,
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


@app.route("/quantum")
def quantum():
    return render_template("quantum.html", page="quantum", glossary=gates.glossary())


@app.route("/team")
def team():
    return render_template(
        "team.html",
        page="team",
        members=MEMBERS,
        advisor=ADVISOR,
        client=CLIENT,
        course=COURSE,
        blurb=PROJECT_BLURB,
        vision=VISION,
        mission=MISSION,
        criteria=SUCCESS_CRITERIA,
        constraints=CONSTRAINTS,
        standards=STANDARDS,
    )


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------

@app.get("/api/examples")
def api_examples():
    return jsonify({"examples": examples.catalogue(), "groups": examples.grouped()})


@app.get("/api/examples/<key>")
def api_example(key):
    entry = examples.load(key)
    if entry is None:
        abort(404, description=f"No example named {key!r}.")
    return jsonify(entry)


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source", "")
    filename = payload.get("filename") or ""
    profile = payload.get("profile") or "baseline"
    # Which front end to use. The page always sends this, from the Interpreter
    # dropdown; without it the filename extension decides.
    language = payload.get("language")

    if not isinstance(source, str) or not source.strip():
        return jsonify({
            "ok": False,
            "diagnostics": [],
            "error": {
                "kind": "empty_input",
                "message": "There is nothing to analyse yet.",
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
                "message": "That file is larger than this mock-up will accept.",
                "detail": f"Limit is {MAX_SOURCE_BYTES // 1024} KB.",
                "hint": "Trim the circuit down to the part you want to slice.",
            },
        }), 413

    return jsonify(analysis.analyze_source(
        source, filename=filename, profile=profile, language=language,
    ))


@app.post("/api/slice")
def api_slice():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")
    node_idx = payload.get("node_idx")
    direction = payload.get("direction", "backward")
    dep_types = payload.get("dep_types")

    if not token or not isinstance(node_idx, int):
        return jsonify({"ok": False, "error": "token and node_idx are required."}), 400
    if direction not in ("backward", "forward"):
        return jsonify({"ok": False, "error": "direction must be backward or forward."}), 400

    result = analysis.slice_program(token, node_idx, direction=direction, dep_types=dep_types)
    return jsonify(result), (200 if result.get("ok") else 409)


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
            "message": "That upload is too large for this mock-up.",
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
