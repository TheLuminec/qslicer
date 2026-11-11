"""Static OpenQASM front end: OpenQASM 2/3 text to the IR, with source locations.

``OpenQASMParser`` goes through ``qiskit.qasm3.load``: the text is parsed, a
QuantumCircuit is built, and the circuit goes through QiskitParser. Nothing is
executed, but where each instruction came from is lost on the way, and
anything the converter cannot build (indexing a register with a loop variable,
OpenQASM 2, classical variables, subroutines) fails the whole file.

``StaticOpenQASMParser`` takes the syntax tree of the OpenQASM reference parser
(the ``openqasm3`` package, whose nodes carry line/column spans) and interprets
it itself, building the same circuit model StaticQiskitParser uses. Every
instruction keeps the span of the statement that produced it, and the call
path through any subroutine that led there.

Understood:

* OpenQASM 3 and 2: ``qubit``/``bit`` and ``qreg``/``creg`` declarations,
  physical qubits (``$0``), ``stdgates.inc``/``qelib1.inc`` gates, ``U``/``CX``,
  ``gate`` and OpenQASM 2 ``opaque`` definitions (kept as one instruction each,
  as Qiskit does), the ``ctrl``/``negctrl``/``inv``/``pow`` modifiers, also on
  ``gphase``, register broadcasting, ranges and index sets;
* ``measure`` in both syntaxes and without a target, ``reset``, ``barrier``,
  ``delay``, ``box`` (its contents are placed as ordinary instructions);
* classical values: ``const``, scalar variables, arrays, ``input``
  parameters (kept symbolic), ``extern`` functions, arithmetic and the
  built-in functions. Bits written with a constant are tracked at compile
  time; a value copied from a measured bit becomes a ``store`` instruction, so
  the dependency survives. Measuring into a ``bool``/``int`` variable gives it
  a classical register of its own;
* ``if``/``else``, ``switch`` and ``while`` on run-time values become one
  ``if_else``/``switch_case``/``while_loop`` instruction each, as Qiskit
  builds them, with the bits the condition reads. On compile-time values the
  branch is simply taken and the loop unrolled;
* ``for`` loops are unrolled, so each iteration's gates are their own nodes. A
  loop that a ``break``/``continue`` under a run-time condition may leave
  early becomes one ``for_loop`` instruction instead;
* subroutines (``def``), inlined, including ``return measure q;``.

Refused, with the line: includes other than the standard two, and the few
shapes that would need a run-time value to place a gate (a qubit index or a
gate parameter computed from a measurement, a ``return`` under a run-time
condition).
"""

from ..ir.qinstruction import SourceLocation
from ..ir.qprogram import QuantumProgram
from .base import AbstractParser

import math
import re
import time

from .staticqiskitparser import (
    MAX_INSTRUCTIONS, MAX_STEPS, CircuitError, StaticCircuit, StaticGate,
    StaticInstruction, StaticParseError, StaticRegister, Symbol, Unknown, _Scope,
    _mcx_name, _negate, _symbolic_binop, _symbolic_call, broadcast, circuit_to_program, SWITCH_HAS_DEFAULT)

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
    "real": lambda z: complex(z).real, "imag": lambda z: complex(z).imag,
    "popcount": lambda v: bin(int(v) & ((1 << v.width) - 1) if isinstance(v, _Word)
                              else int(v)).count("1"),
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
_ARITHMETIC = ("+", "-", "*", "/", "**")

# OpenQASM 2's `opaque name(params) args;` declares a gate with no body. The
# OpenQASM 3 grammar has no such statement, so it is read as an empty `gate`.
_OPAQUE = re.compile(r"^([ \t]*)opaque\b([^;{]*);", re.MULTILINE)
_VERSION_2 = re.compile(r"\A(?:\s|//[^\n]*\n|/\*.*?\*/)*OPENQASM\s+2", re.DOTALL)


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


class _Extern:
    """An ``extern`` function: its result is only known at run time."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


class _Dyn:
    """A classical value only known at run time, and the clbits it was computed from.

    Where an Unknown says only "a run would decide this", a _Dyn also records
    which measured bits it reads, so a condition or a copy built from it gets
    the right dependencies.
    """

    __slots__ = ("bits",)

    def __init__(self, bits=()):
        self.bits = _unique(bits)

    def __repr__(self):
        return f"<run-time value of {self.bits}>"


class _Measured:
    """The result of a ``measure`` that has not been stored anywhere yet."""

    __slots__ = ("qubits",)

    def __init__(self, qubits):
        self.qubits = list(qubits)


class _Word(int):
    """A bit-string value that remembers its width, for ``rotl``/``rotr``."""

    def __new__(cls, value, width):
        word = super().__new__(cls, value)
        word.width = width
        return word


def _unique(bits):
    out = []
    for bit in bits:
        if not any(bit is b for b in out):
            out.append(bit)
    return out


def _runtime(value):
    return isinstance(value, (_Dyn, Unknown))


def _reads(*values):
    """The clbits a set of values was computed from."""
    bits = []
    for value in values:
        if isinstance(value, _Dyn):
            bits += value.bits
        elif isinstance(value, list):
            bits += _reads(*value)
    return _unique(bits)


def _rotate(value, shift, left):
    if not isinstance(value, _Word):
        raise ValueError("needs a bit-string value, whose width is known")
    width = value.width
    shift %= width if width else 1
    mask = (1 << width) - 1
    if not left:
        shift = (width - shift) % width if width else 0
    return _Word(((value << shift) | (value >> (width - shift))) & mask, width)


def _same(a, b):
    """Whether two compile-time values are certainly equal."""
    if a is b:
        return True
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    plain = (int, float, complex, str)
    if isinstance(a, plain) and isinstance(b, plain):
        return a == b
    return False


def _blend(values, reads):
    """What a variable holds after paths that left it with different values."""
    if all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
        return [_blend(list(column), reads) for column in zip(*values)]
    if all(_same(v, values[0]) for v in values[1:]):
        return values[0]
    return _Dyn(list(reads) + _reads(*values))


def _copy_value(value):
    return [_copy_value(v) for v in value] if isinstance(value, list) else value


class _Env:
    """A scope of names: the program, a block, a loop body, a subroutine call."""

    __slots__ = ("names", "parent")

    def __init__(self, parent=None):
        self.names = {}
        self.parent = parent

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env.names:
                return env.names[name]
            if isinstance(env, _Overlay) and name in env.assigned:
                return env.assigned[name]
            env = env.parent
        return None

    def set(self, name, value):
        """Rebind ``name`` where it lives, or bind it here."""
        env = self
        while env is not None:
            if name in env.names:
                env.names[name] = value
                return
            if isinstance(env, _Overlay) and env.outer(name):
                env.assigned[name] = value
                return
            env = env.parent
        self.names[name] = value


class _Overlay(_Env):
    """One path through run-time control flow.

    Assignments to names that live outside are kept here instead, so the
    paths can be compared afterwards: a name every path left equal keeps its
    value, and one they disagree on becomes a run-time value.
    """

    __slots__ = ("assigned",)

    def __init__(self, parent):
        super().__init__(parent)
        self.assigned = {}

    def outer(self, name):
        return name in self.assigned or (self.parent is not None
                                         and self.parent.lookup(name) is not None)


class _Return(Exception):
    def __init__(self, value):
        super().__init__()
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _End(Exception):
    pass


class _DynamicExit(Exception):
    """A break/continue under a run-time condition, in a loop being unrolled."""


class _Path:
    """What running one run-time path produced."""

    __slots__ = ("scope", "overlay", "reads")

    def __init__(self, scope, overlay, reads):
        self.scope = scope
        self.overlay = overlay
        self.reads = reads


def _location(node, via):
    span = getattr(node, "span", None)
    if span is None:
        return None
    # The reference parser's end column is inclusive; SourceLocation's is not.
    return SourceLocation(span.start_line, span.start_column, span.end_line,
                          span.end_column + 1, via)


def _physical_count(tree):
    """One more than the highest physical qubit ($n) the program names."""
    highest = -1
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, qasm_ast.QASMNode):
            if isinstance(node, qasm_ast.Identifier) and node.name.startswith("$"):
                if node.name[1:].isdigit():
                    highest = max(highest, int(node.name[1:]))
            stack.extend(v for k, v in vars(node).items() if k != "span")
    return highest + 1


class _QasmInterpreter:
    """Walks an OpenQASM syntax tree, building a StaticCircuit."""

    def __init__(self, physical=0, time_limit=None):
        self.circuit = StaticCircuit("circuit")
        self.deadline = None if time_limit is None else time.monotonic() + time_limit
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
        self.known = {}                 # clbit -> 0/1, for bits holding a compile-time value
        self.loops = []                 # "unrolled" / "opaque", innermost last
        self.trackers = []              # lists collecting the clbits run-time code reads
        self.physical_size = physical
        self.physical = None            # the register for $n qubits, made on first use
        self.hidden = 0                 # registers made for measured values

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
        if self.deadline is not None and not self.steps % 1024 and time.monotonic() > self.deadline:
            raise self.error("the program runs too long to analyse (time limit reached)")

    def append(self, operation, qubits, clbits=(), condition=None, condition_bits=()):
        self.placed += 1
        if self.placed > MAX_INSTRUCTIONS:
            raise self.error(f"the program places more than {MAX_INSTRUCTIONS} instructions")
        instruction = StaticInstruction(operation, qubits, clbits, _location(self.node, self.via),
                                        condition, condition_bits)
        self.circuit.append(instruction)
        return instruction

    def note_reads(self, bits):
        for tracker in self.trackers:
            tracker.extend(bits)

    def checkpoint(self):
        """Enough state to undo an attempt at unrolling a loop."""
        circuit = self.circuit
        scope = circuit._scopes[-1] if circuit._scopes else None
        envs = []
        env = self.env
        while env is not None:
            envs.append((env, {k: _copy_value(v) for k, v in env.names.items()},
                         dict(env.assigned) if isinstance(env, _Overlay) else None))
            env = env.parent
        return {
            "block": len(circuit.current_block()),
            "scope": (dict(scope.qubits), dict(scope.clbits)) if scope is not None else None,
            "bits": (len(circuit.qubits), len(circuit.clbits),
                     len(circuit.qregs), len(circuit.cregs)),
            "known": dict(self.known), "envs": envs, "placed": self.placed,
            "messages": (len(self.warnings), len(self.notes)),
            "physical": self.physical, "hidden": self.hidden,
        }

    def restore(self, saved):
        circuit = self.circuit
        del circuit.current_block()[saved["block"]:]
        if saved["scope"] is not None:
            scope = circuit._scopes[-1]
            scope.qubits, scope.clbits = dict(saved["scope"][0]), dict(saved["scope"][1])
        nq, nc, nqr, ncr = saved["bits"]
        for bit in circuit.qubits[nq:]:
            del circuit._qindex[bit]
        for bit in circuit.clbits[nc:]:
            del circuit._cindex[bit]
        del circuit.qubits[nq:], circuit.clbits[nc:], circuit.qregs[nqr:], circuit.cregs[ncr:]
        self.known = dict(saved["known"])
        for env, names, assigned in saved["envs"]:
            env.names = {k: _copy_value(v) for k, v in names.items()}
            if assigned is not None:
                env.assigned = dict(assigned)
        self.placed = saved["placed"]
        del self.warnings[saved["messages"][0]:], self.notes[saved["messages"][1]:]
        self.physical, self.hidden = saved["physical"], saved["hidden"]

    # -- classical values ---------------------------------------------------

    def value(self, node, measure=False):
        """A compile-time value: a number, bool, list, Symbol, or _Dyn/Unknown for run-time values.

        With ``measure``, a measurement nobody has stored yet comes back as a
        _Measured for the caller to place; otherwise it is measured into a
        register of its own here.
        """
        result = self._value(node)
        if isinstance(result, _Measured) and not measure:
            result = self._bits_value(self.hidden_register("measure", result))
        return result

    def _value(self, node):
        kind = type(node).__name__
        if kind in ("IntegerLiteral", "FloatLiteral", "BooleanLiteral"):
            return node.value
        if kind == "ImaginaryLiteral":
            return complex(0, node.value)
        if kind == "BitstringLiteral":
            return _Word(node.value, node.width)
        if kind == "DurationLiteral":
            return f"{node.value}{node.unit.name}"
        if kind == "Identifier":
            if node.name in _CONSTANTS and self.env.lookup(node.name) is None:
                return _CONSTANTS[node.name]
            found = self.env.lookup(node.name)
            if found is None:
                raise self.error(f"'{node.name}' is not defined")
            if isinstance(found, _Bits):
                if found.kind == "qubit":
                    raise self.error(f"'{node.name}' is a qubit, not a classical value")
                return self._bits_value(found.bits)
            if isinstance(found, (_GateDef, _SubroutineDef, _Extern)):
                raise self.error(f"'{node.name}' is not a value")
            return found
        if kind == "QuantumMeasurement":
            return _Measured(self.bits(node.qubit, "qubit"))
        if kind == "BinaryExpression":
            return self._binary(node.op.name, self.value(node.lhs), self.value(node.rhs))
        if kind == "UnaryExpression":
            operand = self.value(node.expression)
            op = node.op.name
            if _runtime(operand):
                return _Dyn(_reads(operand))
            if op == "-":
                return _negate(operand)
            if isinstance(operand, Symbol):
                return _Dyn()
            if op == "!":
                return not operand
            if isinstance(operand, _Word):
                return _Word(~operand & ((1 << operand.width) - 1), operand.width)
            return ~operand
        if kind == "FunctionCall":
            return self.call(node)
        if kind == "Cast":
            operand = self.value(node.argument)
            if _runtime(operand) or isinstance(operand, Symbol):
                return operand
            target = type(node.type).__name__
            if target in ("IntType", "UintType"):
                return int(operand)
            if target in ("FloatType", "AngleType"):
                return float(operand)
            if target == "BoolType":
                return bool(operand)
            if target == "BitType":
                width = self.int_value(node.type.size) if node.type.size is not None else 1
                return _Word(int(operand) & ((1 << width) - 1), width) if width > 1 else bool(operand)
            return operand
        if kind == "IndexExpression":
            return self._index_value(node)
        if kind == "ArrayLiteral":
            return [self.value(v) for v in node.values]
        if kind == "SizeOf":
            return self._sizeof(node)
        if kind == "DurationOf":
            return Unknown("durationof")
        raise self.error(f"{kind} expressions are not supported")

    def _bits_value(self, bits):
        """The value a run of clbits holds: a constant when every bit is known."""
        unknown = [b for b in bits if b not in self.known]
        if unknown:
            return _Dyn(unknown)
        value = sum(self.known[b] << i for i, b in enumerate(bits))
        return bool(value) if len(bits) == 1 else _Word(value, len(bits))

    def _index_value(self, node):
        root = node.collection
        while type(root).__name__ == "IndexExpression":
            root = root.collection
        if type(root).__name__ == "Identifier" and isinstance(self.env.lookup(root.name), _Bits):
            return self._bits_value(self.bits(node, "clbit"))
        collection = self.value(node.collection)
        if _runtime(collection):
            return _Dyn(_reads(collection))
        positions = node.index
        if type(positions).__name__ == "DiscreteSet" or any(
                type(p).__name__ == "RangeDefinition" for p in positions):
            raise self.error("slicing a classical value is not supported")
        for position in positions:
            index = self.int_value(position)
            if isinstance(collection, list):
                try:
                    collection = collection[index]
                except IndexError:
                    raise self.error(f"index {index} is out of range for {len(collection)} "
                                     "elements") from None
            elif isinstance(collection, int) and not isinstance(collection, bool):
                collection = bool((collection >> index) & 1)
            else:
                raise self.error("only arrays and integers can be indexed")
        return collection

    def _sizeof(self, node):
        target = self.value(node.target) if type(node.target).__name__ != "Identifier" else \
            self.env.lookup(node.target.name)
        dimension = 0 if node.index is None else self.int_value(node.index)
        if isinstance(target, _Bits):
            return len(target.bits)
        for _ in range(dimension):
            if not isinstance(target, list) or not target:
                raise self.error("sizeof() dimension is out of range")
            target = target[0]
        if isinstance(target, list):
            return len(target)
        raise self.error("sizeof() needs an array or a register")

    def _binary(self, op, left, right):
        if _runtime(left) or _runtime(right):
            return _Dyn(_reads(left, right))
        if isinstance(left, Symbol) or isinstance(right, Symbol):
            if op in _ARITHMETIC:
                return _symbolic_binop(op, left, right)
            return _Dyn()        # a comparison on a program input: decided at run time
        if op not in _BINARY:
            raise self.error(f"operator '{op}' is not supported")
        if op in ("**", "<<") and isinstance(right, int) and right > 10_000:
            raise self.error("this value is too large to analyse")
        try:
            return _BINARY[op](left, right)
        except ZeroDivisionError:
            raise self.error("division by zero") from None
        except TypeError:
            raise self.error(f"operator '{op}' does not apply to these values") from None

    def int_value(self, node):
        value = self.value(node)
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if _runtime(value):
                raise self.error("this index depends on a value only known at run time")
            raise self.error(f"expected an integer, got {value!r}")
        return int(value)

    def param(self, node):
        value = self.value(node)
        if _runtime(value):
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
                if kind != "qubit":
                    raise self.error(f"'{node.name}' is a physical qubit, not a {kind}")
                return [self._physical(node.name)]
            base = self._register(node.name, kind)
            return list(base.bits)
        if node_kind == "IndexedIdentifier":
            bits = list(self._register(node.name.name, kind).bits)
            for index in node.indices:
                bits = self._index(bits, index)
            return bits
        if node_kind == "IndexExpression":
            return self._index(self.bits(node.collection, kind), node.index)
        if node_kind == "Concatenation":
            return self.bits(node.lhs, kind) + self.bits(node.rhs, kind)
        raise self.error(f"expected a {kind} operand")

    def _physical(self, name):
        if not name[1:].isdigit():
            raise self.error(f"'{name}' is not a physical qubit")
        if self.physical is None:
            self.physical = StaticRegister("qubit", "$", self.physical_size)
            self.circuit.add_register(self.physical)
        return self.physical.bits[int(name[1:])]

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
        try:
            self.block(program.statements)
        except _End:
            pass

    def block(self, statements):
        for statement in statements:
            self.statement(statement)

    def child_block(self, statements):
        """Run a block in a scope of its own, so its declarations end with it."""
        saved, self.env = self.env, _Env(self.env)
        try:
            self.block(statements)
        finally:
            self.env = saved

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

    def _s_CompoundStatement(self, node):
        self.child_block(node.statements)

    def _s_Box(self, node):
        # A box only constrains scheduling; what it contains runs as written.
        self.child_block(node.body)

    def _s_EndStatement(self, node):
        if self.circuit._scopes:
            raise self.error("'end' under a run-time condition is not supported")
        raise _End()

    def _s_QubitDeclaration(self, node):
        self._declare(node.qubit.name, "qubit", self._size(node.size))

    def _size(self, size_node):
        return 1 if size_node is None else self.int_value(size_node)

    def _declare(self, name, kind, size):
        if not 0 <= size <= MAX_REGISTER:
            raise self.error(f"register size {size} is out of range")
        if name in self.env.names:
            raise self.error(f"'{name}' is already declared")
        register = StaticRegister(kind, name, size)
        self.circuit.add_register(register)
        self.env.names[name] = _Bits(kind, register.bits, register)
        return register

    def hidden_register(self, name, measured):
        """Measure into a classical register made for the purpose; its bits."""
        self.hidden += 1
        register = StaticRegister("clbit", name, len(measured.qubits))
        self.circuit.add_register(register)
        self._measure(measured.qubits, register.bits)
        return register.bits

    def _s_ClassicalDeclaration(self, node):
        name = node.identifier.name
        type_name = type(node.type).__name__
        init = node.init_expression
        if type_name == "BitType":
            register = self._declare(name, "clbit", self._size(node.type.size))
            if init is not None:
                self._assign_bits(register.bits, init)
            return
        if init is None:
            value = (self._unknown_array(name, node.type.dimensions) if type_name == "ArrayType"
                     else Unknown(name))
        else:
            value = self.value(init, measure=True)
            if isinstance(value, _Measured):
                self.env.names[name] = _Bits("clbit", self.hidden_register(name, value))
                return
        self.env.names[name] = value

    def _unknown_array(self, name, dimensions):
        sizes = [self.int_value(d) for d in dimensions]
        if any(not 0 <= s <= MAX_REGISTER for s in sizes):
            raise self.error("array size is out of range")

        def build(level):
            if level == len(sizes):
                return Unknown(name)
            return [build(level + 1) for _ in range(sizes[level])]
        return build(0)

    def _s_ConstantDeclaration(self, node):
        value = self.value(node.init_expression)
        if _runtime(value):
            raise self.error("a const must be known at compile time")
        self.env.names[node.identifier.name] = value

    def _s_IODeclaration(self, node):
        name = node.identifier.name
        if type(node.type).__name__ == "BitType":
            self._declare(name, "clbit", self._size(node.type.size))
        elif node.io_identifier.name == "input":
            self.env.names[name] = Symbol.leaf(name)      # a parameter of the program
        else:
            self.env.names[name] = Unknown(name)

    def _s_ExternDeclaration(self, node):
        self.globals.names[node.name.name] = _Extern(node.name.name)

    def _s_AliasStatement(self, node):
        value = node.value
        kind = "qubit"
        root = value
        while type(root).__name__ in ("IndexExpression", "Concatenation"):
            root = root.collection if type(root).__name__ == "IndexExpression" else root.lhs
        if type(root).__name__ == "Identifier":
            found = self.env.lookup(root.name)
            if isinstance(found, _Bits):
                kind = found.kind
        self.env.names[node.target.name] = _Bits(kind, self.bits(value, kind))

    def _s_ClassicalAssignment(self, node):
        target = node.lvalue
        indexed = type(target).__name__ == "IndexedIdentifier"
        name = target.name.name if indexed else target.name
        op = node.op.name
        found = self.env.lookup(name)
        if isinstance(found, _Bits):
            bits = self.bits(target, "clbit")
            if op == "=":
                self._assign_bits(bits, node.rvalue)
            else:
                self._write_bits(bits, self._binary(op[:-1], self._bits_value(bits),
                                                    self.value(node.rvalue)))
            return
        if indexed:
            self._assign_element(target, op, node.rvalue)
            return
        value = self.value(node.rvalue, measure=op == "=")
        if isinstance(value, _Measured):
            self.env.set(name, _Bits("clbit", self.hidden_register(name, value)))
            return
        if op != "=":
            value = self._binary(op[:-1], self.env.lookup(name), value)
        self.env.set(name, value)

    def _assign_element(self, target, op, rvalue):
        name = target.name.name
        array = self.env.lookup(name)
        if not isinstance(array, list):
            raise self.error(f"'{name}' is not an array")
        positions = []
        for index in target.indices:
            if type(index).__name__ == "DiscreteSet" or any(
                    type(item).__name__ == "RangeDefinition" for item in index):
                raise self.error("assigning to a slice of an array is not supported")
            positions += [self.int_value(item) for item in index]
        updated = _copy_value(array)
        cell = updated
        try:
            for position in positions[:-1]:
                cell = cell[position]
            value = self.value(rvalue)
            if op != "=":
                value = self._binary(op[:-1], cell[positions[-1]], value)
            cell[positions[-1]] = value
        except (IndexError, TypeError):
            raise self.error(f"index {positions} is out of range for '{name}'") from None
        self.env.set(name, updated)

    def _assign_bits(self, clbits, expression):
        self._write_bits(clbits, self.value(expression, measure=True))

    def _write_bits(self, clbits, value):
        """Put a value into clbits: a measurement, a copy of run-time bits, or a constant."""
        if isinstance(value, _Measured):
            self._measure(value.qubits, clbits)
            return
        if isinstance(value, list):
            raise self.error("an array cannot be stored in classical bits")
        if _runtime(value) or isinstance(value, Symbol):
            reads = _reads(value)
            self.note_reads(reads)
            for bit in clbits:
                self.known.pop(bit, None)
            self.append(StaticGate("store", 0, len(clbits)), [], clbits, None, reads)
            return
        if isinstance(value, (float, complex, str)):
            raise self.error(f"{value!r} cannot be stored in classical bits")
        number = int(value)
        width = len(clbits)
        if isinstance(value, _Word) and value.width != width and width != 1:
            raise self.error(f"a {value.width}-bit value cannot be stored in {width} bits")
        bits = [1 if number else 0] if width == 1 else [(number >> i) & 1 for i in range(width)]
        scope = self.circuit._scopes[-1] if self.circuit._scopes else None
        for bit, bit_value in zip(clbits, bits):
            self.known[bit] = bit_value
            if scope is not None:
                scope.clbits.setdefault(bit, None)     # the enclosing block writes it

    def _s_QuantumMeasurementStatement(self, node):
        qubits = self.bits(node.measure.qubit, "qubit")
        if node.target is None:
            self._measure(qubits, None)
        else:
            self._measure(qubits, self.bits(node.target, "clbit"))

    def _measure(self, qubits, clbits):
        if clbits is None:              # `measure q;`: the result is thrown away
            gate = StaticGate("measure", 1, 0)
            for q, _ in broadcast(gate, [qubits]):
                self.append(gate, q)
            return
        gate = StaticGate("measure", 1, 1)
        if len(qubits) != len(clbits):
            raise self.error(f"measuring {len(qubits)} qubits into {len(clbits)} bits")
        for q, c in broadcast(gate, [qubits, clbits]):
            for bit in c:
                self.known.pop(bit, None)
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
        # A global phase changes no qubit's dependencies, and Qiskit records
        # none either. Controlled, it becomes a phase gate on the controls.
        angle = self.param(node.argument)
        gate = None
        for modifier in reversed(node.modifiers):
            kind = modifier.modifier.name
            if gate is not None:
                gate = self._modify(gate, modifier)
            elif kind == "inv":
                angle = _negate(angle)
            elif kind == "pow":
                power = self.param(modifier.argument)
                angle = _symbolic_binop("*", angle, power) if isinstance(angle, Symbol) or \
                    isinstance(power, Symbol) else angle * power
            else:
                count = 1 if modifier.argument is None else self.int_value(modifier.argument)
                if count < 1:
                    raise self.error(f"{kind}({count}) needs at least one control")
                gate = StaticGate("p", 1, 0, [angle])
                if count > 1:
                    gate = gate.control(count - 1)
                if kind == "negctrl":
                    gate.name = f"{gate.name}_o0"
        if gate is None:
            return
        operands = [self.bits(q, "qubit") for q in node.qubits]
        if len(operands) != gate.num_qubits:
            raise self.error(f"a controlled gphase acts on {gate.num_qubits} qubits, "
                             f"but {len(operands)} were given")
        for qubits, _ in broadcast(gate, operands):
            self.append(gate, qubits)

    def _s_QuantumGateDefinition(self, node):
        self.globals.names[node.name.name] = _GateDef(node)

    def _s_SubroutineDefinition(self, node):
        self.globals.names[node.name.name] = _SubroutineDef(node)

    def _s_ExpressionStatement(self, node):
        value = self.value(node.expression, measure=True)
        if isinstance(value, _Measured):
            self._measure(value.qubits, None)

    def _s_ReturnStatement(self, node):
        if self.call_scopes and len(self.circuit._scopes) > self.call_scopes[-1]:
            raise self.error("'return' under a run-time condition is not supported")
        raise _Return(None if node.expression is None
                      else self.value(node.expression, measure=True))

    def _s_BreakStatement(self, node):
        raise _Break()

    def _s_ContinueStatement(self, node):
        raise _Continue()

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
        if kind == "inv":
            return gate.inverse()
        if kind == "pow":
            power = self.param(modifier.argument)
            if not isinstance(power, Symbol) and power == 1:
                return gate
            self.warn(f"pow({power}) @ {gate.name} is kept as one instruction")
            return StaticGate(f"{gate.name}_pow", gate.num_qubits, 0, list(gate.params))
        count = 1 if modifier.argument is None else self.int_value(modifier.argument)
        if count < 1:
            raise self.error(f"{kind}({count}) needs at least one control")
        controlled = gate.control(count)
        if kind == "negctrl":
            # Qiskit names an open-controlled gate by its control state: cx_o0.
            controlled.name = f"{controlled.name}_o0"
        return controlled

    # -- run-time paths -----------------------------------------------------

    def _path(self, run, mode=None):
        """Run one possible path through run-time control flow, collecting what it places.

        ``mode`` is "opaque" when the path is a loop body standing in for every
        iteration; a break/continue there becomes a marker. Otherwise a
        break/continue that leaves the path is handled by _escape.
        """
        scope, overlay, reads = _Scope(), _Overlay(self.env), []
        saved_env, saved_known = self.env, self.known
        self.known = dict(saved_known)
        self.env = overlay
        self.circuit._scopes.append(scope)
        self.trackers.append(reads)
        if mode is not None:
            self.loops.append(mode)
        try:
            run()
        finally:
            if mode is not None:
                self.loops.pop()
            self.trackers.pop()
            self.circuit._scopes.pop()
            self.env, self.known = saved_env, saved_known
        return _Path(scope, overlay, reads)

    def _arm(self, statements):
        """One arm of a run-time branch."""
        def run():
            try:
                self.child_block(statements)
            except (_Break, _Continue) as exc:
                self._escape(exc)
        return self._path(run)

    def _escape(self, exc):
        """A break/continue leaving a run-time branch."""
        if not self.loops:
            raise self.error(f"'{'break' if isinstance(exc, _Break) else 'continue'}' "
                             "outside a loop") from None
        if self.loops[-1] == "unrolled":
            raise _DynamicExit() from None
        marker = "break_loop" if isinstance(exc, _Break) else "continue_loop"
        self.append(StaticGate(marker, 0), [])

    def _merge(self, paths, reads):
        """Settle the variables and bits the paths wrote, once they have all run."""
        for path in paths:
            for bit in path.scope.clbits:
                self.known.pop(bit, None)
        names = []
        for path in paths:
            names += [n for n in path.overlay.assigned if n not in names]
        for name in names:
            outer = self.env.lookup(name)
            values = [path.overlay.assigned.get(name, outer) for path in paths]
            self.env.set(name, _blend(values, reads))

    @staticmethod
    def _union(paths, first_clbits=()):
        qubits, clbits = [], _unique(first_clbits)
        for path in paths:
            qubits += [b for b in path.scope.qubits if not any(b is q for q in qubits)]
            clbits += [b for b in path.scope.clbits if not any(b is c for c in clbits)]
        return qubits, clbits

    # -- branches -----------------------------------------------------------

    def _s_BranchingStatement(self, node):
        condition = self.value(node.condition)
        if not (_runtime(condition) or isinstance(condition, Symbol)):
            self.child_block(node.if_block if condition else node.else_block)
            return
        bits = _reads(condition)
        self.note_reads(bits)
        record = self._condition_record(node.condition, bits)
        paths = [self._arm(node.if_block), self._arm(node.else_block)]
        self._merge(paths, bits)
        if not any(p.scope.instructions or p.scope.clbits for p in paths):
            return      # it only changed classical variables, which now carry its condition
        qubits, clbits = self._union(paths, bits)
        true_body = StaticCircuit.body(paths[0].scope.instructions, qubits, clbits)
        false_body = (StaticCircuit.body(paths[1].scope.instructions, qubits, clbits)
                      if node.else_block else None)
        self.append(StaticGate("if_else", len(qubits), len(clbits), [true_body, false_body]),
                    qubits, clbits, record, bits)

    def _condition_record(self, node, bits):
        """The condition as Qiskit would record it: (bit or register, value) when it
        has that shape, otherwise the expression's source text."""
        simple = self._simple_condition(node)
        if simple is not None:
            target, value, read = simple
            if len(read) == len(bits) and all(a is b for a, b in zip(read, bits)):
                return target, value
        return openqasm3.dumps(node)

    def _clbit_operand(self, node):
        root = node
        while type(root).__name__ == "IndexExpression":
            root = root.collection
        if type(root).__name__ != "Identifier":
            return None, None
        found = self.env.lookup(root.name)
        if not (isinstance(found, _Bits) and found.kind == "clbit"):
            return None, None
        return found, self.bits(node, "clbit")

    def _simple_condition(self, node):
        kind = type(node).__name__
        if kind == "UnaryExpression" and node.op.name == "!":
            inner = self._simple_condition(node.expression)
            if inner is not None and isinstance(inner[1], bool):
                return inner[0], not inner[1], inner[2]
            return None
        if kind in ("Identifier", "IndexExpression"):
            _, bits = self._clbit_operand(node)
            if bits is not None and len(bits) == 1:
                return bits[0], True, bits
            return None
        if kind == "BinaryExpression" and node.op.name == "==":
            for operand, constant in ((node.lhs, node.rhs), (node.rhs, node.lhs)):
                found, bits = self._clbit_operand(operand)
                if found is None:
                    continue
                value = self.value(constant)
                if not isinstance(value, (int, bool)) or isinstance(value, _Dyn):
                    return None
                if type(operand).__name__ == "Identifier" and found.register is not None \
                        and len(bits) > 1:
                    return found.register, int(value), bits
                if len(bits) == 1:
                    return bits[0], (bool(value) if isinstance(value, bool) else int(value)), bits
                return None
        return None

    def _s_SwitchStatement(self, node):
        target = self.value(node.target)
        cases = [([self.value(v) for v in values], body.statements) for values, body in node.cases]
        default = node.default.statements if node.default is not None else None
        if not (_runtime(target) or isinstance(target, Symbol)):
            for values, statements in cases:
                if any(not _runtime(v) and v == target for v in values):
                    self.child_block(statements)
                    return
            if default is not None:
                self.child_block(default)
            return
        bits = _reads(target)
        self.note_reads(bits)
        bodies = [statements for _, statements in cases] + [default or []]
        paths = [self._arm(statements) for statements in bodies]
        self._merge(paths, bits)
        qubits, clbits = self._union(paths, bits)
        params = [StaticCircuit.body(p.scope.instructions, qubits, clbits) for p in paths]
        if default is None:
            params.pop()
        label = SWITCH_HAS_DEFAULT if default is not None else None
        self.append(StaticGate("switch_case", len(qubits), len(clbits), params, label=label),
                    qubits, clbits, openqasm3.dumps(node.target), bits)

    # -- loops --------------------------------------------------------------

    def _s_ForInLoop(self, node):
        values = self._loop_values(node.set_declaration)
        saved = self.checkpoint()
        self.loops.append("unrolled")
        try:
            self._iterate(node, values)
            return
        except _DynamicExit:
            pass
        finally:
            self.loops.pop()
        # A run-time break/continue: the number of iterations is not known
        # here, so the loop becomes one instruction over everything it may do.
        self.restore(saved)
        self._opaque_loop("for_loop", lambda prepare: self._iterate(node, values, prepare),
                          node.block)

    def _iterate(self, node, values, prepare=None):
        outer = self.env
        try:
            for value in values:
                self.tick()
                if prepare is not None:
                    prepare()
                self.env = _Env(outer)
                self.env.names[node.identifier.name] = value
                try:
                    self.block(node.block)
                except _Break:
                    break
                except _Continue:
                    continue
                finally:
                    self.env = outer
        finally:
            self.env = outer

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
        values = self.value(declaration)
        if isinstance(values, list):
            return values
        if _runtime(values):
            raise self.error("this loop runs over values only known at run time")
        raise self.error("only loops over a range, a set or an array are supported")

    def _s_WhileLoop(self, node):
        saved = self.checkpoint()
        self.loops.append("unrolled")
        try:
            finished = self._unroll_while(node)
        except _DynamicExit:
            finished = None
        finally:
            self.loops.pop()
        if finished:
            return
        if finished is None:
            self.restore(saved)         # a run-time break/continue: none of it can be unrolled
        # The iterations from here on are decided at run time.
        self._opaque_loop("while_loop", self._once(node.block), node.block, node.while_condition)

    def _unroll_while(self, node):
        """Unroll while the condition is known: True if the loop ended, False if
        the condition became a run-time value."""
        while True:
            condition = self.value(node.while_condition)
            if _runtime(condition) or isinstance(condition, Symbol):
                return False
            if not condition:
                return True
            self.tick()
            try:
                self.child_block(node.block)
            except _Break:
                return True
            except _Continue:
                continue

    def _once(self, statements):
        """A loop body run once, standing in for however many iterations happen."""
        def run(prepare):
            prepare()
            try:
                self.child_block(statements)
            except (_Break, _Continue):
                pass        # unconditional: the rest of the body never runs
        return run

    def _opaque_loop(self, name, run, body, condition_node=None):
        """Place a loop whose iterations depend on run-time values as one instruction.

        What the body writes holds a value from an unknown iteration, so every
        iteration starts with those variables and bits unknown. The body runs
        once per iteration it would have if unrolled (a for loop) or once
        standing for all of them (a while loop).
        """
        bits, record = [], None
        if condition_node is not None:
            condition = self.value(condition_node)
            if _runtime(condition) or isinstance(condition, Symbol):
                bits = _reads(condition)
                record = self._condition_record(condition_node, bits)
        carried, written = self._writes(body)

        def prepare():
            for bit in written:
                self.known.pop(bit, None)
            for name_ in carried:
                self.env.set(name_, _Dyn(bits))

        self.note_reads(bits)
        path = self._path(lambda: run(prepare), "opaque")
        loop_reads = _unique(bits + path.reads)
        self._merge([path], loop_reads)
        for bit in written:
            self.known.pop(bit, None)
        for name_ in carried:
            final = path.overlay.assigned.get(name_)
            self.env.set(name_, _Dyn(loop_reads + _reads(final)))
        if record is not None:
            # Later iterations test the condition on what the body left behind.
            again = _reads(self.value(condition_node))
            if len(again) > len(bits):
                bits = _unique(bits + again)
                record = self._condition_record(condition_node, bits)
                self.note_reads(bits)
        qubits, clbits = self._union([path], bits)
        body = StaticCircuit.body(path.scope.instructions, qubits, clbits)
        self.append(StaticGate(name, len(qubits), len(clbits), [body]), qubits, clbits,
                    record, bits)

    def _writes(self, statements):
        """The outer variables and the clbits a loop body may write, read off its syntax.

        A register written through an index counts whole, since the index may
        differ between iterations.
        """
        assigned, declared = [], set()
        stack = list(statements)
        while stack:
            node = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend(node)
                continue
            if not isinstance(node, qasm_ast.QASMNode):
                continue
            kind = type(node).__name__
            target = None
            if kind == "ClassicalAssignment":
                target = node.lvalue
            elif kind == "QuantumMeasurementStatement":
                target = node.target
            elif kind in ("ClassicalDeclaration", "QubitDeclaration", "AliasStatement"):
                declared.add((node.identifier if kind == "ClassicalDeclaration" else
                              node.qubit if kind == "QubitDeclaration" else node.target).name)
            if target is not None:
                root = target.name if type(target).__name__ == "IndexedIdentifier" else target
                if getattr(root, "name", None) and root.name not in assigned:
                    assigned.append(root.name)
            stack.extend(v for k, v in vars(node).items() if k != "span")
        carried, written = [], []
        for name in assigned:
            if name in declared:
                continue
            found = self.env.lookup(name)
            if isinstance(found, _Bits):
                written += [b for b in found.bits if found.kind == "clbit"]
            elif found is not None:
                carried.append(name)
        return carried, _unique(written)

    # -- subroutines --------------------------------------------------------

    call_scopes = ()

    def call(self, node):
        name = node.name.name
        defined = self.globals.lookup(name)
        if isinstance(defined, _SubroutineDef):
            return self._call_subroutine(defined.node, node)
        args = [self.value(a) for a in node.arguments]
        if isinstance(defined, _Extern):
            return _Dyn(_reads(*args))      # computed outside the program, at run time
        if name in ("rotl", "rotr"):
            if len(args) != 2:
                raise self.error(f"{name}() takes 2 arguments")
            if any(_runtime(a) for a in args):
                return _Dyn(_reads(*args))
            try:
                return _rotate(args[0], int(args[1]), name == "rotl")
            except ValueError as exc:
                raise self.error(f"{name}() {exc}") from None
        if name not in _FUNCTIONS:
            raise self.error(f"function '{name}' is not defined")
        if any(_runtime(a) for a in args):
            return _Dyn(_reads(*args))
        if any(isinstance(a, Symbol) for a in args):
            if len(args) == 1 and name in ("sin", "cos", "tan", "exp", "sqrt"):
                return _symbolic_call(name, args[0])
            raise self.error(f"{name}() of a parameter is not supported")
        try:
            return _FUNCTIONS[name](*args)
        except (TypeError, ValueError):
            raise self.error(f"{name}() does not apply to {args!r}") from None

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
                env.names[parameter.name.name] = _copy_value(self.value(argument))
        saved, saved_loops = self.env, self.loops
        self.env, self.loops = env, []          # a loop outside is not the subroutine's
        self.depth += 1
        self.via.append(self.line())
        self.call_scopes = (*self.call_scopes, len(self.circuit._scopes))
        try:
            self.block(definition.body)
        except _Return as result:
            return result.value
        except (_Break, _Continue) as exc:
            name = "break" if isinstance(exc, _Break) else "continue"
            raise self.error(f"'{name}' outside a loop") from None
        finally:
            self.env, self.loops = saved, saved_loops
            self.depth -= 1
            self.via.pop()
            self.call_scopes = self.call_scopes[:-1]
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

    def __init__(self, source: str = None, time_limit: float = None) -> None:
        """Initialize the parser, optionally parsing the source immediately.

        ``time_limit`` caps the wall-clock seconds a parse may take, as it does
        for StaticQiskitParser.
        """
        super().__init__(source)
        self.time_limit = time_limit
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
        source = self.source
        if _VERSION_2.match(source):
            source = _OPAQUE.sub(r"\1gate\2 { }", source)
        tree = _parse_syntax(source)
        interpreter = _QasmInterpreter(_physical_count(tree), self.time_limit)
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
