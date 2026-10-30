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


# The rules once coded QS005-QS007 and QS010 are dependence checks now (QD06, QD07,
# QD02, QD08), and those codes name newer quick checks below; the old rules'
# tests are in tests/benchmark/test_linters.py.


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


# QS005 deprecated Qiskit API ------------------------------------------------------

@pytest.mark.parametrize("line, what, hint", [
    ("from qiskit.circuit.library import QFT", "qiskit.circuit.library.QFT", "QFTGate"),
    ("from qiskit.circuit.library import TwoLocal, QFTGate", "TwoLocal", "n_local"),
    ("qc.mcx([0, 1], 2, mode='noancilla')", "The argument mode of QuantumCircuit.mcx()",
     "MCXGate"),
    ("d = qc.duration", "QuantumCircuit.duration", "removed"),
])
def test_qs005_positive(line, what, hint):
    qs = only(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"), "QS005")
    assert len(qs) == 1 and qs[0]["line"] == 3 and qs[0]["severity"] == "warning"
    assert what in qs[0]["message"] and "deprecated as of Qiskit" in qs[0]["message"]
    assert hint in qs[0]["hint"]


@pytest.mark.parametrize("line", [
    "from qiskit.circuit.library import QFTGate, MCXGate, n_local",
    "qc.mcx([0, 1], 2)",
    "n = qc.num_qubits",
    "values = []\nvalues.duration",                     # not a circuit
])
def test_qs005_negative(line):
    assert "QS005" not in codes(check(f"qc = QuantumCircuit(3, 3)\n{line}\nqc.measure_all()\n"))


# QS006 result discarded -------------------------------------------------------------

@pytest.mark.parametrize("line, what", [
    ("qc.inverse()", "qc.inverse()"),
    ("qc.copy()", "qc.copy()"),
    ("qc.compose(other)", "qc.compose()"),
    ("qc.assign_parameters({})", "qc.assign_parameters()"),
    ("qc.measure_all(inplace=False)", "qc.measure_all()"),
    ("transpile(qc, basis_gates=['cx', 'u'])", "transpile()"),
])
def test_qs006_positive(line, what):
    qs = only(check(f"qc = QuantumCircuit(2, 2)\nother = QuantumCircuit(2)\n{line}\n"), "QS006")
    assert len(qs) == 1 and qs[0]["line"] == 4 and qs[0]["severity"] == "warning"
    assert what in qs[0]["message"] and "discarded" in qs[0]["message"]


@pytest.mark.parametrize("line", [
    "qc = qc.inverse()",
    "qc.compose(other, inplace=True)",
    "qc.measure_all()",                       # inplace by default
    "qc.s(0).inverse()",                      # inverts the gate in place
    "print(qc.inverse())",
    "values = []\nvalues.copy()",             # not a circuit
])
def test_qs006_negative(line):
    assert "QS006" not in codes(check(f"qc = QuantumCircuit(2, 2)\nother = QuantumCircuit(2)\n{line}\n"))


@pytest.mark.parametrize("body, line, message", [
    ("qc = QuantumCircuit(2)\nqc.h(0)\nqc.measure(0, 0)\n", 4, "declares no classical bits"),
    ("qc = QuantumCircuit(2)\nb = qc.clbits[0]\n", 3, "qc.clbits[0] does not exist"),
    ("qc = QuantumCircuit(2, 1)\nwith qc.if_test((qc.clbits[1], 1)):\n    qc.x(0)\n", 3,
     "declares 1 classical bit"),
])
def test_qs004_circuits_without_enough_classical_bits(body, line, message):
    qs = only(check(body), "QS004")
    assert len(qs) == 1 and qs[0]["line"] == line and message in qs[0]["message"]


@pytest.mark.parametrize("body", [
    "qc = QuantumCircuit(2)\nqc.measure_all()\nqc.measure(0, 0)\n",      # measure_all adds bits
    "qc = QuantumCircuit(2)\nqc.add_bits([Clbit()])\nqc.measure(0, 0)\n",
    "qc = QuantumCircuit(qr, cr)\nqc.measure(0, 0)\n",                     # registers: unknown
    "qc = QuantumCircuit(2, 2)\nb = qc.clbits[-1]\n",
])
def test_qs004_unknown_or_enough_classical_bits(body):
    assert "QS004" not in codes(check(body))


# QS007 unpacked list in a fixed-size gate --------------------------------------------

@pytest.mark.parametrize("line", ["qc.ccx(*controls, 3)", "qc.cswap(*pair, 2)", "qc.crx(0.5, *pair)"])
def test_qs007_positive(line):
    qs = only(check(f"qc = QuantumCircuit(4)\ncontrols = [0, 1]\npair = [0, 1]\n{line}\n"), "QS007")
    assert len(qs) == 1 and qs[0]["line"] == 5 and qs[0]["severity"] == "warning"
    assert "mcx" in qs[0]["hint"]


@pytest.mark.parametrize("line", [
    "qc.cx(*pair)",                  # a bare unpacked pair fails loudly if it is not two
    "qc.mcx(controls, 3)",
    "qc.ccx(0, 1, 2)",
    "qc.h(*pair)",                   # single-qubit: broadcasts over the list
])
def test_qs007_negative(line):
    assert "QS007" not in codes(check(f"qc = QuantumCircuit(4)\ncontrols = [0, 1]\npair = [0, 1]\n{line}\n"))


@pytest.mark.parametrize("line, message", [
    ("from qiskit.quantum_info import Operator\nop = Operator([[1, 0], [0, 1]])\nop[0, 0] = 0",
     "Operator objects do not support item assignment"),
    ("from qiskit.quantum_info import Statevector\nsv = Statevector([1, 0])\nsv[1] += 1",
     "Statevector objects do not support item assignment"),
    ("i = qc.clbits[0].index", "Clbit has no attribute index"),
])
def test_qs013_unsupported_operations_on_qiskit_objects(line, message):
    qs = only(check(f"qc = QuantumCircuit(2, 2)\n{line}\n"), "QS013")
    assert len(qs) == 1 and message in qs[0]["message"]


@pytest.mark.parametrize("line", [
    "from qiskit.quantum_info import SparsePauliOp\nop = SparsePauliOp('ZZ')\nop[0] = SparsePauliOp('XX')",
    "import numpy as np\nm = np.eye(2)\nm[0, 0] = 0",
    "i = qc.find_bit(qc.clbits[0]).index",
])
def test_qs013_supported_operations_are_quiet(line):
    assert "QS013" not in codes(check(f"qc = QuantumCircuit(2, 2)\n{line}\n"))


# QS010 simulator result never saved ---------------------------------------------------

AER = "from qiskit_aer import AerSimulator\nqc = QuantumCircuit(1)\nqc.h(0)\nsim = AerSimulator()\n"


@pytest.mark.parametrize("line, getter", [
    ("sv = sim.run(qc).result().get_statevector()", "get_statevector"),
    ("u = sim.run(qc).result().get_unitary()", "get_unitary"),
])
def test_qs010_positive(line, getter):
    qs = only(check(AER + line + "\n"), "QS010")
    assert len(qs) == 1 and qs[0]["line"] == 6 and getter in qs[0]["message"]


@pytest.mark.parametrize("line", [
    "qc.save_statevector()\nsv = sim.run(qc).result().get_statevector()",
    "sv = execute(qc, Aer.get_backend('statevector_simulator')).result().get_statevector()",
    "sv = execute(qc, backend='local_statevector_simulator').result().get_statevector()",
])
def test_qs010_negative(line):
    assert "QS010" not in codes(check(AER + line + "\n"))


# QS015 classical bit assumed on a given circuit ------------------------------------

@pytest.mark.parametrize("line", [
    "qc.measure(q, 0)",
    "qc.measure(q, q)",
    "qc.measure([q, 1], [q, 1])",
    "for i in range(2):\n            qc.measure(i, i)",
])
def test_qs015_positive(line):
    qs = only(check(f"""
    def measure(qc: QuantumCircuit, q: int):
        qc.h(q)
        {line}
        return qc
    """), "QS015")
    assert len(qs) == 1 and qs[0]["line"] in (5, 6) and qs[0]["severity"] == "warning"
    assert "a circuit this function is given" in qs[0]["message"]


@pytest.mark.parametrize("body", [
    # the caller picks the classical bit
    "def measure(qc: QuantumCircuit, q: int, c: int):\n    qc.measure(q, c)\n",
    # the function checks or adds classical bits
    "def measure(qc: QuantumCircuit, q: int):\n    if qc.num_clbits == 0:\n"
    "        qc.add_register(ClassicalRegister(1))\n    qc.measure(q, 0)\n",
    "def measure(qc: QuantumCircuit, q: int):\n    qc.add_bits([Clbit()])\n    qc.measure(q, 0)\n",
    # the circuit is the function's own
    "def measure(qc: QuantumCircuit, q: int):\n    qc = QuantumCircuit(2, 2)\n    qc.measure(q, 0)\n",
    # called in the file: the rebuilt circuit decides (QS014)
    "def measure(qc: QuantumCircuit, q: int):\n    qc.measure(q, 0)\n\nmeasure(QuantumCircuit(1, 1), 0)\n",
    # not annotated as a circuit
    "def measure(qc, q):\n    qc.measure(q, 0)\n",
])
def test_qs015_negative(body):
    assert "QS015" not in codes(check(body))


# QS016 counts from a circuit that never measures -----------------------------------

AER_IMPORT = "from qiskit_aer import AerSimulator\n"


@pytest.mark.parametrize("body, line", [
    ("def sample():\n    qc = QuantumCircuit(2)\n    qc.h(0)\n"
     "    return AerSimulator().run(qc).result().get_counts()\n", 6),
    ("def sample():\n    qc = QuantumCircuit(1)\n    qc.initialize([0, 1], 0)\n"
     "    result = AerSimulator().run(qc, shots=10).result()\n    return result.get_counts(qc)\n", 6),
    # called by the file: its own circuit is still never measured
    ("def sample():\n    qc = QuantumCircuit(1)\n"
     "    return AerSimulator().run(qc).result().get_counts()\n\ncounts = sample()\n", 5),
    ("def read(qc: QuantumCircuit) -> dict:\n"
     "    return AerSimulator().run(qc).result().get_counts()\n", 4),
])
def test_qs016_positive(body, line):
    qs = only(check(AER_IMPORT + body), "QS016")
    assert len(qs) == 1 and qs[0]["line"] == line and qs[0]["severity"] == "warning"
    assert "No counts for experiment" in qs[0]["message"]


@pytest.mark.parametrize("body", [
    "def sample():\n    qc = QuantumCircuit(1, 1)\n    qc.measure(0, 0)\n"
    "    return AerSimulator().run(qc).result().get_counts()\n",
    "def sample():\n    qc = QuantumCircuit(1)\n    qc.measure_all()\n"
    "    return AerSimulator().run(qc).result().get_counts()\n",
    # measured somewhere else: transpile, a helper, a composed circuit
    "def sample():\n    qc = QuantumCircuit(1)\n    tqc = transpile(qc)\n"
    "    return AerSimulator().run(tqc).result().get_counts()\n",
    "def sample():\n    qc = QuantumCircuit(1)\n    add_measurements(qc)\n"
    "    return AerSimulator().run(qc).result().get_counts()\n",
    "def sample(other: QuantumCircuit):\n    qc = QuantumCircuit(1)\n    qc.compose(other, inplace=True)\n"
    "    return AerSimulator().run(qc).result().get_counts()\n",
    # a given circuit the file's own caller may have measured
    "def read(qc: QuantumCircuit) -> dict:\n    return AerSimulator().run(qc).result().get_counts()\n\n"
    "qc = QuantumCircuit(1, 1)\nqc.measure(0, 0)\ncounts = read(qc)\n",
    # no counts read
    "def state():\n    qc = QuantumCircuit(1)\n    qc.save_statevector()\n"
    "    return AerSimulator().run(qc).result().get_statevector()\n",
])
def test_qs016_negative(body):
    assert "QS016" not in codes(check(AER_IMPORT + body))
