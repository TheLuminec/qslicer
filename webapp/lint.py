"""Heuristic static checks for Qiskit source, run before the program is executed.

This is a stand-in for LintQ: it works on the Python AST only, so it never runs
the submitted file and it only sees what is written literally in the source.
(QS012 also imports the installed Qiskit modules a file imports from, to see
whether the names exist; the file itself is still never run.)
Anything computed in a loop, passed through a helper, or built from a variable
is invisible to it -- those cases are left to the real analysis pass.

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
_MEASURE_METHODS = {"measure", "measure_all", "measure_active"}

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
              "measure() writes a classical bit the circuit does not declare."),
    "QS005": ("No measurement", "warning",
              "The circuit never measures anything, and its state is not read another way "
              "(Statevector, Operator, an Estimator)."),
    "QS006": ("Idle qubit", "info",
              "A declared qubit no literal gate call touches (circuits handed to a function "
              "of the file are not judged)."),
    "QS007": ("Classical bit written twice", "warning",
              "Two measurements write the same classical bit; the first result is lost."),
    "QS008": ("c_if() removed", "error", "c_if() was removed in Qiskit 2.0."),
    "QS009": ("execute() removed", "warning", "execute() was removed from qiskit."),
    "QS010": ("Cancelling pair", "info",
              "Two identical self-inverse gates in a row on the same wires cancel."),
    "QS011": ("Removed Qiskit API", "error",
              "A method, name or module that older Qiskit had and current Qiskit does not "
              "(u1, cu1, cnot, iden, bind_parameters, Aer, qiskit.extensions, ...)."),
    "QS012": ("Unknown Qiskit name", "error",
              "An import of a name or module the installed Qiskit does not have, such as "
              "CNOT from qiskit.circuit.library (the gate class is CXGate)."),
    "QS013": ("Unknown Qiskit method or argument", "error",
              "A QuantumCircuit method or attribute, or a keyword argument of a circuit method "
              "or an imported Qiskit class, that the installed Qiskit does not have "
              "(qc.mcx(..., control_state=1): the keyword is ctrl_state)."),
}


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
# circuit that never measures is deliberate (QS005).
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
                        "num_clbits": _const_int(call.args[1]) if len(call.args) > 1 else None,
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
    findings += _check_measurement(tree, collector)
    findings += _check_idle_qubits(collector)
    findings += _check_duplicate_clbits(collector)
    findings += _check_removed_api(tree)
    findings += _check_removed_qiskit(tree, collector)
    findings += _check_unknown_qiskit_names(tree)
    findings += _check_unknown_qiskit_api(tree, collector)
    findings += _check_self_inverse_pairs(collector)

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

        for arg in cargs:
            indices = _qubit_args(arg)
            if indices is None:
                continue
            for index in indices:
                if circuit["num_clbits"] is not None and index >= circuit["num_clbits"]:
                    findings.append(_Finding(
                        "QS004", "error", node,
                        f"measure() writes to classical bit {index}, but {circuit['name']} "
                        f"declares {circuit['num_clbits']}.",
                        "Add classical bits when constructing the circuit: "
                        f"QuantumCircuit({circuit['num_qubits']}, {index + 1}).",
                    ))
    return findings


def _check_measurement(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """A circuit with no measurement produces no slice criterion worth the name.

    Not reported when the circuit's state is read instead (Statevector,
    Operator, an Estimator, a simulator result's get_statevector() or
    get_unitary(), ...), or when the circuit goes into a function
    this file defines (which may measure it, or return it to be measured),
    unless it is also sampled (execute, run, a Sampler): sampling a circuit
    that measures nothing is the mistake this rule is for.
    """
    if not collector.circuits:
        return []
    measured = any(call["method"] in _MEASURE_METHODS for call in collector.calls)
    if measured:
        return []
    names = list(collector.circuits)
    if collector.reads_state:
        return []
    sampled = any(collector.passed_to.get(n, set()) & _SAMPLERS for n in names)
    read = any(collector.passed_to.get(n, set()) & _STATE_READERS for n in names)
    if not sampled and (read or any(collector.escapes(n) for n in names)):
        return []
    first = next(iter(collector.circuits.values()))
    return [_Finding(
        "QS005", "warning", first["node"],
        "This circuit never measures anything.",
        "Slicing still works on the gate graph, but a backward slice from a measurement is "
        "usually the question you actually want to ask.",
    )]


def _check_idle_qubits(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Declared qubits that no literal gate call ever touches (a circuit handed to
    a function this file defines may be used there, so it is not judged)."""
    findings: List[Dict[str, Any]] = []
    for name, circuit in collector.circuits.items():
        declared = circuit["num_qubits"]
        if not declared or collector.escapes(name):
            continue
        touched = set()
        opaque = False
        for call in collector.calls:
            if call["owner"] != name:
                continue
            if call["method"] in ("compose", "append", "from_gate", "to_gate", "measure_all"):
                opaque = True
            args = call["node"].args
            for arg in args:
                indices = _qubit_args(arg)
                if indices is None:
                    if not isinstance(arg, ast.Constant):
                        opaque = True
                    continue
                touched.update(indices)
        if opaque:
            continue
        idle = sorted(set(range(declared)) - touched)
        if idle:
            findings.append(_Finding(
                "QS006", "info", circuit["node"],
                f"Qubit(s) {idle} are declared but never used.",
                "They will show up in the graph as isolated wires. Shrink the register if they "
                "are left over from an earlier draft.",
            ))
    return findings


def _check_duplicate_clbits(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Two measurements writing to the same classical bit: the first result is lost."""
    findings: List[Dict[str, Any]] = []
    seen: Dict[int, ast.AST] = {}
    for call in collector.calls:
        if call["method"] != "measure" or len(call["node"].args) < 2:
            continue
        indices = _qubit_args(call["node"].args[1])
        if indices is None:
            continue
        for index in indices:
            if index in seen:
                finding = _Finding(
                    "QS007", "warning", call["node"],
                    f"Classical bit {index} is written by more than one measurement "
                    f"(first at line {getattr(seen[index], 'lineno', '?')}).",
                    "The later measurement overwrites the earlier outcome. That is legal, and "
                    "sometimes deliberate, but it also hides a dependency.",
                )
                finding["lines"] = [seen[index].lineno, call["node"].lineno]
                findings.append(finding)
            seen[index] = call["node"]
    return findings


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
    return findings


def _check_self_inverse_pairs(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Adjacent identical self-inverse gates on the same wire cancel out."""
    from gates import SELF_INVERSE

    findings: List[Dict[str, Any]] = []
    previous: Optional[Dict[str, Any]] = None
    for call in collector.calls:
        if call["owner"] not in collector.circuits:
            continue
        method = call["method"]
        if method not in SELF_INVERSE:
            previous = None
            continue
        args = [_qubit_args(arg) for arg in call["node"].args]
        signature = (method, tuple(tuple(a) for a in args if a is not None))
        if previous is not None and previous["signature"] == signature and signature[1]:
            finding = _Finding(
                "QS010", "info", call["node"],
                f"{method}() repeats the identical call from line "
                f"{previous['node'].lineno}; the pair cancels.",
                "The entanglement analyzer reads this as an uncomputation. If that is what you "
                "meant, fine -- if not, one of the two is probably a typo.",
            )
            finding["lines"] = [previous["node"].lineno, call["node"].lineno]   # the whole pair
            findings.append(finding)
        previous = {"signature": signature, "node": call["node"]}
    return findings
