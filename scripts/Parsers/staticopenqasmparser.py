"""Static OpenQASM front end: OpenQASM 2/3 text to the IR, with source locations.

``OpenQASMParser`` goes through ``qiskit.qasm3.load``: the text is parsed, a
QuantumCircuit is built, and the circuit goes through QiskitParser. Nothing is
executed, but where each instruction came from is lost on the way, and
anything the converter cannot build (indexing a register with a loop variable,
OpenQASM 2) fails the whole file.

``StaticOpenQASMParser`` takes the syntax tree of the OpenQASM reference parser
(the ``openqasm3`` package, whose nodes carry line/column spans) and interprets
it itself, building the same circuit model StaticQiskitParser uses. Every
instruction keeps the span of the statement that produced it, and the call
path through any subroutine that led there.

Understood:

* OpenQASM 3 and 2: ``qubit``/``bit`` and ``qreg``/``creg`` declarations,
  ``stdgates.inc``/``qelib1.inc`` gates, ``U``/``CX``, ``gate`` definitions
  (kept as one instruction each, as Qiskit does), the ``ctrl``/``negctrl``/
  ``inv``/``pow`` modifiers, register broadcasting, ranges and index sets;
* ``measure`` in both syntaxes, ``reset``, ``barrier``, ``delay``;
* ``if``/``else`` on a bit or a register compared with a constant (an
  ``if_else`` instruction, as Qiskit builds), and on compile-time values (the
  branch is simply taken);
* ``const`` and classical variables, ``input`` parameters (kept symbolic),
  arithmetic and the built-in math functions;
* ``for`` loops over ranges and sets, and ``while`` loops whose condition is
  known at compile time -- both unrolled, so each iteration's gates are their
  own nodes; subroutines (``def``), inlined.

Refused, with the line: conditions that need a run-time value in an
unsupported shape, ``while`` loops on measured values, ``box``, physical
qubits, includes other than the standard two.
"""

from parser import *

import math
import re

try:
    from Parsers.staticqiskitparser import (
        MAX_INSTRUCTIONS, MAX_STEPS, CircuitError, StaticCircuit, StaticGate,
        StaticInstruction, StaticParseError, StaticRegister, Symbol, Unknown, _Scope,
        _mcx_name, _negate, _symbolic_binop, _symbolic_call, broadcast, circuit_to_program)
except ModuleNotFoundError:
    from staticqiskitparser import (
        MAX_INSTRUCTIONS, MAX_STEPS, CircuitError, StaticCircuit, StaticGate,
        StaticInstruction, StaticParseError, StaticRegister, Symbol, Unknown, _Scope,
        _mcx_name, _negate, _symbolic_binop, _symbolic_call, broadcast, circuit_to_program)

try:
    import openqasm3
    from openqasm3 import ast as qasm_ast
except ImportError:  # pragma: no cover - installed with qiskit-qasm3-import
    openqasm3 = None
    qasm_ast = None


MAX_REGISTER = 100_000
MAX_CALL_DEPTH = 40

# Gate name -> (instruction name, number of parameters, number of qubits).
# The union of stdgates.inc, qelib1.inc and the built-ins, named as Qiskit's
# OpenQASM importer names them.
_GATES = {
    "U": ("u", 3, 1), "u": ("u", 3, 1), "CX": ("cx", 0, 2),
    "p": ("p", 1, 1), "phase": ("p", 1, 1),
    **{n: (n, 0, 1) for n in ("x", "y", "z", "h", "s", "sdg", "t", "tdg", "sx", "sxdg", "id")},
    **{n: (n, 1, 1) for n in ("rx", "ry", "rz", "u1")},
    "u2": ("u2", 2, 1), "u3": ("u3", 3, 1),
    **{n: (n, 0, 2) for n in ("cx", "cy", "cz", "ch", "swap", "csx")},
    **{n: (n, 1, 2) for n in ("cp", "crx", "cry", "crz", "cu1", "rxx", "ryy", "rzz")},
    "cphase": ("cp", 1, 2), "cu3": ("cu3", 3, 2), "cu": ("cu", 4, 2),
    "ccx": ("ccx", 0, 3), "cswap": ("cswap", 0, 3), "rccx": ("rccx", 0, 3),
    "c3x": ("mcx", 0, 4), "c4x": ("mcx", 0, 5), "rc3x": ("rcccx", 0, 4), "c3sqrtx": ("c3sx", 0, 4),
}
_STANDARD_INCLUDES = {"stdgates.inc", "qelib1.inc"}

_CONSTANTS = {"pi": math.pi, "π": math.pi, "tau": math.tau, "τ": math.tau,
              "euler": math.e, "ℇ": math.e}

_FUNCTIONS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "arcsin": math.asin,
    "arccos": math.acos, "arctan": math.atan, "exp": math.exp, "ln": math.log,
    "sqrt": math.sqrt, "floor": math.floor, "ceiling": math.ceil, "mod": math.fmod,
    "pow": math.pow, "log": math.log,
}

_BINARY = {
    "+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
    "/": lambda a, b: a / b if isinstance(a, float) or isinstance(b, float) or a % b else a // b,
    "%": lambda a, b: a % b, "**": lambda a, b: a ** b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "&&": lambda a, b: bool(a) and bool(b), "||": lambda a, b: bool(a) or bool(b),
    "&": lambda a, b: a & b, "|": lambda a, b: a | b, "^": lambda a, b: a ^ b,
    "<<": lambda a, b: a << b, ">>": lambda a, b: a >> b,
}


class _Bits:
    """A named register, alias or gate/subroutine argument: a list of bits."""

    __slots__ = ("kind", "bits", "register")

    def __init__(self, kind, bits, register=None):
        self.kind = kind
        self.bits = list(bits)
        self.register = register      # the StaticRegister, for a whole declared register


class _GateDef:
    __slots__ = ("name", "params", "qubits", "body")

    def __init__(self, node):
        self.name = node.name.name
        self.params = [a.name for a in node.arguments]
        self.qubits = [q.name for q in node.qubits]
        self.body = node.body


class _SubroutineDef:
    __slots__ = ("node",)

    def __init__(self, node):
        self.node = node


class _Env:
    """A scope of names: the program, a loop body, a subroutine call."""

    __slots__ = ("names", "parent")

    def __init__(self, parent=None):
        self.names = {}
        self.parent = parent

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env.names:
                return env.names[name]
            env = env.parent
        return None

    def set(self, name, value):
        """Rebind ``name`` where it lives, or bind it here."""
        env = self
        while env is not None:
            if name in env.names:
                env.names[name] = value
                return
            env = env.parent
        self.names[name] = value


class _Return(Exception):
    def __init__(self, value):
        super().__init__()
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


def _location(node, via):
    span = getattr(node, "span", None)
    if span is None:
        return None
    # The reference parser's end column is inclusive; SourceLocation's is not.
    return SourceLocation(span.start_line, span.start_column, span.end_line,
                          span.end_column + 1, via)


class _QasmInterpreter:
    """Walks an OpenQASM syntax tree, building a StaticCircuit."""

    def __init__(self):
        self.circuit = StaticCircuit("circuit")
        self.globals = _Env()
        self.env = self.globals
        self.std_gates = False
        self.warnings = []
        self.notes = []
        self.steps = 0
        self.placed = 0
        self.via = []
        self.depth = 0
        self.node = None                # the statement being run

    # -- bookkeeping --------------------------------------------------------

    def line(self, node=None):
        span = getattr(node if node is not None else self.node, "span", None)
        return span.start_line if span is not None else None

    def error(self, message, node=None):
        return StaticParseError(message, self.line(node))

    def warn(self, message, node=None):
        lineno = self.line(node)
        text = f"line {lineno}: {message}" if lineno else message
        if text not in self.warnings:
            self.warnings.append(text)
            self.notes.append((lineno, message))

    def tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise self.error(f"the program runs too long to analyse (more than {MAX_STEPS} steps)")

    def append(self, operation, qubits, clbits=(), condition=None, condition_bits=()):
        self.placed += 1
        if self.placed > MAX_INSTRUCTIONS:
            raise self.error(f"the program places more than {MAX_INSTRUCTIONS} instructions")
        instruction = StaticInstruction(operation, qubits, clbits, _location(self.node, self.via),
                                        condition, condition_bits)
        self.circuit.append(instruction)
        return instruction

    # -- classical values ---------------------------------------------------

    def value(self, node):
        """A compile-time value: a number, bool, Symbol, or Unknown for run-time values."""
        kind = type(node).__name__
        if kind in ("IntegerLiteral", "FloatLiteral", "BooleanLiteral"):
            return node.value
        if kind == "ImaginaryLiteral":
            return complex(0, node.value)
        if kind == "BitstringLiteral":
            return node.value
        if kind == "DurationLiteral":
            return f"{node.value}{node.unit.name}"
        if kind == "Identifier":
            if node.name in _CONSTANTS and self.env.lookup(node.name) is None:
                return _CONSTANTS[node.name]
            found = self.env.lookup(node.name)
            if found is None:
                raise self.error(f"'{node.name}' is not defined")
            if isinstance(found, _Bits):
                return Unknown(node.name)       # a measured value: known only at run time
            if isinstance(found, (_GateDef, _SubroutineDef)):
                raise self.error(f"'{node.name}' is not a value")
            return found
        if kind == "BinaryExpression":
            return self._binary(node.op.name, self.value(node.lhs), self.value(node.rhs))
        if kind == "UnaryExpression":
            operand = self.value(node.expression)
            if isinstance(operand, Unknown):
                return operand
            op = node.op.name
            if op == "-":
                return _negate(operand)
            if isinstance(operand, Symbol):
                raise self.error(f"operator '{op}' on a parameter is not supported")
            return (not operand) if op == "!" else ~operand
        if kind == "FunctionCall":
            return self.call(node)
        if kind == "Cast":
            operand = self.value(node.argument)
            if isinstance(operand, (Unknown, Symbol)):
                return operand
            target = type(node.type).__name__
            if target in ("IntType", "UintType"):
                return int(operand)
            if target in ("FloatType", "AngleType"):
                return float(operand)
            if target == "BoolType":
                return bool(operand)
            return operand
        if kind == "IndexExpression":
            found = self.value(node.collection)
            return found if isinstance(found, Unknown) else Unknown(kind)
        if kind == "SizeOf":
            target = self.env.lookup(getattr(node.target, "name", ""))
            if isinstance(target, _Bits):
                return len(target.bits)
        raise self.error(f"{kind} expressions are not supported")

    def _binary(self, op, left, right):
        for side in (left, right):
            if isinstance(side, Unknown):
                return side
        if isinstance(left, Symbol) or isinstance(right, Symbol):
            if op in ("+", "-", "*", "/", "**"):
                return _symbolic_binop(op, left, right)
            raise self.error(f"operator '{op}' on a parameter is not supported")
        if op not in _BINARY:
            raise self.error(f"operator '{op}' is not supported")
        if op in ("**", "<<") and isinstance(right, int) and right > 10_000:
            raise self.error("this value is too large to analyse")
        try:
            return _BINARY[op](left, right)
        except ZeroDivisionError:
            raise self.error("division by zero") from None

    def int_value(self, node):
        value = self.value(node)
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, Unknown):
                raise self.error("this index depends on a value only known at run time")
            raise self.error(f"expected an integer, got {value!r}")
        return value

    def param(self, node):
        value = self.value(node)
        if isinstance(value, Unknown):
            raise self.error("this gate parameter depends on a value only known at run time")
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        return value

    # -- quantum and classical operands ---------------------------------------

    def bits(self, node, kind):
        """The bits an operand names, as a list: a register, an index, a range or a set."""
        node_kind = type(node).__name__
        if node_kind == "Identifier":
            if node.name.startswith("$"):
                raise self.error("physical qubits ($n) are not supported")
            base = self._register(node.name, kind)
            return list(base.bits)
        if node_kind == "IndexedIdentifier":
            bits = list(self._register(node.name.name, kind).bits)
            for index in node.indices:
                bits = self._index(bits, index)
            return bits
        if node_kind == "IndexExpression":
            return self._index(self.bits(node.collection, kind), node.index)
        raise self.error(f"expected a {kind} operand")

    def _register(self, name, kind):
        found = self.env.lookup(name)
        if not isinstance(found, _Bits):
            raise self.error(f"'{name}' is not a declared {kind} register")
        if found.kind != kind:
            raise self.error(f"'{name}' holds {found.kind}s, not {kind}s")
        return found

    def _index(self, bits, index):
        if type(index).__name__ == "DiscreteSet":
            return [self._pick(bits, self.int_value(v)) for v in index.values]
        if len(index) != 1:
            raise self.error("multi-dimensional indexing is not supported")
        item = index[0]
        if type(item).__name__ == "RangeDefinition":
            step = 1 if item.step is None else self.int_value(item.step)
            if step == 0:
                raise self.error("a range step cannot be zero")
            start = (0 if step > 0 else len(bits) - 1) if item.start is None else self.int_value(item.start)
            end = (len(bits) - 1 if step > 0 else 0) if item.end is None else self.int_value(item.end)
            positions = range(start, end + (1 if step > 0 else -1), step)   # inclusive
            if len(positions) > MAX_REGISTER:
                raise self.error("this range is too large to analyse")
            return [self._pick(bits, i) for i in positions]
        return [self._pick(bits, self.int_value(item))]

    def _pick(self, bits, position):
        try:
            return bits[position]
        except IndexError:
            raise self.error(f"index {position} is out of range for {len(bits)} bits") from None

    # -- statements ---------------------------------------------------------

    def run(self, program):
        self.block(program.statements)

    def block(self, statements):
        for statement in statements:
            self.statement(statement)

    def statement(self, node):
        self.tick()
        saved, self.node = self.node, node
        try:
            handler = getattr(self, "_s_" + type(node).__name__, None)
            if handler is None:
                raise self.error(f"`{type(node).__name__}` statements are not supported")
            handler(node)
        except CircuitError as exc:
            raise self.error(str(exc)) from None
        finally:
            self.node = saved

    def _s_Include(self, node):
        if node.filename not in _STANDARD_INCLUDES:
            raise self.error(f"include \"{node.filename}\" cannot be resolved; only "
                             "stdgates.inc and qelib1.inc are available")
        self.std_gates = True

    def _s_Pragma(self, node):
        pass

    _s_CalibrationGrammarDeclaration = _s_Pragma

    def _s_CalibrationStatement(self, node):
        self.warn("calibration blocks are ignored")

    _s_CalibrationDefinition = _s_CalibrationStatement

    def _s_QubitDeclaration(self, node):
        self._declare(node.qubit.name, "qubit", node.size)

    def _declare(self, name, kind, size_node):
        size = 1 if size_node is None else self.int_value(size_node)
        if not 0 <= size <= MAX_REGISTER:
            raise self.error(f"register size {size} is out of range")
        if name in self.env.names:
            raise self.error(f"'{name}' is already declared")
        register = StaticRegister(kind, name, size)
        self.circuit.add_register(register)
        self.env.names[name] = _Bits(kind, register.bits, register)

    def _s_ClassicalDeclaration(self, node):
        name = node.identifier.name
        if type(node.type).__name__ == "BitType":
            self._declare(name, "clbit", node.type.size)
            if node.init_expression is not None:
                self._assign_bits(self.env.lookup(name).bits, node.init_expression)
            return
        value = Unknown(name) if node.init_expression is None else self._init(node.init_expression)
        self.env.names[name] = value

    def _init(self, expression):
        if type(expression).__name__ == "QuantumMeasurement":
            raise self.error("measuring into a non-bit variable is not supported")
        return self.value(expression)

    def _s_ConstantDeclaration(self, node):
        value = self.value(node.init_expression)
        if isinstance(value, Unknown):
            raise self.error("a const must be known at compile time")
        self.env.names[node.identifier.name] = value

    def _s_IODeclaration(self, node):
        name = node.identifier.name
        if node.io_identifier.name == "input":
            self.env.names[name] = Symbol.leaf(name)      # a parameter of the program
        else:
            self.env.names[name] = Unknown(name)

    def _s_AliasStatement(self, node):
        value = node.value
        kind = "qubit"
        root = value
        while type(root).__name__ in ("IndexExpression",):
            root = root.collection
        if type(root).__name__ == "Identifier":
            found = self.env.lookup(root.name)
            if isinstance(found, _Bits):
                kind = found.kind
        if type(value).__name__ == "Concatenation":
            bits = self.bits(value.lhs, kind) + self.bits(value.rhs, kind)
        else:
            bits = self.bits(value, kind)
        self.env.names[node.target.name] = _Bits(kind, bits)

    def _s_ClassicalAssignment(self, node):
        target = node.lvalue
        root = target.name if type(target).__name__ == "IndexedIdentifier" else target
        found = self.env.lookup(getattr(root, "name", ""))
        if isinstance(found, _Bits):
            self._assign_bits(self.bits(target, "clbit"), node.rvalue)
            return
        if type(target).__name__ != "Identifier":
            raise self.error("assigning to an element of a classical array is not supported")
        op = node.op.name
        value = self.value(node.rvalue)
        if op != "=":
            current = self.env.lookup(target.name)
            value = self._binary(op[:-1], current, value)
        self.env.set(target.name, value)

    def _assign_bits(self, clbits, expression):
        if type(expression).__name__ != "QuantumMeasurement":
            raise self.error("writing classical bits other than by measurement is not supported")
        self._measure(self.bits(expression.qubit, "qubit"), clbits)

    def _s_QuantumMeasurementStatement(self, node):
        if node.target is None:
            raise self.error("a measurement without a target bit is not supported")
        self._measure(self.bits(node.measure.qubit, "qubit"), self.bits(node.target, "clbit"))

    def _measure(self, qubits, clbits):
        gate = StaticGate("measure", 1, 1)
        if len(qubits) != len(clbits):
            raise self.error(f"measuring {len(qubits)} qubits into {len(clbits)} bits")
        for q, c in broadcast(gate, [qubits, clbits]):
            self.append(gate, q, c)

    def _s_QuantumReset(self, node):
        gate = StaticGate("reset", 1)
        for qubits, _ in broadcast(gate, [self.bits(node.qubits, "qubit")]):
            self.append(gate, qubits)

    def _s_QuantumBarrier(self, node):
        if not node.qubits:
            bits = list(self.circuit.qubits)
        else:
            bits = []
            for operand in node.qubits:
                for bit in self.bits(operand, "qubit"):
                    if not any(bit is b for b in bits):
                        bits.append(bit)
        self.append(StaticGate("barrier", len(bits)), bits)

    def _s_DelayInstruction(self, node):
        duration = self.value(node.duration)
        gate = StaticGate("delay", 1, 0, [duration])
        targets = node.qubits or [None]
        for operand in targets:
            bits = list(self.circuit.qubits) if operand is None else self.bits(operand, "qubit")
            for qubits, _ in broadcast(gate, [bits]):
                self.append(gate, qubits)

    def _s_QuantumPhase(self, node):
        if node.modifiers:
            raise self.error("modifiers on gphase are not supported")
        # A global phase changes no qubit's dependencies; Qiskit records none either.

    def _s_QuantumGateDefinition(self, node):
        self.globals.names[node.name.name] = _GateDef(node)

    def _s_SubroutineDefinition(self, node):
        self.globals.names[node.name.name] = _SubroutineDef(node)

    def _s_ExpressionStatement(self, node):
        self.value(node.expression)

    def _s_ReturnStatement(self, node):
        raise _Return(None if node.expression is None else self.value(node.expression))

    def _s_BreakStatement(self, node):
        raise _Break()

    def _s_ContinueStatement(self, node):
        raise _Continue()

    def _s_Box(self, node):
        raise self.error("box blocks are not supported")

    # -- gates --------------------------------------------------------------

    def _s_QuantumGate(self, node):
        name = node.name.name
        params = [self.param(a) for a in node.arguments]
        gate = self._gate(name, params)
        for modifier in reversed(node.modifiers):
            gate = self._modify(gate, modifier)
        operands = [self.bits(q, "qubit") for q in node.qubits]
        if len(operands) != gate.num_qubits:
            raise self.error(f"'{name}' acts on {gate.num_qubits} qubits, "
                             f"but {len(operands)} were given")
        for qubits, _ in broadcast(gate, operands):
            self.append(gate, qubits)

    def _gate(self, name, params):
        defined = self.globals.lookup(name)
        if isinstance(defined, _GateDef):
            if len(params) != len(defined.params):
                raise self.error(f"'{name}' takes {len(defined.params)} parameters, "
                                 f"got {len(params)}")
            return StaticGate(name, len(defined.qubits), 0, params)
        builtin = name in ("U", "CX")
        if name not in _GATES or not (builtin or self.std_gates):
            hint = "" if self.std_gates or name not in _GATES else " (include \"stdgates.inc\")"
            raise self.error(f"gate '{name}' is not defined{hint}")
        gate_name, num_params, num_qubits = _GATES[name]
        if len(params) != num_params:
            raise self.error(f"'{name}' takes {num_params} parameters, got {len(params)}")
        if gate_name in ("mcx",):
            return StaticGate(_mcx_name(num_qubits - 1), num_qubits)
        return StaticGate(gate_name, num_qubits, 0, params)

    def _modify(self, gate, modifier):
        kind = modifier.modifier.name
        count = 1 if modifier.argument is None else self.int_value(modifier.argument)
        if kind == "inv":
            return gate.inverse()
        if kind == "pow":
            power = self.value(modifier.argument)
            if power == 1:
                return gate
            self.warn(f"pow({power}) @ {gate.name} is kept as one instruction")
            return StaticGate(f"{gate.name}_pow", gate.num_qubits, 0, list(gate.params))
        if count < 1:
            raise self.error(f"{kind}({count}) needs at least one control")
        controlled = gate.control(count)
        if kind == "negctrl":
            # Qiskit names an open-controlled gate by its control state: cx_o0.
            controlled.name = f"{controlled.name}_o0"
        return controlled

    # -- branches and loops -------------------------------------------------

    def _s_BranchingStatement(self, node):
        static = self._static_truth(node.condition)
        if static is not None:
            self.block(node.if_block if static else node.else_block)
            return
        target, value, condition_bits = self._condition(node.condition)
        circuit = self.circuit
        bodies = []
        for block in (node.if_block, node.else_block):
            scope = _Scope()
            circuit._scopes.append(scope)
            try:
                self.block(block)
            finally:
                circuit._scopes.pop()
            bodies.append(scope)
        qubits, clbits = [], list(condition_bits)
        for scope in bodies:
            qubits += [b for b in scope.qubits if not any(b is q for q in qubits)]
            clbits += [b for b in scope.clbits if not any(b is c for c in clbits)]
        true_body = StaticCircuit.body(bodies[0].instructions, qubits, clbits)
        false_body = (StaticCircuit.body(bodies[1].instructions, qubits, clbits)
                      if node.else_block else None)
        self.append(StaticGate("if_else", len(qubits), len(clbits), [true_body, false_body]),
                    qubits, clbits, (target, value), condition_bits)

    def _static_truth(self, condition):
        """The condition's truth when it is known at compile time, else None."""
        try:
            value = self.value(condition)
        except StaticParseError:
            return None
        if isinstance(value, (Unknown, Symbol)):
            return None
        return bool(value)

    def _condition(self, node):
        """(target, value, bits) for a condition on measured bits."""
        kind = type(node).__name__
        if kind == "UnaryExpression" and node.op.name == "!":
            target, value, bits = self._condition(node.expression)
            if isinstance(value, bool):
                return target, not value, bits
        elif kind in ("Identifier", "IndexExpression", "IndexedIdentifier"):
            bits = self.bits(node, "clbit")
            if len(bits) == 1:
                return bits[0], True, bits
        elif kind == "BinaryExpression" and node.op.name == "==":
            for operand, constant in ((node.lhs, node.rhs), (node.rhs, node.lhs)):
                if type(operand).__name__ in ("Identifier", "IndexExpression", "IndexedIdentifier"):
                    found = self.env.lookup(getattr(operand, "name", None) or
                                            getattr(operand.collection, "name", ""))
                    if isinstance(found, _Bits) and found.kind == "clbit":
                        value = self.value(constant)
                        if isinstance(value, (Unknown, Symbol)):
                            break
                        bits = self.bits(operand, "clbit")
                        whole = (type(operand).__name__ == "Identifier"
                                 and found.register is not None and len(bits) > 1)
                        if whole:
                            return found.register, int(value), bits
                        if len(bits) == 1:
                            return bits[0], (bool(value) if isinstance(value, bool) else int(value)), bits
        raise self.error("only conditions that compare a bit or a bit register with a constant "
                         "are supported")

    def _s_ForInLoop(self, node):
        values = self._loop_values(node.set_declaration)
        env = _Env(self.env)
        saved, self.env = self.env, env
        try:
            for value in values:
                self.tick()
                env.names[node.identifier.name] = value
                try:
                    self.block(node.block)
                except _Break:
                    break
                except _Continue:
                    continue
        finally:
            self.env = saved

    def _loop_values(self, declaration):
        kind = type(declaration).__name__
        if kind == "RangeDefinition":
            start = self.int_value(declaration.start)
            end = self.int_value(declaration.end)
            step = 1 if declaration.step is None else self.int_value(declaration.step)
            if step == 0:
                raise self.error("a range step cannot be zero")
            values = range(start, end + (1 if step > 0 else -1), step)       # inclusive
            if len(values) > MAX_STEPS:
                raise self.error("this loop runs too long to analyse")
            return values
        if kind == "DiscreteSet":
            return [self.value(v) for v in declaration.values]
        raise self.error("only loops over a range or a set of values are supported")

    def _s_WhileLoop(self, node):
        while True:
            truth = self._static_truth(node.while_condition)
            if truth is None:
                raise self.error("this while loop depends on a value only known at run time")
            if not truth:
                return
            self.tick()
            try:
                self.block(node.block)
            except _Break:
                return
            except _Continue:
                continue

    # -- subroutines --------------------------------------------------------

    def call(self, node):
        name = node.name.name
        defined = self.globals.lookup(name)
        if isinstance(defined, _SubroutineDef):
            return self._call_subroutine(defined.node, node)
        args = [self.value(a) for a in node.arguments]
        if name not in _FUNCTIONS:
            raise self.error(f"function '{name}' is not defined")
        if any(isinstance(a, Unknown) for a in args):
            return next(a for a in args if isinstance(a, Unknown))
        if any(isinstance(a, Symbol) for a in args):
            if len(args) == 1 and name in ("sin", "cos", "tan", "exp", "sqrt"):
                return _symbolic_call(name, args[0])
            raise self.error(f"{name}() of a parameter is not supported")
        return _FUNCTIONS[name](*args)

    def _call_subroutine(self, definition, call):
        if self.depth >= MAX_CALL_DEPTH:
            raise self.error(f"subroutine calls nest more than {MAX_CALL_DEPTH} deep")
        if len(call.arguments) != len(definition.arguments):
            raise self.error(f"'{definition.name.name}' takes {len(definition.arguments)} "
                             f"arguments, got {len(call.arguments)}")
        env = _Env(self.globals)
        for parameter, argument in zip(definition.arguments, call.arguments):
            if type(parameter).__name__ == "QuantumArgument":
                env.names[parameter.name.name] = _Bits("qubit", self.bits(argument, "qubit"))
            else:
                env.names[parameter.name.name] = self.value(argument)
        saved = self.env
        self.env = env
        self.depth += 1
        self.via.append(self.line())
        try:
            self.block(definition.body)
        except _Return as result:
            return result.value
        finally:
            self.env = saved
            self.depth -= 1
            self.via.pop()
        return None


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

class StaticOpenQASMParser(AbstractParser):
    """
    Parser for OpenQASM 2/3 programs given as text, interpreted statically.

    Expected source type:
        str (OpenQASM source)

    Attributes after parsing:
        program:  the QuantumProgram IR, every instruction with its SourceLocation
        circuit:  the StaticCircuit it was built from
        warnings: what was ignored or approximated, as "line N: ..." strings
        notes:    the same, as (lineno, message) pairs
    """

    def __init__(self, source: str = None) -> None:
        """Initialize the parser, optionally parsing the source immediately."""
        super().__init__(source)
        self.circuit = None
        self.warnings = []
        self.notes = []
        if source is not None:
            self.parse()

    def parse(self) -> QuantumProgram:
        """Interpret the contained OpenQASM source and build the QuantumProgram IR."""
        self.preprocess()
        if openqasm3 is None:
            raise ValueError("StaticOpenQASMParser needs the openqasm3 package "
                             "(pip install openqasm3[parser])")
        if not isinstance(self.source, str):
            raise ValueError("StaticOpenQASMParser expects OpenQASM source as a string")
        tree = _parse_syntax(self.source)
        interpreter = _QasmInterpreter()
        try:
            interpreter.run(tree)
        except RecursionError:
            raise StaticParseError("the program nests too deeply to analyse") from None
        except (_Break, _Continue):
            raise StaticParseError("'break' or 'continue' outside a loop") from None
        except _Return:
            raise StaticParseError("'return' outside a subroutine") from None
        finally:
            self.warnings = interpreter.warnings
            self.notes = interpreter.notes
        self.circuit = interpreter.circuit
        self.program = circuit_to_program(self.circuit, "openqasm-static")
        self.validate()
        return self.program

    def parse_file(self, path: str) -> QuantumProgram:
        """Read an OpenQASM file and build its IR."""
        with open(path, encoding="utf-8-sig") as handle:
            self.source = handle.read()
        return self.parse()


def _parse_syntax(source):
    """The reference parser's syntax tree, or a StaticParseError with the line."""
    try:
        return openqasm3.parse(source)
    except Exception as exc:          # QASM3ParsingError, and antlr's own errors
        message, lineno = str(exc), None
        found = re.match(r"L(\d+):C\d+: (.*)", message)
        if found:
            lineno, message = int(found.group(1)), found.group(2)
        # With the bail-out strategy the parser raises an empty error; the
        # ANTLR exception it chains to still knows the offending token.
        cause = exc.__cause__
        while lineno is None and cause is not None:
            token = getattr(cause, "offendingToken", None)
            for arg in getattr(cause, "args", ()):
                token = token or getattr(arg, "offendingToken", None)
            if token is not None:
                lineno = token.line
                text = token.text if token.text != "<EOF>" else "the end of the file"
                message = f"unexpected {text!r} at column {token.column + 1}"
            cause = cause.__cause__
        raise StaticParseError(f"OpenQASM syntax error: {message or 'invalid syntax'}",
                               lineno) from None
