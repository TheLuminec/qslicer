"""Shared constants and request helpers for the web app tests (not a test module)."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
EXAMPLES_DIR = os.path.join(WEBAPP_DIR, "examples")
MANIFEST_PATH = os.path.join(EXAMPLES_DIR, "manifest.json")


def manifest_entries():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)["examples"]


# A tiny OpenQASM 3 GHZ circuit. Indices:
#   0 h q0 | 1 cx q0,q1 | 2 cx q0,q2 | 3 measure q0 | 4 measure q1 | 5 measure q2
GHZ_QASM = """OPENQASM 3.0;
include "stdgates.inc";
bit[3] c;
qubit[3] q;
h q[0];
cx q[0], q[1];
cx q[0], q[2];
c[0] = measure q[0];
c[1] = measure q[1];
c[2] = measure q[2];
"""

BELL_PY = """from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure(0, 0)
qc.measure(1, 1)
"""


def post_analyze(client, **payload):
    response = client.post("/api/analyze", json=payload)
    return response, response.get_json()


def post_slice(client, **payload):
    response = client.post("/api/slice", json=payload)
    return response, response.get_json()
