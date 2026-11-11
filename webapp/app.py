"""Flask site for the quantum program slicer.

Run it from the repository root:

    python webapp/app.py [--benchmark]

then open http://127.0.0.1:5000/.

Pages
    /          landing
    /slicer    upload or paste a circuit, then explore its graph
    /quantum   background on quantum programs and on how the slicer works
    /lint      every lint rule explained, and which ones run
    /team      who built it
    /benchmark grade the selected lint rules on a benchmark's bugs (?dataset=<id>)

JSON API
    GET  /api/examples          list the bundled example circuits
    GET  /api/examples/<key>    fetch one example's source
    POST /api/analyze           {source, filename, profile, language} -> graph payload
    POST /api/slice             {token, node_idx, direction, dep_types, target_idx,
                                node_indices} -> slice (backward, forward or chop)
    GET  /api/linters           every linter, its rules, and the default selection
    GET  /api/linters/rules/<id>  one rule explained, with what it reports on its example
    GET  /api/benchmark         a dataset (?dataset=<id>, default Bugs4Q): summary and bug list
    GET  /api/benchmark/bugs/<key>  one bug with both programs' sources (?dataset=<id>)
    POST /api/benchmark         {lint_rules, profile, dataset} -> graded report

The Benchmark page and its /api/benchmark routes are off unless the app is
started with --benchmark or with QSLICER_BENCHMARK=1 in the environment (the
way to turn them on under a WSGI server); without it they answer 404.

JSON responses of GZIP_MIN_BYTES or more are gzipped for a client that accepts
it, and a repeated /api/analyze request is answered from the analysis cache.
"""

import gzip
import os
import sys

# webapp/ is added to sys.path so the sibling modules import cleanly whether the
# app is started from the repository root or from inside webapp/.
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

# The analysis lives in the qslicer package (pip install -e .). Run from a
# checkout without installing it, the copy next to webapp/ is used instead.
try:
    import qslicer  # noqa: F401
except ImportError:
    sys.path.append(os.path.dirname(WEBAPP_DIR))

from flask import Flask, abort, jsonify, render_template, request  # noqa: E402

import analysis  # noqa: E402
import benchmark  # noqa: E402
import examples  # noqa: E402
from qslicer import gates  # noqa: E402
from qslicer.lint import registry as linters  # noqa: E402
import rule_docs  # noqa: E402
from team import (  # noqa: E402
    COURSE, MEMBERS, MISSION, PROJECT_BLURB, VISION,
)

MAX_SOURCE_BYTES = 256 * 1024

# JSON API responses at least this large are gzipped for a client that accepts
# it; a large circuit's analysis is megabytes of very repetitive JSON. Level 5
# is most of level 9's saving at a fraction of its time.
GZIP_MIN_BYTES = 4 * 1024
GZIP_LEVEL = 5

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
# The Benchmark page is for people working on the lint rules, so it is opt-in.
app.config["BENCHMARK"] = os.environ.get("QSLICER_BENCHMARK", "").lower() in ("1", "true", "yes", "on")


def _benchmark_enabled() -> bool:
    return bool(app.config.get("BENCHMARK"))


def _require_benchmark():
    if not _benchmark_enabled():
        abort(404, description="The benchmark is not enabled on this server.")


@app.context_processor
def inject_globals():
    """Values every template needs for the nav bar and the backend banner."""
    nav_items = [
        ("index", "Home"),
        ("slicer", "Slicer"),
        ("lint", "Lint"),
        ("benchmark", "Benchmark"),
        ("quantum", "Quantum Info"),
        ("team", "Team"),
    ]
    if not _benchmark_enabled():
        nav_items.remove(("benchmark", "Benchmark"))
    return {
        "nav_items": nav_items,
        "benchmark_enabled": _benchmark_enabled(),
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
        interpreters=analysis.interpreters(),
    )


# The endpoint is named for the page; the function is not, so it does not
# shadow the lint module the linters use.
@app.route("/lint", endpoint="lint")
def lint_page():
    return render_template("lint.html", page="lint")


# Likewise for the benchmark module.
@app.route("/benchmark", endpoint="benchmark")
def benchmark_page():
    _require_benchmark()
    dataset = _benchmark_dataset(request.args.get("dataset"))
    if dataset is None:
        abort(404, description=f"No benchmark named {request.args.get('dataset')!r}.")
    return render_template(
        "benchmark.html",
        page="benchmark",
        dataset=benchmark.summary(dataset),
        datasets=[benchmark.summary(name) for name in benchmark.dataset_ids()],
    )


def _benchmark_dataset(name):
    """The dataset a request names (the default when it names none), or None.

    Only folder names under benchmarks/ are accepted, never a path.
    """
    if not name:
        return benchmark.load()
    return benchmark.load(name) if name in benchmark.dataset_ids() else None


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
    # Values the user picked for what only a run could decide, and which of the
    # file's circuits to show; both checked properly by analysis.parse_bindings.
    bindings = payload.get("bindings") or []
    if not isinstance(bindings, list) or len(bindings) > 50:
        return _bad_request("bindings must be a list of at most 50 {name, line, value} objects.")
    circuit = payload.get("circuit", 0)
    if isinstance(circuit, bool) or not isinstance(circuit, int) or circuit < 0:
        return _bad_request("circuit must be a non-negative integer.")
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

    # The same request again (another visit to an example, Analyze pressed twice)
    # gets the response already made, for as long as its token's QDG is cached.
    request_args = dict(filename=filename, profile=profile, language=language,
                        lint_rules=lint_rules, bindings=bindings, circuit=circuit)
    key = analysis.response_key(source, **request_args)
    record = analysis.cached_response(key)
    if record is not None:
        return _json_bytes_response(record["body"], record["gzip"], cache="hit")
    with analysis.no_full_gc():
        result = analysis.analyze_source(source, **request_args)
        body = _json_bytes(result)
    # Compressed now for a response that is kept (browsers ask for gzip), so a
    # hit never compresses; a failure only for a client that wants it.
    gzipped = None
    if len(body) >= GZIP_MIN_BYTES and (result.get("ok") or _accepts_gzip()):
        gzipped = _gzip(body)
    if result.get("ok"):
        analysis.store_response(key, result["token"], body, gzipped)
    return _json_bytes_response(body, gzipped, cache="miss")


def _json_bytes(obj) -> bytes:
    """``obj`` encoded exactly as ``jsonify`` would encode it (compact unless debugging)."""
    return app.json.response(obj).get_data()


def _gzip(body: bytes) -> bytes:
    # mtime=0: the same body always compresses to the same bytes.
    return gzip.compress(body, compresslevel=GZIP_LEVEL, mtime=0)


def _accepts_gzip() -> bool:
    """Whether the request's Accept-Encoding allows gzip (an explicit q=0 refuses it)."""
    anything = 0.0
    for value, quality in request.accept_encodings:
        if value.lower() in ("gzip", "x-gzip"):
            return quality > 0
        if value == "*":
            anything = quality
    return anything > 0


def _json_bytes_response(body: bytes, gzipped=None, cache: str = ""):
    """A 200 JSON response from encoded bytes, gzipped when the client takes it."""
    response = app.response_class(body, mimetype=app.json.mimetype)
    if gzipped is not None:
        response.vary.add("Accept-Encoding")
        if _accepts_gzip():
            response.set_data(gzipped)
            response.headers["Content-Encoding"] = "gzip"
    if cache:
        response.headers["X-Analysis-Cache"] = cache
    return response


@app.after_request
def compress_api_json(response):
    """gzip any other large JSON API response for a client that accepts it.

    One that already says it varies by Accept-Encoding was dealt with by its
    view (/api/analyze keeps its gzipped body with the cached response).
    """
    if (not request.path.startswith("/api/") or response.mimetype != "application/json"
            or response.direct_passthrough or response.is_streamed
            or "Content-Encoding" in response.headers
            or "Accept-Encoding" in response.vary):
        return response
    body = response.get_data()
    if len(body) < GZIP_MIN_BYTES:
        return response
    response.vary.add("Accept-Encoding")
    if _accepts_gzip():
        response.set_data(_gzip(body))
        response.headers["Content-Encoding"] = "gzip"
    return response


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
    target_idx = payload.get("target_idx")
    node_indices = payload.get("node_indices")

    def is_int(value):
        # bool is a subclass of int, so JSON true/false would otherwise pass.
        return isinstance(value, int) and not isinstance(value, bool)

    if node_indices is not None:
        # Several criteria at once; node_idx is then not needed.
        if not token or not isinstance(token, str):
            return _bad_request("token and node_indices are required.", stage="slicing")
        limit = analysis.MAX_SLICE_NODES
        if (not isinstance(node_indices, list) or not 1 <= len(node_indices) <= limit
                or not all(is_int(i) for i in node_indices)):
            return _bad_request(f"node_indices must be a list of 1 to {limit} integers.",
                                stage="slicing")
        if direction != "backward":
            return _bad_request("node_indices can only be used with a backward slice.",
                                stage="slicing")
    elif not token or not isinstance(token, str) or not is_int(node_idx):
        return _bad_request("token and node_idx are required.", stage="slicing")
    if direction not in ("backward", "forward", "chop"):
        return _bad_request("direction must be backward, forward or chop.", stage="slicing")
    if direction == "chop" and not is_int(target_idx):
        return _bad_request("A chop needs an integer target_idx.", stage="slicing")
    if dep_types is not None and (
        not isinstance(dep_types, list) or not all(isinstance(n, str) for n in dep_types)
    ):
        return _bad_request("dep_types must be a list of dependency names.", stage="slicing")

    result = analysis.slice_program(token, node_idx, direction=direction, dep_types=dep_types,
                                    target_idx=target_idx if direction == "chop" else None,
                                    node_indices=node_indices)
    return jsonify(result), (200 if result.get("ok") else 409)


@app.get("/api/linters")
def api_linters():
    return jsonify({"linters": linters.catalog(), "default": linters.default_selection(),
                    "plugin_errors": linters.plugin_errors()})


@app.get("/api/linters/rules/<path:rule_id>")
def api_linter_rule(rule_id):
    """One rule in depth: its documentation, and what it reports on the example."""
    entry = next((dict(rule, linter=linter["label"], linter_id=linter["id"])
                  for linter in linters.catalog() for rule in linter["rules"]
                  if rule["id"] == rule_id), None)
    if entry is None:
        abort(404, description=f"No lint rule named {rule_id!r}.")
    docs = rule_docs.doc(entry["code"]) or {}
    entry.update(explain=docs.get("explain"), limits=docs.get("limits"),
                 example=docs.get("example"), fixed=docs.get("fixed"), findings=None)
    # LintQ's original (CodeQL) shares its codes with the port, which gives its warnings.
    runner = rule_id.replace("lintq-codeql/", "lintq/", 1)
    if entry["example"] and linters.normalize_selection([runner]):
        entry["findings"] = {
            version: [d for d in linters.run(entry[version], "example.py", "python-static",
                                             [runner]) if d.get("code") == entry["code"]]
            for version in ("example", "fixed") if entry[version]}
        entry["run_with"] = runner.partition("/")[0]
    return jsonify(entry)


@app.get("/api/benchmark")
def api_benchmark_dataset():
    _require_benchmark()
    dataset = _benchmark_dataset(request.args.get("dataset"))
    if dataset is None:
        abort(404, description=f"No benchmark named {request.args.get('dataset')!r}.")
    return jsonify({"dataset": benchmark.summary(dataset),
                    "bugs": [benchmark.describe_bug(bug) for bug in dataset.bugs],
                    "external": [{"tool": e["tool"], "label": e["label"],
                                  "version": e.get("version", "")}
                                 for e in benchmark.external_results(dataset)]})


@app.get("/api/benchmark/bugs/<key>")
def api_benchmark_bug(key):
    _require_benchmark()
    dataset = _benchmark_dataset(request.args.get("dataset"))
    bug = dataset.get_bug(key) if dataset is not None else None
    if bug is None:
        abort(404, description=f"No benchmark bug named {key!r}.")
    return jsonify(benchmark.describe_bug(bug, with_sources=True))


@app.post("/api/benchmark")
def api_benchmark_run():
    _require_benchmark()
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
    name = payload.get("dataset")
    dataset = _benchmark_dataset(name) if name is None or isinstance(name, str) else None
    if dataset is None:
        return _bad_request(f"Unknown benchmark {name!r}.", stage="benchmark")
    report = benchmark.run(chosen, profile=profile, dataset=dataset)
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
    import argparse

    parser = argparse.ArgumentParser(description="Run the quantum program slicer site locally.")
    parser.add_argument("--benchmark", action="store_true",
                        help="also serve the Benchmark page, which grades lint rules on "
                             "datasets of real bugs (same as QSLICER_BENCHMARK=1)")
    if parser.parse_args().benchmark:
        app.config["BENCHMARK"] = True
    if not analysis.backend_available():
        print("WARNING: the analysis backend failed to import:", analysis.backend_error())
        print("         The site will run, but analysis requests will return an error.")
    app.run(debug=True, host="127.0.0.1", port=5000)
