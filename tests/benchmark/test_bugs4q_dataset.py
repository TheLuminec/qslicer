"""The vendored Bugs4Q dataset and the script that imports it."""

import importlib.util
import json
import os

import pytest

import benchmark

SYNC = os.path.join(benchmark.DATASET_DIR, "sync_upstream.py")


def _sync_module():
    spec = importlib.util.spec_from_file_location("sync_upstream", SYNC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_lists_every_qiskit_bug_once():
    bugs = benchmark.bugs()
    assert len(bugs) == 62
    keys = [bug["key"] for bug in bugs]
    assert len(set(keys)) == len(keys)
    assert {bug["source"] for bug in bugs} == {"GitHub issue", "Stack Overflow", "Stack Exchange"}
    assert sum(1 for k in keys if k.startswith("github-")) == 37
    assert sum(1 for k in keys if k.startswith("stackoverflow-")) == 7
    assert sum(1 for k in keys if k.startswith("stackexchange-")) == 18


def test_every_program_is_vendored():
    with open(benchmark.MANIFEST_PATH, encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert len(manifest["commit"]) == 40
    assert manifest["license"] == "Apache-2.0"
    assert os.path.isfile(os.path.join(benchmark.PROGRAMS_DIR, "LICENSE"))
    for bug in manifest["bugs"]:
        for column in ("buggy", "fixed", "mod"):
            if bug[column]:
                assert os.path.isfile(os.path.join(benchmark.PROGRAMS_DIR, bug[column])), \
                    (bug["key"], column)
        assert bug["buggy"] != bug["fixed"], bug["key"]


def test_every_fix_changes_code():
    """Every bug can be graded: no fix is a comment-only or whitespace-only change."""
    for bug in benchmark.bugs():
        assert bug["truth"]["kind"] in ("edit", "rewrite", "omission"), bug["key"]
        assert bug["truth"]["lines"], bug["key"]
        assert max(bug["truth"]["lines"]) <= len(bug["sources"]["buggy"].splitlines())


def test_readme_link_fix_for_terra_7032():
    """Upstream's "Fixed" link for #7032 names the buggy file; the manifest does not."""
    bug = next(b for b in benchmark.bugs() if b["reference"] == "#7032")
    assert bug["fixed"] == "Terra-6000-7100/7032_Fixed/fixed_version.py"


def test_sync_reads_the_readme_tables(tmp_path):
    files = {
        "A/1/buggy.py": "x = 1\n", "A/1/fixed.py": "x = 2\n",
        "B/Bug_2/bug_version.py": "y = 1\n", "B/Fixed_2/fixed_version.py": "y = 2\n",
        "C/3_Bug/bug_version.py": "z = 1\n", "C/3_Fixed/fixed_version.py": "z = 2\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "## Qiskit Reproduceable Bugs from GitHub\n"
        "| Bug Id | Issue No | Buggy | Fixed | Modify | Status | Version | Type |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | [#5](https://github.com/x/issues/5) | [Buggy](./A/1/buggy.py) | "
        "[Fixed](A/1/fixed.py) | [Mod](./A/1/mod.py) | Closed | 0.1 | pipes \\| inside |\n"
        "| 2 | [#6](https://github.com/x/issues/6) | [Buggy](./C/3_Bug/wrongname.py) | "
        "[Fixed](/C/3_Fixed/fixed_version.py) | --- | Closed | --- | renamed |\n"
        "## Qiskit Reproduceable Bugs from Stackflow\n"
        "| Bug Id | Buggy | Fixed | Modify | Status |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| [2](https://stackoverflow.com/questions/99/title) | [Buggy](./B/Bug_2) | "
        "[Fixed](./B/Fixed_2) | --- | Closed |\n"
        "## Cirq\n"
        "| Bug Id | Buggy |\n| --- | --- |\n| 1 | [Buggy](./Cirq/1/buggy.py) |\n",
        encoding="utf-8")

    bugs, problems = _sync_module().read_tables(str(tmp_path))
    assert problems == []
    by_key = {bug["key"]: bug for bug in bugs}
    assert set(by_key) == {"github-1", "github-2", "stackoverflow-2"}
    assert by_key["github-1"]["type"] == "pipes | inside"
    assert by_key["github-1"]["mod"] is None                    # the link names no file
    assert by_key["github-2"]["buggy"] == "C/3_Bug/bug_version.py"  # broken name, one .py
    assert by_key["github-2"]["fixed"] == "C/3_Fixed/fixed_version.py"
    assert by_key["stackoverflow-2"]["buggy"] == "B/Bug_2/bug_version.py"  # a directory link
    assert by_key["stackoverflow-2"]["reference"] == "#99"


def test_sync_reports_links_it_cannot_resolve(tmp_path):
    (tmp_path / "README.md").write_text(
        "## Qiskit Reproduceable Bugs from GitHub\n"
        "| Bug Id | Issue No | Buggy | Fixed |\n| --- | --- | --- | --- |\n"
        "| 1 | #1 | [Buggy](./missing/buggy.py) | [Fixed](./missing/fixed.py) |\n",
        encoding="utf-8")
    _bugs, problems = _sync_module().read_tables(str(tmp_path))
    assert len(problems) == 2 and all("github-1" in p for p in problems)


@pytest.mark.parametrize("key", ["github-12", "stackexchange-2"])
def test_known_fault_lines(key):
    """Spot checks against the bugs as their reports describe them."""
    bug = benchmark.get_bug(key)
    lines = bug["sources"]["buggy"].splitlines()
    faulty = [lines[n - 1].strip() for n in sorted(bug["truth"]["lines"])]
    if key == "github-12":      # Terra #6571: measure_all() on a circuit with registers
        assert faulty == ["qc.measure_all()"]
    else:                       # QCSE 9246: the inverse QFT's loops
        assert "circ.swap(q[i], q[n - i - 1])" in faulty
        assert any(line.startswith("circ.cu1(") for line in faulty)
