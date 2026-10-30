"""The quick checks: errors Python or Qiskit would raise, found without running the file.

They read the Python AST, so they see what is written literally: a syntax
error, a QuantumCircuit that is never built, an index outside a literal
register, removed or unknown Qiskit APIs (QS012 and QS013 look names and
arguments up in the installed Qiskit, importing its modules, never the file).
QS014 adds the errors Qiskit itself raises while the static parser rebuilds the
circuit, which also covers indices computed in loops and helpers.

Mistakes about what the circuit does (no measurement, idle qubits, overwritten
measurements, cancelling pairs, dead gates) need the rebuilt circuit and are
the dependence checks' (linter_plugins/dependence_checks.py).

Each finding is a dict with: code, severity (error/warning/info), line, column,
message and hint, and, when it is about more than its own line, ``lines``: every
source line it concerns (the slicer marks the graph nodes they produced).
"""

import ast
import re
from typing import Any, Dict, List, Optional

# Gate methods that take qubit indices as positional arguments.
_GATE_ARITY = {
    "h": 1, "x": 1, "y": 1, "z": 1, "s": 1, "sdg": 1, "t": 1, "tdg": 1,
    "sx": 1, "sxdg": 1, "id": 1,
    "cx": 2, "cy": 2, "cz": 2, "ch": 2, "swap": 2, "iswap": 2, "dcx": 2, "ecr": 2,
    "ccx": 3, "cswap": 3, "fredkin": 3, "toffoli": 3,
}
# Parameterized gates: (number of angle params, number of qubits).
_PARAM_GATES = {
    "rx": (1, 1), "ry": (1, 1), "rz": (1, 1), "p": (1, 1), "u1": (1, 1),
    "u2": (2, 1), "u3": (3, 1), "u": (3, 1),
    "crx": (1, 2), "cry": (1, 2), "crz": (1, 2), "cp": (1, 2),
    "rxx": (1, 2), "ryy": (1, 2), "rzz": (1, 2),
}

# Every code check_source can report: (title, severity, description). The
# linter registry (linters.py) offers these as individually selectable rules.
RULES = {
    "QS000": ("Python syntax error", "error", "The file does not parse as Python."),
    "QS001": ("No circuit", "error",
              "No QuantumCircuit is constructed or loaded anywhere in the file, and no function "
              "takes one."),
    "QS002": ("Qubit index out of range", "error",
              "A literal qubit index falls outside the circuit's declared qubits."),
    "QS003": ("Repeated qubit", "error", "A multi-qubit gate names the same qubit twice."),
    "QS004": ("Classical bit out of range", "error",
              "measure() writes, or qc.clbits[i] reads, a classical bit the circuit does not "
              "declare (QuantumCircuit(n) declares none)."),
    "QS005": ("Deprecated Qiskit API", "warning",
              "A class, function, circuit method or keyword argument the installed Qiskit marks "
              "deprecated (QFT, TwoLocal, mcx(mode=...)): it still runs, but will be removed."),
    "QS006": ("Result discarded", "warning",
              "A call that returns a new circuit (qc.inverse(), qc.copy(), assign_parameters, "
              "transpile, ...) used as a statement on its own: the result is thrown away and "
              "the circuit is unchanged."),
    "QS007": ("Unpacked list in a fixed-size gate", "warning",
              "A gate with a fixed number of qubits given an unpacked list and more qubits, "
              "such as ccx(*controls, target): with another list length it fails, or, for "
              "ccx, silently takes the extra qubit as ctrl_state. mcx(controls, target) takes "
              "any number."),
    "QS008": ("c_if() removed", "error", "c_if() was removed in Qiskit 2.0."),
    "QS009": ("execute() removed", "warning", "execute() was removed from qiskit."),
    "QS010": ("Simulator result never saved", "error",
              "result.get_statevector() (or get_unitary, get_density_matrix) on an AerSimulator "
              "run, but nothing calls save_statevector() (save_unitary, save_density_matrix): "
              "Aer raises 'No statevector for experiment'."),
    "QS011": ("Removed Qiskit API", "error",
              "A method, name or module that older Qiskit had and current Qiskit does not "
              "(u1, cu1, cnot, iden, bind_parameters, Aer, qiskit.extensions, ...)."),
    "QS012": ("Unknown Qiskit name", "error",
              "An import of a name or module the installed Qiskit does not have, such as "
              "CNOT from qiskit.circuit.library (the gate class is CXGate)."),
    "QS013": ("Unknown Qiskit method or argument", "error",
              "A QuantumCircuit method or attribute, or a keyword argument of a circuit method "
              "or an imported Qiskit class, that the installed Qiskit does not have "
              "(qc.mcx(..., control_state=1): the keyword is ctrl_state), item assignment on "
              "an Operator or Statevector, or an attribute of a bit (qc.clbits[0].index)."),
    "QS014": ("Qiskit rejects an operation", "error",
              "Rebuilding the circuit hits an error Qiskit raises when the program runs: a qubit "
              "index out of range, the same qubit twice, the wrong number of arguments."),
    "QS015": ("Classical bit assumed on a given circuit", "warning",
              "A function measures into a classical bit of a circuit it is given, picked by a "
              "literal or from the qubit index, but never checks the circuit has classical "
              "bits or adds them: given QuantumCircuit(n), which declares none, Qiskit raises "
              "CircuitError."),
}
# Quantum-side checks that read the rebuilt circuit (no measurement, idle qubits,
# measurements overwritten, cancelling pairs) are dependence checks, QD01-QD08,
# in linter_plugins/dependence_checks.py. Until 2026-10-29 they were quick checks
# QS005-QS007 and QS010; those codes now name newer quick checks.


class _Finding(dict):
    """Small helper so findings read nicely at the call sites."""

    def __init__(self, code, severity, node, message, hint=""):
        super().__init__(
            code=code,
            severity=severity,
            line=getattr(node, "lineno", 0),
            column=getattr(node, "col_offset", 0) + 1,
            message=message,
            hint=hint,
        )


def _const_int(node: ast.AST) -> Optional[int]:
    """Return the value of an integer literal, or None if it is not one.

    ``-1`` parses as a unary minus applied to a constant, so that is unwrapped too.
    """
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)):
        value = _const_int(node.operand)
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _int_list(node: ast.AST) -> Optional[List[int]]:
    """Return a list/tuple of integer literals, or None."""
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_const_int(elt) for elt in node.elts]
        if all(v is not None for v in values):
            return values  # type: ignore[return-value]
    return None


def _qubit_args(node: ast.AST) -> Optional[List[int]]:
    """Normalize a gate argument into the list of qubit indices it names."""
    single = _const_int(node)
    if single is not None:
        return [single]
    return _int_list(node)


# Calls that read a circuit's state or operator rather than sampling it, so a
# circuit that never measures is deliberate (QD06).
_STATE_READERS = frozenset({
    "Statevector", "DensityMatrix", "Operator", "Clifford", "from_instruction",
    "from_circuit", "evolve", "save_statevector", "save_unitary", "save_density_matrix",
    "Estimator", "EstimatorV2", "StatevectorEstimator", "BackendEstimatorV2",
})
# Result methods that read a simulated state: a file that calls one runs its
# circuit on a statevector or unitary simulator, where measuring is not wanted.
_STATE_RESULTS = frozenset({"get_statevector", "get_unitary", "get_density_matrix"})
# Calls that sample a circuit: without a measurement they return nothing useful.
_SAMPLERS = frozenset({"execute", "run", "Sampler", "SamplerV2", "StatevectorSampler",
                       "BackendSamplerV2"})
# Ways a file gets a circuit without calling QuantumCircuit(...) itself (QS001).
_CIRCUIT_SOURCES = frozenset({"random_circuit", "from_qasm_str", "from_qasm_file"})
_LOADER_MODULES = frozenset({"qasm2", "qasm3", "qpy"})


class _CircuitCollector(ast.NodeVisitor):
    """Finds ``QuantumCircuit(...)`` assignments and the calls made on them, and
    where those circuits go: into functions this file defines, or into calls that
    read their state or sample them."""

    def __init__(self) -> None:
        self.circuits: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Dict[str, Any]] = []
        self.has_circuit_call = False
        self.takes_circuit = False        # a function has a parameter annotated QuantumCircuit
        self.functions: set = set()        # names of functions defined in this file
        self.passed_to: Dict[str, set] = {}   # variable -> names of the calls it is passed to
        self.reads_state = False          # get_statevector() and friends, anywhere
        self.circuit_params: set = set()  # parameters annotated QuantumCircuit
        self.attributes: List[Dict[str, Any]] = []   # name.attr reads, calls included

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.add(node.name)
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        for arg in arguments:
            if arg.annotation is not None and _callee_name(arg.annotation) == "QuantumCircuit":
                self.takes_circuit = True
                self.circuit_params.add(arg.arg)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and isinstance(node.ctx, ast.Load):
            self.attributes.append({"owner": node.value.id, "attr": node.attr, "node": node})
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        call = node.value
        if isinstance(call, ast.Call) and _callee_name(call.func) == "QuantumCircuit":
            self.has_circuit_call = True
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.circuits[target.id] = {
                        "name": target.id,
                        "node": node,
                        "num_qubits": _const_int(call.args[0]) if call.args else None,
                        # QuantumCircuit(3) has no classical bits; registers, or anything
                        # but one literal size, leave it unknown.
                        "num_clbits": (_const_int(call.args[1]) if len(call.args) > 1 else
                                       0 if len(call.args) == 1 and not call.keywords
                                       and _const_int(call.args[0]) is not None else None),
                    }
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee_name(node.func)
        owner = None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            owner = node.func.value.id
            self.calls.append({
                "owner": owner,
                "method": node.func.attr,
                "node": node,
            })
        if (callee == "QuantumCircuit" or owner == "QuantumCircuit" or callee in _CIRCUIT_SOURCES
                or (owner in _LOADER_MODULES and callee in ("load", "loads"))):
            self.has_circuit_call = True
        if callee in _STATE_RESULTS:
            self.reads_state = True
        if callee == "run" and owner and "estimator" in owner.lower():
            callee = "Estimator"            # estimator.run([(qc, obs)]) reads, it does not sample
        arguments = list(node.args) + [keyword.value for keyword in node.keywords]
        while arguments:
            arg = arguments.pop()
            if isinstance(arg, ast.Starred):
                arg = arg.value
            if isinstance(arg, (ast.List, ast.Tuple)):          # sampler.run([qc])
                arguments.extend(arg.elts)
            elif isinstance(arg, ast.Name) and arg.id != owner:
                self.passed_to.setdefault(arg.id, set()).add(callee)
        self.generic_visit(node)

    def escapes(self, name: str) -> bool:
        """The circuit is handed to a function this file defines, which may use it."""
        return bool(self.passed_to.get(name, set()) & self.functions)


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def syntax_error(source: str) -> Optional[Dict[str, Any]]:
    """The QS000 finding for a file Python cannot parse, else None."""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return {
            "code": "QS000",
            "severity": "error",
            "line": exc.lineno or 0,
            "column": exc.offset or 0,
            "message": f"Python could not parse this file: {exc.msg}.",
            "hint": "Fix the syntax error before the circuit can be analyzed.",
        }
    return None


def check_source(source: str) -> List[Dict[str, Any]]:
    """Run every check over ``source`` and return the findings, ordered by line."""
    syntax = syntax_error(source)
    if syntax is not None:
        return [syntax]
    tree = ast.parse(source)

    collector = _CircuitCollector()
    collector.visit(tree)

    findings: List[Dict[str, Any]] = []
    findings += _check_has_circuit(tree, collector)
    findings += _check_indices(collector)
    findings += _check_clbit_subscripts(tree, collector)
    findings += _check_removed_api(tree)
    findings += _check_removed_qiskit(tree, collector)
    findings += _check_unknown_qiskit_names(tree)
    findings += _check_unknown_qiskit_api(tree, collector)
    findings += _check_deprecated_qiskit(tree, collector)
    findings += _check_discarded_results(tree, collector)
    findings += _check_unpacked_fixed_gates(collector)
    findings += _check_unsaved_results(tree)
    findings += _check_assumed_clbits(tree)

    findings.sort(key=lambda f: (f["line"], f["column"], f["code"]))
    return findings


def _check_has_circuit(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """No circuit is made or loaded anywhere, and no function is given one."""
    if collector.has_circuit_call or collector.takes_circuit:
        return []
    root = ast.Module(body=[], type_ignores=[])
    root.lineno = 1  # type: ignore[attr-defined]
    root.col_offset = 0  # type: ignore[attr-defined]
    return [_Finding(
        "QS001", "error", root,
        "No QuantumCircuit is constructed anywhere in this file.",
        "The static parser reads the file and then looks for the QuantumCircuit it builds. "
        "Assign one at module level, for example qc = QuantumCircuit(3, 3).",
    )]


def _check_indices(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Qubit and clbit indices that fall outside the declared registers."""
    findings: List[Dict[str, Any]] = []
    for call in collector.calls:
        circuit = collector.circuits.get(call["owner"])
        if circuit is None:
            continue
        method, node = call["method"], call["node"]

        arity = _GATE_ARITY.get(method)
        param_spec = _PARAM_GATES.get(method)
        if arity is None and param_spec is None and method != "measure":
            continue

        if method == "measure":
            qargs = node.args[:1]
            cargs = node.args[1:2]
        elif param_spec is not None:
            qargs = node.args[param_spec[0]:param_spec[0] + param_spec[1]]
            cargs = []
        else:
            qargs = node.args[:arity]
            cargs = []

        # Qiskit indexes qubits like a Python list: -1 is the last qubit, and
        # anything below -num_qubits is out of range.
        num_qubits = circuit["num_qubits"]
        seen_in_call: List[int] = []
        scalar_args = True
        for arg in qargs:
            indices = _qubit_args(arg)
            if indices is None:
                scalar_args = False
                continue
            if _const_int(arg) is None:
                scalar_args = False        # a list broadcasts; repeats are not a clash
            for index in indices:
                if num_qubits is not None and not -num_qubits <= index < num_qubits:
                    findings.append(_Finding(
                        "QS002", "error", node,
                        f"{method}() uses qubit {index}, but {circuit['name']} only declares "
                        f"{num_qubits} qubit(s) (0-{num_qubits - 1}, or -{num_qubits}--1 "
                        "counting from the end).",
                        "Widen the register or correct the index.",
                    ))
                    scalar_args = False
                elif num_qubits is None and index < 0:
                    scalar_args = False    # cannot tell which qubit it wraps to
                else:
                    seen_in_call.append(index % num_qubits if index < 0 else index)

        gate_qubits = param_spec[1] if param_spec is not None else (arity or 0)
        if scalar_args and gate_qubits > 1 and len(seen_in_call) != len(set(seen_in_call)):
            findings.append(_Finding(
                "QS003", "error", node,
                f"{method}() names the same qubit more than once ({seen_in_call}).",
                "A controlled gate needs distinct control and target wires; Qiskit raises a "
                "CircuitError at build time.",
            ))

        num_clbits = _known_clbits(collector, call["owner"])
        for arg in cargs:
            indices = _qubit_args(arg)
            if indices is None:
                continue
            for index in indices:
                if num_clbits is not None and index >= num_clbits:
                    findings.append(_Finding(
                        "QS004", "error", node,
                        f"measure() writes to classical bit {index}, but {circuit['name']} "
                        + (f"declares {num_clbits}." if num_clbits else
                           "declares no classical bits."),
                        "Add classical bits when constructing the circuit: "
                        f"QuantumCircuit({circuit['num_qubits']}, {index + 1}), or use "
                        "measure_all(), which adds its own.",
                    ))
    return findings


def _known_clbits(collector: _CircuitCollector, name: str) -> Optional[int]:
    """A circuit's literal number of classical bits, unless something may add more."""
    circuit = collector.circuits.get(name)
    if circuit is None or any(call["owner"] == name and call["method"] in
                              ("add_register", "add_bits", "measure_all", "measure_active")
                              for call in collector.calls):
        return None
    return circuit["num_clbits"]


def _check_clbit_subscripts(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """QS004 for qc.clbits[i] past the circuit's classical bits (an IndexError)."""
    findings: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "clbits" and isinstance(node.value.value, ast.Name)):
            continue
        name = node.value.value.id
        num_clbits = _known_clbits(collector, name)
        index = _const_int(node.slice)
        if num_clbits is not None and index is not None and not -num_clbits <= index < num_clbits:
            findings.append(_Finding(
                "QS004", "error", node,
                f"{name}.clbits[{index}] does not exist: {name} "
                + (f"declares {num_clbits} classical bit(s)." if num_clbits else
                   "declares no classical bits."),
                f"Add classical bits when constructing the circuit: QuantumCircuit(n, {index + 1}).",
            ))
    return findings


def measurement_excused(source: str) -> bool:
    """Whether a circuit of ``source`` that never measures is fine as it is (QD06).

    It is when its state is read instead (Statevector, Operator, an Estimator,
    a simulator result's get_statevector() or get_unitary(), ...), or when it
    goes into a function of this file (which may return it to be measured
    elsewhere), unless it is also sampled (execute, run, a Sampler): sampling a
    circuit that measures nothing is the mistake QD06 is for.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    collector = _CircuitCollector()
    collector.visit(tree)
    names = list(collector.circuits)
    if collector.reads_state:
        return True
    sampled = any(collector.passed_to.get(n, set()) & _SAMPLERS for n in names)
    read = any(collector.passed_to.get(n, set()) & _STATE_READERS for n in names)
    return not sampled and (read or any(collector.escapes(n) for n in names))


def circuit_line(source: str) -> Optional[int]:
    """The line of the first QuantumCircuit(...) assignment in ``source``, or None."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    collector = _CircuitCollector()
    collector.visit(tree)
    lines = [c["node"].lineno for c in collector.circuits.values()]
    return min(lines) if lines else None


# QS014: errors the static parser raises where Qiskit would. Other exceptions it
# reports (NameError, TypeError, ...) can come from what it cannot model, so they
# are not findings: on the benchmarks they also appear on fixed programs.
_QISKIT_ERRORS = frozenset({"CircuitError", "QiskitError"})


def rejected_operation(failure: Optional[Exception]) -> List[Dict[str, Any]]:
    """QS014 from the static parser's failure (``LintContext.failure()``), if Qiskit's own.

    The parser raises Qiskit's errors where Qiskit would, at the line inside a
    helper where they happen, so this sees indices computed in loops and
    functions, which QS002/QS003 cannot.
    """
    raised = getattr(failure, "raised", None)
    line = getattr(failure, "lineno", None)
    if raised not in _QISKIT_ERRORS or not line:
        return []
    detail = getattr(failure, "message", str(failure)).split(f"{raised}: ", 1)[-1]
    return [{"code": "QS014", "severity": "error", "line": line, "column": 1,
             "message": f"Qiskit raises {raised} here: {detail}",
             "hint": "The program stops at this line when it runs. Check the qubit and bit "
                     "indices against the circuit's size, and the number of qubits the gate "
                     "takes."}]


class _RemovedApiVisitor(ast.NodeVisitor):
    """Flags calls that Qiskit 1.x deprecated and Qiskit 2.0 removed."""

    def __init__(self) -> None:
        self.findings: List[Dict[str, Any]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node.func)
        if name == "c_if":
            self.findings.append(_Finding(
                "QS008", "error", node,
                "c_if() was removed in Qiskit 2.0.",
                "Use a with qc.if_test((clbit, value)): block instead. The parser reads the "
                "condition metadata from if_test, so classical dependencies only appear in the "
                "graph when you use the modern form.",
            ))
        elif name == "execute":
            self.findings.append(_Finding(
                "QS009", "warning", node,
                "execute() was removed from qiskit; use a backend's run() method.",
                "For example: AerSimulator().run(transpile(qc, sim), shots=1000).",
            ))
        self.generic_visit(node)


def _check_removed_api(tree: ast.AST) -> List[Dict[str, Any]]:
    visitor = _RemovedApiVisitor()
    visitor.visit(tree)
    return visitor.findings


# QS011: Qiskit APIs that no longer exist (checked against Qiskit 2.6.0), with what
# replaces them. c_if and execute keep their own codes, QS008 and QS009.
_REMOVED_METHODS = {
    "u1": "p(theta, qubit)", "u2": "u(pi/2, phi, lam, qubit)", "u3": "u(theta, phi, lam, qubit)",
    "cu1": "cp(theta, control, target)", "cu3": "cu(theta, phi, lam, 0, control, target)",
    "mcu1": "mcp(lam, controls, target)", "cnot": "cx()", "toffoli": "ccx()",
    "fredkin": "cswap()", "mct": "mcx()", "mcmt": "append(MCMTGate(...), qubits)",
    "iden": "id()", "i": "id()", "bind_parameters": "assign_parameters()",
    "squ": "append(UnitaryGate(...), [qubit])", "isometry": "append(Isometry(...), qubits)",
    "iso": "append(Isometry(...), qubits)", "diagonal": "append(DiagonalGate(...), qubits)",
    "snapshot": "AerSimulator's save_statevector()", "qasm": "qiskit.qasm2.dumps(qc)",
    "combine": "compose()", "extend": "compose(..., inplace=True)",
}
# Names common enough elsewhere (list.extend, an index i) that only a call on a
# known QuantumCircuit counts.
_GENERIC_METHODS = {"i", "snapshot", "qasm", "diagonal", "iso", "combine", "extend"}
_REMOVED_NAMES = {
    "Aer": "from qiskit_aer import Aer (or AerSimulator)",
    "BasicAer": "from qiskit.providers.basic_provider import BasicProvider",
    "IBMQ": "QiskitRuntimeService from qiskit_ibm_runtime",
    "compile": "transpile() and then backend.run()",
    "assemble": "transpile() and then backend.run()",
    "schedule": "nothing: Qiskit Pulse was removed in Qiskit 2.0",
}
_REMOVED_MODULES = {
    "qiskit.extensions": "qiskit.circuit.library",
    "qiskit.tools.visualization": "qiskit.visualization",
    "qiskit.tools": "nothing (job_monitor and friends were removed)",
    "qiskit.opflow": "qiskit.quantum_info (SparsePauliOp, Statevector)",
    "qiskit.algorithms": "the separate qiskit_algorithms package",
    "qiskit.providers.aer": "qiskit_aer",
    "qiskit.providers.ibmq": "qiskit_ibm_runtime",
    "qiskit.test": "nothing",
    "qiskit.ignis": "nothing: Ignis was retired",
    "qiskit.aqua": "nothing: Aqua was retired",
}


def _removed_module(name: str) -> Optional[str]:
    for module in sorted(_REMOVED_MODULES, key=len, reverse=True):
        if name == module or name.startswith(module + "."):
            return module
    return None


def _check_removed_qiskit(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Calls, imports and attributes of Qiskit APIs that no longer exist."""
    findings: List[Dict[str, Any]] = []

    def report(node, what, replacement):
        findings.append(_Finding(
            "QS011", "error", node,
            f"{what} no longer exists in Qiskit.",
            f"Use {replacement} instead. Code written for older Qiskit versions fails here.",
        ))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver = node.func.value
            if method in _REMOVED_METHODS and (
                    method not in _GENERIC_METHODS
                    or (isinstance(receiver, ast.Name) and receiver.id in collector.circuits)):
                report(node, f"QuantumCircuit.{method}()", _REMOVED_METHODS[method])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            module = _removed_module(node.module)
            if module:
                report(node, f"The module {module}", _REMOVED_MODULES[module])
            elif node.module == "qiskit":
                for alias in node.names:
                    if alias.name in _REMOVED_NAMES:
                        report(node, f"qiskit.{alias.name}", _REMOVED_NAMES[alias.name])
            elif node.module == "qiskit.utils":
                for alias in node.names:
                    if alias.name == "QuantumInstance":
                        report(node, "qiskit.utils.QuantumInstance",
                               "the primitives (Sampler, Estimator)")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = _removed_module(alias.name)
                if module:
                    report(node, f"The module {module}", _REMOVED_MODULES[module])
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "qiskit" and node.attr in _REMOVED_NAMES):
            report(node, f"qiskit.{node.attr}", _REMOVED_NAMES[node.attr])
    return findings


# QS012: imports checked against the Qiskit actually installed. Only modules of
# the qiskit packages are imported (the library, never the checked file), and a
# package that is not installed is skipped, since then nothing can be said.
_QISKIT_MODULE = re.compile(r"^qiskit(_[a-z0-9_]+)?(\.[A-Za-z_]\w*)*$")
# Invented names that have an obvious real one; otherwise "<Name>Gate" or a
# close match in the module is suggested.
_LIKELY_MEANT = {"CNOT": "CXGate", "CCNOT": "CCXGate", "Toffoli": "CCXGate",
                 "Fredkin": "CSwapGate", "MCT": "MCXGate"}
# Removed names QS011 does not cover as imports, with what replaces them.
_REMOVED_ELSEWHERE = dict(_REMOVED_NAMES, **{
    "execute": "a backend's run(), e.g. AerSimulator().run(transpile(qc, sim))",
    "pulse": "nothing: Qiskit Pulse was removed in Qiskit 2.0",
})
# Where a name imported from the wrong module usually lives.
_HOMES = ("qiskit", "qiskit.circuit.library", "qiskit.quantum_info", "qiskit_aer",
          "qiskit.primitives", "qiskit.visualization")
_MODULE_CACHE: Dict[str, Any] = {}


def _qiskit_module(name: str) -> Any:
    """The installed module ``name``; None if it does not exist; False if that
    cannot be told (its package is not installed, or importing it failed)."""
    if name not in _MODULE_CACHE:
        import importlib
        import importlib.util
        try:
            if importlib.util.find_spec(name.split(".")[0]) is None:
                _MODULE_CACHE[name] = False
            elif importlib.util.find_spec(name) is None:
                _MODULE_CACHE[name] = None
            else:
                _MODULE_CACHE[name] = importlib.import_module(name)
        except ModuleNotFoundError:
            _MODULE_CACHE[name] = None          # a parent package is missing
        except Exception:                        # noqa: BLE001 -- an optional dependency, say
            _MODULE_CACHE[name] = False
    return _MODULE_CACHE[name]


def _has_name(module_name: str, name: str) -> Optional[bool]:
    module = _qiskit_module(module_name)
    if not module:
        return None if module is False else False
    if hasattr(module, name):
        return True
    sub = _qiskit_module(f"{module_name}.{name}")      # `from qiskit import qasm2`
    return None if sub is False else bool(sub)


def _suggest(module_name: str, name: str) -> str:
    import difflib
    module = _qiskit_module(module_name)
    if name in _REMOVED_ELSEWHERE:
        replacement = _REMOVED_ELSEWHERE[name]
        if replacement.startswith("nothing"):
            return f"It was removed from Qiskit, with no replacement ({replacement[9:]})."
        return f"It was removed from Qiskit: use {replacement} instead."
    if name in _LIKELY_MEANT and module and hasattr(module, _LIKELY_MEANT[name]):
        return f"Did you mean {_LIKELY_MEANT[name]}? Or call the circuit method, e.g. qc.cx()."
    if module and hasattr(module, name + "Gate"):
        return (f"Gate classes end in Gate: {name}Gate. Or call the circuit method, "
                f"e.g. qc.{name.lower()}().")
    for home in _HOMES:
        if home != module_name and _has_name(home, name):
            return f"It lives elsewhere: from {home} import {name}."
    close = difflib.get_close_matches(name, [n for n in dir(module) if not n.startswith("_")],
                                      n=1) if module else []
    return f"Did you mean {close[0]}?" if close else "Check the name against the Qiskit docs."


def _check_unknown_qiskit_names(tree: ast.AST) -> List[Dict[str, Any]]:
    """Imports of names or modules the installed Qiskit lacks (QS011's removed
    APIs excepted, which it reports with their replacements)."""
    findings: List[Dict[str, Any]] = []
    try:
        import qiskit
        version = qiskit.__version__
    except Exception:                            # noqa: BLE001 -- no Qiskit: nothing to check
        return findings

    def report(node, message, hint):
        findings.append(_Finding("QS012", "error", node, message, hint))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            module = node.module
            if not _QISKIT_MODULE.match(module) or _removed_module(module):
                continue
            if _qiskit_module(module) is None:
                report(node, f"There is no module {module} in Qiskit {version}.",
                       "Check the module path against the Qiskit docs.")
                continue
            for alias in node.names:
                if alias.name == "*" or (module == "qiskit" and alias.name in _REMOVED_NAMES) \
                        or (module == "qiskit.utils" and alias.name == "QuantumInstance"):
                    continue
                if _has_name(module, alias.name) is False:
                    report(node, f"{module} has no name {alias.name} in Qiskit {version}.",
                           _suggest(module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _QISKIT_MODULE.match(alias.name) and not _removed_module(alias.name) \
                        and _qiskit_module(alias.name) is None:
                    report(node, f"There is no module {alias.name} in Qiskit {version}.",
                           "Check the module path against the Qiskit docs.")
    return findings


def _unknown_keywords(target: Any, call: ast.Call) -> List[tuple]:
    """``(keyword, suggestion)`` for each keyword ``target``'s signature does not take."""
    import difflib
    import inspect
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return []
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return []
    names = [name for name in params if name != "self"]
    bad = []
    for keyword in call.keywords:
        if keyword.arg is not None and keyword.arg not in params:
            close = difflib.get_close_matches(keyword.arg, names, n=1)
            bad.append((keyword.arg, f"Did you mean {close[0]}?" if close else
                        f"It takes: {', '.join(names) or 'no arguments'}."))
    return bad


def _check_unknown_qiskit_api(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """QS013: methods, attributes and keyword arguments the installed Qiskit lacks, on
    known circuits (assigned QuantumCircuit(...), or parameters annotated so) and in
    calls of names imported from Qiskit. Removed methods are QS011's."""
    import difflib
    findings: List[Dict[str, Any]] = []
    try:
        import qiskit
        from qiskit import QuantumCircuit
        version = qiskit.__version__
    except Exception:                            # noqa: BLE001 -- no Qiskit: nothing to check
        return findings
    circuits = set(collector.circuits) | collector.circuit_params
    public = [n for n in dir(QuantumCircuit) if not n.startswith("_")]
    reported = set()
    for use in collector.attributes:
        attr, node = use["attr"], use["node"]
        if use["owner"] not in circuits or attr.startswith("_") or attr in _REMOVED_METHODS \
                or hasattr(QuantumCircuit, attr) or (node.lineno, attr) in reported:
            continue
        reported.add((node.lineno, attr))
        close = difflib.get_close_matches(attr, public, n=1)
        findings.append(_Finding(
            "QS013", "error", node, f"QuantumCircuit has no attribute {attr} in Qiskit {version}.",
            f"Did you mean {close[0]}?" if close else "Check the name against the Qiskit docs."))
    for call in collector.calls:
        if call["owner"] in circuits and hasattr(QuantumCircuit, call["method"]):
            for keyword, hint in _unknown_keywords(getattr(QuantumCircuit, call["method"]),
                                                    call["node"]):
                findings.append(_Finding(
                    "QS013", "error", call["node"],
                    f"QuantumCircuit.{call['method']}() takes no argument {keyword} in "
                    f"Qiskit {version}.", hint))

    imported: Dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level \
                and _QISKIT_MODULE.match(node.module) and not _removed_module(node.module):
            module = _qiskit_module(node.module)
            for alias in node.names:
                if module and alias.name != "*" and hasattr(module, alias.name):
                    imported[alias.asname or alias.name] = getattr(module, alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in imported:
            for keyword, hint in _unknown_keywords(imported[node.func.id], node):
                findings.append(_Finding(
                    "QS013", "error", node,
                    f"{node.func.id}() takes no argument {keyword} in Qiskit {version}.", hint))
    findings += _unsupported_item_assignment(tree, imported, version)
    findings += _unknown_bit_attributes(tree, circuits, version)
    return findings


def _unsupported_item_assignment(tree: ast.AST, imported: Dict[str, Any],
                                 version: str) -> List[Dict[str, Any]]:
    """QS013 for op[i, j] = v on an object of an imported Qiskit class that does not support
    item assignment (Operator, Statevector, DensityMatrix): a TypeError."""
    findings: List[Dict[str, Any]] = []
    instances: Dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id in imported \
                and isinstance(imported[node.value.func.id], type):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    instances[target.id] = imported[node.value.func.id]
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AugAssign) else [])
        for target in targets:
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                    and target.value.id in instances \
                    and not hasattr(instances[target.value.id], "__setitem__"):
                cls = instances[target.value.id].__name__
                findings.append(_Finding(
                    "QS013", "error", node,
                    f"{cls} objects do not support item assignment in Qiskit {version}.",
                    f"Build the matrix or vector first (with numpy), then make the {cls} from "
                    f"it; {target.value.id}.data is a read-only view."))
    return findings


def _unknown_bit_attributes(tree: ast.AST, circuits: set, version: str) -> List[Dict[str, Any]]:
    """QS013 for qc.qubits[i].index and the like: Qiskit 2 bits have no public attributes."""
    from qiskit.circuit import Clbit, Qubit
    findings: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Subscript)
                and isinstance(node.value.value, ast.Attribute)
                and node.value.value.attr in ("qubits", "clbits")
                and isinstance(node.value.value.value, ast.Name)
                and node.value.value.value.id in circuits and not node.attr.startswith("_")):
            continue
        kind = Qubit if node.value.value.attr == "qubits" else Clbit
        if not hasattr(kind, node.attr):
            owner = node.value.value.value.id
            findings.append(_Finding(
                "QS013", "error", node,
                f"{kind.__name__} has no attribute {node.attr} in Qiskit {version}.",
                f"Ask the circuit instead: {owner}.find_bit(bit).index gives a bit's index."))
    return findings


# QS005: deprecations, read from the ".. deprecated::" notes Qiskit's deprecation
# decorators add to docstrings, so the list follows the installed version.
_DEPRECATED = re.compile(r"\.\. deprecated::\s*(\S+)\s*\n((?:[ \t]+\S.*\n?)+)")
_DEPRECATION_CACHE: Dict[int, List[Dict[str, Any]]] = {}


def _deprecations(obj: Any) -> List[Dict[str, Any]]:
    """``[{since, argument, text, hint}]`` for an object (argument is None when the
    whole object is deprecated rather than one of its keyword arguments)."""
    if id(obj) not in _DEPRECATION_CACHE:
        docs = [getattr(obj, "__doc__", None)]
        if isinstance(obj, type):
            docs.append(getattr(obj.__init__, "__doc__", None))
        found = []
        for doc in filter(None, docs):
            for since, body in _DEPRECATED.findall(doc):
                text = " ".join(body.split())
                argument = re.search(r"argument ``(\w+)`` is deprecated", text)
                advice = re.search(r"(?:\('|\b)((?:Use|Instead)\b.*?)(?:',\))?$", text)
                removal = re.search(r"It will be removed[^.]*\.(?:\d+\.)*", text)
                found.append({"since": since, "argument": argument.group(1) if argument else None,
                              "text": text,
                              "hint": advice.group(1) if advice else
                              (removal.group(0) + " " if removal else "")
                              + "The Qiskit release notes name its replacement."})
        _DEPRECATION_CACHE[id(obj)] = found
    return _DEPRECATION_CACHE[id(obj)]


def _check_deprecated_qiskit(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """QS005: imports of deprecated Qiskit names, deprecated QuantumCircuit members on
    known circuits, and deprecated keyword arguments of either."""
    findings: List[Dict[str, Any]] = []
    try:
        import qiskit
        from qiskit import QuantumCircuit
        version = qiskit.__version__
    except Exception:                            # noqa: BLE001 -- no Qiskit: nothing to check
        return findings

    def report(node, what, deprecation):
        findings.append(_Finding(
            "QS005", "warning", node,
            f"{what} is deprecated as of Qiskit {deprecation['since']} (installed: {version}).",
            deprecation["hint"]))

    def whole(obj):
        return next((d for d in _deprecations(obj) if d["argument"] is None), None)

    def keywords(obj, call, label):
        for d in _deprecations(obj):
            if d["argument"] and any(k.arg == d["argument"] for k in call.keywords):
                report(call, f"The argument {d['argument']} of {label}", d)

    imported: Dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level \
                and _QISKIT_MODULE.match(node.module) and not _removed_module(node.module):
            module = _qiskit_module(node.module)
            for alias in node.names:
                obj = getattr(module, alias.name, None) if module and alias.name != "*" else None
                if obj is None:
                    continue
                imported[alias.asname or alias.name] = obj
                deprecation = whole(obj)
                if deprecation:
                    report(node, f"{node.module}.{alias.name}", deprecation)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in imported:
            keywords(imported[node.func.id], node, f"{node.func.id}()")

    circuits = set(collector.circuits) | collector.circuit_params
    reported = set()
    for use in collector.attributes:
        member = getattr(QuantumCircuit, use["attr"], None)
        if use["owner"] not in circuits or member is None or use["attr"].startswith("_"):
            continue
        deprecation = whole(member)
        if deprecation and (use["node"].lineno, use["attr"]) not in reported:
            reported.add((use["node"].lineno, use["attr"]))
            report(use["node"], f"QuantumCircuit.{use['attr']}", deprecation)
    for call in collector.calls:
        member = getattr(QuantumCircuit, call["method"], None)
        if call["owner"] in circuits and member is not None:
            keywords(member, call["node"], f"QuantumCircuit.{call['method']}()")
    return findings


# QS006: QuantumCircuit methods that return a new circuit and leave the original as it
# is, with the inplace default that makes them do so (None: they have no inplace).
_RETURNS_NEW = {
    "inverse": None, "copy": None, "copy_empty_like": None, "decompose": None,
    "reverse_bits": None, "reverse_ops": None, "power": None, "repeat": None, "control": None,
    "to_gate": None, "to_instruction": None, "assign_parameters": False, "compose": False,
    "tensor": False, "measure_all": True, "measure_active": True,
    "remove_final_measurements": True,
}


def _check_discarded_results(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """QS006: a statement that is nothing but a call returning a new circuit, on a known
    circuit (or transpile()). Calls on what a gate returns (qc.s(0).inverse()) act in
    place and are not judged."""
    findings: List[Dict[str, Any]] = []
    circuits = set(collector.circuits) | collector.circuit_params
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        func = call.func
        if (isinstance(func, ast.Name) and func.id == "transpile") or \
                (isinstance(func, ast.Attribute) and func.attr == "transpile"):
            findings.append(_Finding(
                "QS006", "warning", node,
                "transpile() returns a new circuit, which is discarded here; the circuit "
                "itself is unchanged.",
                "Keep the result: tqc = transpile(qc, ...), and run or print tqc."))
            continue
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in circuits and func.attr in _RETURNS_NEW):
            continue
        inplace_default = _RETURNS_NEW[func.attr]
        inplace = next((k.value for k in call.keywords if k.arg == "inplace"), None)
        if inplace_default is not None:
            on = inplace.value if isinstance(inplace, ast.Constant) else (
                inplace_default if inplace is None else None)
            if on is not False:                  # in place, or cannot tell
                continue
        owner = func.value.id
        findings.append(_Finding(
            "QS006", "warning", node,
            f"{owner}.{func.attr}() returns a new circuit, which is discarded here; {owner} "
            "itself is unchanged.",
            (f"Pass inplace=True, or keep the result: {owner} = {owner}.{func.attr}(...)."
             if inplace_default is not None else
             f"Keep the result: {owner} = {owner}.{func.attr}(...), or name the new circuit.")))
    return findings


# QS007: gates with a fixed number of qubits (two or more).
_MULTI_QUBIT_GATES = frozenset({name for name, n in _GATE_ARITY.items() if n > 1}
                               | {name for name, (_, n) in _PARAM_GATES.items() if n > 1})


def _check_unpacked_fixed_gates(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """QS007: qc.ccx(*controls, target) and the like on a known circuit. A bare
    qc.cx(*pair) is left alone: it is common, and fails loudly if the pair is not two."""
    findings: List[Dict[str, Any]] = []
    circuits = set(collector.circuits) | collector.circuit_params
    for call in collector.calls:
        node = call["node"]
        if (call["owner"] in circuits and call["method"] in _MULTI_QUBIT_GATES
                and len(node.args) > 1 and any(isinstance(a, ast.Starred) for a in node.args)):
            findings.append(_Finding(
                "QS007", "warning", node,
                f"{call['method']}() acts on a fixed number of qubits, but takes an unpacked "
                "list here, so how many it gets depends on the list's length.",
                f"If the list can have another length, {call['method']}() fails"
                + (" or silently reads the extra qubit as ctrl_state" if call["method"] == "ccx"
                   else "") + ". mcx(controls, target) takes any number of controls."))
    return findings


# QS010: what each result getter needs saved, and backends that save it themselves.
_SAVED_BY = {"get_statevector": "save_statevector", "get_unitary": "save_unitary",
             "get_density_matrix": "save_density_matrix"}
_SAVES_ITSELF = ("statevector_simulator", "unitary_simulator", "StatevectorSimulator",
                 "UnitarySimulator")


def _check_unsaved_results(tree: ast.AST) -> List[Dict[str, Any]]:
    """QS010: a result getter with no matching save_* anywhere in the file. Old-style
    backends ('statevector_simulator', StatevectorSimulator) save on their own."""
    names, strings, getters = set(), [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _callee_name(node.func)
            names.add(name)
            if name in _SAVED_BY:
                getters.append((name, node))
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    if any(auto in names or any(auto in text for text in strings) for auto in _SAVES_ITSELF):
        return []
    return [_Finding(
        "QS010", "error", node,
        f"{name}() needs the circuit to save that data, but nothing calls "
        f"{_SAVED_BY[name]}(): the simulator raises 'No {name[4:].replace('_', ' ')} for "
        "experiment'.",
        f"Call qc.{_SAVED_BY[name]}() before running (or use quantum_info's "
        "Statevector(qc) without a simulator).")
        for name, node in getters if _SAVED_BY[name] not in names]


# QS015: anything in a function that checks for, adds or names classical bits.
_CLBIT_GUARDS = ("add_register", "add_bits", "num_clbits", "clbits", "cregs", "measure_all",
                 "ClassicalRegister", "Clbit")


def _check_assumed_clbits(tree: ast.AST) -> List[Dict[str, Any]]:
    """QS015: measure() into a given circuit's classical bit chosen by a literal or the
    qubit index, in a function that never checks or adds classical bits. Functions the
    file calls are left to the rebuilt circuit (QS014), and a bit the caller passes in
    (measure(qubit, clbit)) is the caller's to get right."""
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    findings: List[Dict[str, Any]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in called:
            continue
        if any(guard in ast.unparse(fn) for guard in _CLBIT_GUARDS):
            continue
        params = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        stored = {node.id for node in ast.walk(fn)
                  if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
        loop_vars = {node.id for loop in ast.walk(fn) if isinstance(loop, (ast.For, ast.comprehension))
                     for node in ast.walk(loop.target) if isinstance(node, ast.Name)}
        circuits = {a.arg for a in params if a.annotation is not None
                    and "QuantumCircuit" in ast.unparse(a.annotation)} - stored
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "measure" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in circuits and len(node.args) == 2):
                continue
            qubit, clbit = node.args
            qubit_names = {n.id for n in ast.walk(qubit) if isinstance(n, ast.Name)}
            clbit_names = {n.id for n in ast.walk(clbit) if isinstance(n, ast.Name)}
            if clbit_names - qubit_names - loop_vars - {"range", "len"}:
                continue                       # the bit comes from somewhere else
            findings.append(_Finding(
                "QS015", "warning", node,
                f"measure() writes classical bit {ast.unparse(clbit)} of {node.func.value.id}, "
                "a circuit this function is given, but nothing here checks it has classical "
                "bits: given QuantumCircuit(n), which declares none, Qiskit raises CircuitError.",
                "Add them when missing (if qc.num_clbits == 0: "
                "qc.add_register(ClassicalRegister(qc.num_qubits))), or take the classical bit "
                "as an argument."))
    return findings
