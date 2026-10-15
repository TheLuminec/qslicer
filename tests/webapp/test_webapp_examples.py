"""examples.py and examples/manifest.json, plus every example analyzed (slow).

The slow sweep also checks the concrete claims the manifest blurbs and
highlights make (the manifest says every claim was checked against the
analyzer's actual output, so drift should fail loudly).
"""

import json
import os

import pytest

import examples
from webapp_helpers import EXAMPLES_DIR, MANIFEST_PATH, manifest_entries, post_slice

ENTRIES = manifest_entries()
KEYS = [entry["key"] for entry in ENTRIES]
PY_KEYS = [e["key"] for e in ENTRIES if e["file"].endswith(".py")]


# ------------------------------------------------------- manifest (fast) --

def test_manifest_is_valid_json_with_categories():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        manifest = json.load(handle)
    category_ids = [c["id"] for c in manifest["categories"]]
    assert len(category_ids) == len(set(category_ids))
    assert all(c["title"] for c in manifest["categories"])
    assert len(KEYS) == len(set(KEYS)), "duplicate example keys"
    files = [e["file"] for e in ENTRIES]
    assert len(files) == len(set(files)), "two entries point at the same file"
    for entry in ENTRIES:
        assert entry["category"] in category_ids, entry["key"]
        assert entry["title"] and entry["blurb"], entry["key"]


@pytest.mark.parametrize("entry", ENTRIES, ids=KEYS)
def test_manifest_entry_file_exists(entry):
    path = os.path.join(EXAMPLES_DIR, entry["file"])
    assert os.path.isfile(path)
    assert entry["file"].endswith(examples.SOURCE_SUFFIXES)


@pytest.mark.parametrize("entry", ENTRIES, ids=KEYS)
def test_manifest_entry_loads_with_matching_language(entry):
    loaded = examples.load(entry["key"])
    assert loaded is not None
    assert loaded["filename"] == entry["file"]
    assert loaded["title"] == entry["title"]
    with open(os.path.join(EXAMPLES_DIR, entry["file"]), encoding="utf-8") as handle:
        assert loaded["source"] == handle.read()
    expected = "qasm" if entry["file"].endswith(".qasm") else "qiskit"
    assert loaded["interpreter"] == expected
    if expected == "qasm":
        assert loaded["source"].lstrip().startswith("//") or "OPENQASM" in loaded["source"]
        assert entry["category"] == "openqasm"


def test_every_file_on_disk_is_in_the_manifest():
    on_disk = set(examples._files_on_disk())
    assert on_disk == {e["file"] for e in ENTRIES}
    assert all(e["category"] != examples.UNSORTED["id"] for e in examples.catalog())


@pytest.mark.parametrize("filename, expected", [
    ("a.py", "qiskit"), ("a.qasm", "qasm"), ("a.QASM3", "qasm"), ("a.txt", "qiskit"), ("", "qiskit"),
])
def test_interpreter_for(filename, expected):
    assert examples.interpreter_for(filename) == expected


def test_catalog_follows_manifest_order():
    assert [e["key"] for e in examples.catalog()] == KEYS
    for entry in examples.catalog():
        assert set(entry) == {"key", "file", "category", "title", "blurb", "highlight",
                              "interpreter", "available"}


def test_grouped_follows_category_order():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        order = [c["id"] for c in json.load(handle)["categories"]]
    groups = examples.grouped()
    assert [g["id"] for g in groups] == [cid for cid in order
                                         if any(e["category"] == cid for e in ENTRIES)]
    assert sum(len(g["examples"]) for g in groups) == len(ENTRIES)


def test_load_unknown_key_is_none():
    assert examples.load("nope") is None


def test_unlisted_file_appears_as_unsorted(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "categories": [{"id": "basics", "title": "Basics"}],
        "examples": [{"key": "a", "file": "a.py", "category": "basics", "title": "A"},
                     {"key": "gone", "file": "missing.py", "category": "basics"}],
    }), encoding="utf-8")
    (tmp_path / "a.py").write_text("qc = 1\n", encoding="utf-8")
    (tmp_path / "my_extra_circuit.py").write_text("qc = 2\n", encoding="utf-8")
    (tmp_path / "_private.py").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(examples, "EXAMPLE_DIR", str(tmp_path))
    monkeypatch.setattr(examples, "MANIFEST_PATH", str(tmp_path / "manifest.json"))

    catalog = examples.catalog()
    assert [e["key"] for e in catalog] == ["a", "my_extra_circuit"]
    extra = catalog[1]
    assert extra["category"] == "unsorted" and extra["title"] == "My extra circuit"
    groups = examples.grouped()
    assert [g["title"] for g in groups] == ["Basics", "Unsorted"]
    assert examples.load("my_extra_circuit")["source"] == "qc = 2\n"
    assert examples.load("gone") is None


def test_missing_directory_falls_back_to_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(examples, "EXAMPLE_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(examples, "MANIFEST_PATH", str(tmp_path / "absent" / "manifest.json"))
    catalog = examples.catalog()
    assert [e["key"] for e in catalog] == ["builtin_bell"]
    loaded = examples.load("builtin_bell")
    assert loaded["filename"] == "bell_pair.py"
    assert "qc.cx(0, 1)" in loaded["source"]
    assert examples.grouped()[0]["examples"][0]["key"] == "builtin_bell"


def test_corrupt_manifest_still_lists_files(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "x.qasm").write_text("OPENQASM 3.0;", encoding="utf-8")
    monkeypatch.setattr(examples, "EXAMPLE_DIR", str(tmp_path))
    monkeypatch.setattr(examples, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
    catalog = examples.catalog()
    assert [(e["key"], e["interpreter"]) for e in catalog] == [("x", "qasm")]


# ------------------------------------------------- every example (slow) --

@pytest.mark.slow
@pytest.mark.requirement("FR-01")
@pytest.mark.requirement("FR-02")
@pytest.mark.requirement("VV-04")
@pytest.mark.parametrize("key", KEYS)
def test_example_analyzes_successfully(example_results, key):
    status, body, entry = example_results[key]
    assert status == 200
    assert body["ok"] is True, body.get("error")
    assert body["language"] == ("qasm" if entry["interpreter"] == "qasm" else "python")
    assert body["filename"] == entry["filename"]
    assert body["program"]["num_instructions"] == len(body["nodes"]) > 0
    assert not any(d["code"] == "QS000" for d in body["diagnostics"])


@pytest.mark.slow
@pytest.mark.requirement("FR-15")
@pytest.mark.parametrize("key", KEYS)
def test_example_slices_contain_criterion_and_respect_edges(client, example_results, key):
    import networkx as nx

    _, body, _ = example_results[key]
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(range(len(body["nodes"])))
    graph.add_edges_from((e["source"], e["target"]) for e in body["edges"])
    for idx in range(len(body["nodes"])):
        for direction, reach in (("backward", nx.ancestors), ("forward", nx.descendants)):
            response, result = post_slice(client, token=body["token"], node_idx=idx,
                                          direction=direction)
            assert response.status_code == 200
            assert result["slice"] == sorted(reach(graph, idx) | {idx}), (idx, direction)


def _ok(example_results, key):
    status, body, _ = example_results[key]
    assert status == 200 and body["ok"], body.get("error")
    return body


def _slice(client, body, idx, direction="backward", dep_types=None):
    payload = {"token": body["token"], "node_idx": idx, "direction": direction}
    if dep_types:
        payload["dep_types"] = dep_types
    return post_slice(client, **payload)[1]


@pytest.mark.slow
def test_claim_bell_pair_both_gates_from_either_measurement(client, example_results):
    body = _ok(example_results, "bell_pair")
    measures = [n["id"] for n in body["nodes"] if n["is_measurement"]]
    gates_ = [n["id"] for n in body["nodes"] if not n["is_measurement"]]
    for idx in measures:
        assert set(gates_) <= set(_slice(client, body, idx)["slice"])


@pytest.mark.slow
def test_claim_independent_wires_slice_from_3_is_two(client, example_results):
    body = _ok(example_results, "independent_wires")
    assert _slice(client, body, 3)["size"] == 2


@pytest.mark.slow
def test_claim_grover_slice_from_12_is_11_of_14(client, example_results):
    body = _ok(example_results, "grover_two_qubit")
    result = _slice(client, body, 12)
    assert (result["size"], result["total"]) == (11, 14)


@pytest.mark.slow
def test_claim_stress_test_dimensions(example_results):
    body = _ok(example_results, "stress_test")
    assert body["program"]["num_qubits"] == 6
    assert body["program"]["num_instructions"] == 44
    assert len(body["edges"]) == 58


@pytest.mark.slow
def test_claim_ghz_qasm_graph_matches_python(example_results):
    python = _ok(example_results, "ghz_state")
    qasm = _ok(example_results, "ghz_openqasm")
    strip = lambda nodes: [(n["name"], n["qubits"], n["clbits"]) for n in nodes]  # noqa: E731
    assert strip(python["nodes"]) == strip(qasm["nodes"])
    edges = lambda b: sorted((e["source"], e["target"], e["dependency"]) for e in b["edges"])  # noqa: E731
    assert edges(python) == edges(qasm)


@pytest.mark.slow
def test_claim_qasm_examples_skip_static_checks(example_results):
    for key in ("ghz_openqasm", "grover_openqasm"):
        body = _ok(example_results, key)
        assert [d["code"] for d in body["diagnostics"]] == ["QS100"]
        assert body["route"] == "OpenQASM, parsed without executing"


@pytest.mark.slow
def test_claim_uncomputation_instruction_3_is_uncomputed(example_results):
    body = _ok(example_results, "uncomputation")
    groups = body["entanglement"]["groups"]
    assert any(3 in g["uncomputed"] for g in groups)
    assert not any(3 in g["computed"] for g in groups)


@pytest.mark.slow
def test_claim_linter_showcase_diagnostics(example_results):
    body = _ok(example_results, "linter_showcase")
    assert {"QS005", "QS006", "QS010"} <= {d["code"] for d in body["diagnostics"]}


@pytest.mark.slow
def test_claim_teleportation_has_measurement_dependencies(example_results):
    body = _ok(example_results, "quantum_teleportation")
    assert body["stats"]["edges_by_dependency"].get("MEASUREMENT", 0) > 0


@pytest.mark.slow
def test_claim_qaoa_rzz_is_entangling_and_phase(example_results):
    body = _ok(example_results, "qaoa_maxcut")
    rzz = [n for n in body["nodes"] if n["name"] == "rzz"]
    assert rzz and all(n["gate"]["entangling"] and n["gate"]["phase"] for n in rzz)


@pytest.mark.slow
def test_claim_swap_test_three_qubit_node(example_results):
    body = _ok(example_results, "swap_test")
    assert any(n["name"] == "cswap" and len(n["qubits"]) == 3 for n in body["nodes"])
