"""In-depth documentation of every lint rule, for the rule pickers.

Each entry, keyed by rule code, has:

* ``explain`` -- what the rule checks and why it matters;
* ``limits`` -- where it deliberately stays quiet, so a missing warning is not
  read as a clean bill of health;
* ``example`` -- a short program the rule fires on, and ``fixed``, the same
  program corrected. ``/api/linters/rules/<id>`` runs the rule on both, so the
  page shows the rule's real message on the real line, and
  tests/webapp/test_webapp_rule_docs.py checks that it still fires on the
  example and not on the fix.

LintQ's two linters (the port and the original through CodeQL) share codes
LQ00-LQ10 and so share entries; the examples run on the port, which gives the
original's warnings.
"""

from typing import Dict, Optional

_HEAD = "from qiskit import QuantumCircuit\n"

DOCS: Dict[str, Dict[str, Optional[str]]] = {
    # ---------------------------------------------------------------- quick
    "QS000": {
        "explain": "The file is not valid Python, so nothing else can be read: no circuit, no "
                   "graph, no other checks. The finding points at the line Python stopped on, "
                   "which is often just after the real mistake (an unclosed bracket shows up on "
                   "the next line).",
        "limits": "Only Python syntax; a program that parses can still fail when it runs.",
        "example": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0\nqc.cx(0, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n",
    },
    "QS001": {
        "explain": "No QuantumCircuit is built or loaded anywhere in the file, and no function "
                   "takes one. The slicer needs a circuit to analyse; usually it was built in "
                   "another file or notebook cell, or under a name the reader cannot see.",
        "limits": "Counts QuantumCircuit(...), its classmethods (from_qasm_str, ...), "
                  "random_circuit, qasm2/qasm3/qpy loads, and parameters annotated QuantumCircuit.",
        "example": "gates = ['h', 'cx']\nprint('building', gates)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n",
    },
    "QS002": {
        "explain": "A gate names a qubit the circuit does not have. Qubits are numbered from 0, "
                   "so a 2-qubit circuit has qubits 0 and 1; Qiskit raises a CircuitError when "
                   "the line runs. The classic cause is counting from 1.",
        "limits": "Only literal indices on a circuit built in the same file with a literal "
                  "size. Computed indices are QD04's (dependence checks).",
        "example": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 2)\n"
                           "qc.measure([0, 1], [0, 1])\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\n"
                         "qc.measure([0, 1], [0, 1])\n",
    },
    "QS003": {
        "explain": "A multi-qubit gate names the same qubit twice, such as cx(1, 1). A control "
                   "cannot also be its own target; Qiskit raises a CircuitError.",
        "limits": "Literal indices only; a repeat computed in a loop is QD04's.",
        "example": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(1, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n",
    },
    "QS004": {
        "explain": "measure() writes into a classical bit the circuit does not declare. "
                   "QuantumCircuit(2, 1) has one classical bit, numbered 0.",
        "limits": "Literal indices on a circuit with a literal number of classical bits.",
        "example": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(1, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(1, 1)\n",
    },
    "QS005": {
        "explain": "The circuit is never measured, yet it is sampled (run on a backend, "
                   "execute, a Sampler): the counts come back empty. Students often build the "
                   "gates and forget measure_all().",
        "limits": "Quiet when the state is read without measuring (Statevector, Operator, an "
                  "Estimator, a simulator's get_statevector()) or the circuit goes into a "
                  "function of the file that may measure it.",
        "example": _HEAD + "from qiskit_aer import AerSimulator\n\nqc = QuantumCircuit(2)\n"
                           "qc.h(0)\nqc.cx(0, 1)\ncounts = AerSimulator().run(qc).result()"
                           ".get_counts()\n",
        "fixed": _HEAD + "from qiskit_aer import AerSimulator\n\nqc = QuantumCircuit(2)\n"
                         "qc.h(0)\nqc.cx(0, 1)\nqc.measure_all()\ncounts = AerSimulator()"
                         ".run(qc).result().get_counts()\n",
    },
    "QS006": {
        "explain": "A declared qubit is never touched by any gate. Often harmless, a register "
                   "sized for an earlier draft, but it can also mean a gate meant for that "
                   "qubit names another one.",
        "limits": "Literal indices only; any computed index, compose/append, or handing the "
                  "circuit to a function of the file makes the rule stay quiet.",
        "example": _HEAD + "qc = QuantumCircuit(3, 2)\nqc.h(0)\nqc.cx(0, 1)\n"
                           "qc.measure([0, 1], [0, 1])\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\n"
                         "qc.measure([0, 1], [0, 1])\n",
    },
    "QS007": {
        "explain": "Two measurements write the same classical bit, so the first result is "
                   "overwritten before anything reads it. Usually the second should go into "
                   "the next bit.",
        "limits": "Literal bit indices; QD02 follows loops and conditions.",
        "example": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(0, 0)\n"
                           "qc.measure(1, 0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(0, 0)\n"
                         "qc.measure(1, 1)\n",
    },
    "QS008": {
        "explain": "c_if() was removed in Qiskit 2.0; the line fails. Classically conditioned "
                   "gates are now written as a with qc.if_test(...) block, which the slicer "
                   "also reads as a classical dependency.",
        "limits": None,
        "example": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.measure(0, 0)\n"
                           "qc.x(1).c_if(0, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.measure(0, 0)\n"
                         "with qc.if_test((qc.clbits[0], 1)):\n    qc.x(1)\n",
    },
    "QS009": {
        "explain": "execute() was removed from qiskit. Code copied from older tutorials calls "
                   "it; today a backend's run() (after transpile) or a Sampler does the job.",
        "limits": "Reports the call; the import of execute is QS012's.",
        "example": _HEAD + "from qiskit_aer import AerSimulator\n\nqc = QuantumCircuit(1, 1)\n"
                           "qc.h(0)\nqc.measure(0, 0)\njob = execute(qc, AerSimulator())\n",
        "fixed": _HEAD + "from qiskit_aer import AerSimulator\n\nqc = QuantumCircuit(1, 1)\n"
                         "qc.h(0)\nqc.measure(0, 0)\njob = AerSimulator().run(qc)\n",
    },
    "QS010": {
        "explain": "Two identical self-inverse gates (h, x, cx, ...) in a row on the same wires "
                   "cancel out, so together they do nothing. Usually one is a duplicate, or a "
                   "different gate was meant.",
        "limits": "Adjacent literal calls only; it can fire on circuits that are deliberately "
                  "their own inverse (mirror circuits), so it is informational.",
        "example": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.h(0)\nqc.measure(0, 0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\n",
    },
    "QS011": {
        "explain": "A method, name or module that older Qiskit had and Qiskit 2.6.0 does not: "
                   "u1/u2/u3, cu1, cnot, toffoli, iden, bind_parameters, Aer from qiskit, "
                   "qiskit.extensions, ... The hint names the replacement.",
        "limits": "A fixed list of removed APIs; names that never existed are QS012/QS013's.",
        "example": _HEAD + "import math\n\nqc = QuantumCircuit(2)\nqc.h(0)\n"
                           "qc.cu1(math.pi / 2, 0, 1)\n",
        "fixed": _HEAD + "import math\n\nqc = QuantumCircuit(2)\nqc.h(0)\n"
                         "qc.cp(math.pi / 2, 0, 1)\n",
    },
    "QS012": {
        "explain": "An import of a name or module the installed Qiskit does not have. Language "
                   "models and old tutorials invent names such as CNOT or X in "
                   "qiskit.circuit.library (the classes are CXGate and XGate). The hint "
                   "suggests the real name or where it lives.",
        "limits": "Imports from qiskit packages that are installed; others cannot be checked.",
        "example": _HEAD + "from qiskit.circuit.library import CNOT\n\nqc = QuantumCircuit(2)\n"
                           "qc.h(0)\nqc.append(CNOT(), [0, 1])\n",
        "fixed": _HEAD + "from qiskit.circuit.library import CXGate\n\nqc = QuantumCircuit(2)\n"
                         "qc.h(0)\nqc.append(CXGate(), [0, 1])\n",
    },
    "QS013": {
        "explain": "A QuantumCircuit method or attribute, or a keyword argument of a circuit "
                   "method or an imported Qiskit class, that does not exist: qc.clbit "
                   "(clbits), qc.num_qbits, qc.mcx(..., control_state=1) (ctrl_state). Each "
                   "fails when the line runs; the hint gives the closest real name.",
        "limits": "Only circuits the file builds or receives as parameters annotated "
                  "QuantumCircuit, and calls of names imported from Qiskit.",
        "example": _HEAD + "qc = QuantumCircuit(3)\nqc.x(0)\nqc.mcx([0, 1], 2, "
                           "control_state=1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(3)\nqc.x(0)\nqc.mcx([0, 1], 2, ctrl_state=1)\n",
    },
    # ----------------------------------------------------------- dependence
    "QD01": {
        "explain": "No measured bit depends on this gate: the dependence graph proves removing "
                   "it cannot change the output. Typically a gate after the last measurement of "
                   "its qubit, or on a qubit that is never measured.",
        "limits": "Needs the static parser to rebuild the circuit; circuits with no "
                  "measurement get no QD01.",
        "example": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\nqc.x(0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.x(0)\nqc.measure(0, 0)\n",
    },
    "QD02": {
        "explain": "A measurement writes a classical bit that a later measurement overwrites "
                   "before anything reads it, so the first result never reaches the output. "
                   "Unlike QS007 it follows loops and if_test reads.",
        "limits": "A bit read by an if_test in between is not overwritten.",
        "example": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.cx(0, 1)\n"
                           "for q in range(2):\n    qc.measure(q, 0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\n"
                         "for q in range(2):\n    qc.measure(q, q)\n",
    },
    "QD03": {
        "explain": "A qubit is measured before any gate acts on it, so it is still |0> and the "
                   "result is always 0. Usually a preparation gate is missing or the wrong "
                   "qubit is measured.",
        "limits": "Reset counts as returning a qubit to |0>.",
        "example": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.measure([0, 1], [0, 1])\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\n"
                         "qc.measure([0, 1], [0, 1])\n",
    },
    "QD04": {
        "explain": "Rebuilding the circuit hits an error Qiskit itself raises when the program "
                   "runs: a qubit index out of range, the same qubit twice, a gate given the "
                   "wrong number of qubits. Unlike QS002/QS003 it sees indices computed in "
                   "loops and helpers, and points at the line inside the helper.",
        "limits": "Only Qiskit's own errors; other exceptions can come from what the static "
                  "parser cannot model, so they are not reported.",
        "example": _HEAD + "def entangle(qc, pair):\n    qc.h(pair[0])\n"
                           "    qc.cx(pair[0], pair[1])\n    return qc\n\n"
                           "qc = entangle(QuantumCircuit(2), [0, 2])\n",
        "fixed": _HEAD + "def entangle(qc, pair):\n    qc.h(pair[0])\n"
                         "    qc.cx(pair[0], pair[1])\n    return qc\n\n"
                         "qc = entangle(QuantumCircuit(2), [0, 1])\n",
    },
    "QD05": {
        "explain": "A controlled gate whose control qubit is provably still |0> when it runs, "
                   "so the gate never acts. Typical causes: control and target swapped (a GHZ "
                   "chain written cx(i + 1, i)), or the h/x preparing the control is missing "
                   "or placed after the gate.",
        "limits": "Reports a line only when none of its gates can act (a loop line that acts "
                  "on some iterations is fine); skips functions handed their circuit, and "
                  "gates with ctrl_state.",
        "example": _HEAD + "qc = QuantumCircuit(3, 3)\nqc.h(0)\nfor i in range(2):\n"
                           "    qc.cx(i + 1, i)\nqc.measure(range(3), range(3))\n",
        "fixed": _HEAD + "qc = QuantumCircuit(3, 3)\nqc.h(0)\nfor i in range(2):\n"
                         "    qc.cx(i, i + 1)\nqc.measure(range(3), range(3))\n",
    },
    # ---------------------------------------------------------------- LintQ
    "LQ00": {
        "explain": "LintQ skipped the program because it has very many quantum operations; its "
                   "analyses are not meant for programs that large.",
        "limits": None,
        "example": None,
        "fixed": None,
    },
    "LQ01": {
        "explain": "A qubit is measured twice with nothing acting on it in between. The second "
                   "measurement gives the same result as the first, so it is redundant or a "
                   "different qubit was meant.",
        "limits": "LintQ's DoubleMeasurement query, high precision.",
        "example": _HEAD + "qc = QuantumCircuit(1, 2)\nqc.h(0)\nqc.measure(0, 0)\n"
                           "qc.measure(0, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\n",
    },
    "LQ02": {
        "explain": "An unconditional gate acts on a qubit right after it is measured. After a "
                   "measurement the qubit is in a basis state, so the gate is usually meant "
                   "for before the measurement, or should depend on its result (if_test).",
        "limits": "LintQ's OpAfterMeasurement query, high precision; conditional gates are fine.",
        "example": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.measure(0, 0)\nqc.x(0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(1, 1)\nqc.h(0)\nqc.x(0)\nqc.measure(0, 0)\n",
    },
    "LQ03": {
        "explain": "measure_all() adds a new classical register by default, so on a circuit "
                   "that already has classical bits the results land in the new register and "
                   "the declared bits stay 0. Use measure_all(add_bits=False) or measure().",
        "limits": "LintQ's MeasureAll query, high precision.",
        "example": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure_all()\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure_all()\n",
    },
    "LQ04": {
        "explain": "A gate conditioned on classical bits that no earlier measurement in the "
                   "circuit wrote: the condition is always false (bits start at 0), so the "
                   "gate never runs.",
        "limits": "LintQ's ConditionalGateWithoutMeas query, high precision.",
        "example": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.x(1).c_if(0, 1)\n"
                           "qc.measure(0, 0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 1)\nqc.h(0)\nqc.measure(0, 0)\n"
                         "qc.x(1).c_if(0, 1)\n",
    },
    "LQ05": {
        "explain": "compose() returns a new circuit unless inplace=True; here the result is "
                   "neither stored nor returned, so the composition is lost and the circuit is "
                   "unchanged.",
        "limits": "LintQ's GhostComposition query, high precision.",
        "example": _HEAD + "qc = QuantumCircuit(2)\nbell = QuantumCircuit(2)\nbell.h(0)\n"
                           "bell.cx(0, 1)\nqc.compose(bell)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nbell = QuantumCircuit(2)\nbell.h(0)\n"
                         "bell.cx(0, 1)\nqc.compose(bell, inplace=True)\n",
    },
    "LQ06": {
        "explain": "A gate is added to a circuit after it was transpiled at optimization level "
                   "3. The new gate is not optimised or mapped to the device, so it may not "
                   "run on the target backend; add gates before transpiling.",
        "limits": "LintQ's OpAfterOptimization query, high precision.",
        "example": _HEAD + "from qiskit import transpile\n\nqc = QuantumCircuit(2)\nqc.h(0)\n"
                           "opt = transpile(qc, basis_gates=['cx', 'u'], optimization_level=3)\n"
                           "opt.cx(0, 1)\n",
        "fixed": _HEAD + "from qiskit import transpile\n\nqc = QuantumCircuit(2)\nqc.h(0)\n"
                         "qc.cx(0, 1)\nopt = transpile(qc, basis_gates=['cx', 'u'], "
                         "optimization_level=3)\n",
    },
    "LQ07": {
        "explain": "A qubit is measured although no gate acted on it, so the classical bit is "
                   "constant 0, the same mistake as QD03 found by LintQ's own analysis.",
        "limits": "LintQ's ConstantClassicBit query, medium precision.",
        "example": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.measure(0, 0)\n"
                           "qc.measure(1, 1)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure(0, 0)\n"
                         "qc.measure(1, 1)\n",
    },
    "LQ08": {
        "explain": "A circuit is built but none of its qubits is ever used. Often the gates went "
                   "to a second circuit by mistake, so the one that is printed, run or returned "
                   "is empty.",
        "limits": "LintQ's OversizedCircuit query, medium precision. Despite its message "
                  "(\"some of its qubits\") it fires only when no qubit is used at all; QS006 "
                  "and the dependence checks cover single idle qubits.",
        "example": _HEAD + "qc = QuantumCircuit(2)\ncircuit = QuantumCircuit(2)\ncircuit.h(0)\n"
                           "circuit.cx(0, 1)\nprint(qc)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\nprint(qc)\n",
    },
    "LQ09": {
        "explain": "The circuit has more qubits than classical bits and is never measured with "
                   "measure_all(), so some qubits can never be measured: their results cannot "
                   "be read out.",
        "limits": "LintQ's UnmeasurableQubits query, medium precision; it fires on circuits "
                  "that are not meant to be measured (statevector work) too.",
        "example": _HEAD + "qc = QuantumCircuit(3, 1)\nqc.h(0)\nqc.cx(0, 1)\nqc.cx(1, 2)\n"
                           "qc.measure(2, 0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(3, 3)\nqc.h(0)\nqc.cx(0, 1)\nqc.cx(1, 2)\n"
                         "qc.measure([0, 1, 2], [0, 1, 2])\n",
    },
    "LQ10": {
        "explain": "The deprecated iden() is called; it was removed from Qiskit (use id()). "
                   "QS011 reports the same call with its replacement.",
        "limits": "LintQ's DeprecatedIdentity query.",
        "example": _HEAD + "qc = QuantumCircuit(1)\nqc.iden(0)\nqc.h(0)\n",
        "fixed": _HEAD + "qc = QuantumCircuit(1)\nqc.id(0)\nqc.h(0)\n",
    },
}


def doc(code: str) -> Optional[Dict[str, Optional[str]]]:
    return DOCS.get(code)
