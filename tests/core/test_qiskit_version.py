"""The installed Qiskit is the version the project is frozen at.

requirements.txt pins Qiskit exactly; the parsers, QS011's list of removed
APIs and the LintQ port are maintained against that version. A different one
installed means results may silently differ, so the suite says so first.
"""

import os
import re
from importlib.metadata import version

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pins():
    pins = {}
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"\s*(qiskit[\w-]*)==([\w.]+)", line)
            if match:
                pins[match.group(1)] = match.group(2)
    return pins


def test_qiskit_is_pinned_exactly():
    assert {"qiskit", "qiskit-aer", "qiskit-qasm3-import"} <= set(_pins())


@pytest.mark.parametrize("package", sorted(_pins()))
def test_installed_version_matches_the_pin(package):
    assert version(package) == _pins()[package], (
        f"{package} {version(package)} is installed but the project is frozen at "
        f"{_pins()[package]}: run pip install -r requirements.txt")
