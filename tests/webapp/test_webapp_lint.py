"""lint.py: one positive and one negative case for every QS code it implements."""

import textwrap

import pytest

import lint

HEADER = "from qiskit import QuantumCircuit\n"


def check(body):
    return lint.check_source(HEADER + textwrap.dedent(body))


def codes(findings):
    return [f["code"] for f in findings]


def only(findings, code):
    return [f for f in findings if f["code"] == code]


CLEAN = """
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure(0, 0)
qc.measure(1, 1)
"""


def test_clean_circuit_has_no_findings():
    assert check(CLEAN) == []


@pytest.mark.requirement("FR-21")
def test_every_finding_has_the_documented_shape():
    findings = check("""
        qc = QuantumCircuit(3, 1)
        qc.h(7)
        qc.x(1)
        qc.x(1)
        qc.c_if(0, 1)
        """)
    assert findings
    required = {"code", "severity", "line", "column", "message", "hint"}
    for f in findings:
        assert required <= set(f) <= required | {"lines"}
        if "lines" in f:           # every source line a finding concerns, its own included
            assert f["line"] in f["lines"] and all(isinstance(n, int) and n >= 1 for n in f["lines"])
        assert f["severity"] in ("error", "warning", "info")
        assert isinstance(f["line"], int) and f["line"] >= 1
        assert isinstance(f["column"], int) and f["column"] >= 1
        assert f["message"] and f["hint"]


def test_findings_are_sorted_by_line():
    findings = check("""
        qc = QuantumCircuit(2)
        qc.h(5)
        qc.c_if(0, 1)
        """)
    assert [(f["line"], f["column"], f["code"]) for f in findings] == \
        sorted((f["line"], f["column"], f["code"]) for f in findings)


# QS000 syntax error ---------------------------------------------------------

def test_qs000_positive():
    findings = lint.check_source("qc = QuantumCircuit(2\nqc.h(0)\n")
    assert codes(findings) == ["QS000"]
    assert findings[0]["severity"] == "error"
    assert findings[0]["line"] >= 1
    assert "could not parse" in findings[0]["message"]
    assert findings[0]["hint"]


def test_qs000_negative():
    assert "QS000" not in codes(check(CLEAN))


# QS001 no QuantumCircuit -----------------------------------------------------

def test_qs001_positive():
    findings = lint.check_source("x = 1\nprint(x)\n")
    qs = only(findings, "QS001")
    assert len(qs) == 1 and qs[0]["severity"] == "error" and qs[0]["line"] == 1


def test_qs001_negative_counts_any_constructor_call():
    # Not assigned to a name, but still constructed: no QS001.
    assert "QS001" not in codes(lint.check_source("from qiskit import QuantumCircuit\n"
                                                  "circuits = [QuantumCircuit(1)]\n"))
    assert "QS001" not in codes(lint.check_source("import qiskit\nqc = qiskit.QuantumCircuit(1)\n"))


@pytest.mark.parametrize("source", [
    "from qiskit import QuantumCircuit\nqc = QuantumCircuit.from_qasm_str(text)\n",
    "from qiskit.circuit.random import random_circuit\nqc = random_circuit(3, 4)\n",
    "from qiskit import qasm2\nqc = qasm2.loads(text)\n",
    # A library of circuit functions, given their circuit by the caller.
    "from qiskit import QuantumCircuit\ndef flip(qc: QuantumCircuit, q: int):\n    qc.x(q)\n",
])
def test_qs001_negative_loaders_and_circuit_parameters(source):
    assert "QS001" not in codes(lint.check_source(source))


def test_qs001_still_fires_for_an_unrelated_loads():
    assert "QS001" in codes(lint.check_source("import json\ndata = json.loads(text)\n"))


# QS002 qubit index out of range / negative ----------------------------------

def test_qs002_positive_out_of_range():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.cx(0, 2)
        qc.measure([0, 1], [0, 1])
        """)
    qs = only(findings, "QS002")
    assert len(qs) == 1
    assert qs[0]["line"] == 5
    assert "qubit 2" in qs[0]["message"] and "0-1" in qs[0]["message"]
    assert qs[0]["hint"]


def test_qs002_positive_param_gate():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.rx(0.5, 3)
        qc.measure_all()
        """)
    qs = only(findings, "QS002")
    assert [f["line"] for f in qs] == [4]
    assert "rx()" in qs[0]["message"]


def test_qs002_out_of_range_negative_index():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.x(-5)
        qc.measure_all()
        """)
    assert "QS002" in codes(findings)


def test_qs002_negative():
    findings = check("""
        qc = QuantumCircuit(3, 3)
        qc.rx(5, 2)
        qc.ccx(0, 1, 2)
        qc.h(n)
        qc.measure([0, 1, 2], [0, 1, 2])
        """)
    # rx's first argument is an angle, not a qubit; h(n) is not a literal.
    assert "QS002" not in codes(findings)


# QS003 repeated qubit in a multi-qubit gate ---------------------------------

def test_qs003_positive():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.cx(1, 1)
        qc.measure_all()
        """)
    qs = only(findings, "QS003")
    assert len(qs) == 1 and qs[0]["line"] == 4 and qs[0]["severity"] == "error"


def test_qs003_negative():
    assert "QS003" not in codes(check(CLEAN))
    # A single-qubit gate broadcast over a list is not a repeated control.
    assert "QS003" not in codes(check("""
        qc = QuantumCircuit(2, 2)
        qc.h([0, 0])
        qc.measure_all()
        """))


def test_qs003_parameterized_two_qubit_gate():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.cp(0.5, 1, 1)
        qc.measure_all()
        """)
    assert "QS003" in codes(findings)


# QS004 classical bit out of range ------------------------------------------

def test_qs004_positive():
    findings = check("""
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.measure(1, 1)
        """)
    qs = only(findings, "QS004")
    assert len(qs) == 1 and qs[0]["line"] == 5
    assert "QuantumCircuit(2, 2)" in qs[0]["hint"]


def test_qs004_negative():
    assert "QS004" not in codes(check(CLEAN))


# QS005 never measures -------------------------------------------------------

def test_qs005_positive():
    findings = check("""
        qc = QuantumCircuit(1)
        qc.h(0)
        """)
    qs = only(findings, "QS005")
    assert len(qs) == 1 and qs[0]["severity"] == "warning" and qs[0]["line"] == 3


@pytest.mark.parametrize("call", ["qc.measure(0, 0)", "qc.measure_all()", "qc.measure_active()"])
def test_qs005_negative(call):
    findings = check(f"""
        qc = QuantumCircuit(1, 1)
        qc.h(0)
        {call}
        """)
    assert "QS005" not in codes(findings)


@pytest.mark.parametrize("use", [
    "state = Statevector(qc)",
    "state = Statevector.from_instruction(qc)",
    "unitary = Operator(qc)",
    "job = estimator.run([(qc, observable)])",              # reads expectation values
    "flip(qc, 0)",                                           # a helper here may measure it
    "state = execute(qc, sim).result().get_statevector()",   # statevector simulator
])
def test_qs005_negative_state_is_read_not_sampled(use):
    findings = check(f"""
        def flip(qc, q):
            qc.x(q)

        qc = QuantumCircuit(1)
        qc.h(0)
        {use}
        """)
    assert "QS005" not in codes(findings)


@pytest.mark.parametrize("use", [
    "counts = execute(qc, backend).result().get_counts()",
    "job = backend.run(qc)",
    "job = sampler.run([qc])",
    "flip(qc, 0)\njob = backend.run(qc)",                    # also sampled: still a mistake
])
def test_qs005_positive_sampling_an_unmeasured_circuit(use):
    findings = lint.check_source(HEADER + "def flip(qc, q):\n    qc.x(q)\n\n"
                                 "qc = QuantumCircuit(1)\nqc.h(0)\n" + use + "\n")
    assert "QS005" in codes(findings)


# QS006 idle qubits ----------------------------------------------------------

def test_qs006_positive():
    findings = check("""
        qc = QuantumCircuit(4, 1)
        qc.h(0)
        qc.cx(0, 1)
        qc.measure(0, 0)
        """)
    qs = only(findings, "QS006")
    assert len(qs) == 1 and qs[0]["severity"] == "info" and qs[0]["line"] == 3
    assert "[2, 3]" in qs[0]["message"]


@pytest.mark.parametrize("body", [
    CLEAN,
    # Opaque uses: something the AST cannot see through.
    "qc = QuantumCircuit(3)\nqc.h(0)\nqc.measure_all()\n",
    "qc = QuantumCircuit(3)\nfor i in range(3):\n    qc.h(i)\n",
    "qc = QuantumCircuit(3)\nqc.append(gate, [0, 1, 2])\n",
    # Handed to a function of this file, which may use the other qubits.
    "def prepare(qc):\n    qc.x(2)\n\nqc = QuantumCircuit(3)\nqc.h(0)\nprepare(qc)\n",
])
def test_qs006_negative(body):
    assert "QS006" not in codes(check(body))


# QS007 two measurements into one clbit -------------------------------------

def test_qs007_positive():
    findings = check("""
        qc = QuantumCircuit(2, 1)
        qc.measure(0, 0)
        qc.measure(1, 0)
        """)
    qs = only(findings, "QS007")
    assert len(qs) == 1 and qs[0]["line"] == 5 and qs[0]["severity"] == "warning"
    assert "first at line 4" in qs[0]["message"]


def test_qs007_negative():
    assert "QS007" not in codes(check(CLEAN))
    assert "QS007" not in codes(check("qc = QuantumCircuit(2, 2)\nqc.measure([0, 1], [0, 1])\n"))


# QS008 c_if -----------------------------------------------------------------

def test_qs008_positive():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.measure(0, 0)
        qc.x(1).c_if(0, 1)
        """)
    qs = only(findings, "QS008")
    assert len(qs) == 1 and qs[0]["line"] == 5 and qs[0]["severity"] == "error"
    assert "if_test" in qs[0]["hint"]


def test_qs008_negative():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.measure(0, 0)
        with qc.if_test((qc.clbits[0], 1)):
            qc.x(1)
        qc.measure(1, 1)
        """)
    assert "QS008" not in codes(findings)


# QS009 execute() ------------------------------------------------------------

def test_qs009_positive():
    findings = check("""
        from qiskit import execute
        qc = QuantumCircuit(1, 1)
        qc.measure(0, 0)
        job = execute(qc, backend)
        """)
    qs = only(findings, "QS009")
    assert len(qs) == 1 and qs[0]["line"] == 6 and qs[0]["severity"] == "warning"


def test_qs009_negative():
    findings = check("""
        qc = QuantumCircuit(1, 1)
        qc.measure(0, 0)
        job = backend.run(qc)
        """)
    assert "QS009" not in codes(findings)


# QS010 adjacent canceling pair ---------------------------------------------

def test_qs010_positive():
    findings = check("""
        qc = QuantumCircuit(2, 2)
        qc.cx(0, 1)
        qc.cx(0, 1)
        qc.measure_all()
        """)
    qs = only(findings, "QS010")
    assert len(qs) == 1 and qs[0]["line"] == 5 and qs[0]["severity"] == "info"
    assert "line 4" in qs[0]["message"]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(2)\nqc.cx(0, 1)\nqc.cx(1, 0)\nqc.measure_all()\n",      # different wires
    "qc = QuantumCircuit(1)\nqc.h(0)\nqc.barrier()\nqc.h(0)\nqc.measure_all()\n",  # separated
    "qc = QuantumCircuit(1)\nqc.s(0)\nqc.s(0)\nqc.measure_all()\n",              # not self-inverse
    "qc = QuantumCircuit(1)\nqc.h(i)\nqc.h(i)\nqc.measure_all()\n",              # not literal
])
def test_qs010_negative(body):
    assert "QS010" not in codes(check(body))


def test_linter_showcase_example_trips_the_advertised_checks():
    import examples

    found = set(codes(lint.check_source(examples.load("linter_showcase")["source"])))
    assert {"QS005", "QS006", "QS010"} <= found


# QS011 removed Qiskit API -------------------------------------------------------

@pytest.mark.parametrize("line, what", [
    ("qc.u1(0.5, 0)", "QuantumCircuit.u1()"),
    ("qc.cu1(0.5, 0, 1)", "QuantumCircuit.cu1()"),
    ("qc.cnot(0, 1)", "QuantumCircuit.cnot()"),
    ("qc.iden(0)", "QuantumCircuit.iden()"),
    ("helper_circuit.toffoli(0, 1, 2)", "QuantumCircuit.toffoli()"),   # any receiver
    ("qc.bind_parameters({})", "QuantumCircuit.bind_parameters()"),
    ("qc.extend(other)", "QuantumCircuit.extend()"),                  # a known circuit
    ("from qiskit import Aer", "qiskit.Aer"),
    ("from qiskit import IBMQ, transpile", "qiskit.IBMQ"),
    ("backend = qiskit.BasicAer.get_backend('qasm_simulator')", "qiskit.BasicAer"),
    ("from qiskit.extensions import UnitaryGate", "qiskit.extensions"),
    ("from qiskit.tools.monitor import job_monitor", "qiskit.tools"),
    ("import qiskit.providers.aer", "qiskit.providers.aer"),
    ("from qiskit.utils import QuantumInstance", "qiskit.utils.QuantumInstance"),
])
def test_qs011_positive(line, what):
    findings = check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n")
    qs = only(findings, "QS011")
    assert len(qs) == 1 and qs[0]["line"] == 3 and qs[0]["severity"] == "error"
    assert what in qs[0]["message"] and qs[0]["hint"].startswith("Use ")


@pytest.mark.parametrize("line", [
    "qc.p(0.5, 0)",
    "qc.cp(0.5, 0, 1)",
    "qc.id(0)",
    "qc.assign_parameters({})",
    "values = []\nvalues.extend([1, 2])",        # list.extend, not a circuit
    "from qiskit import transpile, QuantumRegister",
    "from qiskit_aer import Aer",
    "from qiskit.visualization import plot_histogram",
    "from qiskit import *",
])
def test_qs011_negative(line):
    assert "QS011" not in codes(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"))


def test_qs011_leaves_c_if_and_execute_to_their_own_codes():
    findings = check("""
        from qiskit import execute
        qc = QuantumCircuit(1, 1)
        qc.x(0).c_if(0, 1)
        job = execute(qc, backend)
        """)
    assert {"QS008", "QS009"} <= set(codes(findings))
    assert "QS011" not in codes(findings)


# QS012 unknown Qiskit name ------------------------------------------------------

@pytest.mark.parametrize("line, message, hint", [
    ("from qiskit.circuit.library import CNOT", "qiskit.circuit.library has no name CNOT",
     "Did you mean CXGate?"),
    ("from qiskit.circuit.library import X, HGate", "qiskit.circuit.library has no name X",
     "XGate"),
    ("from qiskit.circuit.library import MCX", "has no name MCX", "MCXGate"),
    ("from qiskit import AerSimulator", "qiskit has no name AerSimulator",
     "from qiskit_aer import AerSimulator"),
    ("from qiskit.quantum_info.operators import PauliY", "has no name PauliY",
     "Did you mean Pauli?"),
    ("from qiskit import execute", "qiskit has no name execute", "backend's run()"),
    ("from qiskit import pulse", "qiskit has no name pulse", "no replacement"),
    ("from qiskit.made_up import thing", "There is no module qiskit.made_up", ""),
    ("import qiskit.not_a_module", "There is no module qiskit.not_a_module", ""),
])
def test_qs012_positive(line, message, hint):
    findings = check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n")
    qs = only(findings, "QS012")
    assert len(qs) == 1 and qs[0]["line"] == 3 and qs[0]["severity"] == "error"
    assert message in qs[0]["message"] and "2.6.0" in qs[0]["message"]
    assert hint in qs[0]["hint"]


@pytest.mark.parametrize("line", [
    "from qiskit import QuantumCircuit, transpile, QuantumRegister, qasm2",
    "from qiskit.circuit.library import CXGate, QFT, MCXGate",
    "from qiskit.quantum_info import Statevector, Operator, Pauli",
    "from qiskit_aer import AerSimulator, Aer",
    "import qiskit.qasm3",
    "from qiskit import *",
    "from qiskit_not_installed_here import Anything",        # cannot tell: no finding
    "from numpy import CNOT",                                # not Qiskit
])
def test_qs012_negative(line):
    assert "QS012" not in codes(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"))


@pytest.mark.parametrize("line", [
    "from qiskit import Aer",
    "from qiskit.extensions import UnitaryGate",
    "import qiskit.providers.aer",
    "from qiskit.utils import QuantumInstance",
])
def test_qs012_leaves_removed_apis_to_qs011(line):
    findings = check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n")
    assert "QS011" in codes(findings) and "QS012" not in codes(findings)


# QS013 unknown Qiskit method or argument -----------------------------------------

@pytest.mark.parametrize("line, message, hint", [
    ("qc.clbit(0)", "QuantumCircuit has no attribute clbit", "Did you mean clbits?"),
    ("n = qc.num_qbits", "QuantumCircuit has no attribute num_qbits", "num_qubits"),
    ("qc.mcx([0, 1], 2, control_state=1)", "mcx() takes no argument control_state",
     "Did you mean ctrl_state?"),
    ("qc.h(0, lable='x')", "h() takes no argument lable", "Did you mean label?"),
    ("from qiskit.circuit.library import IntegerComparator\n"
     "cmp = IntegerComparator(2, 3, ge=True)", "IntegerComparator() takes no argument ge",
     "Did you mean geq?"),
])
def test_qs013_positive(line, message, hint):
    qs = only(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"), "QS013")
    assert len(qs) == 1 and qs[0]["severity"] == "error"
    assert message in qs[0]["message"] and "2.6.0" in qs[0]["message"] and hint in qs[0]["hint"]


def test_qs013_reads_parameters_annotated_quantumcircuit():
    findings = check("""
        def flip(qc: QuantumCircuit, q: int) -> QuantumCircuit:
            qc.mcx([0], q, control_state=0)
            return qc
        """)
    assert [f["line"] for f in only(findings, "QS013")] == [4]


@pytest.mark.parametrize("line", [
    "qc.mcx([0, 1], 2, ctrl_state=1)",
    "qc.h(0, label='x')",
    "n = qc.num_qubits + len(qc.clbits)",
    "qc.compose(other, qubits=[0, 1], inplace=True)",
    "values = []\nvalues.clbit()",                            # not a circuit
    "from qiskit.circuit.library import QFTGate\ngate = QFTGate(3)",
    "qc.cu1(0.5, 0, 1)",                                     # removed: QS011's
])
def test_qs013_negative(line):
    assert "QS013" not in codes(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"))
