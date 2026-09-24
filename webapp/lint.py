"""Heuristic static checks for Qiskit source, run before the program is executed.

This is a stand-in for LintQ: it works on the Python AST only, so it never runs
the submitted file and it only sees what is written literally in the source.
Anything computed in a loop, passed through a helper, or built from a variable
is invisible to it -- those cases are left to the real analysis pass.

Each finding is a dict with: code, severity (error/warning/info), line, column,
message and hint.
"""

import ast
from typing import Any, Dict, List, Optional

# Gate methods that take qubit indices as positional arguments.
_GATE_ARITY = {
    "h": 1, "x": 1, "y": 1, "z": 1, "s": 1, "sdg": 1, "t": 1, "tdg": 1,
    "sx": 1, "sxdg": 1, "id": 1,
    "cx": 2, "cy": 2, "cz": 2, "ch": 2, "swap": 2, "iswap": 2, "dcx": 2, "ecr": 2,
    "ccx": 3, "cswap": 3, "fredkin": 3, "toffoli": 3,
}
# Parameterised gates: (number of angle params, number of qubits).
_PARAM_GATES = {
    "rx": (1, 1), "ry": (1, 1), "rz": (1, 1), "p": (1, 1), "u1": (1, 1),
    "u2": (2, 1), "u3": (3, 1), "u": (3, 1),
    "crx": (1, 2), "cry": (1, 2), "crz": (1, 2), "cp": (1, 2),
    "rxx": (1, 2), "ryy": (1, 2), "rzz": (1, 2),
}
_MEASURE_METHODS = {"measure", "measure_all", "measure_active"}


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
    """Return the value of an integer literal, or None if it is not one."""
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
    """Normalise a gate argument into the list of qubit indices it names."""
    single = _const_int(node)
    if single is not None:
        return [single]
    return _int_list(node)


class _CircuitCollector(ast.NodeVisitor):
    """Finds ``QuantumCircuit(...)`` assignments and the calls made on them."""

    def __init__(self) -> None:
        self.circuits: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Dict[str, Any]] = []
        self.has_circuit_call = False

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
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            self.calls.append({
                "owner": node.func.value.id,
                "method": node.func.attr,
                "node": node,
            })
        if _callee_name(node.func) == "QuantumCircuit":
            self.has_circuit_call = True
        self.generic_visit(node)


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def check_source(source: str) -> List[Dict[str, Any]]:
    """Run every check over ``source`` and return the findings, ordered by line."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [{
            "code": "QS000",
            "severity": "error",
            "line": exc.lineno or 0,
            "column": exc.offset or 0,
            "message": f"Python could not parse this file: {exc.msg}.",
            "hint": "Fix the syntax error before the circuit can be analysed.",
        }]

    collector = _CircuitCollector()
    collector.visit(tree)

    findings: List[Dict[str, Any]] = []
    findings += _check_has_circuit(tree, collector)
    findings += _check_indices(collector)
    findings += _check_measurement(tree, collector)
    findings += _check_idle_qubits(collector)
    findings += _check_duplicate_clbits(collector)
    findings += _check_removed_api(tree)
    findings += _check_self_inverse_pairs(collector)

    findings.sort(key=lambda f: (f["line"], f["column"], f["code"]))
    return findings


def _check_has_circuit(tree: ast.AST, collector: _CircuitCollector) -> List[Dict[str, Any]]:
    if collector.has_circuit_call:
        return []
    root = ast.Module(body=[], type_ignores=[])
    root.lineno = 1  # type: ignore[attr-defined]
    root.col_offset = 0  # type: ignore[attr-defined]
    return [_Finding(
        "QS001", "error", root,
        "No QuantumCircuit is constructed anywhere in this file.",
        "The parser executes the file and then looks for a QuantumCircuit in the resulting "
        "namespace. Assign one at module level, for example qc = QuantumCircuit(3, 3).",
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

        seen_in_call: List[int] = []
        for arg in qargs:
            indices = _qubit_args(arg)
            if indices is None:
                continue
            for index in indices:
                if index < 0:
                    findings.append(_Finding(
                        "QS002", "error", node,
                        f"Negative qubit index {index} passed to {method}().",
                        "Qubit indices start at 0.",
                    ))
                elif circuit["num_qubits"] is not None and index >= circuit["num_qubits"]:
                    findings.append(_Finding(
                        "QS002", "error", node,
                        f"{method}() uses qubit {index}, but {circuit['name']} only declares "
                        f"{circuit['num_qubits']} qubit(s) (0-{circuit['num_qubits'] - 1}).",
                        "Widen the register or correct the index.",
                    ))
                seen_in_call.append(index)

        if len(seen_in_call) != len(set(seen_in_call)) and method in _GATE_ARITY and _GATE_ARITY[method] > 1:
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
    """A circuit with no measurement produces no slice criterion worth the name."""
    if not collector.circuits:
        return []
    measured = any(call["method"] in _MEASURE_METHODS for call in collector.calls)
    if measured:
        return []
    first = next(iter(collector.circuits.values()))
    return [_Finding(
        "QS005", "warning", first["node"],
        "This circuit never measures anything.",
        "Slicing still works on the gate graph, but a backward slice from a measurement is "
        "usually the question you actually want to ask.",
    )]


def _check_idle_qubits(collector: _CircuitCollector) -> List[Dict[str, Any]]:
    """Declared qubits that no literal gate call ever touches."""
    findings: List[Dict[str, Any]] = []
    for name, circuit in collector.circuits.items():
        declared = circuit["num_qubits"]
        if not declared:
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
                findings.append(_Finding(
                    "QS007", "warning", call["node"],
                    f"Classical bit {index} is written by more than one measurement "
                    f"(first at line {getattr(seen[index], 'lineno', '?')}).",
                    "The later measurement overwrites the earlier outcome. That is legal, and "
                    "sometimes deliberate, but it also hides a dependency.",
                ))
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
            findings.append(_Finding(
                "QS010", "info", call["node"],
                f"{method}() repeats the identical call from line "
                f"{previous['node'].lineno}; the pair cancels.",
                "The entanglement analyser reads this as an uncomputation. If that is what you "
                "meant, fine -- if not, one of the two is probably a typo.",
            ))
        previous = {"signature": signature, "node": call["node"]}
    return findings
