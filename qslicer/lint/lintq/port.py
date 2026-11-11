"""LintQ, reimplemented on the Python syntax tree.

LintQ (Paltenghi and Pradel, "Analyzing Quantum Programs with LintQ", FSE
2024; https://github.com/sola-st/LintQ, MIT licensed) writes its analyses as
CodeQL queries over a model of Qiskit programs: circuits, registers, gates and
the qubits they touch, found through CodeQL's API graphs, local data flow and
control-flow graphs. This module rebuilds that model in Python and runs the
ten queries of LintQ's ``LintQ-all.qls`` suite over it, so LintQ's warnings
are available without CodeQL. It does not execute or interpret the program.

The aim is LintQ's behaviour, not a better linter: where LintQ cannot resolve
something, neither does this. In particular, as in LintQ:

* Qiskit names resolve only through explicit imports (``from qiskit import
  QuantumCircuit``, ``import qiskit``); ``from qiskit import *`` resolves
  nothing.
* Values travel by local data flow only: within one function or the module
  body, through plain assignments. A circuit passed into a helper is a
  different matter in the helper.
* A qubit index is known when it is an integer literal (directly or through a
  local variable), a whole register, ``range`` of literals, a literal
  arithmetic expression, or a list of literals. ``qr[0]`` is not: LintQ
  resolves the register of a subscript through CodeQL's points-to analysis,
  which does not see into Qiskit, so in practice the index stays unknown.
* "May follow" is reachability on the control-flow graph, so a gate earlier in
  a loop body follows a measurement later in it.

``tests/lintq/`` checks the port against warnings the original produced on
LintQ's own test programs, the Bugs4Q programs and this repository's
examples; ``qslicer/lint/plugins/lintq.py`` offers it as a linter. Each query
function below names the ``.ql`` file it ports.

This module is a port of LintQ, Copyright (c) 2024 Matteo Paltenghi, Michael
Pradel, used under the MIT License; the full text is in ``LICENSE`` beside
this file and ships with the package.
"""

import ast
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# --------------------------------------------------------------------------
# LintQ's gate catalogue (qiskit/BitUse.qll)
# --------------------------------------------------------------------------

# OperatorSpecificationAttributeName: circuit methods LintQ treats as operators.
ATTRIBUTE_NAMES = frozenset({
    "x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry", "rz", "rv", "u1", "u2", "u3", "id",
    "u", "iden", "i", "sx", "p", "cx", "cnot", "cy", "cz", "ch", "cs", "csdg", "csx", "crz", "cry",
    "crx", "cu1", "cu3", "cu", "ccx", "ccz", "toffoli", "cswap", "fredkin", "mct", "rccx",
    "rcccx", "cp", "rxx", "ryy", "rzz", "rzx", "swap", "iswap", "ms", "cr", "r", "ecr",
    "measure", "measure_all", "reset", "unitary", "initialize",
})
# OperatorSpecificationObjectName: gate classes LintQ follows into append().
OBJECT_NAMES = frozenset({
    "XGate", "YGate", "ZGate", "HGate", "SGate", "SdgGate", "TGate", "TdgGate", "RXGate",
    "RYGate", "RZGate", "RVGate", "U1Gate", "U2Gate", "U3Gate", "IGate", "SXGate", "PhaseGate",
    "UGate", "CXGate", "CYGate", "CZGate", "CHGate", "CSGate", "CSdgGate", "CSXGate", "CRZGate",
    "CRYGate", "CRXGate", "CU1Gate", "CU3Gate", "CCXGate", "CCZGate", "CSwapGate", "MCXGate",
    "RCCXGate", "RC3XGate", "CPhaseGate", "RXXGate", "RYYGate", "RZZGate", "RZXGate",
    "SwapGate", "iSwapGate", "MSGate", "CRGate", "RGate", "ECRGate", "Measure", "Reset",
    "UnitaryGate", "Initialize",
})

_ONE, _CT, _PAIR = ("qubit",), ("control_qubit", "target_qubit"), ("qubit1", "qubit2")
_CCT = ("control_qubit1", "control_qubit2", "target_qubit")
_CTT = ("control_qubit", "target_qubit1", "target_qubit2")

# name -> (unitary, parameter names, qubit argument names): LintQ's
# OperatorSpecification subclasses. A name in ATTRIBUTE_NAMES or OBJECT_NAMES
# without an entry here (mct, ecr, MCXGate, ...) is an operator whose qubits
# LintQ never resolves.
_SPEC_GROUPS = [
    (False, (), _ONE, ["reset", "Reset", "measure", "Measure"]),
    (False, (), (), ["measure_all"]),
    (False, ("params",), ("qubits",), ["initialize", "Initialize"]),
    (True, ("data",), (), ["UnitaryGate"]),
    (True, ("obj",), ("qubits",), ["unitary"]),
    (True, (), _ONE, ["h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "i", "id", "iden",
                      "HGate", "XGate", "YGate", "ZGate", "SGate", "SdgGate", "TGate",
                      "TdgGate", "SXGate", "IGate"]),
    (True, ("theta",), _ONE, ["p", "PhaseGate", "rx", "RXGate", "ry", "RYGate", "u1", "U1Gate"]),
    (True, ("phi",), _ONE, ["rz", "RZGate"]),
    (True, ("vx", "vy", "vz"), _ONE, ["rv", "RVGate"]),
    (True, ("phi", "lam"), _ONE, ["u2", "U2Gate"]),
    (True, ("theta", "phi", "lam"), _ONE, ["u3", "U3Gate", "u", "UGate"]),
    (True, ("theta",), _CT, ["cp", "CPhaseGate", "crz", "CRZGate", "cry", "CRYGate", "crx",
                             "CRXGate", "cu1", "CU1Gate"]),
    (True, (), _CT, ["cx", "CXGate", "cnot", "cy", "CYGate", "cz", "CZGate", "ch", "CHGate",
                     "cs", "CSGate", "csdg", "CSdgGate", "csx", "CSXGate"]),
    (True, (), _PAIR, ["swap", "SwapGate", "iswap", "iSwapGate"]),
    (True, ("theta", "phi", "lam"), _CT, ["cu3", "CU3Gate"]),
    (True, ("theta", "phi", "lam", "gamma"), _CT, ["cu"]),
    (True, (), _CTT, ["cswap", "CSwapGate", "fredkin"]),
    (True, (), _CCT, ["ccx", "CCXGate", "toffoli", "ccz", "CCZGate"]),
    (True, ("theta",), _PAIR, ["rxx", "RXXGate", "ryy", "RYYGate", "rzz", "RZZGate", "rzx",
                               "RZXGate"]),
]
SPECS: Dict[str, Tuple[bool, Tuple[str, ...], Tuple[str, ...]]] = {
    name: (unitary, params, qubits)
    for unitary, params, qubits, names in _SPEC_GROUPS for name in names
    if name in ATTRIBUTE_NAMES or name in OBJECT_NAMES
}

_GATE_MODULES = ("qiskit.circuit.library", "qiskit.extensions", "qiskit.circuit",
                 "qiskit.circuit.library.standard_gates")
_PARAMETRIZED_LIBRARY = ("TwoLocal", "NLocal", "RealAmplitudes", "EfficientSU2",
                         "ExcitationPreserving", "PauliTwoDesign", "QAOAAnsatz")
_EXTENDER_EXCLUDED = {"isinstance", "type", "transpile", "assemble", "execute", "append",
                      "compose", "copy"}

# The ten queries of LintQ-all.qls: code -> (LintQ id, precision, title).
RULES = {
    "LQ01": ("ql-double-measurement", "high", "Double measurement"),
    "LQ02": ("ql-operation-after-measurement", "high", "Operation after measurement"),
    "LQ03": ("ql-measure-all-abuse", "high", "Measure-all abuse"),
    "LQ04": ("ql-conditional-without-measurement", "high", "Conditional gate without measurement"),
    "LQ05": ("ql-ghost-composition", "high", "Ghost composition"),
    "LQ06": ("ql-op-after-optimization", "high", "Operation after optimization"),
    "LQ07": ("ql-constant-classic-bit", "medium", "Constant classical bit"),
    "LQ08": ("ql-oversized-circuit", "medium", "Oversized circuit"),
    "LQ09": ("ql-unmeasurable-qubits", "medium", "Unmeasurable qubits"),
    "LQ10": ("ql-deprecated-identity", "medium", "Deprecated identity"),
}
CODE_OF = {lintq_id: code for code, (lintq_id, _, _) in RULES.items()}

# Not a LintQ query: the note check_source gives instead of analysing a huge program.
TOO_LARGE = "LQ00"
MAX_OPERATORS = 3000


# --------------------------------------------------------------------------
# Scopes and the control-flow graph
# --------------------------------------------------------------------------

_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ClassDef) + _COMPREHENSIONS


class _Def:
    """A definition of a local variable: an assignment, a loop target, a parameter..."""

    __slots__ = ("name", "value", "node", "stmt")

    def __init__(self, name, value, node, stmt):
        self.name = name
        self.value = value      # the assigned expression for ``name = value``, else None
        self.node = node        # the AST node that defines it, a local source itself
        self.stmt = stmt        # CFG node id


class _Scope:
    """One function, class body, comprehension or the module: its own CFG and variables."""

    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        self.succ: Dict[int, Set[int]] = defaultdict(set)
        self.stmt_of: Dict[int, Any] = {}       # CFG node id -> AST statement (or header)
        self.defs: Dict[int, List[_Def]] = defaultdict(list)
        self.entry = None
        self.reach_in: Dict[int, Dict[str, Set[_Def]]] = {}
        self._reach: Dict[int, Set[int]] = {}
        self.imports: Dict[str, Optional[str]] = {}   # local name -> dotted qiskit path

    def new(self, stmt):
        node_id = len(self.stmt_of)
        self.stmt_of[node_id] = stmt
        return node_id

    def edge(self, a, b):
        self.succ[a].add(b)

    def reachable(self, a):
        """CFG nodes reachable from ``a`` in one step or more."""
        if a not in self._reach:
            seen: Set[int] = set()
            stack = list(self.succ.get(a, ()))
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                stack.extend(self.succ.get(n, ()))
            self._reach[a] = seen
        return self._reach[a]


def _targets(target):
    """Names bound by an assignment target, with whether the target is a plain name."""
    if isinstance(target, ast.Name):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _targets(elt)]
    if isinstance(target, ast.Starred):
        return _targets(target.value)
    return []


class _CFGBuilder:
    """Builds a statement-level CFG for one scope and records its definitions."""

    def __init__(self, analysis, scope):
        self.a = analysis
        self.scope = scope
        self.loops: List[Tuple[int, List[int]]] = []   # (continue target, break sources)
        self.constants = _single_constant_bindings(scope.node)

    def truth(self, test):
        """The value of a branch condition when it is a constant, else None.

        CodeQL's Python CFG prunes branches whose condition it can evaluate, such
        as ``if k > 0`` after the only assignment ``k = 1``; so does this.
        """
        return _constant_truth(test, self.constants)

    def node(self, stmt, preds):
        n = self.scope.new(stmt)
        for p in preds:
            self.scope.edge(p, n)
        self.a.register_stmt(stmt, self.scope, n)
        return n

    def define(self, name, value, node, at):
        self.scope.defs[at].append(_Def(name, value, node, at))

    def build(self, body, args=None):
        entry = self.scope.new(None)
        self.scope.entry = entry
        if args is not None:
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                self.define(arg.arg, None, arg, entry)
            for arg in (args.vararg, args.kwarg):
                if arg is not None:
                    self.define(arg.arg, None, arg, entry)
        self.block(body, [entry])

    def block(self, stmts, preds):
        for stmt in stmts:
            preds = self.stmt(stmt, preds)
        return preds

    def stmt(self, s, preds):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            n = self.node(s, preds)
            self.define(s.name, None, s, n)
            return [n]
        if isinstance(s, ast.Assign):
            n = self.node(s, preds)
            for target in s.targets:
                plain = isinstance(target, ast.Name)
                for name in _targets(target):
                    self.define(name.id, s.value if plain else None, name, n)
                    if not plain:
                        self.a.unpacked[name] = _unpacked_part(target, name, s.value)
            return [n]
        if isinstance(s, ast.AnnAssign):
            n = self.node(s, preds)
            if isinstance(s.target, ast.Name) and s.value is not None:
                self.define(s.target.id, s.value, s.target, n)
            return [n]
        if isinstance(s, ast.AugAssign):
            n = self.node(s, preds)
            for name in _targets(s.target):
                self.define(name.id, None, name, n)
            return [n]
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            n = self.node(s, preds)
            for alias in s.names:
                if alias.name != "*":
                    local = alias.asname or alias.name.split(".")[0]
                    self.define(local, None, s, n)
            return [n]
        if isinstance(s, ast.If):
            test = self.node(s.test, preds)
            truth = self.truth(s.test)
            body = self.block(s.body, [] if truth is False else [test])
            into_else = [] if truth is True else [test]
            orelse = self.block(s.orelse, into_else) if s.orelse else into_else
            return body + orelse
        if isinstance(s, (ast.For, ast.AsyncFor)):
            pre = self.node(s.iter, preds)
            head = self.node(s, [pre])
            for name in _targets(s.target):
                self.define(name.id, None, name, head)
            self.loops.append((head, []))
            body_end = self.block(s.body, [head])
            for p in body_end:
                self.scope.edge(p, head)
            _, breaks = self.loops.pop()
            after = self.block(s.orelse, [head]) if s.orelse else [head]
            return after + breaks
        if isinstance(s, ast.While):
            head = self.node(s.test, preds)
            truth = self.truth(s.test)
            self.loops.append((head, []))
            body_end = self.block(s.body, [] if truth is False else [head])
            for p in body_end:
                self.scope.edge(p, head)
            _, breaks = self.loops.pop()
            exit_ = [] if truth is True else [head]
            after = self.block(s.orelse, exit_) if s.orelse else exit_
            return after + breaks
        if isinstance(s, (ast.With, ast.AsyncWith)):
            n = self.node(s, preds)
            for item in s.items:
                if item.optional_vars is not None:
                    for name in _targets(item.optional_vars):
                        self.define(name.id, None, name, n)
            return self.block(s.body, [n])
        if isinstance(s, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            start = len(self.scope.stmt_of)
            body_end = self.block(s.body, preds)
            body_nodes = list(range(start, len(self.scope.stmt_of))) or list(preds)
            ends = []
            for handler in s.handlers:
                h = self.node(handler, body_nodes + list(preds))
                if handler.name:
                    self.define(handler.name, None, handler, h)
                ends += self.block(handler.body, [h])
            ends += self.block(s.orelse, body_end) if s.orelse else body_end
            if s.finalbody:
                return self.block(s.finalbody, ends + body_nodes)
            return ends
        if isinstance(s, ast.Match) if hasattr(ast, "Match") else False:
            head = self.node(s.subject, preds)
            ends = [head]
            for case in s.cases:
                ends += self.block(case.body, [head])
            return ends
        n = self.node(s, preds)
        if isinstance(s, (ast.Return, ast.Raise)):
            return []
        if isinstance(s, ast.Break) and self.loops:
            self.loops[-1][1].append(n)
            return []
        if isinstance(s, ast.Continue) and self.loops:
            self.scope.edge(n, self.loops[-1][0])
            return []
        return [n]


def _scope_bindings(scope_node):
    """Every (name, bound value or None) in a scope, not in scopes nested in it."""
    found = []
    if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope_node.args
        for arg in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
            if arg is not None:
                found.append((arg.arg, None))
    body = scope_node.body if isinstance(getattr(scope_node, "body", None), list) else []
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.name, None))
            continue
        if isinstance(node, (ast.Lambda,) + _COMPREHENSIONS):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((target.id, node.value))
                else:
                    found += [(n.id, None) for n in _targets(target)]
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            found += [(n.id, None) for n in _targets(node.target)]
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            found += [(n.id, None) for n in _targets(node.target)]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    found += [(n.id, None) for n in _targets(item.optional_vars)]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found += [((a.asname or a.name).split(".")[0], None) for a in node.names]
        elif isinstance(node, ast.NamedExpr):
            found.append((node.target.id, None))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            found.append((node.name, None))
        stack.extend(ast.iter_child_nodes(node))
    return found


def _single_constant_bindings(scope_node):
    """Names bound exactly once in the scope, to a literal constant."""
    count: Dict[str, int] = defaultdict(int)
    value: Dict[str, Any] = {}
    for name, bound in _scope_bindings(scope_node):
        count[name] += 1
        value[name] = bound
    return {name: value[name].value for name in count
            if count[name] == 1 and isinstance(value[name], ast.Constant)}


_COMPARISONS = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
                ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
                ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
                ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}
_NOTHING = object()


def _constant_value(expr, constants):
    if isinstance(expr, ast.Constant):
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in constants:
        return constants[expr.id]
    return _NOTHING


def _constant_truth(test, constants):
    try:
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            inner = _constant_truth(test.operand, constants)
            return None if inner is None else not inner
        if isinstance(test, ast.BoolOp):
            values = [_constant_truth(v, constants) for v in test.values]
            if isinstance(test.op, ast.And):
                return False if False in values else (True if all(v is True for v in values) else None)
            return True if True in values else (False if all(v is False for v in values) else None)
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and type(test.ops[0]) in _COMPARISONS:
            left = _constant_value(test.left, constants)
            right = _constant_value(test.comparators[0], constants)
            if left is _NOTHING or right is _NOTHING:
                return None
            return bool(_COMPARISONS[type(test.ops[0])](left, right))
        value = _constant_value(test, constants)
        return None if value is _NOTHING else bool(value)
    except TypeError:
        return None


def _reaching_definitions(scope):
    """Classic iterative reaching definitions over the scope's CFG."""
    nodes = list(scope.stmt_of)
    preds: Dict[int, Set[int]] = defaultdict(set)
    for a, succs in scope.succ.items():
        for b in succs:
            preds[b].add(a)
    out: Dict[int, Dict[str, frozenset]] = {n: {} for n in nodes}
    rin: Dict[int, Dict[str, Set[_Def]]] = {n: {} for n in nodes}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            merged: Dict[str, Set[_Def]] = defaultdict(set)
            for p in preds.get(n, ()):
                for name, ds in out[p].items():
                    merged[name] |= ds
            rin[n] = merged
            new_out = {name: frozenset(ds) for name, ds in merged.items()}
            for d in scope.defs.get(n, ()):
                new_out[d.name] = frozenset()
            for d in scope.defs.get(n, ()):
                new_out[d.name] = new_out[d.name] | {d}
            if new_out != out[n]:
                out[n] = new_out
                changed = True
    scope.reach_in = rin


# --------------------------------------------------------------------------
# The model: circuits, registers, operators and qubit uses
# --------------------------------------------------------------------------

class Circuit:
    """A QuantumCircuit in LintQ's sense: the call that makes one."""

    def __init__(self, call, kind):
        self.call = call
        self.kind = kind    # constructor, compose, copy, library, transpile, udf, assign_parameters


class Register:
    def __init__(self, call, kind):
        self.call = call
        self.kind = kind    # "quantum" or "classical"


class Operator:
    """A QuantumOperator: a gate method call, or a gate object appended to a circuit."""

    def __init__(self, call, name, circuits, via_append=None):
        self.call = call
        self.name = name
        self.circuits = circuits        # set of Circuit
        self.via_append = via_append    # the append() call, for gate objects

    @property
    def unitary(self):
        spec = SPECS.get(self.name)
        return spec is not None and spec[0]

    @property
    def is_measurement(self):
        return self.name in ("measure", "Measure", "measure_all")


class QubitUse:
    """An expression naming qubits for an operator: LintQ's QubitUse."""

    def __init__(self, node, op, kind):
        self.node = node
        self.op = op
        self.kind = kind    # "attribute", "append" or "measure_all"


class LintQAnalysis:
    def __init__(self, source: str, filename: str = "<source>"):
        self.source = source
        self.filename = filename
        self.tree = ast.parse(source, filename=filename)
        self.parent: Dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        self.scope_of: Dict[ast.AST, _Scope] = {}
        self.stmt_id: Dict[ast.AST, Tuple[_Scope, int]] = {}
        self.order: Dict[ast.AST, int] = {}
        self._sources: Dict[ast.AST, Tuple[Any, ...]] = {}
        self._reaching: Dict[ast.AST, Tuple[Any, ...]] = {}
        self._abs: Dict[Any, Set[int]] = {}
        self._locs: Dict[ast.AST, Any] = {}
        self._size_memo: Dict[Tuple, Set[int]] = {}
        self._regs_memo: Dict[Tuple, List[Register]] = {}
        self._on: Optional[Dict[Tuple[int, int], List[Operator]]] = None
        self.unpacked: Dict[ast.AST, ast.AST] = {}
        self._build_scopes()
        for index, node in enumerate(_postorder(self.tree)):
            self.order[node] = index
        self._build_model()

    # -- scopes ------------------------------------------------------------

    def _build_scopes(self):
        self.module_scope = None
        self._scope_queue = [(self.tree, None)]
        while self._scope_queue:
            node, parent = self._scope_queue.pop(0)
            scope = _Scope(node, parent)
            if parent is None:
                self.module_scope = scope
            builder = _CFGBuilder(self, scope)
            if isinstance(node, ast.Module):
                builder.build(node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                builder.build(node.body, node.args)
            elif isinstance(node, ast.Lambda):
                builder.build([ast.Expr(node.body)], node.args)
                ast.copy_location(builder.scope.stmt_of[1], node)
            elif isinstance(node, ast.ClassDef):
                builder.build(node.body)
            else:   # comprehension: the element expression, in a loop
                entry = scope.new(None)
                scope.entry = entry
                loop = builder.node(node, [entry])
                for gen in node.generators:
                    for name in _targets(gen.target):
                        builder.define(name.id, None, name, loop)
                scope.edge(loop, loop)
            _reaching_definitions(scope)
            self._collect_imports(scope)

    def register_stmt(self, stmt, scope, node_id):
        """Attribute the expressions of ``stmt`` (not nested scopes) to its CFG node."""
        def visit(n, top):
            # A def or class statement opens a scope of its own; a comprehension
            # registered as the root of its own scope does not.
            if isinstance(n, _SCOPE_NODES) and not (top and isinstance(n, _COMPREHENSIONS)):
                self.scope_of.setdefault(n, scope)
                self.stmt_id.setdefault(n, (scope, node_id))
                self._scope_queue.append((n, scope))
                # Defaults and decorators are evaluated in the enclosing scope.
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    for d in n.args.defaults + [d for d in n.args.kw_defaults if d is not None]:
                        visit(d, False)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for d in n.decorator_list:
                        visit(d, False)
                if isinstance(n, ast.ClassDef):
                    for b in n.bases:
                        visit(b, False)
                if isinstance(n, _COMPREHENSIONS):
                    visit(n.generators[0].iter, False)
                return
            self.scope_of[n] = scope
            self.stmt_id[n] = (scope, node_id)
            if isinstance(n, (ast.If, ast.While)) and n is stmt:
                return
            for child in ast.iter_child_nodes(n):
                if top and isinstance(stmt, (ast.For, ast.AsyncFor)) and child in stmt.body + stmt.orelse:
                    continue
                if top and isinstance(stmt, (ast.With, ast.AsyncWith)) and child in stmt.body:
                    continue
                if top and isinstance(stmt, ast.ExceptHandler) and child in stmt.body:
                    continue
                if top and isinstance(stmt, (ast.For, ast.AsyncFor)) and child is stmt.iter:
                    continue
                if top and isinstance(stmt, _COMPREHENSIONS) and child is stmt.generators[0]:
                    visit(child.target, False)
                    for cond in child.ifs:
                        visit(cond, False)
                    continue
                visit(child, False)
        if stmt is None:
            return
        visit(stmt, True)

    def _collect_imports(self, scope):
        body = getattr(scope.node, "body", [])
        if not isinstance(body, list):
            return
        for stmt in ast.walk(scope.node):
            if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            if self.scope_of.get(stmt) is not scope and not (
                    stmt in self.stmt_id and self.stmt_id[stmt][0] is scope):
                continue
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.asname:
                        scope.imports[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".")[0]
                        scope.imports[root] = root
            elif stmt.module and not stmt.level:
                for alias in stmt.names:
                    if alias.name != "*":
                        scope.imports[alias.asname or alias.name] = f"{stmt.module}.{alias.name}"

    # -- local data flow ------------------------------------------------------

    def where(self, node):
        return self.stmt_id.get(node)

    def sources(self, expr) -> Tuple[Any, ...]:
        """The local source nodes whose value may reach ``expr`` (LocalSourceNode.flowsTo)."""
        if expr is None:
            return ()
        if expr in self._sources:
            return self._sources[expr]
        self._sources[expr] = ()        # cycle guard
        result: List[Any] = []
        if isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load):
            loc = self.where(expr)
            found = False
            if loc is not None:
                scope, node_id = loc
                for d in scope.reach_in.get(node_id, {}).get(expr.id, ()):
                    found = True
                    if d.value is not None:
                        result.extend(self.sources(d.value))
                    else:
                        result.append(d.node)
            if not found:
                result.append(expr)     # a free or global variable: a source of its own
        elif isinstance(expr, ast.NamedExpr):
            result.extend(self.sources(expr.value))
        else:
            result.append(expr)
        unique = tuple(dict.fromkeys(result))
        self._sources[expr] = unique
        return unique

    def reaching(self, expr, depth=0) -> Tuple[Any, ...]:
        """API::Node.getAValueReachingSink: like ``sources``, but type tracking also
        follows values out of helpers' returns, into their parameters from the
        calls, and through tuple unpacking."""
        if expr is None:
            return ()
        if expr in self._reaching:
            return self._reaching[expr]
        self._reaching[expr] = ()
        out: List[Any] = []
        for src in self.sources(expr):
            out.append(src)
            if depth > 6:
                continue
            if isinstance(src, ast.Call):
                for fd in self.callees(src):
                    for ret in _returns(fd):
                        out.extend(self.reaching(ret.value, depth + 1))
            elif isinstance(src, ast.arg):
                fd, position = self._param_owner.get(src, (None, None))
                if fd is not None:
                    for call in self.calls_to(fd):
                        arg = _arg(call, position, src.arg)
                        out.extend(self.reaching(arg, depth + 1))
            elif src in self.unpacked:
                out.extend(self.reaching(self.unpacked[src], depth + 1))
        unique = tuple(dict.fromkeys(out))
        self._reaching[expr] = unique
        return unique

    def callees(self, call):
        """Functions defined in the file that a ``name(...)`` call may call."""
        if not isinstance(call.func, ast.Name):
            return []
        scopes = []
        scope = self.scope_of.get(call)
        while scope is not None:
            scopes.append(scope)
            scope = scope.parent
        return [fd for fd in self.functions if fd.name == call.func.id
                and any(self.scope_of.get(fd) is sc for sc in scopes)]

    def calls_to(self, fd):
        return [c for c in self._all_calls if fd in self.callees(c)]

    def int_values(self, expr) -> Set[int]:
        """Integer literals that reach ``expr`` by local flow (IntegerLiteral sources)."""
        return {s.value for s in self.sources(expr) if _is_int(s)}

    # -- API graphs -----------------------------------------------------------

    def api_path(self, expr) -> Optional[str]:
        """The dotted Qiskit path an expression refers to through imports, if any.

        Within a scope this follows the imports that reach the use, so an import
        below the use does not count; names free in a function resolve against
        the enclosing scopes' imports, wherever they are.
        """
        if isinstance(expr, ast.Attribute):
            base = self.api_path(expr.value)
            return f"{base}.{expr.attr}" if base else None
        if not isinstance(expr, ast.Name):
            return None
        loc = self.where(expr)
        if loc is None:
            return None
        scope, node_id = loc
        defs = scope.reach_in.get(node_id, {}).get(expr.id, ())
        if defs:
            for d in defs:
                if isinstance(d.node, (ast.Import, ast.ImportFrom)):
                    path = _import_path(d.node, expr.id)
                    if path and path.split(".")[0] == "qiskit":
                        return path
            return None
        if any(d.name == expr.id for ds in scope.defs.values() for d in ds):
            return None
        scope = scope.parent
        while scope is not None:
            if expr.id in scope.imports:
                path = scope.imports[expr.id]
                return path if path and path.split(".")[0] == "qiskit" else None
            if any(d.name == expr.id for ds in scope.defs.values() for d in ds):
                return None
            scope = scope.parent
        return None

    def is_api_call(self, call, *paths):
        return isinstance(call, ast.Call) and self.api_path(call.func) in paths

    # -- the model ------------------------------------------------------------

    def _calls(self):
        return [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)]

    def attribute_calls(self, owners, name):
        """``X.name(...)`` calls where X receives one of ``owners`` by local flow."""
        owner_set = set(owners)
        found = []
        for call in self._all_calls:
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr == name:
                if any(s in owner_set for s in self.sources(func.value)):
                    found.append(call)
        return found

    def receivers(self, call, of):
        """The members of ``of`` (keyed by call node) that ``call.func.value`` receives."""
        if not isinstance(call.func, ast.Attribute):
            return []
        return [of[s] for s in self.sources(call.func.value) if s in of]

    def _build_model(self):
        self._all_calls = self._calls()
        calls = self._all_calls

        # Registers (Register.qll)
        self.registers: Dict[ast.AST, Register] = {}
        for call in calls:
            if self.is_api_call(call, "qiskit.QuantumRegister", "qiskit.circuit.QuantumRegister"):
                self.registers[call] = Register(call, "quantum")
            elif self.is_api_call(call, "qiskit.ClassicalRegister",
                                  "qiskit.circuit.ClassicalRegister"):
                self.registers[call] = Register(call, "classical")

        # Functions that return a circuit, by name, for UDF calls (Circuit.qll)
        self.functions = [n for n in ast.walk(self.tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self._param_owner = {}
        for fd in self.functions:
            for i, arg in enumerate(fd.args.posonlyargs + fd.args.args):
                self._param_owner[arg] = (fd, i)
            for arg in fd.args.kwonlyargs:
                self._param_owner[arg] = (fd, None)

        # Circuits: a fixpoint, since compose/copy calls on circuits are circuits too.
        self.circuits: Dict[ast.AST, Circuit] = {}
        for call in calls:
            if not _returns_new_value(call):
                continue
            if self.is_api_call(call, "qiskit.QuantumCircuit", "qiskit.circuit.QuantumCircuit"):
                self.circuits[call] = Circuit(call, "constructor")
            elif self.is_api_call(call, *[f"qiskit.circuit.library.{n}" for n in _PARAMETRIZED_LIBRARY],
                                  *[f"qiskit.{n}" for n in _PARAMETRIZED_LIBRARY]):
                self.circuits[call] = Circuit(call, "library")
            elif self.is_api_call(call, "qiskit.transpile"):
                self.circuits[call] = Circuit(call, "transpile")
            elif isinstance(call.func, ast.Attribute) and call.func.attr == "assign_parameters" \
                    and any(isinstance(s, ast.Call) for s in self.sources(call.func.value)):
                self.circuits[call] = Circuit(call, "assign_parameters")
        changed = True
        while changed:
            changed = False
            for call in calls:
                if call in self.circuits or not _returns_new_value(call):
                    continue
                kind = None
                if isinstance(call.func, ast.Attribute) and call.func.attr in ("compose", "copy") \
                        and self.receivers(call, self.circuits):
                    kind = call.func.attr
                elif self._udf_returning_circuit(call):
                    kind = "udf"
                if kind:
                    self.circuits[call] = Circuit(call, kind)
                    changed = True

        circuit_calls = list(self.circuits)

        # Operators (Gate.qll): gate method calls, and gate objects appended to circuits.
        self.operators: Dict[ast.AST, Operator] = {}
        for call in calls:
            if isinstance(call.func, ast.Attribute) and call.func.attr in ATTRIBUTE_NAMES:
                owners = set(self.receivers(call, self.circuits))
                if owners:
                    self.operators[call] = Operator(call, call.func.attr, owners)
        self.appends = {}
        for call in self.attribute_calls(circuit_calls, "append"):
            owners = set(self.receivers(call, self.circuits))
            self.appends[call] = owners
            instruction = _arg(call, 0, "instruction")
            for src in self.reaching(instruction):
                name = self._gate_object_name(src)
                if name is not None:
                    op = self.operators.get(src)
                    if op is None:
                        self.operators[src] = Operator(src, name, set(owners), call)
                    else:
                        op.circuits |= owners

        # Qubit uses (BitUse.qll)
        self.uses: List[QubitUse] = []
        for op in self.operators.values():
            if op.via_append is None and op.name == "measure_all":
                self.uses.append(QubitUse(op.call, op, "measure_all"))
                continue
            spec = SPECS.get(op.name)
            if spec is None:
                continue
            _, params, qubits = spec
            if op.via_append is None:
                for i in range(len(params), len(params) + len(qubits)):
                    if i < len(op.call.args):
                        for src in self.reaching(op.call.args[i]):
                            self.uses.append(QubitUse(src, op, "attribute"))
                for kw in op.call.keywords:
                    if kw.arg in qubits:
                        for src in self.reaching(kw.value):
                            self.uses.append(QubitUse(src, op, "attribute"))
            else:
                qargs = _arg(op.via_append, 1, "qargs")
                for src in self.reaching(qargs):
                    if isinstance(src, ast.List):
                        for elt in src.elts:
                            if self._is_local_source(elt):
                                self.uses.append(QubitUse(elt, op, "append"))
                    else:
                        self.uses.append(QubitUse(src, op, "append"))
        self.uses_of: Dict[ast.AST, List[QubitUse]] = defaultdict(list)
        for use in self.uses:
            self.uses_of[use.op.call].append(use)

        # Subcircuits (Circuit.qll: SubCircuit, InstructionCircuit)
        self.subcircuits: Set[ast.AST] = set()
        self.parents: Dict[ast.AST, Set[ast.AST]] = defaultdict(set)
        for call, owners in self.appends.items():
            for src in self.reaching(_arg(call, 0, "instruction")):
                if src in self.circuits:
                    self.subcircuits.add(src)
                    self.parents[src] |= {o.call for o in owners}
        for call in self.attribute_calls(circuit_calls, "compose"):
            owners = self.receivers(call, self.circuits)
            for src in self.reaching(_arg(call, 0, "other")):
                if src in self.circuits:
                    self.subcircuits.add(src)
                    self.parents[src] |= {o.call for o in owners}
        self.children: Dict[ast.AST, Set[ast.AST]] = defaultdict(set)
        for sub, owners in self.parents.items():
            for owner in owners:
                self.children[owner].add(sub)
        for name in ("to_instruction", "to_gate"):
            for call in self.attribute_calls(circuit_calls, name):
                for c in self.receivers(call, self.circuits):
                    self.subcircuits.add(c.call)
        returns = [n for n in ast.walk(self.tree) if isinstance(n, ast.Return) and n.value]
        for circ in circuit_calls:
            for ret in returns:
                if self._return_holds(ret, circ):
                    self.subcircuits.add(circ)
        self._udf_subcircuits(returns)

        # Unknown operators (UnknownQuantumOperator.qll)
        self.unknown_ops: Dict[ast.AST, Set[ast.AST]] = defaultdict(set)   # circuit -> calls
        for op in self.operators.values():
            spec = SPECS.get(op.name)
            if spec is None:
                continue
            known = len({id(u.node) for u in self.uses_of[op.call]
                         if any(i >= 0 for i in self.abs_index(u))})
            if known < len(spec[2]):
                for c in op.circuits:
                    self.unknown_ops[c.call].add(op.call)
        for call, owners in self.appends.items():
            if not any(src in self.operators for src in self.sources(_arg(call, 0, "instruction"))):
                for c in owners:
                    self.unknown_ops[c.call].add(call)
        self._circuit_extenders(circuit_calls)

    def _is_local_source(self, expr):
        return not (isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load)) \
            and not isinstance(expr, ast.NamedExpr)

    def _gate_object_name(self, src):
        if not isinstance(src, ast.Call):
            return None
        path = self.api_path(src.func)
        if not path:
            return None
        module, _, name = path.rpartition(".")
        if name in OBJECT_NAMES and module in _GATE_MODULES:
            return name
        return None

    def _function_named(self, call):
        """The function defined in the call's own scope that a ``name(...)`` call names."""
        if not isinstance(call.func, ast.Name):
            return []
        scope = self.scope_of.get(call)
        return [fd for fd in self.functions
                if fd.name == call.func.id and self.scope_of.get(fd) is scope]

    def _return_holds(self, ret, circ):
        """LintQ: ``ret.contains(qc)`` or ``qc`` flows to an expression in the return."""
        for n in ast.walk(ret):
            if n is circ or (isinstance(n, ast.expr) and circ in self.sources(n)):
                return True
        return False

    def _udf_returning_circuit(self, call):
        for fd in self._function_named(call):
            for ret in ast.walk(fd):
                if isinstance(ret, ast.Return) and ret.value is not None:
                    if any(self._return_holds(ret, c) for c in list(self.circuits)):
                        return True
        return False

    def udf_circuits(self, call):
        """Circuits returned by the function a UDF call names (getCircuitInLocalScope)."""
        found = []
        for fd in self._function_named(call):
            for ret in ast.walk(fd):
                if isinstance(ret, ast.Return) and ret.value is not None:
                    found += [c for c in self.circuits if self._return_holds(ret, c)]
        return found

    def _udf_subcircuits(self, returns):
        composing = []
        for call in self.appends:
            composing.append(_arg(call, 0, "instruction"))
        for call in self.attribute_calls(list(self.circuits), "compose"):
            composing.append(_arg(call, 0, "other"))
        for target in composing:
            for src in self.sources(target):
                if isinstance(src, ast.Call):
                    for fd in self._function_named(src):
                        for ret in ast.walk(fd):
                            if isinstance(ret, ast.Return) and ret.value is not None:
                                for c in self.circuits:
                                    if self._return_holds(ret, c):
                                        self.subcircuits.add(c)

    def _circuit_extenders(self, circuit_calls):
        imported_here = {}
        for scope in self._all_scopes():
            imported_here[id(scope)] = {
                alias.asname or alias.name
                for stmt in ast.walk(scope.node) if isinstance(stmt, ast.ImportFrom)
                and self.where(stmt) and self.where(stmt)[0] is scope
                for alias in stmt.names}
        function_names = {fd.name for fd in self.functions}
        for call in self._all_calls:
            if not isinstance(call.func, ast.Name) or call.func.id in _EXTENDER_EXCLUDED:
                continue
            name = call.func.id
            scope = self.scope_of.get(call)
            if name not in function_names and name not in imported_here.get(id(scope), ()):
                continue
            for arg in call.args:
                for src in self.sources(arg):
                    if src in self.circuits:
                        self.unknown_ops[src].add(call)
        # CircuitExtenderFunctionImplicit: a helper in the call's scope that uses a
        # variable named like the circuit without assigning it.
        for call in self._all_calls:
            for fd in self._function_named(call):
                used = {n.id for n in ast.walk(fd) if isinstance(n, ast.Name)
                        and isinstance(n.ctx, ast.Load)}
                stored = {n.id for n in ast.walk(fd) if isinstance(n, ast.Name)
                          and isinstance(n.ctx, (ast.Store, ast.Del))}
                for circ in circuit_calls:
                    for name in self.circuit_names(circ):
                        if name in used and name not in stored:
                            self.unknown_ops[circ].add(call)

    def _all_scopes(self):
        seen, out = set(), []
        for scope in [self.module_scope] + [s for s, _ in self.stmt_id.values()]:
            if id(scope) not in seen:
                seen.add(id(scope))
                out.append(scope)
        return out

    # -- circuit facts (Circuit.qll) -----------------------------------------

    def circuit_names(self, circ) -> List[str]:
        """Targets of the assignment containing the circuit's call (getName)."""
        node = circ
        while node is not None and not isinstance(node, ast.stmt):
            node = self.parent.get(node)
        if isinstance(node, ast.Assign):
            return [t.id for t in node.targets if isinstance(t, ast.Name)]
        return []

    def name_of(self, circ):
        names = self.circuit_names(circ)
        return names[0] if names else ""

    def registers_of(self, circ, kind):
        """Registers passed to the circuit (any argument) or to its add_register()."""
        key = (circ, kind)
        if key not in self._regs_memo:
            self._regs_memo[key] = self._registers_of(circ, kind)
        return self._regs_memo[key]

    def _registers_of(self, circ, kind):
        found = []
        for arg in circ.args:
            for src in self.sources(arg):
                reg = self.registers.get(src)
                if reg is not None and reg.kind == kind and reg not in found:
                    found.append(reg)
        for call in self.attribute_calls([circ], "add_register"):
            for src in self.sources(_arg(call, 0, None)):
                reg = self.registers.get(src)
                if reg is not None and reg.kind == kind and reg not in found:
                    found.append(reg)
        return found

    def register_sizes(self, reg) -> Set[int]:
        return {s.value for s in self.reaching(_arg(reg.call, 0, "size")) if _is_int(s)}

    def _sizes(self, circ, kind, position, seen):
        c = self.circuits[circ]
        if c.kind == "transpile":
            out = set()
            for src in self.reaching(_arg(circ, 0, "circuits")):
                if src in self.circuits:
                    out |= self._sizes(src, kind, position, seen)
            return out
        if c.kind == "udf":
            out = set()
            for inner in self.udf_circuits(circ):
                out |= self._sizes(inner, kind, position, seen)
            return out
        if c.kind == "compose":
            out = set()
            for parent in self.receivers(circ, self.circuits):
                out |= self._sizes(parent.call, kind, position, seen)
            return out
        from_regs = sum(min(self.register_sizes(r)) for r in self.registers_of(circ, kind)
                        if self.register_sizes(r))
        ints = self.int_values(circ.args[position]) if len(circ.args) > position else set()
        ints = ints or {0}
        if c.kind == "copy" and from_regs == 0 and ints == {0}:
            out = set()
            for original in self.receivers(circ, self.circuits):
                if original.call not in seen:
                    out |= self._sizes(original.call, kind, position, seen | {circ})
            return out
        return {from_regs + i for i in ints}

    def num_qubits(self, circ) -> Set[int]:
        key = (circ, "quantum")
        if key not in self._size_memo:
            self._size_memo[key] = self._sizes(circ, "quantum", 0, frozenset())
        return self._size_memo[key]

    def num_clbits(self, circ) -> Set[int]:
        key = (circ, "classical")
        if key not in self._size_memo:
            self._size_memo[key] = self._sizes(circ, "classical", 1, frozenset())
        return self._size_memo[key]

    def has_unknown_size_register(self, circ):
        return any(not self.register_sizes(r) for r in self.registers_of(circ, "quantum"))

    # -- qubit indices (BitUse.qll) --------------------------------------------

    def use_register(self, use) -> List[Register]:
        """getARegister: only a register flowing to the use itself; a subscript's
        register would need points-to, which does not reach into Qiskit."""
        reg = self.registers.get(use.node)
        return [reg] if reg is not None and reg.kind == "quantum" else []

    def rel_index(self, use) -> Set[int]:
        """getAnIndexIfAny."""
        node = use.node
        if use.kind == "measure_all":
            out = set()
            for c in use.op.circuits:
                for n in self.num_qubits(c.call):
                    out |= set(range(n))
            return out
        if _is_int(node):
            return {node.value}
        reg = self.registers.get(node)
        if reg is not None and reg.kind == "quantum":
            out = set()
            for size in self.register_sizes(reg):
                out |= set(range(size))
            return out
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
            return self._range(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            out = set()
            for left in self.int_values(node.left):
                for right in self.int_values(node.right):
                    out.add(left + right if isinstance(node.op, ast.Add) else
                            left - right if isinstance(node.op, ast.Sub) else left * right)
            return out
        if isinstance(node, ast.List):
            out = set()
            for elt in node.elts:
                out |= self.int_values(elt)
            return out
        return set()

    def _range(self, call):
        """resolveRange. Its one-argument case does not check the argument count, so
        ``range(2, 7)`` also reads as ``range(2)``: LintQ gives qubits 0-6."""
        out = set()
        if call.args:
            out |= {i for end in self.int_values(call.args[0]) for i in range(0, end)}
        if len(call.args) >= 2:
            out |= {i for start in self.int_values(call.args[0])
                    for end in self.int_values(call.args[1]) for i in range(start, end)}
        return out

    def index(self, use) -> Set[int]:
        """getAnIndex: the indices, or -1 when none is known."""
        return self.rel_index(use) or {-1}

    def abs_index(self, use) -> Set[int]:
        """getAnAbsoluteIndex: counting from the circuit's first qubit, or -1."""
        if use not in self._abs:
            self._abs[use] = self._abs_index(use)
        return self._abs[use]

    def _abs_index(self, use) -> Set[int]:
        regs = self.use_register(use)
        if not regs:
            return self.rel_index(use) or {-1}
        out = set()
        for c in use.op.circuits:
            if c.kind != "constructor":
                out.add(-1)
                continue
            for reg in regs:
                for pos, arg in enumerate(c.call.args):
                    if reg.call in self.sources(arg):
                        # QL sums the distinct sizes of the registers before this one.
                        sizes = set()
                        for a2 in c.call.args[:pos]:
                            for src in self.sources(a2):
                                r2 = self.registers.get(src)
                                if r2 is not None and r2.kind == "quantum":
                                    sizes |= self.register_sizes(r2)
                        out |= {sum(sizes) + i for i in self.rel_index(use)}
        return out or {-1}

    # -- control flow ------------------------------------------------------

    def strictly_reaches(self, a, b):
        """ControlFlowNode.strictlyReaches between two expressions (calls)."""
        la, lb = self._loc(a), self._loc(b)
        if la is None or lb is None or la[0] is not lb[0]:
            return False
        if la[1] == lb[1]:
            return la[2] < lb[2] or la[1] in la[3]
        return lb[1] in la[3]

    def _loc(self, node):
        """(scope, CFG node, evaluation order, CFG nodes reachable from it), cached."""
        loc = self._locs.get(node, _NOTHING)
        if loc is _NOTHING:
            where = self.where(node)
            loc = None if where is None else (where[0], where[1], self.order[node],
                                              where[0].reachable(where[1]))
            self._locs[node] = loc
        return loc

    def _same_qubit(self, op_a, op_b, circ, index):
        """manipulateSameQubit in QuantumDataFlow.qll."""
        if circ not in op_a.circuits or circ not in op_b.circuits:
            return False
        regs = self.registers_of(circ.call, "quantum")
        nq = self.num_qubits(circ.call)
        loose = len(regs) <= 1 and any(n > 0 for n in nq)
        for ua in self.uses_of[op_a.call]:
            if index not in self.abs_index(ua):
                continue
            for ub in self.uses_of[op_b.call]:
                if index not in self.abs_index(ub):
                    continue
                ra, rb = self.use_register(ua), self.use_register(ub)
                if (ra and rb and set(map(id, ra)) & set(map(id, rb))) or loose:
                    return True
        return False

    def touches(self, op):
        """(circuit, qubit) pairs an operator names with a known absolute index."""
        return [(circ, i) for circ in op.circuits
                for i in sorted({i for u in self.uses_of[op.call] for i in self.abs_index(u)})
                if i >= 0]

    def ops_on(self, circ, index):
        """Operators with a use of absolute qubit ``index`` in ``circ``: the only ones
        manipulateSameQubit can relate on that qubit."""
        if self._on is None:
            self._on = defaultdict(list)
            for op in self.operators.values():
                for c, i in self.touches(op):
                    self._on[(id(c), i)].append(op)
        return self._on.get((id(circ), index), [])

    def _in_order_after_circuit(self, start, end, circ):
        return (self.strictly_reaches(circ.call, start.call)
                and self.strictly_reaches(circ.call, end.call)
                and not self.strictly_reaches(start.call, circ.call))

    def follows_directly(self, start, end, circ, index):
        """mayFollowDirectly on one circuit and qubit."""
        if not self.strictly_reaches(start.call, end.call):
            return False
        if not self._same_qubit(start, end, circ, index):
            return False
        if not self._in_order_after_circuit(start, end, circ):
            return False
        reaches = self.strictly_reaches
        s_call, e_call = start.call, end.call
        for mid in self.ops_on(circ, index):
            m_call = mid.call
            if m_call is s_call or m_call is e_call:
                continue
            if reaches(s_call, m_call) and reaches(m_call, e_call) \
                    and self._same_qubit(start, mid, circ, index) \
                    and self._same_qubit(mid, end, circ, index):
                return False
        return True

    def directly_after(self, start, keep):
        """(end, circuit, qubit) for operators ``end`` (where ``keep(end)``) that may
        directly follow ``start``."""
        for circ, index in self.touches(start):
            for end in self.ops_on(circ, index):
                if keep(end) and self.follows_directly(start, end, circ, index):
                    yield end, circ, index

    def may_follow(self, start, end, index):
        """mayFollow: ``end`` may come after ``start`` on qubit ``index`` of one circuit."""
        if not self.strictly_reaches(start.call, end.call):
            return False
        for circ in start.circuits & end.circuits:
            if not self._in_order_after_circuit(start, end, circ):
                continue
            regs = self.registers_of(circ.call, "quantum")
            loose = len(regs) <= 1 and any(n > 0 for n in self.num_qubits(circ.call))
            for ua in self.uses_of[start.call]:
                for ub in self.uses_of[end.call]:
                    if index in self.abs_index(ua) and index in self.abs_index(ub) and index >= 0:
                        ra, rb = self.use_register(ua), self.use_register(ub)
                        if (ra and rb and set(map(id, ra)) & set(map(id, rb))) or loose:
                            return True
        return False

    def is_conditional(self, op):
        """``qc.x(0).c_if(...)``: a c_if call on the gate call itself."""
        parent = self.parent.get(op.call)
        if isinstance(parent, ast.Attribute) and parent.attr == "c_if":
            return isinstance(self.parent.get(parent), ast.Call)
        return False

    # -- the queries -------------------------------------------------------

    def run(self, codes: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        wanted = set(codes) if codes is not None else set(RULES)
        findings: List[Dict[str, Any]] = []
        for code, query in _QUERIES:
            if code in wanted:
                for node, message in query(self):
                    findings.append(_finding(code, node, message))
        merged: Dict[Tuple, Dict[str, Any]] = {}
        for f in findings:
            key = (f["code"], f["line"], f["column"])
            if key in merged:
                if f["message"] not in merged[key]["message"]:
                    merged[key]["message"] += " " + f["message"]
            else:
                merged[key] = f
        return sorted(merged.values(), key=lambda f: (f["line"], f["column"], f["code"]))


def _q_double_measurement(a):
    """Measurement/DoubleMeasurement.ql"""
    for first in [op for op in a.operators.values() if op.is_measurement]:
        for second, _circ, qubit in a.directly_after(first, lambda op: op.is_measurement):
            yield second.call, (f"Two consecutive measurements on qubit '{qubit}' at "
                                f"locations: ({_line(first.call)}, {_col(first.call)}) and "
                                f"({_line(second.call)}, {_col(second.call)})")


def _q_op_after_measurement(a):
    """Measurement/OpAfterMeasurement.ql"""
    for measure in [op for op in a.operators.values() if op.is_measurement]:
        def is_gate(op):
            return op.unitary and not a.is_conditional(op)
        for gate, _circ, qubit in a.directly_after(measure, is_gate):
            yield gate.call, (f"Operation '{gate.name}' on qubit {qubit} after measurement "
                              f"at location: ({_line(measure.call)}, {_col(measure.call)}).")


def _q_measure_all_abuse(a):
    """Measurement/MeasureAll.ql"""
    for op in a.operators.values():
        if op.name != "measure_all" or op.via_append is not None:
            continue
        add_bits = _arg(op.call, 1, "add_bits")
        if any(isinstance(s, ast.Constant) and s.value is False for s in a.reaching(add_bits)):
            continue
        for circ in op.circuits:
            clbits = [n for n in a.num_clbits(circ.call) if n > 0]
            if clbits:
                yield op.call, (f"measure_all() on the circuit '{a.name_of(circ.call)}' (at "
                                f"location:{_line(circ.call)}, {_col(circ.call)})  when it has "
                                f"already {max(clbits)} classical bits.")


def _q_conditional_without_measurement(a):
    """Measurement/ConditionalGateWithoutMeas.ql"""
    for gate in a.operators.values():
        if not gate.unitary or not a.is_conditional(gate):
            continue
        for circ in gate.circuits:
            if circ.call in a.subcircuits or a.children.get(circ.call):
                continue
            measurements = [m for m in a.operators.values()
                            if m.is_measurement and circ in m.circuits]
            if not all(a.strictly_reaches(gate.call, m.call)
                       and not a.strictly_reaches(m.call, gate.call) for m in measurements):
                continue
            for qubit in sorted({i for u in a.uses_of[gate.call] for i in a.index(u) if i >= 0}):
                yield gate.call, (f"Conditional gate '{gate.name}' on qubit '{qubit}' "
                                  f"(l: {_line(gate.call)}, c: {_col(gate.call)}) without "
                                  "preceeding measurement.")


def _q_ghost_composition(a):
    """Interconnection/GhostComposition.ql"""
    for call in a.attribute_calls(list(a.circuits), "compose"):
        if not _returns_new_value(call):
            continue
        node, stored = call, False
        while node is not None and not isinstance(node, ast.stmt):
            node = a.parent.get(node)
        if isinstance(node, ast.Assign) and _contains(node.value, call):
            stored = True
        if isinstance(node, ast.Return) and node.value is not None and _contains(node.value, call):
            stored = True
        if not stored:
            yield call, f"Ghost composition at location: ({_line(call)}, {_col(call)})"


def _q_op_after_optimization(a):
    """Optimization/OpAfterOptimization.ql"""
    for circ in a.circuits.values():
        if circ.kind != "transpile":
            continue
        if not any(_is_int(s) and s.value == 3
                   for s in a.reaching(_arg(circ.call, 7, "optimization_level"))):
            continue
        for op in a.operators.values():
            if circ in op.circuits:
                yield op.call, (f"Operation {op.name} applied to transpiled circuit: "
                                f"{a.name_of(circ.call)}.")


def _q_constant_classic_bit(a):
    """Resource/ConstantClassicBit.ql"""
    for circ in a.circuits.values():
        if circ.kind != "constructor" or a.unknown_ops.get(circ.call) or a.children.get(circ.call):
            continue
        for measure in a.operators.values():
            if not measure.is_measurement or circ not in measure.circuits:
                continue
            for qubit in sorted({i for u in a.uses_of[measure.call] for i in a.index(u) if i >= 0}):
                candidates = {id(g): g for c in measure.circuits for g in a.ops_on(c, qubit)}
                touched = any(g.unitary and circ in g.circuits and a.may_follow(g, measure, qubit)
                              for g in candidates.values())
                if not touched:
                    yield circ.call, (f"Circuit '{a.name_of(circ.call)}' measures qubit "
                                      f"'{qubit}' but never uses it.")


def _q_oversized_circuit(a):
    """Resource/OversizedCircuit.ql"""
    for circ in a.circuits.values():
        if circ.kind != "constructor":
            continue
        if a.children.get(circ.call) or a.unknown_ops.get(circ.call) \
                or a.has_unknown_size_register(circ.call):
            continue
        if any(op.name in ("initialize", "Initialize") and circ in op.circuits
               for op in a.operators.values()):
            continue
        for n in sorted(a.num_qubits(circ.call)):
            if n <= 0:
                continue
            used = any(circ in u.op.circuits and any(0 <= i < n for i in a.abs_index(u))
                       for u in a.uses)
            if not used:
                yield circ.call, (f"Circuit '{a.name_of(circ.call)}' never manipulates some of "
                                  f"its {n}qubits.")


def _q_unmeasurable_qubits(a):
    """Resource/UnmeasurableQubits.ql"""
    for circ in a.circuits.values():
        if circ.call in a.subcircuits or a.has_unknown_size_register(circ.call):
            continue
        if any(op.name == "measure_all" and circ in op.circuits for op in a.operators.values()):
            continue
        for q in sorted(a.num_qubits(circ.call)):
            for c in sorted(a.num_clbits(circ.call)):
                if q > c:
                    yield circ.call, (f"Circuit '{a.name_of(circ.call)}' has more qubits ({q}) "
                                      f"than classical  bits ({c})")


def _q_deprecated_identity(a):
    """Extra/DeprecatedIdentity.ql"""
    for call in a.attribute_calls(list(a.circuits), "iden"):
        yield call, "The deprecated iden() API is called."


_QUERIES = [
    ("LQ01", _q_double_measurement),
    ("LQ02", _q_op_after_measurement),
    ("LQ03", _q_measure_all_abuse),
    ("LQ04", _q_conditional_without_measurement),
    ("LQ05", _q_ghost_composition),
    ("LQ06", _q_op_after_optimization),
    ("LQ07", _q_constant_classic_bit),
    ("LQ08", _q_oversized_circuit),
    ("LQ09", _q_unmeasurable_qubits),
    ("LQ10", _q_deprecated_identity),
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _import_path(stmt, name):
    """The dotted path an import statement binds ``name`` to."""
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if alias.asname == name:
                return alias.name
            if alias.asname is None and alias.name.split(".")[0] == name:
                return name
    elif stmt.module and not stmt.level:
        for alias in stmt.names:
            if (alias.asname or alias.name) == name and alias.name != "*":
                return f"{stmt.module}.{alias.name}"
    return None


def _unpacked_part(target, name, value):
    """What ``name`` receives from ``target = value``: the matching element of a
    literal tuple, else the whole value."""
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
            and len(target.elts) == len(value.elts):
        for t, v in zip(target.elts, value.elts):
            if t is name:
                return v
            if isinstance(t, (ast.Tuple, ast.List)) and any(n is name for n in ast.walk(t)):
                return _unpacked_part(t, name, v)
    return value


def _returns(fd):
    """The return statements of a function, not of functions nested in it."""
    found, stack = [], list(fd.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Return) and node.value is not None:
            found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _postorder(node):
    for child in ast.iter_child_nodes(node):
        yield from _postorder(child)
    yield node


def _is_int(node):
    return isinstance(node, ast.Constant) and type(node.value) is int


def _arg(call, position, keyword):
    """API::CallNode.getParameter(position, keyword): the argument expression, or None."""
    if keyword is not None:
        for kw in call.keywords:
            if kw.arg == keyword:
                return kw.value
    if position is not None and position < len(call.args) \
            and not any(isinstance(a, ast.Starred) for a in call.args[:position + 1]):
        return call.args[position]
    return None


def _returns_new_value(call):
    """ReturnsNewValue: no ``inplace=True`` literal."""
    return not any(kw.arg == "inplace" and isinstance(kw.value, ast.Constant)
                   and kw.value.value is True for kw in call.keywords)


def _contains(expr, node):
    return any(n is node for n in ast.walk(expr))


def _line(node):
    return getattr(node, "lineno", 0)


def _col(node):
    return getattr(node, "col_offset", 0) + 1


def _finding(code, node, message):
    lintq_id, precision, title = RULES[code]
    return {
        "code": code,
        "severity": "warning",
        "line": _line(node),
        "column": _col(node),
        "message": message,
        "hint": f"LintQ {lintq_id} ({precision} precision).",
        "lintq_id": lintq_id,
    }


def check_source(source: str, filename: str = "<source>",
                 codes: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """LintQ's warnings for ``source``; a file Python cannot parse gets none.

    The "directly follows" queries grow with the cube of the operators on one
    qubit, so a program with more than MAX_OPERATORS gets one TOO_LARGE note
    instead of an analysis that could run for minutes.
    """
    try:
        analysis = LintQAnalysis(source, filename)
    except (SyntaxError, ValueError, RecursionError):
        return []
    if len(analysis.operators) > MAX_OPERATORS:
        return [{"code": TOO_LARGE, "severity": "info", "line": 0, "column": 0,
                 "message": f"LintQ skipped: {len(analysis.operators)} quantum operations, more "
                            f"than the {MAX_OPERATORS} it analyses.",
                 "hint": "Lint a smaller part of the program.", "lintq_id": ""}]
    return analysis.run(codes)
