"""Run the JavaScript unit tests (node:test) so `python -m pytest` covers them too."""

import os
import shutil
import subprocess

import pytest

JS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(JS_DIR))


@pytest.mark.requirement("NFR-10")
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_node_unit_tests_pass():
    files = sorted(f for f in os.listdir(JS_DIR) if f.endswith(".test.js"))
    assert files, "no *.test.js files found"
    result = subprocess.run(
        ["node", "--test", *[os.path.join("tests", "js", f) for f in files]],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
