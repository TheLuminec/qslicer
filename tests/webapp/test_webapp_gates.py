"""gates.py: describe(), glossary(), flag sets, and the matrices against Qiskit.

The ``matrix`` strings are written in the small markup ``static/js/mathfmt.js``
renders. The evaluator below understands enough of it to turn every matrix that
is a numeric ``\\matrix{...}`` (optionally with a scalar prefactor) or a
``\\text{diag}(...)`` into numbers, substituting fixed values for theta, phi
and lambda. Those are compared, exactly (no global-phase slack), with
``qiskit.quantum_info.Operator`` of the corresponding Qiskit gate. Multi-qubit
matrices are in Qiskit's little-endian order: the first qubit argument is the
least significant bit of the basis index.
"""

import cmath
import math
import re

import numpy as np
import pytest
from qiskit.circuit import library as lib
from qiskit.quantum_info import Operator

import gates

THETA, PHI, LAM = 0.7, 0.3, 1.1

# name -> Qiskit gate instance with the parameters above.
QISKIT_GATES = {
    "h": lib.HGate(), "x": lib.XGate(), "y": lib.YGate(), "z": lib.ZGate(),
    "s": lib.SGate(), "sdg": lib.SdgGate(), "t": lib.TGate(), "tdg": lib.TdgGate(),
    "sx": lib.SXGate(), "id": lib.IGate(),
    "rx": lib.RXGate(THETA), "ry": lib.RYGate(THETA), "rz": lib.RZGate(THETA),
    "p": lib.PhaseGate(LAM), "u": lib.UGate(THETA, PHI, LAM), "u1": lib.U1Gate(LAM),
    "u2": lib.U2Gate(PHI, LAM), "u3": lib.U3Gate(THETA, PHI, LAM),
    "cx": lib.CXGate(), "cy": lib.CYGate(), "cz": lib.CZGate(), "ch": lib.CHGate(),
    "crx": lib.CRXGate(THETA), "cry": lib.CRYGate(THETA), "crz": lib.CRZGate(THETA),
    "cp": lib.CPhaseGate(LAM), "csx": lib.CSXGate(),
    "ccx": lib.CCXGate(), "c3x": lib.C3XGate(), "c4x": lib.C4XGate(), "mcx": lib.MCXGate(2),
    "swap": lib.SwapGate(), "iswap": lib.iSwapGate(), "cswap": lib.CSwapGate(),
    "dcx": lib.DCXGate(), "ecr": lib.ECRGate(),
    "rxx": lib.RXXGate(THETA), "ryy": lib.RYYGate(THETA), "rzz": lib.RZZGate(THETA),
}
NOT_UNITARY = {"measure", "measure_all", "reset", "if_else", "c_if", "barrier", "init",
               "while_loop", "for_loop", "switch_case", "store"}


# ------------------------------------------------------- markup evaluator --

class _Parser:
    """Recursive descent over the mathfmt markup, producing a complex number."""

    TOKEN = re.compile(r"\s*(\\[A-Za-z]+|\d+(?:\.\d+)?|[-+*/^(){}]|[A-Za-z])")
    NAMES = {r"\i": 1j, r"\pi": math.pi, r"\theta": THETA, r"\phi": PHI, r"\lambda": LAM}

    def __init__(self, text):
        self.tokens = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            match = self.TOKEN.match(text, pos)
            if not match:
                raise ValueError(f"cannot tokenize {text[pos:]!r}")
            self.tokens.append(match.group(1))
            pos = match.end()
            while pos < len(text) and text[pos].isspace():
                pos += 1
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected=None):
        token = self.peek()
        if expected is not None and token != expected:
            raise ValueError(f"expected {expected!r}, got {token!r}")
        self.i += 1
        return token

    def parse(self):
        value = self.expr()
        if self.peek() is not None:
            raise ValueError(f"trailing tokens {self.tokens[self.i:]}")
        return value

    def expr(self):
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.factor()
        while True:
            token = self.peek()
            if token == "/":
                self.take()
                value = value / self.factor()
            elif token == "*":
                self.take()
                value = value * self.factor()
            elif token is not None and token not in ("+", "-", ")", "}"):
                value = value * self.factor()      # implicit multiplication
            else:
                return value

    def factor(self):
        if self.peek() == "-":
            self.take()
            return -self.factor()
        base = self.atom()
        if self.peek() == "^":
            self.take()
            exponent = self.group()
            base = cmath.exp(exponent) if base == "e" else base ** exponent
        if base == "e":
            raise ValueError("bare e")
        return base

    def group(self):
        self.take("{")
        value = self.expr()
        self.take("}")
        return value

    def atom(self):
        token = self.take()
        if token in self.NAMES:
            return self.NAMES[token]
        if token == "e":
            return "e"
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return float(token)
        if token == r"\frac":
            return self.group() / self.group()
        if token == r"\sqrt":
            return cmath.sqrt(self.group())
        if token in (r"\cos", r"\sin"):
            argument = self.factor()
            return cmath.cos(argument) if token == r"\cos" else cmath.sin(argument)
        if token == "(":
            value = self.expr()
            self.take(")")
            return value
        if token == "{":
            value = self.expr()
            self.take("}")
            return value
        raise ValueError(f"unexpected token {token!r}")


def evaluate(text):
    return _Parser(text).parse()


def _split_braced(text, start):
    """Return the contents of the brace group opening at text[start]."""
    assert text[start] == "{"
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:pos], pos + 1
    raise ValueError("unbalanced braces")


def matrix_of(markup):
    """Numeric matrix for a \\matrix{...} or \\text{diag}(...) string, else None."""
    if markup.startswith(r"\text{diag}(") and markup.endswith(")"):
        entries = markup[len(r"\text{diag}("):-1].split(",")
        return np.diag([evaluate(e) for e in entries])
    at = markup.find(r"\matrix{")
    if at < 0:
        return None
    prefix = markup[:at].strip()
    body, end = _split_braced(markup, at + len(r"\matrix"))
    if markup[end:].strip():
        return None
    rows = [[evaluate(cell) for cell in row.split("&")] for row in body.split(r"\\")]
    scale = evaluate(prefix) if prefix else 1
    return scale * np.array(rows, dtype=complex)


def test_evaluator_sanity():
    assert evaluate(r"\frac{1}{\sqrt{2}}") == pytest.approx(1 / math.sqrt(2))
    assert evaluate(r"e^{\i\pi/4}") == pytest.approx(cmath.exp(1j * math.pi / 4))
    assert evaluate(r"-e^{\i\lambda}\sin\frac{\theta}{2}") == \
        pytest.approx(-cmath.exp(1j * LAM) * math.sin(THETA / 2))
    assert evaluate(r"1-\i") == pytest.approx(1 - 1j)
    assert evaluate(r"e^{\i(\phi+\lambda)}") == pytest.approx(cmath.exp(1j * (PHI + LAM)))


EVALUABLE = sorted(name for name, info in gates._GATES.items()
                   if matrix_of(info.get("matrix", "")) is not None)


def test_most_unitary_gates_have_checkable_matrices():
    # Guards the evaluator against silently skipping everything.
    assert {"h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "id", "rx", "ry", "rz", "p",
            "u", "u1", "u2", "u3", "cx", "cy", "cz", "ch", "crz", "cp", "swap",
            "iswap"} <= set(EVALUABLE)


@pytest.mark.parametrize("name", EVALUABLE)
def test_matrix_matches_qiskit(name):
    expected = Operator(QISKIT_GATES[name]).data
    actual = matrix_of(gates._GATES[name]["matrix"])
    assert actual.shape == expected.shape
    np.testing.assert_allclose(actual, expected, atol=1e-12)


# ----------------------------------------------------------- flag checks --

@pytest.mark.parametrize("name", sorted(gates.SELF_INVERSE))
def test_self_inverse_gates_square_to_identity(name):
    op = Operator(QISKIT_GATES[name]).data
    np.testing.assert_allclose(op @ op, np.eye(op.shape[0]), atol=1e-12)


@pytest.mark.parametrize("name", sorted(gates.PHASE))
def test_phase_gates_are_diagonal(name):
    op = Operator(QISKIT_GATES[name]).data
    np.testing.assert_allclose(op - np.diag(np.diag(op)), 0, atol=1e-12)


# The identity is diagonal but is deliberately not flagged as a phase gate.
@pytest.mark.parametrize("name", sorted(set(QISKIT_GATES) - gates.PHASE - {"id"}))
def test_non_phase_gates_are_not_diagonal(name):
    op = Operator(QISKIT_GATES[name]).data
    assert not np.allclose(op - np.diag(np.diag(op)), 0)


@pytest.mark.parametrize("name", sorted(set(QISKIT_GATES) - gates.SELF_INVERSE - {"id"}))
def test_non_self_inverse_gates_do_not_square_to_identity(name):
    op = Operator(QISKIT_GATES[name]).data
    assert not np.allclose(op @ op, np.eye(op.shape[0]))


@pytest.mark.parametrize("name", sorted(QISKIT_GATES))
def test_declared_qubit_count_matches_qiskit(name):
    assert gates.describe(name)["qubits"] == QISKIT_GATES[name].num_qubits


def test_every_gate_is_covered_by_these_tests():
    assert set(gates._GATES) == set(QISKIT_GATES) | NOT_UNITARY


def test_entangling_flag_is_consistent_with_category():
    for name in gates.ENTANGLING:
        assert gates.describe(name)["category"] == gates.MULTI, name
        assert gates.describe(name)["qubits"] >= 2, name
    for entry in gates.glossary():
        if entry["category"] == gates.SINGLE:
            assert not entry["entangling"], entry["name"]
    assert "swap" not in gates.ENTANGLING   # moves entanglement, cannot create it


def test_flag_sets_match_describe():
    for name in gates._GATES:
        info = gates.describe(name)
        assert info["entangling"] == (name in gates.ENTANGLING)
        assert info["phase"] == (name in gates.PHASE)
        assert info["self_inverse"] == (name in gates.SELF_INVERSE)


# ---------------------------------------------------- describe/glossary --

REQUIRED = {"name", "title", "category", "qubits", "summary", "detail", "matrix",
            "entangling", "phase", "self_inverse", "known"}
CATEGORIES = {gates.SINGLE, gates.MULTI, gates.MEASURE, gates.BARRIER, gates.CLASSICAL}


@pytest.mark.parametrize("name", sorted(gates._GATES))
def test_describe_known_entry(name):
    info = gates.describe(name)
    assert set(info) == REQUIRED
    assert info["name"] == name and info["known"] is True
    assert info["category"] in CATEGORIES
    for key in ("title", "summary", "detail", "matrix"):
        assert isinstance(info[key], str) and info[key], key
    assert isinstance(info["qubits"], int) and info["qubits"] >= 0
    # Inline math is balanced: an odd number of $ would break the renderer.
    for key in ("summary", "detail"):
        assert info[key].count("$") % 2 == 0, key
    assert info["matrix"].count("{") == info["matrix"].count("}")


def test_describe_fallback():
    info = gates.describe("my_custom_gate")
    assert set(info) == REQUIRED
    assert info["known"] is False
    assert info["title"] == "MY_CUSTOM_GATE"
    assert info["category"] == gates.SINGLE
    assert info["qubits"] == 0
    assert not (info["entangling"] or info["phase"] or info["self_inverse"])


def test_glossary_order():
    order = [gates.SINGLE, gates.MULTI, gates.MEASURE, gates.CLASSICAL, gates.BARRIER]
    entries = gates.glossary()
    assert len(entries) == len(gates._GATES)
    keys = [(order.index(e["category"]), e["name"]) for e in entries]
    assert keys == sorted(keys)
    assert entries[0]["category"] == gates.SINGLE
    assert entries[-1]["category"] == gates.BARRIER
    assert all(e["known"] for e in entries)
