"""Static Qiskit front end: builds the IR from a Python file without running it.

``QiskitParser.parse_file`` executes the submitted file with ``runpy`` and then
looks for the QuantumCircuit it left behind. That handles anything Qiskit can
build, but the file runs with the parser's privileges -- which is why the web
app has to push it into a separate, time-limited process.

``StaticQiskitParser`` never executes the file. It parses it with ``ast`` and
walks the tree with a small interpreter of its own that understands just enough
Python, and just enough of Qiskit, to rebuild the circuit:

* plain Python: arithmetic, strings, lists/tuples/dicts/sets, comprehensions,
  ``for``/``while``/``if``, functions (closures, defaults, ``*args``), lambdas,
  ``try``/``except`` and a whitelist of pure built-ins (``range``, ``len``,
  ``enumerate``, ``zip``, ``sorted``...);
* ``math``, ``itertools``, the scalar/array basics of ``numpy``, and *seeded*
  random numbers (``np.random.default_rng(7)``, ``np.random.seed(1)``,
  ``random.seed(1)``), which give exactly the values a real run would;
* ``argparse``: a parser's arguments take their defaults, as a run with no
  command-line arguments would; a required argument is unknown;
* ``functools.lru_cache``/``cache`` on a function in the file, which then
  returns the same object for the same arguments, as it would at run time;
* the Qiskit circuit API: ``QuantumCircuit`` and registers, every standard gate
  method with Qiskit's argument broadcasting, ``measure``/``measure_all``,
  ``barrier``, ``reset``, ``append``, ``compose``, ``to_gate``, ``inverse``,
  ``decompose``, ``if_test`` (with ``else``), ``Parameter``,
  ``ParameterVector``, ``assign_parameters``, the standard gate classes of
  ``qiskit.circuit.library`` and ``QFT``/``QFTGate``.

Everything else -- simulators, transpilers, plotting, file I/O, any import that
is not modelled -- evaluates to an *unknown* value instead of running. Unknown
values are harmless until a circuit needs one: a qubit index, a register size,
or a loop or branch that adds gates. Then the parse stops with a
StaticParseError naming the line, rather than guessing. Loops and branches over
unknown values that cannot change a circuit are skipped and reported in
``parser.warnings``.

The circuit chosen is the one QiskitParser would choose: the module-level
QuantumCircuit with the most instructions. The file is read as a script, so
``if __name__ == "__main__":`` blocks are followed: most programs build their
circuit from ``main()``. Pass ``run_name="<run_path>"`` to skip them, as
``runpy.run_path`` would.

Known differences from executing the file:

* calls into code the parser does not model (``transpile``, a pass manager, a
  class the file defines) are not followed, so a circuit they return is never a
  candidate. QiskitParser would pick up a transpiled copy; this parser keeps
  the circuit as written;
* ``decompose()`` expands composite gates (``to_gate`` blocks, QFT) but leaves
  standard gates alone, and commuting gates inside an expanded QFT may come out
  in a different, equally valid, order;
* ``mcrx``/``mcry``/``mcrz`` are one instruction each rather than Qiskit's
  synthesized sequence.

Resource use is bounded (statements run, instructions placed, collection sizes,
call depth), so a hostile file cannot hang or exhaust the parser.
"""

from ..ir.qinstruction import QuantumInstruction, SourceLocation
from ..ir.qprogram import QuantumProgram
from .base import AbstractParser

import ast
import builtins as _builtins
import copy as _copy
import functools
import itertools
import math
import numbers
import operator
import random as _random
import re
import string
import time

try:
    import numpy as _np
except ImportError:  # pragma: no cover - numpy ships with qiskit, but is optional here
    _np = None


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

MAX_STEPS = 2_000_000          # statements executed + loop iterations
MAX_INSTRUCTIONS = 200_000     # instructions placed, across all circuits
MAX_CALL_DEPTH = 40            # nested calls of functions defined in the file
MAX_COLLECTION = 1_000_000     # elements in any list, string or array built
MAX_BITS = 100_000             # bits in one register


# --------------------------------------------------------------------------
# Errors and control-flow signals
# --------------------------------------------------------------------------

class StaticParseError(ValueError):
    """The circuit cannot be determined without running the program.

    ``raised`` names the exception class when the reason is that the program
    itself raises one (``CircuitError``, ``NameError``, ...), else it is None.
    """

    def __init__(self, message, lineno=None, raised=None, needs=None):
        self.message = message
        self.lineno = lineno
        self.raised = raised
        # When an unknown value stopped the parse and it came from a variable or
        # a command-line argument, a dict saying which: ``name``, ``line`` (where
        # it is assigned or declared), ``kind`` ("variable" or "argument") and
        # ``expression`` (what produced it). Passing a value for it in
        # ``bindings`` lets the next parse go further.
        self.needs = needs
        super().__init__(f"line {lineno}: {message}" if lineno else message)


class QiskitError(Exception):
    """Stand-in for qiskit.exceptions.QiskitError."""


class CircuitError(QiskitError):
    """Stand-in for qiskit.circuit.exceptions.CircuitError, raised where Qiskit would."""


class _ProgramError(Exception):
    """The interpreted program raised ``exc``, as running it would have."""

    def __init__(self, exc, lineno):
        super().__init__(exc)
        self.exc = exc
        self.lineno = lineno


class _NeedsUnknown(Exception):
    """An Unknown value was used where a concrete value was needed.

    Raised with the Unknown itself where it is at hand, so its origin survives
    to the error; ``str(exc)`` is the text either way.
    """

    def __init__(self, value):
        self.unknown = value if isinstance(value, Unknown) else None
        super().__init__(value.text if isinstance(value, Unknown) else value)


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, value):
        super().__init__()
        self.value = value


_MISSING = object()


def _text(node, limit=60):
    """Source text of a node, shortened for messages."""
    try:
        text = ast.unparse(node)
    except Exception:
        text = type(node).__name__
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --------------------------------------------------------------------------
# Values the interpreter produces
# --------------------------------------------------------------------------

class Unknown:
    """A value only running the program could produce.

    Unknown values flow through assignments, containers and calls to code the
    parser does not model. Anything that needs the concrete value raises
    _NeedsUnknown, which turns the enclosing expression unknown too -- or, where
    a circuit depends on it, becomes a StaticParseError.
    """

    __slots__ = ("text", "origin")

    def __init__(self, text="?", origin=None):
        self.text = text
        # (name, lineno, kind, expression) of the variable or argument this value
        # first came from, or None. Values computed from it keep the same origin,
        # so the user is asked for the root cause, not each thing derived from it.
        self.origin = origin

    def __repr__(self):
        return f"<unknown: {self.text}>"

    def _needed(self, *_args):
        raise _NeedsUnknown(self)

    __bool__ = __index__ = __int__ = __float__ = __complex__ = _needed
    __len__ = __iter__ = __getitem__ = __contains__ = _needed
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _needed
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _needed
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = _needed
    __mod__ = __rmod__ = __pow__ = __rpow__ = __matmul__ = __rmatmul__ = _needed
    __neg__ = __pos__ = __abs__ = __round__ = _needed
    __hash__ = object.__hash__


def _needs_of(cause):
    """The ``needs`` dict of a StaticParseError caused by ``cause``, or None."""
    unknown = cause.unknown if isinstance(cause, _NeedsUnknown) else cause
    if not isinstance(unknown, Unknown) or unknown.origin is None:
        return None
    name, line, kind, expression = unknown.origin
    return {"name": name, "line": line, "kind": kind, "expression": expression}


def _derived(text, *values):
    """An Unknown for ``text`` that keeps the origin of the first unknown it came from."""
    for value in values:
        if isinstance(value, _NeedsUnknown):
            value = value.unknown
        if isinstance(value, Unknown) and value.origin is not None:
            return Unknown(text, value.origin)
    return Unknown(text)


_SYMBOL_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp,
    "log": math.log, "sqrt": math.sqrt, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "arcsin": math.asin, "arccos": math.acos, "arctan": math.atan,
}

_SYMBOL_OPS = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
    "/": operator.truediv, "**": operator.pow,
}


class Symbol:
    """A qiskit Parameter, or an arithmetic expression over Parameters.

    Leaves are compared by identity, as Parameters are: two ``Parameter("a")``
    calls make two different parameters.
    """

    __slots__ = ("op", "args", "name", "vector", "index")

    _PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "neg": 3, "**": 4}

    def __init__(self, op, args=(), name=None, vector=None, index=None):
        self.op = op
        self.args = tuple(args)
        self.name = name
        self.vector = vector
        self.index = index

    @classmethod
    def leaf(cls, name, vector=None, index=None):
        return cls("leaf", (), str(name), vector, index)

    def free(self):
        """The leaf parameters this expression uses, in first-use order."""
        found = {}
        self._collect(found)
        return list(found)

    def _collect(self, found):
        if self.op == "leaf":
            found.setdefault(self, None)
        for arg in self.args:
            if isinstance(arg, Symbol):
                arg._collect(found)

    def bind(self, values):
        """Substitute ``values`` ({leaf: value}); a number once nothing is left free."""
        if self.op == "leaf":
            return values.get(self, self)
        if self.op == "call":
            name, arg = self.args
            arg = arg.bind(values) if isinstance(arg, Symbol) else arg
            return _symbolic_call(name, arg)
        parts = [a.bind(values) if isinstance(a, Symbol) else a for a in self.args]
        if self.op == "neg":
            return _negate(parts[0])
        return _symbolic_binop(self.op, parts[0], parts[1])

    def __str__(self):
        return self._render(0)

    __repr__ = __str__

    def _render(self, outer):
        if self.op == "leaf":
            return self.name
        if self.op == "call":
            return f"{self.args[0]}({_render(self.args[1], 0)})"
        if self.op == "neg":
            prec = self._PRECEDENCE["neg"]
            text = "-" + _render(self.args[0], prec + 1)
        else:
            prec = self._PRECEDENCE[self.op]
            left_prec = prec + 1 if self.op == "**" else prec
            right_prec = prec + 1 if self.op in ("-", "/") else prec
            sep = " " if self.op in ("+", "-") else ""
            text = (f"{_render(self.args[0], left_prec)}{sep}{self.op}{sep}"
                    f"{_render(self.args[1], right_prec)}")
        return f"({text})" if prec < outer else text


def _render(value, outer):
    if isinstance(value, Symbol):
        return value._render(outer)
    text = repr(value) if isinstance(value, float) else str(value)
    return f"({text})" if text.startswith("-") and outer > 1 else text


def _symbolic_binop(op, left, right):
    if isinstance(left, Symbol) or isinstance(right, Symbol):
        return Symbol(op, (left, right))
    return _SYMBOL_OPS[op](left, right)


def _symbolic_call(name, arg):
    if isinstance(arg, Symbol):
        return Symbol("call", (name, arg))
    return _SYMBOL_FUNCS[name](arg)


def _negate(value):
    if isinstance(value, Symbol):
        return Symbol("neg", (value,))
    return -value


def _symbol_sort_key(symbol):
    """Qiskit's parameter order: by name, vector elements by vector then index."""
    if symbol.vector is not None:
        return (symbol.vector, symbol.index)
    return (symbol.name, -1)


class _ModuleRef:
    """An imported module (or a name inside one) that is never actually imported."""

    __slots__ = ("path",)

    def __init__(self, path):
        self.path = path

    def __repr__(self):
        return f"<module {self.path}>"


class _Native:
    """A function or class the interpreter implements itself.

    ``fn(interp, args, kwargs)`` does the work. ``pytype`` is the real Python
    type for wrappers of ``int``, ``list`` and friends, so they can be handed to
    real code (``np.array(x, dtype=float)``); ``cls`` is what ``isinstance``
    should test against.
    """

    __slots__ = ("name", "fn", "qiskit", "pytype", "cls")

    def __init__(self, name, fn, qiskit=False, pytype=None, cls=None):
        self.name = name
        self.fn = fn
        self.qiskit = qiskit
        self.pytype = pytype
        self.cls = cls if cls is not None else pytype

    def __repr__(self):
        return f"<built-in {self.name}>"


class _BoundMethod:
    __slots__ = ("obj", "name")

    def __init__(self, obj, name):
        self.obj = obj
        self.name = name

    def __repr__(self):
        return f"<method {self.name}>"


class _UserFunction:
    """A function or lambda defined in the file being parsed."""

    __slots__ = ("node", "name", "defaults", "kw_defaults", "closure", "is_generator", "memo")

    def __init__(self, node, name, defaults, kw_defaults, closure, is_generator=False):
        self.node = node
        self.name = name
        self.defaults = defaults
        self.kw_defaults = kw_defaults
        self.closure = closure
        self.is_generator = is_generator
        self.memo = None        # {arguments: result} under @lru_cache / @cache

    def __repr__(self):
        return f"<function {self.name}>"


class _ArgParser:
    """argparse.ArgumentParser, as far as knowing what parse_args() returns goes.

    Arguments take their defaults: the file is read as if it were run with no
    command-line arguments. A required one is unknown, and asks for a value.
    """

    __slots__ = ("arguments", "defaults")

    def __init__(self):
        self.arguments = []     # (dest, value) in the order they were added
        self.defaults = {}      # set_defaults()

    def __repr__(self):
        return "<ArgumentParser>"


class _Namespace:
    """argparse.Namespace: the attributes parse_args() sets."""

    __slots__ = ("values",)

    def __init__(self, values):
        self.values = dict(values)

    def __repr__(self):
        return "Namespace(" + ", ".join(f"{k}={v!r}" for k, v in self.values.items()) + ")"


class _RandomSource:
    """A random generator: numpy Generator, numpy RandomState or random.Random.

    ``impl`` is None when the generator is unseeded; its numbers are then
    unknown, since no two runs would agree on them.
    """

    __slots__ = ("kind", "impl")

    def __init__(self, kind, impl):
        self.kind = kind
        self.impl = impl

    def __repr__(self):
        return f"<random {self.kind}>"


# --------------------------------------------------------------------------
# The circuit model
# --------------------------------------------------------------------------

class StaticBit:
    """A qubit or clbit. Compared by identity, like Qiskit's bits."""

    __slots__ = ("kind", "register", "index")

    def __init__(self, kind, register=None, index=None):
        self.kind = kind
        self.register = register
        self.index = index

    def __repr__(self):
        cls = "Qubit" if self.kind == "qubit" else "Clbit"
        if self.register is None:
            return f"{cls}()"
        return f"{cls}({self.register.name}, {self.index})"


class StaticRegister:
    __slots__ = ("kind", "name", "bits")

    def __init__(self, kind, name, size):
        self.kind = kind
        self.name = name
        self.bits = [StaticBit(kind, self, i) for i in range(size)]

    @property
    def size(self):
        return len(self.bits)

    def __len__(self):
        return len(self.bits)

    def __iter__(self):
        return iter(self.bits)

    def __repr__(self):
        cls = "QuantumRegister" if self.kind == "qubit" else "ClassicalRegister"
        return f"{cls}({len(self.bits)}, '{self.name}')"


# Instruction names whose inverse is themselves.
_SELF_INVERSE = {
    "h", "x", "y", "z", "id", "cx", "cy", "cz", "ch", "swap", "ccx", "ccz",
    "cswap", "mcx", "ecr", "barrier", "delay", "pauli",
}
# Instruction names whose inverse is a differently named gate.
_INVERSE_NAMES = {
    "s": "sdg", "sdg": "s", "t": "tdg", "tdg": "t", "sx": "sxdg", "sxdg": "sx",
    "cs": "csdg", "csdg": "cs",
}
# Instruction names inverted by negating every parameter.
_NEGATED = {
    "rx", "ry", "rz", "p", "u1", "crx", "cry", "crz", "cp", "cu1", "rxx", "ryy",
    "rzz", "rzx", "mcphase", "mcrx", "mcry", "mcrz", "rv",
}
_NOT_INVERTIBLE = {"measure", "reset", "if_else", "initialize", "state_preparation"}


class StaticGate:
    """An operation before it is placed on bits: Qiskit's Instruction/Gate."""

    __slots__ = ("name", "num_qubits", "num_clbits", "params", "definition", "label")

    def __init__(self, name, num_qubits, num_clbits=0, params=None, definition=None, label=None):
        self.name = name
        self.num_qubits = num_qubits
        self.num_clbits = num_clbits
        self.params = list(params or [])
        self.definition = definition
        self.label = label

    def with_params(self, params):
        return StaticGate(self.name, self.num_qubits, self.num_clbits, params,
                          self.definition, self.label)

    def renamed(self, name, params=None):
        return StaticGate(name, self.num_qubits, self.num_clbits,
                          self.params if params is None else params, None, self.label)

    def inverse(self):
        name, params = self.name, list(self.params)
        if name in _NOT_INVERTIBLE:
            raise CircuitError(f"the {name} instruction has no inverse")
        if name in _SELF_INVERSE:
            return self.with_params(params)
        if name in _INVERSE_NAMES:
            return self.renamed(_INVERSE_NAMES[name])
        if name in _NEGATED:
            return self.renamed(name, [_negate(p) for p in params])
        if name in ("u", "u3", "cu3", "cu"):
            theta, phi, lam = params[:3]
            rest = [_negate(p) for p in params[3:]]
            return self.renamed(name, [_negate(theta), _negate(lam), _negate(phi)] + rest)
        if name == "u2":
            phi, lam = params
            return self.renamed(name, [_symbolic_binop("-", _negate(lam), math.pi),
                                       _symbolic_binop("+", _negate(phi), math.pi)])
        if name in ("r", "xx_minus_yy", "xx_plus_yy"):
            return self.renamed(name, [_negate(params[0])] + params[1:])
        definition = self.definition.inverse() if self.definition is not None else None
        new_name = name[:-3] if name.endswith("_dg") else name + "_dg"
        return StaticGate(new_name, self.num_qubits, self.num_clbits, params,
                          definition, self.label)

    def control(self, num_ctrl_qubits=1):
        n, base = num_ctrl_qubits, self.name
        if base == "x":
            name = _mcx_name(n)
        elif base == "p":
            name = "cp" if n == 1 else "mcphase"
        elif base == "z" and n == 2:
            name = "ccz"
        elif base == "swap" and n == 1:
            name = "cswap"
        else:
            name = "c" * n + base
        return StaticGate(name, self.num_qubits + n, 0, list(self.params))

    def __repr__(self):
        return (f"Instruction(name='{self.name}', num_qubits={self.num_qubits}, "
                f"num_clbits={self.num_clbits}, params={self.params})")


class StaticInstruction:
    """An operation placed on bits: Qiskit's CircuitInstruction."""

    __slots__ = ("operation", "qubits", "clbits", "location", "condition", "condition_bits")

    def __init__(self, operation, qubits, clbits=(), location=None, condition=None,
                 condition_bits=()):
        self.operation = operation
        self.qubits = list(qubits)
        self.clbits = list(clbits)
        self.location = location        # SourceLocation, or None for library-made gates
        self.condition = condition
        self.condition_bits = list(condition_bits)

    @property
    def name(self):
        return self.operation.name

    @property
    def params(self):
        return self.operation.params

    def copy(self):
        return StaticInstruction(self.operation, self.qubits, self.clbits, self.location,
                                 self.condition, self.condition_bits)

    def __repr__(self):
        return (f"CircuitInstruction({self.operation!r}, qubits={self.qubits}, "
                f"clbits={self.clbits})")


def _remap(instr, qmap, cmap, location=None):
    """Copy ``instr`` onto other bits (compose, decompose), bodies included.

    The copy keeps its own source location; ``location`` stands in for
    instructions that have none (gates a library made, such as a QFT's).
    """
    op = instr.operation
    if op.name == "if_else":
        bodies = []
        for body in op.params:
            if body is None:
                bodies.append(None)
                continue
            bodies.append(StaticCircuit.body(
                [_remap(i, qmap, cmap, location) for i in body.data],
                [qmap[b] for b in body.qubits], [cmap[b] for b in body.clbits]))
        op = op.with_params(bodies)
    condition = instr.condition
    if condition is not None:
        target, value = condition
        if isinstance(target, StaticBit):
            target = cmap.get(target, target)
        elif isinstance(target, StaticRegister) and any(
                cmap.get(b, b) is not b for b in target.bits):
            target = tuple(cmap.get(b, b) for b in target.bits)
        condition = (target, value)
    return StaticInstruction(
        op, [qmap[q] for q in instr.qubits], [cmap[c] for c in instr.clbits],
        instr.location if instr.location is not None else location, condition,
        [cmap.get(b, b) for b in instr.condition_bits])


class StaticCircuit:
    """Qiskit's QuantumCircuit, as far as the IR needs it."""

    def __init__(self, name):
        self.name = name
        self.qregs = []
        self.cregs = []
        self.qubits = []
        self.clbits = []
        self._qindex = {}
        self._cindex = {}
        self.data = []
        self._scopes = []       # open `with qc.if_test(...)` blocks, innermost last

    @property
    def num_qubits(self):
        return len(self.qubits)

    @property
    def num_clbits(self):
        return len(self.clbits)

    @property
    def parameters(self):
        found = {}
        for instr in self.data:
            for param in instr.operation.params:
                if isinstance(param, Symbol):
                    for leaf in param.free():
                        found.setdefault(leaf, None)
        return sorted(found, key=_symbol_sort_key)

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)

    def __repr__(self):
        return (f"<QuantumCircuit '{self.name}': {self.num_qubits} qubits, "
                f"{self.num_clbits} clbits, {len(self.data)} instructions>")

    # -- bits ---------------------------------------------------------------

    def add_bits(self, bits):
        for bit in bits:
            index, bits_list = ((self._qindex, self.qubits) if bit.kind == "qubit"
                                else (self._cindex, self.clbits))
            if bit in index:
                raise CircuitError(f"bit {bit!r} is already in the circuit")
            index[bit] = len(bits_list)
            bits_list.append(bit)

    def add_register(self, register):
        if any(register is r for r in self.qregs + self.cregs):
            raise CircuitError(f"register {register!r} is already in the circuit")
        (self.qregs if register.kind == "qubit" else self.cregs).append(register)
        self.add_bits(register.bits)

    def has(self, bit):
        return bit in (self._qindex if bit.kind == "qubit" else self._cindex)

    def find(self, bit):
        index = self._qindex if bit.kind == "qubit" else self._cindex
        if bit not in index:
            raise StaticParseError(f"{bit!r} is not part of circuit '{self.name}'")
        return index[bit]

    # -- instructions -------------------------------------------------------

    def append(self, instr):
        if self._scopes:
            self._scopes[-1].add(instr)
        else:
            self.data.append(instr)

    def current_block(self):
        return self._scopes[-1].instructions if self._scopes else self.data

    # -- derived circuits ---------------------------------------------------

    @classmethod
    def body(cls, instructions, qubits, clbits):
        """The body of a control-flow block, over the bits the block uses."""
        circuit = cls("body")
        circuit.add_bits(qubits)
        circuit.add_bits(clbits)
        circuit.data = list(instructions)
        return circuit

    def copy_empty(self, name=None):
        out = StaticCircuit(self.name if name is None else name)
        out.qregs, out.cregs = list(self.qregs), list(self.cregs)
        out.qubits, out.clbits = list(self.qubits), list(self.clbits)
        out._qindex, out._cindex = dict(self._qindex), dict(self._cindex)
        return out

    def copy(self, name=None):
        out = self.copy_empty(name)
        out.data = [i.copy() for i in self.data]
        return out

    def inverse(self):
        out = self.copy_empty(self.name + "_dg")
        for instr in reversed(self.data):
            new = instr.copy()
            new.operation = instr.operation.inverse()
            out.data.append(new)
        return out

    def decompose(self, names=None, reps=1):
        current = self
        for _ in range(reps):
            out = current.copy_empty()
            for instr in current.data:
                definition = instr.operation.definition
                if definition is None or (names is not None and instr.operation.name not in names):
                    out.data.append(instr.copy())
                    continue
                qmap = dict(zip(definition.qubits, instr.qubits))
                cmap = dict(zip(definition.clbits, instr.clbits))
                out.data.extend(_remap(sub, qmap, cmap, instr.location)
                                for sub in definition.data)
            current = out
        return current

    def to_gate(self, label=None):
        if self.num_clbits:
            raise CircuitError("Circuit with classical bits cannot be converted to gate.")
        return StaticGate(self.name, self.num_qubits, 0, [], self.copy(), label)

    def to_instruction(self, label=None):
        return StaticGate(self.name, self.num_qubits, self.num_clbits, [], self.copy(), label)


class _Scope:
    """Instructions collected inside one ``with qc.if_test(...)`` block."""

    def __init__(self):
        self.instructions = []
        self.qubits = {}        # ordered sets: first use first
        self.clbits = {}

    def add(self, instr):
        self.instructions.append(instr)
        self.note(instr)

    def note(self, instr):
        for bit in instr.qubits:
            self.qubits.setdefault(bit, None)
        for bit in instr.clbits:
            self.clbits.setdefault(bit, None)


class _InstructionSet:
    """What a gate method returns: the instructions it just placed."""

    __slots__ = ("instructions",)

    def __init__(self, instructions):
        self.instructions = instructions

    def __repr__(self):
        return f"<InstructionSet of {len(self.instructions)}>"


class _IfContext:
    """``qc.if_test(condition)``, waiting to be entered by a ``with``."""

    def __init__(self, circuit, condition, condition_bits, location):
        self.circuit = circuit
        self.condition = condition
        self.condition_bits = condition_bits
        self.location = location
        self.entered = False
        self.instruction = None          # the if_else, once the block closes
        self.true_instructions = None
        self.else_used = False


class _ElseContext:
    """The ``as else_`` target of an if_test block."""

    def __init__(self, if_context):
        self.if_context = if_context


# --------------------------------------------------------------------------
# Gate tables
# --------------------------------------------------------------------------

_ONE = ("qubit",)
_CT = ("control_qubit", "target_qubit")
_PAIR = ("qubit1", "qubit2")
_CCT = ("control_qubit1", "control_qubit2", "target_qubit")

# QuantumCircuit method -> (instruction name, parameter args, qubit args)
_GATE_METHODS = {
    **{n: (n, (), _ONE) for n in ("h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "sxdg", "id")},
    "rx": ("rx", ("theta",), _ONE),
    "ry": ("ry", ("theta",), _ONE),
    "rz": ("rz", ("phi",), _ONE),
    "p": ("p", ("theta",), _ONE),
    "r": ("r", ("theta", "phi"), _ONE),
    "u": ("u", ("theta", "phi", "lam"), _ONE),
    "rv": ("rv", ("vx", "vy", "vz"), _ONE),
    **{n: (n, (), _CT) for n in ("cx", "cy", "cz", "ch", "cs", "csdg", "csx")},
    **{n: (n, ("theta",), _CT) for n in ("crx", "cry", "crz", "cp")},
    "cu": ("cu", ("theta", "phi", "lam", "gamma"), _CT),
    **{n: (n, (), _PAIR) for n in ("swap", "iswap", "dcx", "ecr")},
    **{n: (n, ("theta",), _PAIR) for n in ("rxx", "ryy", "rzz", "rzx")},
    "ccx": ("ccx", (), _CCT),
    "ccz": ("ccz", (), _CCT),
    "rccx": ("rccx", (), _CCT),
    "cswap": ("cswap", (), ("control_qubit", "target_qubit1", "target_qubit2")),
    "rcccx": ("rcccx", (), ("control_qubit1", "control_qubit2", "control_qubit3", "target_qubit")),
}

# qiskit.circuit.library class -> (instruction name, number of params, number of qubits)
_GATE_CLASSES = {
    "HGate": ("h", 0, 1), "XGate": ("x", 0, 1), "YGate": ("y", 0, 1), "ZGate": ("z", 0, 1),
    "SGate": ("s", 0, 1), "SdgGate": ("sdg", 0, 1), "TGate": ("t", 0, 1), "TdgGate": ("tdg", 0, 1),
    "SXGate": ("sx", 0, 1), "SXdgGate": ("sxdg", 0, 1), "IGate": ("id", 0, 1),
    "RXGate": ("rx", 1, 1), "RYGate": ("ry", 1, 1), "RZGate": ("rz", 1, 1),
    "PhaseGate": ("p", 1, 1), "RGate": ("r", 2, 1), "UGate": ("u", 3, 1),
    "U1Gate": ("u1", 1, 1), "U2Gate": ("u2", 2, 1), "U3Gate": ("u3", 3, 1),
    "CXGate": ("cx", 0, 2), "CYGate": ("cy", 0, 2), "CZGate": ("cz", 0, 2), "CHGate": ("ch", 0, 2),
    "CSGate": ("cs", 0, 2), "CSdgGate": ("csdg", 0, 2), "CSXGate": ("csx", 0, 2),
    "SwapGate": ("swap", 0, 2), "iSwapGate": ("iswap", 0, 2), "DCXGate": ("dcx", 0, 2),
    "ECRGate": ("ecr", 0, 2),
    "CRXGate": ("crx", 1, 2), "CRYGate": ("cry", 1, 2), "CRZGate": ("crz", 1, 2),
    "CPhaseGate": ("cp", 1, 2), "CU1Gate": ("cu1", 1, 2), "CU3Gate": ("cu3", 3, 2),
    "CUGate": ("cu", 4, 2),
    "RXXGate": ("rxx", 1, 2), "RYYGate": ("ryy", 1, 2), "RZZGate": ("rzz", 1, 2),
    "RZXGate": ("rzx", 1, 2), "XXMinusYYGate": ("xx_minus_yy", 2, 2),
    "XXPlusYYGate": ("xx_plus_yy", 2, 2),
    "CCXGate": ("ccx", 0, 3), "CCZGate": ("ccz", 0, 3), "CSwapGate": ("cswap", 0, 3),
    "RCCXGate": ("rccx", 0, 3), "C3XGate": ("mcx", 0, 4), "RC3XGate": ("rcccx", 0, 4),
    "C3SXGate": ("c3sx", 0, 4), "C4XGate": ("mcx", 0, 5),
}


def _mcx_name(num_controls):
    return {1: "cx", 2: "ccx"}.get(num_controls, "mcx")


def _qft_definition(num_qubits, approximation_degree=0, do_swaps=True, inverse=False,
                    insert_barriers=False):
    """The gates of Qiskit's QFT (synth_qft_full), as a circuit."""
    circuit = StaticCircuit("QFT")
    circuit.add_register(StaticRegister("qubit", "q", num_qubits))
    q = circuit.qubits
    for j in reversed(range(num_qubits)):
        circuit.data.append(StaticInstruction(StaticGate("h", 1), [q[j]]))
        entanglements = max(0, j - max(0, approximation_degree - (num_qubits - j - 1)))
        for k in reversed(range(j - entanglements, j)):
            lam = math.pi * (2.0 ** (k - j))
            circuit.data.append(StaticInstruction(StaticGate("cp", 2, 0, [lam]), [q[j], q[k]]))
        if insert_barriers:
            circuit.data.append(StaticInstruction(StaticGate("barrier", num_qubits), q))
    if do_swaps:
        for i in range(num_qubits // 2):
            circuit.data.append(
                StaticInstruction(StaticGate("swap", 2), [q[i], q[num_qubits - i - 1]]))
    return circuit.inverse() if inverse else circuit


def broadcast(gate, bit_lists):
    """Qiskit's argument broadcasting: one (qubits, clbits) pair per instruction.

    ``bit_lists`` holds one list of bits per gate argument, qubits first. A
    list of length one is reused against longer ones; other lengths must agree.
    """
    if any(len(bits) == 0 for bits in bit_lists):
        return []
    width = max((len(bits) for bits in bit_lists), default=1)
    for bits in bit_lists:
        if len(bits) not in (1, width):
            raise CircuitError(f"arguments of '{gate.name}' cannot be broadcast together "
                               f"(lengths {[len(b) for b in bit_lists]})")
    out = []
    for i in range(width):
        combo = [bits[i] if len(bits) > 1 else bits[0] for bits in bit_lists]
        qubits = combo[:gate.num_qubits]
        if len({id(q) for q in qubits}) != len(qubits):
            raise CircuitError(f"duplicate qubit arguments to '{gate.name}'")
        out.append((qubits, combo[gate.num_qubits:]))
    return out


_STATIC_TYPES = (StaticCircuit, StaticGate, StaticRegister, StaticInstruction, _InstructionSet,
                 _IfContext, _ElseContext)

# QuantumCircuit attributes and methods that read a circuit without changing it.
_READ_ONLY_ATTRS = {
    "draw", "depth", "size", "width", "count_ops", "num_qubits", "num_clbits", "qubits",
    "clbits", "qregs", "cregs", "name", "parameters", "num_parameters", "data", "copy",
    "inverse", "decompose", "to_gate", "to_instruction", "global_phase", "metadata",
    "num_nonlocal_gates", "num_tensor_factors", "num_unitary_factors",
    "num_connected_components", "qasm", "find_bit", "has_register",
}

# QuantumCircuit methods whose result the parser does not need: they return unknown.
_INFO_METHODS = {
    "draw", "depth", "num_nonlocal_gates", "num_connected_components", "num_unitary_factors",
    "num_tensor_factors", "qasm", "find_bit", "has_register", "get_instructions",
    "estimate_duration", "has_parameter", "has_calibration_for",
}


# --------------------------------------------------------------------------
# The interpreter: statements
# --------------------------------------------------------------------------

class _Frame:
    """One scope: the module, a function call or a comprehension."""

    __slots__ = ("vars", "parent", "global_names", "nonlocal_names")

    def __init__(self, variables, parent):
        self.vars = variables
        self.parent = parent            # enclosing scope, for closures
        self.global_names = set()
        self.nonlocal_names = set()


_PASSTHROUGH = (StaticParseError, _ProgramError, _NeedsUnknown, _Break, _Continue, _Return)


def _stored_names(nodes):
    """Names a block of code may bind, not counting nested function bodies."""
    names = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
            continue
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return names


# Methods of lists, dicts, sets and arrays that change the object in place.
_MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "pop", "popitem", "clear", "update", "add",
    "discard", "setdefault", "sort", "reverse", "difference_update", "intersection_update",
    "symmetric_difference_update", "fill", "resize", "put", "itemset",
}


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _mutated_names(nodes):
    """Names whose objects a block may change in place (``x.append(..)``, ``x[i] = ..``).

    Nested function bodies are not counted, as in ``_stored_names``.
    """
    names = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATING_METHODS):
            root = _root_name(node.func.value)
            if root:
                names.append(root)
        elif (isinstance(node, (ast.Attribute, ast.Subscript))
              and isinstance(node.ctx, (ast.Store, ast.Del))):
            root = _root_name(node.value)
            if root:
                names.append(root)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _find_unknown(value, depth=2):
    """An Unknown inside ``value`` (looking a couple of container levels deep), or None."""
    if isinstance(value, Unknown):
        return value
    if depth and isinstance(value, (list, tuple, set, frozenset, dict)):
        items = value.values() if isinstance(value, dict) else value
        for item in itertools.islice(items, 10_000):
            found = _find_unknown(item, depth - 1)
            if found is not None:
                return found
    return None


def _is_generator(function_node):
    stack = list(function_node.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


class _Interpreter:
    """Walks a module's AST, building circuits instead of running code."""

    def __init__(self, filename="<source>", run_name="__main__", time_limit=None,
                 bindings=None):
        self.filename = filename
        # {(lineno, name): value} the user picked for values only a run could give.
        self.bindings = dict(bindings or {})
        self.used_bindings = set()
        # Every circuit the program got hold of, in the order it did, with the
        # line that made it -- including ones built inside functions, which are
        # gone from every scope by the end of the run.
        self.circuits = []
        self._circuit_ids = set()
        # Circuits that went into another one (compose, append, to_gate) or were
        # replaced by a derived copy (inverse, copy, decompose ...).
        self.consumed = set()
        # Wall-clock seconds the run may take, on top of MAX_STEPS; None for no limit.
        self.deadline = None if time_limit is None else time.monotonic() + time_limit
        self.globals = {"__name__": run_name, "__file__": filename}
        self.module_frame = _Frame(self.globals, None)
        self.warnings = []
        self.notes = []                 # (lineno, message) for each warning
        self.steps = 0
        self.placed = 0
        self.depth = 0
        self.lineno = None
        self._stmt_node = None          # the statement being run
        self._call_node = None          # the call being made
        self._via = []                  # lines of the calls into functions defined in the file
        self.star_imports = []
        self.counters = {}
        self.np_legacy = _RandomSource("np-legacy", None)
        self.py_random = _RandomSource("python", None)
        self.builtins = _make_builtins()

    # -- bookkeeping --------------------------------------------------------

    def warn(self, message, lineno=None):
        lineno = self.lineno if lineno is None else lineno
        text = f"line {lineno}: {message}" if lineno else message
        if text not in self.warnings:
            self.warnings.append(text)
            self.notes.append((lineno, message))

    def error(self, message, lineno=None, cause=None):
        """A StaticParseError at ``lineno`` (default: the current line).

        ``cause`` is the Unknown (or _NeedsUnknown) that stopped the parse, if
        any; when it came from a variable or argument, the error says which, so
        the caller can ask the user for a value.
        """
        return StaticParseError(message, self.lineno if lineno is None else lineno,
                                needs=_needs_of(cause))

    def unknown_value(self, name, value, lineno=None, kind="variable"):
        """What ``name`` gets when it is assigned the unknown ``value`` at ``lineno``.

        The user's value for that name and line if they gave one (see
        StaticQiskitParser's ``bindings``); otherwise ``value``, marked as coming
        from here unless it already traces back to somewhere earlier.
        """
        lineno = self.lineno if lineno is None else lineno
        key = (lineno, name)
        if key in self.bindings:
            self.used_bindings.add(key)
            return _copy.deepcopy(self.bindings[key])
        if value.origin is None:
            value.origin = (name, lineno, kind, value.text)
        return value

    def next_name(self, prefix):
        count = self.counters.get(prefix, 0)
        self.counters[prefix] = count + 1
        return f"{prefix}{count}"

    def _tick(self, node):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise self.error("the program runs too long to analyse statically "
                             f"(more than {MAX_STEPS} steps)", getattr(node, "lineno", None))
        if self.deadline is not None and not self.steps % 1024 and time.monotonic() > self.deadline:
            raise self.error("the program runs too long to analyse statically "
                             "(time limit reached)", getattr(node, "lineno", None))
        self.lineno = getattr(node, "lineno", self.lineno)
        if isinstance(node, ast.stmt):
            self._stmt_node = node

    def here(self):
        """The source location of whatever is placing an instruction right now."""
        node = self._call_node if self._call_node is not None else self._stmt_node
        return SourceLocation.of(node, self._via) if node is not None else None

    def check_size(self, value):
        try:
            size = value.size if _np is not None and isinstance(value, _np.ndarray) else (
                len(value) if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset))
                else 0)
        except TypeError:
            return value
        if size > MAX_COLLECTION:
            raise self.error(f"a value grows past {MAX_COLLECTION} elements, too large to analyse")
        return value

    def bounded(self, iterable):
        """Materialize ``iterable`` into a list, refusing to grow it without limit."""
        out = []
        for item in iterable:
            out.append(item)
            if len(out) > MAX_COLLECTION:
                raise self.error(f"a collection grows past {MAX_COLLECTION} elements")
        return out

    # -- running ------------------------------------------------------------

    def run(self, tree):
        try:
            self.exec_block(tree.body, self.module_frame)
        except _ProgramError as err:
            raise StaticParseError(
                f"the program raises {type(err.exc).__name__}: {err.exc}", err.lineno,
                raised=type(err.exc).__name__) from None
        except _NeedsUnknown as exc:
            raise self.error(f"`{exc}` cannot be determined without running the program",
                             cause=exc) from None
        except (_Break, _Continue):
            raise self.error("'break' or 'continue' outside a loop") from None
        except _Return:
            raise self.error("'return' outside a function") from None
        except RecursionError:
            raise self.error("the program nests too deeply to analyse statically") from None

    def exec_block(self, statements, frame):
        for statement in statements:
            self.exec_stmt(statement, frame)

    def exec_stmt(self, node, frame):
        self._tick(node)
        handler = getattr(self, "_exec_" + type(node).__name__, None)
        if handler is None:
            raise self.error(f"`{type(node).__name__}` statements are not supported", node.lineno)
        try:
            handler(node, frame)
        except _NeedsUnknown as exc:
            self.skip(node, frame, f"statement that depends on unknown `{exc}`", cause=exc)
        except _PASSTHROUGH:
            raise
        except (RecursionError, MemoryError):
            raise self.error("the program is too large or nests too deeply to analyse "
                             "statically", node.lineno) from None
        except Exception as exc:
            raise _ProgramError(exc, node.lineno) from None

    # -- names --------------------------------------------------------------

    def lookup(self, name, frame):
        value = self.peek(name, frame)
        if value is not _MISSING:
            return value
        for path in self.star_imports:
            value = self.module_attr(path, name)
            if value is not None:
                return value
        if any(path.split(".")[0] not in _MODELLED_ROOTS for path in self.star_imports):
            self.warn(f"`{name}` may come from a star import the parser does not model")
            return Unknown(name)
        if hasattr(_builtins, name):
            self.warn(f"built-in `{name}` is not modelled; its result is unknown")
            return Unknown(name)
        # `from qiskit import *` is how many programs start. A name it may have
        # supplied that the parser does not model (transpile, or Aer in older
        # Qiskit) is treated like `from qiskit import transpile`: an opaque value
        # that is harmless until a circuit depends on it.
        for path in self.star_imports:
            if path.split(".")[0] == "qiskit":
                self.warn(f"`{name}` is taken to come from `from {path} import *`; the parser "
                          "does not model it")
                return _ModuleRef(f"{path}.{name}")
        raise NameError(f"name '{name}' is not defined")

    def peek(self, name, frame):
        """Look a name up without side effects; _MISSING if it is not bound."""
        if name not in frame.global_names:
            scope = frame
            while scope is not None:
                if name in scope.vars:
                    return scope.vars[name]
                scope = scope.parent
        if name in self.globals:
            return self.globals[name]
        if name in self.builtins:
            return self.builtins[name]
        return _MISSING

    def store(self, name, value, frame):
        if name in frame.global_names:
            self.globals[name] = value
            return
        if name in frame.nonlocal_names:
            scope = frame.parent
            while scope is not None and name not in scope.vars:
                scope = scope.parent
            (scope or frame).vars[name] = value
            return
        frame.vars[name] = value

    # -- skipping code that depends on unknown values -------------------------

    def skip(self, node, frame, what, cause=None):
        """Skip ``node`` because ``what`` is unknown -- if no circuit can depend on it.

        ``cause`` is the unknown value responsible, when there is one.
        """
        lineno = getattr(node, "lineno", None)
        if self.touches_circuits(node, frame):
            raise self.error(
                f"cannot analyse a {what}: only running the program could tell, and the "
                "code it controls works on a circuit", lineno, cause=cause)
        for name in _stored_names([node]):
            # Whatever the skipped code would have set is as unknown as its cause.
            self.store(name, self.unknown_value(name, _derived(name, cause), lineno), frame)
        # The skipped code may also have changed objects in place. Forgetting
        # them is what ends a polling loop such as
        #     while jobs:
        #         if job.status() in DONE: jobs.remove(job)
        # instead of spinning on a list that never shrinks.
        for name in _mutated_names([node]):
            value = self.peek(name, frame)
            if value is _MISSING or isinstance(value, (Unknown, _ModuleRef, _UserFunction)):
                continue
            self.store(name, Unknown(name), frame)
        self.warn(f"skipped a {what} (it does not touch any circuit)",
                  getattr(node, "lineno", None))

    def is_circuit_value(self, value, depth=0):
        if isinstance(value, _STATIC_TYPES):
            return True
        if isinstance(value, _Native):
            return value.qiskit
        if isinstance(value, _BoundMethod):
            return self.is_circuit_value(value.obj, depth)
        if isinstance(value, (list, tuple, set, frozenset)) and depth < 2:
            return any(self.is_circuit_value(v, depth + 1) for v in itertools.islice(value, 1000))
        if isinstance(value, dict) and depth < 2:
            return any(self.is_circuit_value(v, depth + 1)
                       for v in itertools.islice(value.values(), 1000))
        return False

    def _opaque_callee(self, func, frame):
        """Whether a call goes to code the parser does not model (so cannot change circuits)."""
        root = func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return False
        value = self.peek(root.id, frame)
        if isinstance(value, Unknown):
            return True
        if isinstance(value, _ModuleRef):
            return value.path.split(".")[0] not in _MODELLED_ROOTS
        return isinstance(value, _Native) and not value.qiskit

    def touches_circuits(self, node, frame, seen=None):
        """Whether running ``node`` could change a circuit."""
        seen = set() if seen is None else seen
        harmless = set()
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                    and sub.attr in _READ_ONLY_ATTRS):
                harmless.add(id(sub.value))
            elif isinstance(sub, ast.Call) and self._opaque_callee(sub.func, frame):
                for arg in list(sub.args) + [kw.value for kw in sub.keywords]:
                    if isinstance(arg, ast.Starred):
                        arg = arg.value
                    if isinstance(arg, ast.Name):
                        harmless.add(id(arg))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Name) or id(sub) in harmless:
                continue
            value = self.peek(sub.id, frame)
            if self.is_circuit_value(value):
                return True
            if isinstance(value, _UserFunction) and id(value) not in seen:
                seen.add(id(value))
                if self.touches_circuits(value.node, value.closure, seen):
                    return True
        return False

    # -- simple statements --------------------------------------------------

    def _exec_Expr(self, node, frame):
        self.eval(node.value, frame)

    def _exec_Pass(self, node, frame):
        pass

    def _exec_Break(self, node, frame):
        raise _Break()

    def _exec_Continue(self, node, frame):
        raise _Continue()

    def _exec_Return(self, node, frame):
        raise _Return(None if node.value is None else self.eval(node.value, frame))

    def _exec_Global(self, node, frame):
        frame.global_names.update(node.names)

    def _exec_Nonlocal(self, node, frame):
        frame.nonlocal_names.update(node.names)

    def _exec_Assign(self, node, frame):
        value = self.eval(node.value, frame)
        for target in node.targets:
            self.assign(target, value, frame)

    def _exec_AnnAssign(self, node, frame):
        if node.value is not None:
            self.assign(node.target, self.eval(node.value, frame), frame)

    def _exec_AugAssign(self, node, frame):
        target = node.target
        right = self.eval(node.value, frame)
        if isinstance(target, ast.Name):
            current = self.lookup(target.id, frame)
            self.store(target.id, self.augop(node.op, current, right), frame)
        elif isinstance(target, ast.Subscript):
            obj = self.eval(target.value, frame)
            key = self.eval(target.slice, frame)
            self.setitem(obj, key, self.augop(node.op, self.getitem(obj, key), right))
        elif isinstance(target, ast.Attribute):
            obj = self.eval(target.value, frame)
            self.setattr(obj, target.attr, self.augop(node.op, self.getattr(obj, target.attr), right))
        else:
            raise self.error("unsupported augmented assignment target")

    def _exec_Delete(self, node, frame):
        for target in node.targets:
            if isinstance(target, ast.Name):
                scope = frame if target.id not in frame.global_names else self.module_frame
                if target.id not in scope.vars:
                    raise NameError(f"name '{target.id}' is not defined")
                del scope.vars[target.id]
            elif isinstance(target, ast.Subscript):
                obj = self.eval(target.value, frame)
                key = self.eval(target.slice, frame)
                if isinstance(obj, Unknown):
                    continue
                if not isinstance(obj, (list, dict)):
                    raise self.error(f"cannot delete items of {type(obj).__name__}")
                del obj[key]
            else:
                raise self.error("deleting attributes is not supported")

    def _exec_Assert(self, node, frame):
        truth = self.truth(self.eval(node.test, frame))
        if truth is False:
            message = self.eval(node.msg, frame) if node.msg is not None else ""
            raise AssertionError(message)

    def _exec_Raise(self, node, frame):
        if node.exc is None:
            raise RuntimeError("re-raise outside an except block")
        exc = self.eval(node.exc, frame)
        if isinstance(exc, type) and issubclass(exc, BaseException):
            exc = exc()
        if not isinstance(exc, BaseException):
            exc = RuntimeError(f"raise {_text(node.exc)}")
        raise _ProgramError(exc, node.lineno)

    def _exec_Import(self, node, frame):
        for alias in node.names:
            if alias.asname:
                self.store(alias.asname, _ModuleRef(alias.name), frame)
            else:
                root = alias.name.split(".")[0]
                self.store(root, _ModuleRef(root), frame)

    def _exec_ImportFrom(self, node, frame):
        if node.level or not node.module:
            for alias in node.names:
                if alias.name != "*":
                    self.store(alias.asname or alias.name, Unknown(alias.name), frame)
            self.warn("relative imports are not followed")
            return
        for alias in node.names:
            if alias.name == "*":
                self.star_imports.append(node.module)
                continue
            value = self.module_attr(node.module, alias.name)
            if value is None:
                value = _ModuleRef(f"{node.module}.{alias.name}")
            self.store(alias.asname or alias.name, value, frame)

    def _exec_FunctionDef(self, node, frame):
        defaults = [self.eval(d, frame) for d in node.args.defaults]
        kw_defaults = [_MISSING if d is None else self.eval(d, frame)
                       for d in node.args.kw_defaults]
        function = _UserFunction(node, node.name, defaults, kw_defaults, frame,
                                 _is_generator(node))
        # Other decorators are not applied: the function is called as written.
        if any(self._is_memoizer(d, frame) for d in node.decorator_list):
            function.memo = {}
        self.store(node.name, function, frame)

    def _is_memoizer(self, decorator, frame):
        """Whether a decorator is functools.lru_cache / cache, used bare or called."""
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        root = target
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return False
        try:
            value = self.eval(target, frame)
        except StaticParseError:
            return False
        return isinstance(value, _Native) and value.name in ("lru_cache", "cache")

    def _exec_AsyncFunctionDef(self, node, frame):
        self.store(node.name, Unknown(f"async function {node.name}"), frame)

    def _exec_ClassDef(self, node, frame):
        self.warn(f"class `{node.name}` is not interpreted; using it gives unknown values")
        self.store(node.name, Unknown(node.name), frame)

    # -- compound statements ------------------------------------------------

    def _exec_If(self, node, frame):
        test = self.eval(node.test, frame)
        truth = self.truth(test)
        if truth is None:
            self.skip(node, frame, "`if` with an unknown condition", cause=test)
            return
        self.exec_block(node.body if truth else node.orelse, frame)

    def _exec_For(self, node, frame):
        iterable = self.eval(node.iter, frame)
        if isinstance(iterable, Unknown):
            self.skip(node, frame, "`for` loop over an unknown sequence", cause=iterable)
            return
        try:
            iterator = iter(self.iterable(iterable))
        except _NeedsUnknown as exc:
            self.skip(node, frame, "`for` loop over an unknown sequence", cause=exc)
            return
        for item in iterator:
            self._tick(node)
            self.assign(node.target, item, frame)
            try:
                self.exec_block(node.body, frame)
            except _Break:
                return
            except _Continue:
                continue
        self.exec_block(node.orelse, frame)

    def _exec_While(self, node, frame):
        first = True
        while True:
            test = self.eval(node.test, frame)
            truth = self.truth(test)
            if truth is None:
                if first:
                    self.skip(node, frame, "`while` loop with an unknown condition", cause=test)
                    return
                # The iterations already run stand; the rest are skipped like any
                # unknown code, as long as no circuit depends on them.
                if not self.touches_circuits(node, frame):
                    self.skip(node, frame, "`while` loop whose condition stops being known",
                              cause=test)
                    return
                raise self.error("the condition of this `while` loop stops being known "
                                 "part-way through", node.lineno, cause=test)
            first = False
            if not truth:
                break
            self._tick(node)
            try:
                self.exec_block(node.body, frame)
            except _Break:
                return
            except _Continue:
                continue
        self.exec_block(node.orelse, frame)

    def _exec_Try(self, node, frame):
        try:
            try:
                self.exec_block(node.body, frame)
            except _ProgramError as err:
                handler = self._matching_handler(node.handlers, err, frame)
                if handler is None:
                    raise
                if handler.name:
                    self.store(handler.name, err.exc, frame)
                self.exec_block(handler.body, frame)
            else:
                self.exec_block(node.orelse, frame)
        except StaticParseError:
            raise
        except BaseException:
            self.exec_block(node.finalbody, frame)
            raise
        self.exec_block(node.finalbody, frame)

    def _matching_handler(self, handlers, err, frame):
        for handler in handlers:
            if handler.type is None:
                return handler
            kinds = self.eval(handler.type, frame)
            kinds = kinds if isinstance(kinds, tuple) else (kinds,)
            for kind in kinds:
                if (isinstance(kind, type) and issubclass(kind, BaseException)
                        and isinstance(err.exc, kind)):
                    return handler
        return None

    def _exec_With(self, node, frame):
        self._with_items(node, 0, frame)

    def _with_items(self, node, index, frame):
        if index == len(node.items):
            self.exec_block(node.body, frame)
            return
        item = node.items[index]
        context = self.eval(item.context_expr, frame)
        if isinstance(context, _IfContext):
            self._with_if(context, node, index, item, frame)
        elif isinstance(context, _ElseContext):
            self._with_else(context.if_context, node, index, item, frame)
        else:
            if item.optional_vars is not None:
                self.assign(item.optional_vars, Unknown(_text(item.context_expr)), frame)
            self._with_items(node, index + 1, frame)

    def _with_if(self, context, node, index, item, frame):
        if context.entered:
            raise CircuitError("an if_test context can only be entered once")
        context.entered = True
        # The if_else spans the whole block, so its body's lines are part of it.
        context.location = SourceLocation.of(node, self._via) or context.location
        circuit = context.circuit
        scope = _Scope()
        circuit._scopes.append(scope)
        if item.optional_vars is not None:
            self.assign(item.optional_vars, _ElseContext(context), frame)
        self._run_block(circuit, scope, lambda: self._with_items(node, index + 1, frame),
                        lambda: self._close_if(context, scope))

    def _with_else(self, context, node, index, item, frame):
        circuit = context.circuit
        if context.instruction is None or context.else_used:
            raise CircuitError("else_ can only be used once, after its if_test block")
        block = circuit.current_block()
        if not block or block[-1] is not context.instruction:
            raise CircuitError("the else block must immediately follow its if_test block")
        context.else_used = True
        scope = _Scope()
        circuit._scopes.append(scope)
        if item.optional_vars is not None:
            self.assign(item.optional_vars, Unknown("else"), frame)
        self._run_block(circuit, scope, lambda: self._with_items(node, index + 1, frame),
                        lambda: self._close_else(context, scope))

    @staticmethod
    def _run_block(circuit, scope, body, close):
        """Run a control-flow block; close it on normal exit, break, continue or return."""
        try:
            body()
        except (_Break, _Continue, _Return):
            close()
            raise
        except BaseException:
            if circuit._scopes and circuit._scopes[-1] is scope:
                circuit._scopes.pop()
            raise
        close()

    def _close_if(self, context, scope):
        circuit = context.circuit
        circuit._scopes.pop()
        qubits = list(scope.qubits)
        clbits = list(context.condition_bits) + [
            b for b in scope.clbits if not any(b is c for c in context.condition_bits)]
        body = StaticCircuit.body(scope.instructions, qubits, clbits)
        operation = StaticGate("if_else", len(qubits), len(clbits), [body, None])
        instruction = StaticInstruction(operation, qubits, clbits, context.location,
                                        context.condition, context.condition_bits)
        context.instruction = instruction
        context.true_instructions = scope.instructions
        self.count(1)
        circuit.append(instruction)

    def _close_else(self, context, scope):
        circuit = context.circuit
        circuit._scopes.pop()
        instruction = context.instruction
        instruction.qubits = instruction.qubits + [
            b for b in scope.qubits if not any(b is q for q in instruction.qubits)]
        instruction.clbits = instruction.clbits + [
            b for b in scope.clbits if not any(b is c for c in instruction.clbits)]
        true_body = StaticCircuit.body(context.true_instructions, instruction.qubits,
                                       instruction.clbits)
        false_body = StaticCircuit.body(scope.instructions, instruction.qubits,
                                        instruction.clbits)
        instruction.operation = StaticGate("if_else", len(instruction.qubits),
                                           len(instruction.clbits), [true_body, false_body])
        if circuit._scopes:
            # The if_else already sits in an enclosing block, which must see the new bits.
            circuit._scopes[-1].note(instruction)

    # -- assignment ---------------------------------------------------------

    def assign(self, target, value, frame):
        if isinstance(target, ast.Name):
            if isinstance(value, Unknown):
                value = self.unknown_value(target.id, value)
            self.store(target.id, value, frame)
        elif isinstance(target, (ast.Tuple, ast.List)):
            self._unpack(target.elts, value, frame)
        elif isinstance(target, ast.Subscript):
            obj = self.eval(target.value, frame)
            key = self.eval(target.slice, frame)
            self.setitem(obj, key, value)
        elif isinstance(target, ast.Attribute):
            self.setattr(self.eval(target.value, frame), target.attr, value)
        else:
            raise self.error(f"cannot assign to {type(target).__name__}")

    def _unpack(self, targets, value, frame):
        if isinstance(value, Unknown):
            for target in targets:
                inner = target.value if isinstance(target, ast.Starred) else target
                self.assign(inner, _derived(_text(inner), value), frame)
            return
        items = self.bounded(self.iterable(value))
        starred = [i for i, t in enumerate(targets) if isinstance(t, ast.Starred)]
        if not starred:
            if len(items) != len(targets):
                raise ValueError(f"expected {len(targets)} values to unpack, got {len(items)}")
            for target, item in zip(targets, items):
                self.assign(target, item, frame)
            return
        star = starred[0]
        after = len(targets) - star - 1
        if len(items) < len(targets) - 1:
            raise ValueError("not enough values to unpack")
        for target, item in zip(targets[:star], items[:star]):
            self.assign(target, item, frame)
        self.assign(targets[star].value, items[star:len(items) - after], frame)
        for target, item in zip(targets[star + 1:], items[len(items) - after:]):
            self.assign(target, item, frame)

    def setitem(self, obj, key, value):
        if isinstance(obj, Unknown):
            return
        if isinstance(key, Unknown):
            raise _NeedsUnknown(key)
        if isinstance(obj, (list, dict)) or (_np is not None and isinstance(obj, _np.ndarray)):
            obj[key] = value
            return
        raise self.error(f"item assignment on {type(obj).__name__} is not supported")

    def setattr(self, obj, name, value):
        if isinstance(obj, Unknown):
            return
        if isinstance(obj, _Namespace):
            obj.values[name] = value
            return
        if isinstance(obj, StaticCircuit) and name == "name":
            obj.name = value if isinstance(value, str) else str(value)
            return
        if isinstance(obj, StaticGate) and name in ("name", "label"):
            setattr(obj, name, value)
            return
        raise self.error(f"assigning to `.{name}` of {type(obj).__name__} is not supported")

    # -- expressions --------------------------------------------------------

    def eval(self, node, frame):
        handler = getattr(self, "_eval_" + type(node).__name__, None)
        if handler is None:
            raise self.error(f"`{type(node).__name__}` expressions are not supported",
                             getattr(node, "lineno", None))
        try:
            return handler(node, frame)
        except _NeedsUnknown as exc:
            return _derived(_text(node), exc)

    def truth(self, value):
        """True/False, or None when the value is unknown."""
        if isinstance(value, Unknown):
            return None
        try:
            return bool(value)
        except _NeedsUnknown:
            return None

    def iterable(self, value):
        if isinstance(value, Unknown):
            raise _NeedsUnknown(value)
        if isinstance(value, (StaticRegister, StaticCircuit)):
            return list(value)
        if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset, range)):
            return value
        if _np is not None and isinstance(value, _np.ndarray):
            return value
        if isinstance(value, (enumerate, zip, map, filter, reversed, type({}.keys()),
                              type({}.values()), type({}.items()))):
            return value
        if hasattr(value, "__next__") and type(value).__module__ in ("builtins", "itertools"):
            return value
        raise TypeError(f"'{type(value).__name__}' object is not iterable")

    def _eval_Constant(self, node, frame):
        return node.value

    def _eval_Name(self, node, frame):
        return self.lookup(node.id, frame)

    def _eval_Attribute(self, node, frame):
        return self.getattr(self.eval(node.value, frame), node.attr)

    def _eval_Subscript(self, node, frame):
        return self.getitem(self.eval(node.value, frame), self.eval(node.slice, frame))

    def _eval_Slice(self, node, frame):
        return slice(*(None if part is None else self.eval(part, frame)
                       for part in (node.lower, node.upper, node.step)))

    def _eval_Index(self, node, frame):          # Python < 3.9 trees
        return self.eval(node.value, frame)

    def _eval_BinOp(self, node, frame):
        return self.binop(node.op, self.eval(node.left, frame), self.eval(node.right, frame))

    def _eval_UnaryOp(self, node, frame):
        value = self.eval(node.operand, frame)
        if isinstance(node.op, ast.Not):
            truth = self.truth(value)
            if truth is None:
                raise _NeedsUnknown(_derived(_text(node), value))
            return not truth
        if isinstance(value, Unknown):
            raise _NeedsUnknown(value)
        if isinstance(value, Symbol):
            if isinstance(node.op, ast.USub):
                return _negate(value)
            if isinstance(node.op, ast.UAdd):
                return value
        if isinstance(value, (Symbol,) + _STATIC_TYPES):
            raise TypeError(f"bad operand type for unary operator: {type(value).__name__}")
        return _UNARY_OPS[type(node.op)](value)

    def _eval_BoolOp(self, node, frame):
        result = self.eval(node.values[0], frame)
        for value in node.values[1:]:
            truth = self.truth(result)
            if truth is None:
                raise _NeedsUnknown(_derived(_text(node), result))
            if isinstance(node.op, ast.And) and not truth:
                return result
            if isinstance(node.op, ast.Or) and truth:
                return result
            result = self.eval(value, frame)
        return result

    def _eval_Compare(self, node, frame):
        left = self.eval(node.left, frame)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = self.eval(comparator, frame)
            result = self.compare(op, left, right)
            truth = self.truth(result)
            if truth is None:
                raise _NeedsUnknown(_derived(_text(node), result))
            if not truth:
                return result
            left = right
        return result

    def compare(self, op, left, right):
        if isinstance(op, (ast.Is, ast.IsNot)):
            if isinstance(left, Unknown) or isinstance(right, Unknown):
                raise _NeedsUnknown(_derived(_text(op), left, right))
            return (left is right) if isinstance(op, ast.Is) else (left is not right)
        for side in (left, right):
            if isinstance(side, Unknown):
                raise _NeedsUnknown(side)
        if isinstance(op, (ast.In, ast.NotIn)):
            container = list(right) if isinstance(right, (StaticRegister, StaticCircuit)) else right
            found = any(left is item for item in container) if isinstance(
                left, (StaticBit, StaticInstruction)) else left in container
            return found if isinstance(op, ast.In) else not found
        return _COMPARE_OPS[type(op)](left, right)

    def _eval_IfExp(self, node, frame):
        test = self.eval(node.test, frame)
        truth = self.truth(test)
        if truth is None:
            raise _NeedsUnknown(_derived(_text(node.test), test))
        return self.eval(node.body if truth else node.orelse, frame)

    def _display(self, elements, frame):
        out = []
        for element in elements:
            if isinstance(element, ast.Starred):
                out.extend(self.bounded(self.iterable(self.eval(element.value, frame))))
            else:
                out.append(self.eval(element, frame))
            if len(out) > MAX_COLLECTION:
                self.check_size(out)
        return out

    def _eval_List(self, node, frame):
        return self._display(node.elts, frame)

    def _eval_Tuple(self, node, frame):
        return tuple(self._display(node.elts, frame))

    def _eval_Set(self, node, frame):
        return set(self._display(node.elts, frame))

    def _eval_Dict(self, node, frame):
        out = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                mapping = self.eval(value, frame)
                if isinstance(mapping, Unknown):
                    raise _NeedsUnknown(mapping)
                out.update(mapping)
            else:
                out[self.eval(key, frame)] = self.eval(value, frame)
        return out

    def _comprehension(self, node, frame, emit):
        inner = _Frame({}, frame)

        def loop(index):
            if index == len(node.generators):
                emit(inner)
                return
            generator = node.generators[index]
            source = self.eval(generator.iter, frame if index == 0 else inner)
            for item in self.iterable(source):
                self._tick(generator)
                self.assign(generator.target, item, inner)
                keep = True
                for condition in generator.ifs:
                    test = self.eval(condition, inner)
                    truth = self.truth(test)
                    if truth is None:
                        raise _NeedsUnknown(_derived(_text(condition), test))
                    if not truth:
                        keep = False
                        break
                if keep:
                    loop(index + 1)

        loop(0)

    def _eval_ListComp(self, node, frame):
        out = []

        def emit(inner):
            out.append(self.eval(node.elt, inner))
            if len(out) > MAX_COLLECTION:
                self.check_size(out)

        self._comprehension(node, frame, emit)
        return out

    _eval_GeneratorExp = _eval_ListComp

    def _eval_SetComp(self, node, frame):
        return set(self._eval_ListComp(node, frame))

    def _eval_DictComp(self, node, frame):
        out = {}

        def emit(inner):
            out[self.eval(node.key, inner)] = self.eval(node.value, inner)
            if len(out) > MAX_COLLECTION:
                self.check_size(out)

        self._comprehension(node, frame, emit)
        return out

    def _eval_Lambda(self, node, frame):
        defaults = [self.eval(d, frame) for d in node.args.defaults]
        kw_defaults = [_MISSING if d is None else self.eval(d, frame)
                       for d in node.args.kw_defaults]
        return _UserFunction(node, "<lambda>", defaults, kw_defaults, frame)

    def _eval_NamedExpr(self, node, frame):
        value = self.eval(node.value, frame)
        self.store(node.target.id, value, frame)
        return value

    def _eval_JoinedStr(self, node, frame):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append(self._eval_FormattedValue(value, frame))
        return self.check_size("".join(parts))

    def _eval_FormattedValue(self, node, frame):
        value = self.eval(node.value, frame)
        if isinstance(value, Unknown):
            raise _NeedsUnknown(value)
        if node.conversion == ord("s"):
            value = str(value)
        elif node.conversion == ord("r"):
            value = repr(value)
        elif node.conversion == ord("a"):
            value = ascii(value)
        spec = "" if node.format_spec is None else self.eval(node.format_spec, frame)
        if isinstance(spec, Unknown):
            raise _NeedsUnknown(spec)
        self.check_format_spec(spec)
        return format(value, spec)

    def _eval_Starred(self, node, frame):
        raise self.error("a starred expression is only allowed in a call or a display")

    def check_format_spec(self, spec):
        if re.search(r"\d{5,}", spec) or "{" in spec or "*" in spec:
            raise self.error(f"format specification `{spec}` is not supported")

    def check_format_string(self, text):
        for _literal, field, spec, _conversion in string.Formatter().parse(text):
            if field and ("." in field or "[" in field):
                raise self.error("format fields that read attributes or items are not supported")
            if spec:
                self.check_format_spec(spec)

    # -- operators ----------------------------------------------------------

    def binop(self, op, left, right):
        for side in (left, right):
            if isinstance(side, Unknown):
                raise _NeedsUnknown(side)
        if isinstance(left, Symbol) or isinstance(right, Symbol):
            symbol = _SYMBOLIC_BINOPS.get(type(op))
            if (symbol and isinstance(left, (Symbol, numbers.Number))
                    and isinstance(right, (Symbol, numbers.Number))):
                return _symbolic_binop(symbol, left, right)
            raise TypeError("unsupported operation on a circuit parameter")
        if isinstance(left, _STATIC_TYPES) or isinstance(right, _STATIC_TYPES):
            raise self.error("operators on Qiskit objects are not supported by the static parser")
        self._guard_binop(op, left, right)
        return self.check_size(_BINARY_OPS[type(op)](left, right))

    def augop(self, op, left, right):
        """``left op= right``: in place for mutable containers, as Python does."""
        if isinstance(left, list) and isinstance(op, ast.Add):
            if isinstance(right, Unknown):
                raise _NeedsUnknown(right)
            items = self.bounded(self.iterable(right))
            if len(left) + len(items) > MAX_COLLECTION:
                self.check_size(left + items)
            left.extend(items)
            return left
        if isinstance(left, (set, dict)) and type(op) in _INPLACE_OPS:
            if isinstance(right, Unknown):
                raise _NeedsUnknown(right)
            return _INPLACE_OPS[type(op)](left, right)
        return self.binop(op, left, right)

    def _guard_binop(self, op, left, right):
        sequences = (str, bytes, list, tuple)
        too_big = False
        if isinstance(op, ast.Mult):
            for seq, count in ((left, right), (right, left)):
                if isinstance(seq, sequences) and isinstance(count, numbers.Integral):
                    too_big |= len(seq) * max(int(count), 0) > MAX_COLLECTION
        elif isinstance(op, ast.Add):
            if isinstance(left, sequences) and isinstance(right, sequences):
                too_big = len(left) + len(right) > MAX_COLLECTION
        elif isinstance(op, ast.Pow):
            if isinstance(left, numbers.Integral) and isinstance(right, numbers.Integral):
                base, exponent = abs(int(left)), int(right)
                too_big = base > 1 and exponent * base.bit_length() > 100_000
        elif isinstance(op, ast.LShift):
            too_big = isinstance(right, numbers.Integral) and int(right) > 100_000
        elif isinstance(op, ast.Mod) and isinstance(left, str):
            if re.search(r"\d{5,}", left) or "*" in left:
                raise self.error("this %-format string is not supported")
        if too_big:
            raise self.error("this expression builds a value too large to analyse statically")

    # -- subscripts and attributes ------------------------------------------

    def getitem(self, obj, key):
        if isinstance(obj, Unknown):
            raise _NeedsUnknown(obj)
        if isinstance(key, Unknown):
            raise _NeedsUnknown(key)
        if isinstance(obj, StaticRegister):
            return obj.bits[key]
        if isinstance(obj, StaticCircuit):
            return obj.data[key]
        if isinstance(obj, (list, tuple, str, bytes, dict, range)):
            return obj[key]
        if _np is not None and isinstance(obj, (_np.ndarray, _np.generic)):
            return obj[key]
        if isinstance(obj, _Native) and obj.pytype is not None:
            return Unknown(f"{obj.name}[...]")       # a type annotation such as list[int]
        raise TypeError(f"'{type(obj).__name__}' object is not subscriptable")

    def getattr(self, obj, name):
        if name.startswith("_"):
            raise self.error(f"access to the private attribute `{name}` is not supported")
        if isinstance(obj, Unknown):
            return _derived(f"{obj.text}.{name}", obj)
        if isinstance(obj, _ModuleRef):
            value = self.module_attr(obj.path, name)
            return value if value is not None else _ModuleRef(f"{obj.path}.{name}")
        if isinstance(obj, StaticCircuit):
            if name in ("name", "num_qubits", "num_clbits", "parameters"):
                return getattr(obj, name)
            if name in ("qubits", "clbits", "qregs", "cregs", "data"):
                return list(getattr(obj, name))
            if name == "num_parameters":
                return len(obj.parameters)
            if name == "global_phase":
                return 0.0
            if name == "metadata":
                return {}
            return _BoundMethod(obj, name)
        if isinstance(obj, StaticRegister):
            if name in ("name", "size"):
                return getattr(obj, name)
            return _BoundMethod(obj, name)
        if isinstance(obj, StaticGate):
            if name in ("name", "num_qubits", "num_clbits", "label", "definition"):
                return getattr(obj, name)
            if name == "params":
                return list(obj.params)
            return _BoundMethod(obj, name)
        if isinstance(obj, StaticInstruction):
            if name in ("operation", "name"):
                return getattr(obj, name)
            if name in ("qubits", "clbits", "params"):
                return tuple(getattr(obj, name))
            raise self.error(f"`CircuitInstruction.{name}` is not supported")
        if isinstance(obj, Symbol):
            if name == "name":
                return str(obj)
            raise self.error(f"`Parameter.{name}` is not supported")
        if isinstance(obj, (_InstructionSet, _RandomSource, _ArgParser)):
            return _BoundMethod(obj, name)
        if isinstance(obj, _Namespace):
            if name in obj.values:
                return obj.values[name]
            raise AttributeError(f"'Namespace' object has no attribute '{name}'")
        if isinstance(obj, StaticBit):
            return Unknown(f"{obj!r}.{name}")
        if _np is not None and isinstance(obj, (_np.ndarray, _np.generic)):
            if name in _NUMPY_ATTRS:
                return getattr(obj, name)
            if name in _NUMPY_METHODS:
                return _BoundMethod(obj, name)
        elif isinstance(obj, numbers.Number) and name in ("real", "imag", "numerator",
                                                           "denominator"):
            return getattr(obj, name)
        elif isinstance(obj, range) and name in ("start", "stop", "step"):
            return getattr(obj, name)
        else:
            methods = _PLAIN_METHODS.get(type(obj), ())
            if name in methods:
                return _BoundMethod(obj, name)
        raise self.error(f"`{type(obj).__name__}.{name}` is not supported by the static parser")

    # -- calls --------------------------------------------------------------

    def _eval_Call(self, node, frame):
        func = self.eval(node.func, frame)
        relevant = isinstance(func, _UserFunction) or self.is_qiskit_callable(func)
        saved = self._call_node
        try:
            args = []
            for arg in node.args:
                if isinstance(arg, ast.Starred):
                    args.extend(self.bounded(self.iterable(self.eval(arg.value, frame))))
                else:
                    args.append(self.eval(arg, frame))
            kwargs = {}
            for keyword in node.keywords:
                value = self.eval(keyword.value, frame)
                if keyword.arg is not None:
                    kwargs[keyword.arg] = value
                elif isinstance(value, Unknown):
                    raise _NeedsUnknown(value)
                else:
                    kwargs.update(value)
            self._call_node = node
            return self.call(func, args, kwargs)
        except _NeedsUnknown as exc:
            if relevant:
                raise self.error(f"`{_text(node)}` depends on `{exc}`, which cannot be "
                                 "determined without running the program", cause=exc) from None
            raise
        finally:
            self._call_node = saved

    def call_text(self):
        return _text(self._call_node) if self._call_node is not None else "call"

    def is_qiskit_callable(self, func):
        if isinstance(func, _Native):
            return func.qiskit
        return isinstance(func, _BoundMethod) and isinstance(func.obj, _STATIC_TYPES)

    def call(self, func, args, kwargs):
        if isinstance(func, _UserFunction):
            return self.call_user(func, args, kwargs)
        if isinstance(func, _Native):
            return self.note_circuit(func.fn(self, args, kwargs))
        if isinstance(func, _BoundMethod):
            result = self.call_method(func.obj, func.name, args, kwargs)
            if isinstance(func.obj, StaticCircuit):
                self.note_circuit_use(func.obj, func.name, args, kwargs)
            return self.note_circuit(result)
        if isinstance(func, (Unknown, _ModuleRef)):
            for value in itertools.chain(args, kwargs.values()):
                if isinstance(value, StaticCircuit):
                    self.warn(f"`{self.call_text()}` is not modelled; the circuit passed to it "
                              "is assumed to be left unchanged")
            return _derived(self.call_text(), func)
        if isinstance(func, type) and issubclass(func, BaseException):
            return func(*args)
        raise TypeError(f"'{type(func).__name__}' object is not callable")

    def note_circuit(self, value):
        """Remember a circuit the program has just been handed; returns ``value``."""
        if isinstance(value, StaticCircuit) and id(value) not in self._circuit_ids:
            self._circuit_ids.add(id(value))
            self.circuits.append((value, self.lineno))
        return value

    def note_circuit_use(self, circuit, method, args, kwargs):
        """Mark circuits that ``circuit.method(...)`` builds into or derives from."""
        if method in ("compose", "append"):
            for value in itertools.chain(args, kwargs.values()):
                if isinstance(value, StaticCircuit) and value is not circuit:
                    self.consumed.add(id(value))
        elif method in _DERIVING_METHODS:
            self.consumed.add(id(circuit))

    def call_user(self, function, args, kwargs):
        if function.memo is not None:
            # @lru_cache: the same arguments give back the same object.
            key = (tuple(args), tuple(sorted(kwargs.items(), key=lambda kv: kv[0])))
            try:
                if key in function.memo:
                    return function.memo[key]
            except (TypeError, _NeedsUnknown):
                key = None
            result = self._call_user(function, args, kwargs)
            if key is not None:
                function.memo[key] = result
            return result
        return self._call_user(function, args, kwargs)

    def _call_user(self, function, args, kwargs):
        if function.is_generator:
            self.warn(f"generator function `{function.name}` is not interpreted")
            return Unknown(self.call_text())
        if self.depth >= MAX_CALL_DEPTH:
            raise self.error(f"calls nest more than {MAX_CALL_DEPTH} deep")
        frame = _Frame(self._bind_function(function, args, kwargs), function.closure)
        saved_lineno, saved_stmt = self.lineno, self._stmt_node
        self.depth += 1
        self._via.append(self._call_node.lineno if self._call_node is not None else self.lineno)
        try:
            if isinstance(function.node, ast.Lambda):
                return self.eval(function.node.body, frame)
            self.exec_block(function.node.body, frame)
            return None
        except _Return as result:
            return result.value
        finally:
            self.depth -= 1
            self._via.pop()
            self.lineno, self._stmt_node = saved_lineno, saved_stmt

    def _bind_function(self, function, args, kwargs):
        spec = function.node.args
        name = function.name
        positional = [a.arg for a in spec.posonlyargs + spec.args]
        positional_only = {a.arg for a in spec.posonlyargs}
        keyword_only = [a.arg for a in spec.kwonlyargs]
        bound = dict(zip(positional, args))
        extra = list(args[len(positional):])
        if extra and spec.vararg is None:
            raise TypeError(f"{name}() takes {len(positional)} positional arguments "
                            f"but {len(args)} were given")
        if spec.vararg is not None:
            bound[spec.vararg.arg] = tuple(extra)
        rest = {}
        for key, value in kwargs.items():
            if (key in positional and key not in positional_only) or key in keyword_only:
                if key in bound:
                    raise TypeError(f"{name}() got multiple values for argument '{key}'")
                bound[key] = value
            elif spec.kwarg is not None:
                rest[key] = value
            else:
                raise TypeError(f"{name}() got an unexpected keyword argument '{key}'")
        first_default = len(positional) - len(function.defaults)
        for index, arg in enumerate(positional):
            if arg not in bound:
                if index < first_default:
                    raise TypeError(f"{name}() missing required argument: '{arg}'")
                bound[arg] = function.defaults[index - first_default]
        for arg, default in zip(keyword_only, function.kw_defaults):
            if arg not in bound:
                if default is _MISSING:
                    raise TypeError(f"{name}() missing required keyword argument: '{arg}'")
                bound[arg] = default
        if spec.kwarg is not None:
            bound[spec.kwarg.arg] = rest
        return bound

    def bind(self, fname, names, args, kwargs, defaults=None):
        """Match call arguments to a parameter list; unrecognised keywords go to ``extra``."""
        defaults = defaults or {}
        if len(args) > len(names):
            raise TypeError(f"{fname}() takes at most {len(names)} positional arguments "
                            f"but {len(args)} were given")
        bound = dict(zip(names, args))
        extra = {}
        for key, value in kwargs.items():
            if key in names:
                if key in bound:
                    raise TypeError(f"{fname}() got multiple values for argument '{key}'")
                bound[key] = value
            else:
                extra[key] = value
        for name in names:
            if name not in bound:
                if name not in defaults:
                    raise TypeError(f"{fname}() missing required argument: '{name}'")
                bound[name] = defaults[name]
        return bound, extra

    def flag(self, value, what):
        truth = self.truth(value)
        if truth is None:
            raise self.error(f"`{what}` cannot be determined without running the program",
                             cause=value)
        return truth

    # -- handing values to real Python code ----------------------------------

    def to_py(self, value):
        if isinstance(value, _Native) and value.pytype is not None:
            return value.pytype
        if isinstance(value, (_UserFunction, _Native, _BoundMethod)):
            node = self._call_node

            def call(*args, **kwargs):
                saved = self._call_node
                self._call_node = node
                try:
                    return self.call(value, list(args), kwargs)
                finally:
                    self._call_node = saved
            return call
        if isinstance(value, tuple):
            return tuple(self.to_py(v) for v in value)
        return value

    def call_python(self, fn, args, kwargs, deep=False):
        """Call a real, side-effect-free Python function on interpreted values.

        ``deep`` also refuses unknowns nested in containers, for code (numpy) that
        would otherwise turn them into arrays of opaque objects.
        """
        for value in itertools.chain(args, kwargs.values()):
            unknown = _find_unknown(value, 2 if deep else 0)
            if unknown is not None:
                raise _NeedsUnknown(unknown)
        result = fn(*[self.to_py(a) for a in args],
                    **{k: self.to_py(v) for k, v in kwargs.items()})
        return self.check_size(result)

    # -- methods ------------------------------------------------------------

    def call_method(self, obj, name, args, kwargs):
        if isinstance(obj, StaticCircuit):
            return self.circuit_method(obj, name, args, kwargs)
        if isinstance(obj, StaticGate):
            return self.gate_method(obj, name, args, kwargs)
        if isinstance(obj, _InstructionSet):
            if name == "inverse":
                for instruction in obj.instructions:
                    instruction.operation = instruction.operation.inverse()
                return obj
            raise AttributeError(f"'InstructionSet' object has no attribute '{name}'")
        if isinstance(obj, StaticRegister):
            if name == "index":
                bit = args[0] if args else None
                for i, candidate in enumerate(obj.bits):
                    if candidate is bit:
                        return i
                raise ValueError(f"{bit!r} is not in register {obj!r}")
            raise self.error(f"`Register.{name}()` is not supported by the static parser")
        if isinstance(obj, _RandomSource):
            return self.random_call(obj, name, args, kwargs)
        if isinstance(obj, _ArgParser):
            return self.argparse_method(obj, name, args, kwargs)
        return self.plain_method(obj, name, args, kwargs)

    # -- argparse -------------------------------------------------------------

    def argparse_method(self, parser, name, args, kwargs):
        if name == "add_argument":
            return self._add_argument(parser, args, kwargs)
        if name in ("add_argument_group", "add_mutually_exclusive_group"):
            return parser           # its arguments land in the same namespace
        if name == "set_defaults":
            parser.defaults.update(kwargs)
            return None
        if name in ("parse_args", "parse_known_args", "parse_intermixed_args"):
            argv = args[0] if args else kwargs.get("args")
            if argv is not None:
                self.warn(f"`{self.call_text()}` is given its own argument list; the parser "
                          "ignores it and uses the defaults")
            values = dict(parser.arguments)
            values.update(parser.defaults)
            namespace = _Namespace(values)
            shown = ", ".join(f"{k}={'?' if isinstance(v, Unknown) else repr(v)}"
                              for k, v in values.items())
            self.warn(f"command-line arguments take their defaults ({shown})")
            return (namespace, []) if name == "parse_known_args" else namespace
        if name in ("print_help", "print_usage", "format_help", "format_usage"):
            return "" if name.startswith("format") else None
        if name in ("error", "exit"):
            raise SystemExit(args[0] if args else 0)
        self.warn(f"`ArgumentParser.{name}()` is not modelled; its result is unknown")
        return Unknown(self.call_text())

    def _add_argument(self, parser, flags, kwargs):
        for flag in flags:
            if not isinstance(flag, str):
                raise _NeedsUnknown(flag) if isinstance(flag, Unknown) else TypeError(
                    "argument names must be strings")
        if not flags and "dest" not in kwargs:
            raise TypeError("add_argument() needs a name or flag")
        positional = bool(flags) and not flags[0].startswith("-")
        dest = kwargs.get("dest")
        if dest is None:
            if positional:
                dest = flags[0]
            else:
                long = next((f for f in flags if f.startswith("--")), flags[0])
                dest = long.lstrip("-").replace("-", "_")
        action = kwargs.get("action", "store")
        if action in ("help", "version"):
            return None
        nargs = kwargs.get("nargs")
        if "default" in kwargs:
            value = kwargs["default"]
        elif action == "store_true":
            value = False
        elif action == "store_false":
            value = True
        elif positional and nargs == "*":
            value = []
        else:
            value = None
        # argparse runs a string default through `type`, as if it had been typed.
        if (isinstance(value, str) and "type" in kwargs and action == "store"
                and kwargs["type"] is not None):
            value = self.call(kwargs["type"], [value], {})
        required = (positional and nargs not in ("?", "*")) or kwargs.get("required") is True
        if required:
            flag = flags[0] if flags else dest
            value = self.unknown_value(dest, Unknown(f"command-line argument {flag}"),
                                       kind="argument")
        parser.arguments.append((dest, value))
        return None

    def plain_method(self, obj, name, args, kwargs):
        """A whitelisted method of a str, list, dict, set, number or numpy array."""
        if name not in _STORING_METHODS:
            for value in itertools.chain(args, kwargs.values()):
                if isinstance(value, Unknown):
                    raise _NeedsUnknown(value)
                if isinstance(value, (list, tuple)):
                    inner = next((v for v in itertools.islice(value, 10_000)
                                  if isinstance(v, Unknown)), None)
                    if inner is not None:
                        raise _NeedsUnknown(_derived(_text(self._call_node), inner))
        if name in ("zfill", "ljust", "rjust", "center", "expandtabs") and args:
            if isinstance(args[0], numbers.Integral) and int(args[0]) > MAX_COLLECTION:
                raise self.error("this string would be too large to analyse")
        if name == "format" and isinstance(obj, str):
            self.check_format_string(obj)
        if name == "replace" and isinstance(obj, str) and len(args) >= 2:
            count = obj.count(args[0]) if args[0] else len(obj) + 1
            if count * len(str(args[1])) > MAX_COLLECTION:
                raise self.error("this string would be too large to analyse")
        if name in ("extend", "update") and isinstance(obj, (list, set)) and args:
            args = [self.bounded(self.iterable(args[0]))] + list(args[1:])
            if len(obj) + len(args[0]) > MAX_COLLECTION:
                raise self.error(f"a collection grows past {MAX_COLLECTION} elements")
        result = getattr(obj, name)(*[self.to_py(a) for a in args],
                                    **{k: self.to_py(v) for k, v in kwargs.items()})
        return self.check_size(result)

    # -- placing instructions -----------------------------------------------

    def count(self, n):
        self.placed += n
        if self.placed > MAX_INSTRUCTIONS:
            raise self.error(f"the program places more than {MAX_INSTRUCTIONS} instructions")

    def gate_param(self, value):
        if isinstance(value, Unknown):
            self.warn(f"gate parameter `{value.text}` cannot be computed statically; "
                      "it is kept as a symbol")
            return Symbol.leaf(value.text)
        if isinstance(value, (Symbol, str)):
            return value
        if isinstance(value, numbers.Real):
            return float(value)
        if isinstance(value, numbers.Complex):
            return complex(value)
        raise CircuitError(f"invalid gate parameter of type {type(value).__name__}")

    def resolve_bits(self, circuit, spec, kind):
        """The bits a qubit/clbit argument names: an index, a bit, a register or a list."""
        if isinstance(spec, Unknown):
            raise self.error(f"the {kind} argument `{spec.text}` cannot be determined "
                             "without running the program", cause=spec)
        if isinstance(spec, (StaticBit, numbers.Integral)):
            return [self._one_bit(circuit, spec, kind)]
        if isinstance(spec, StaticRegister):
            if spec.kind != kind:
                raise CircuitError(f"expected a {kind} register, got {spec!r}")
            return [self._one_bit(circuit, bit, kind) for bit in spec.bits]
        if isinstance(spec, slice):
            return list((circuit.qubits if kind == "qubit" else circuit.clbits)[spec])
        if isinstance(spec, (list, tuple, range)) or (
                _np is not None and isinstance(spec, _np.ndarray)):
            return [self._one_bit(circuit, item, kind) for item in spec]
        raise CircuitError(f"invalid {kind} argument: {spec!r}")

    def _one_bit(self, circuit, spec, kind):
        if isinstance(spec, Unknown):
            raise self.error(f"the {kind} argument `{spec.text}` cannot be determined "
                             "without running the program", cause=spec)
        if isinstance(spec, StaticBit):
            if spec.kind != kind or not circuit.has(spec):
                raise CircuitError(f"{spec!r} is not a {kind} of circuit '{circuit.name}'")
            return spec
        if isinstance(spec, numbers.Integral):
            bits = circuit.qubits if kind == "qubit" else circuit.clbits
            try:
                return bits[int(spec)]
            except IndexError:
                raise CircuitError(f"Index {int(spec)} out of range for size {len(bits)}.") from None
        raise CircuitError(f"invalid {kind} argument: {spec!r}")

    def place(self, circuit, gate, qubit_specs, clbit_specs):
        """Put ``gate`` on the circuit, broadcasting arguments the way Qiskit does."""
        if gate.name == "barrier":
            bits = []
            for spec in qubit_specs:
                for bit in self.resolve_bits(circuit, spec, "qubit"):
                    if not any(bit is b for b in bits):
                        bits.append(bit)
            gate = StaticGate("barrier", len(bits), 0, [], label=gate.label)
            instruction = StaticInstruction(gate, bits, [], self.here())
            self.count(1)
            circuit.append(instruction)
            return _InstructionSet([instruction])
        lists = ([self.resolve_bits(circuit, s, "qubit") for s in qubit_specs]
                 + [self.resolve_bits(circuit, s, "clbit") for s in clbit_specs])
        if len(qubit_specs) != gate.num_qubits or len(clbit_specs) != gate.num_clbits:
            raise CircuitError(
                f"'{gate.name}' expects {gate.num_qubits} qubit and {gate.num_clbits} clbit "
                f"arguments, got {len(qubit_specs)} and {len(clbit_specs)}")
        location = self.here()
        placed = [StaticInstruction(gate, qubits, clbits, location)
                  for qubits, clbits in broadcast(gate, lists)]
        self.count(len(placed))
        for instruction in placed:
            circuit.append(instruction)
        return _InstructionSet(placed)

    def _insert(self, circuit, instructions, front):
        self.count(len(instructions))
        if front:
            if circuit._scopes:
                raise self.error("compose(front=True) inside a control-flow block is not supported")
            circuit.data[0:0] = instructions
        else:
            for instruction in instructions:
                circuit.append(instruction)

    def _specs(self, value, what):
        """A list of qubit/clbit arguments, as ``append`` takes them."""
        if value is None:
            return []
        if isinstance(value, Unknown):
            raise self.error(f"`{what}` cannot be determined without running the program",
                             cause=value)
        if isinstance(value, StaticRegister):
            return list(value.bits)
        if isinstance(value, (list, tuple, range)) or (
                _np is not None and isinstance(value, _np.ndarray)):
            return list(value)
        raise CircuitError(f"`{what}` must be a list of bits")

    # -- QuantumCircuit methods ---------------------------------------------

    def circuit_method(self, circuit, name, args, kwargs):
        if name in _GATE_METHODS:
            gate_name, param_names, qubit_names = _GATE_METHODS[name]
            bound, extra = self.bind(name, param_names + qubit_names, args, kwargs)
            gate = StaticGate(gate_name, len(qubit_names), 0,
                              [self.gate_param(bound[p]) for p in param_names],
                              label=extra.get("label"))
            return self.place(circuit, gate, [bound[q] for q in qubit_names], [])
        handler = getattr(self, "_qc_" + name, None)
        if handler is not None:
            return handler(circuit, args, kwargs)
        if name in _INFO_METHODS:
            return Unknown(self.call_text())
        raise self.error(f"`QuantumCircuit.{name}()` is not supported by the static parser")

    def _qc_measure(self, circuit, args, kwargs):
        bound, _ = self.bind("measure", ["qubit", "cbit"], args, kwargs)
        return self.place(circuit, StaticGate("measure", 1, 1), [bound["qubit"]], [bound["cbit"]])

    def _qc_reset(self, circuit, args, kwargs):
        bound, _ = self.bind("reset", ["qubit"], args, kwargs)
        return self.place(circuit, StaticGate("reset", 1), [bound["qubit"]], [])

    def _qc_barrier(self, circuit, args, kwargs):
        specs = list(args) if args else [list(circuit.qubits)]
        return self.place(circuit, StaticGate("barrier", 0, label=kwargs.get("label")), specs, [])

    def _qc_delay(self, circuit, args, kwargs):
        bound, _ = self.bind("delay", ["duration", "qarg", "unit"], args, kwargs,
                             {"qarg": None, "unit": "dt"})
        target = list(circuit.qubits) if bound["qarg"] is None else bound["qarg"]
        return self.place(circuit, StaticGate("delay", 1, 0, [bound["duration"]]), [target], [])

    def _controlled(self, circuit, name, controls, target, params=(), ancillas=None):
        control_bits = self.resolve_bits(circuit, controls, "qubit")
        specs = [[bit] for bit in control_bits] + [target]
        if ancillas is not None:
            specs += [[bit] for bit in self.resolve_bits(circuit, ancillas, "qubit")]
        gate = StaticGate(name, len(specs), 0, [self.gate_param(p) for p in params])
        return self.place(circuit, gate, specs, [])

    def _qc_mcx(self, circuit, args, kwargs):
        bound, _ = self.bind("mcx", ["control_qubits", "target_qubit", "ancilla_qubits", "mode",
                                     "ctrl_state"], args, kwargs,
                             {"ancilla_qubits": None, "mode": None, "ctrl_state": None})
        controls = self.resolve_bits(circuit, bound["control_qubits"], "qubit")
        return self._controlled(circuit, _mcx_name(len(controls)), controls,
                                bound["target_qubit"], (), bound["ancilla_qubits"])

    def _qc_mcp(self, circuit, args, kwargs):
        bound, _ = self.bind("mcp", ["lam", "control_qubits", "target_qubit", "ctrl_state"],
                             args, kwargs, {"ctrl_state": None})
        return self._controlled(circuit, "mcphase", bound["control_qubits"],
                                bound["target_qubit"], [bound["lam"]])

    def _mc_rotation(self, name, circuit, args, kwargs):
        bound, _ = self.bind(name, ["theta", "q_controls", "q_target", "use_basis_gates"],
                             args, kwargs, {"use_basis_gates": False})
        self.warn(f"`{name}` is recorded as one instruction, not Qiskit's synthesized sequence")
        return self._controlled(circuit, name, bound["q_controls"], bound["q_target"],
                                [bound["theta"]])

    def _qc_mcrx(self, circuit, args, kwargs):
        return self._mc_rotation("mcrx", circuit, args, kwargs)

    def _qc_mcry(self, circuit, args, kwargs):
        return self._mc_rotation("mcry", circuit, args, kwargs)

    def _qc_mcrz(self, circuit, args, kwargs):
        return self._mc_rotation("mcrz", circuit, args, kwargs)

    def _on_bits(self, circuit, name, params, qubits):
        """One instruction over an explicit list of qubits (no broadcasting)."""
        bits = (list(circuit.qubits) if qubits is None
                else self.resolve_bits(circuit, qubits, "qubit"))
        gate = StaticGate(name, len(bits), 0, params)
        return self.place(circuit, gate, [[bit] for bit in bits], [])

    def _qc_unitary(self, circuit, args, kwargs):
        bound, _ = self.bind("unitary", ["obj", "qubits", "label"], args, kwargs, {"label": None})
        return self._on_bits(circuit, "unitary", [bound["obj"]], bound["qubits"])

    def _qc_pauli(self, circuit, args, kwargs):
        bound, _ = self.bind("pauli", ["pauli_string", "qubits"], args, kwargs)
        return self._on_bits(circuit, "pauli", [bound["pauli_string"]], bound["qubits"])

    def _state_params(self, state):
        if isinstance(state, (str, numbers.Integral)):
            return [state]
        return [complex(v) for v in self.bounded(self.iterable(state))]

    def _qc_initialize(self, circuit, args, kwargs):
        bound, _ = self.bind("initialize", ["params", "qubits", "normalize"], args, kwargs,
                             {"qubits": None, "normalize": False})
        return self._on_bits(circuit, "initialize", self._state_params(bound["params"]),
                             bound["qubits"])

    def _qc_prepare_state(self, circuit, args, kwargs):
        bound, _ = self.bind("prepare_state", ["state", "qubits", "label", "normalize"], args,
                             kwargs, {"qubits": None, "label": None, "normalize": False})
        return self._on_bits(circuit, "state_preparation", self._state_params(bound["state"]),
                             bound["qubits"])

    def _qc_measure_all(self, circuit, args, kwargs):
        bound, _ = self.bind("measure_all", ["inplace", "add_bits"], args, kwargs,
                             {"inplace": True, "add_bits": True})
        inplace = self.flag(bound["inplace"], "inplace")
        target = circuit if inplace else circuit.copy()
        n = target.num_qubits
        if self.flag(bound["add_bits"], "add_bits"):
            register = StaticRegister("clbit", "meas", n)
            target.add_register(register)
            clbits = register.bits
        else:
            if target.num_clbits < n:
                raise CircuitError("The number of classical bits must be equal or greater "
                                   "than the number of qubits.")
            clbits = target.clbits[:n]
        self.place(target, StaticGate("barrier", n), [list(target.qubits)], [])
        self.place(target, StaticGate("measure", 1, 1), [list(target.qubits)], [list(clbits)])
        return None if inplace else target

    def _qc_append(self, circuit, args, kwargs):
        bound, _ = self.bind("append", ["instruction", "qargs", "cargs"], args, kwargs,
                             {"qargs": None, "cargs": None})
        operation = bound["instruction"]
        if isinstance(operation, StaticCircuit):
            operation = operation.to_instruction()
        if not isinstance(operation, StaticGate):
            raise self.error(f"cannot append `{_text(self._call_node.args[0]) if self._call_node.args else '?'}`: "
                             "the static parser cannot see what instruction it is")
        return self.place(circuit, operation, self._specs(bound["qargs"], "qargs"),
                          self._specs(bound["cargs"], "cargs"))

    def _qc_compose(self, circuit, args, kwargs):
        bound, _ = self.bind("compose", ["other", "qubits", "clbits", "front", "inplace", "wrap"],
                             args, kwargs, {"qubits": None, "clbits": None, "front": False,
                                            "inplace": False, "wrap": False})
        other = bound["other"]
        front = self.flag(bound["front"], "front")
        inplace = self.flag(bound["inplace"], "inplace")
        if isinstance(other, StaticCircuit) and self.flag(bound["wrap"], "wrap"):
            other = other.to_gate() if other.num_clbits == 0 else other.to_instruction()
        target = circuit if inplace else circuit.copy()
        if isinstance(other, StaticGate):
            qubits = (target.qubits[:other.num_qubits] if bound["qubits"] is None
                      else self.resolve_bits(target, bound["qubits"], "qubit"))
            clbits = (target.clbits[:other.num_clbits] if bound["clbits"] is None
                      else self.resolve_bits(target, bound["clbits"], "clbit"))
            if len(qubits) != other.num_qubits or len(clbits) != other.num_clbits:
                raise CircuitError("the qubits/clbits given do not match the instruction's width")
            self._insert(target, [StaticInstruction(other, qubits, clbits, self.here())], front)
        elif isinstance(other, StaticCircuit):
            if other.num_qubits > target.num_qubits or other.num_clbits > target.num_clbits:
                raise CircuitError("Trying to compose with another QuantumCircuit which has "
                                   "more 'in' edges.")
            qubits = (target.qubits[:other.num_qubits] if bound["qubits"] is None
                      else self.resolve_bits(target, bound["qubits"], "qubit"))
            clbits = (target.clbits[:other.num_clbits] if bound["clbits"] is None
                      else self.resolve_bits(target, bound["clbits"], "clbit"))
            if len(qubits) != other.num_qubits or len(clbits) != other.num_clbits:
                raise CircuitError("Number of items in qubits parameter does not match number "
                                   "of qubits in the circuit.")
            qmap = dict(zip(other.qubits, qubits))
            cmap = dict(zip(other.clbits, clbits))
            self._insert(target, [_remap(i, qmap, cmap, self.here()) for i in other.data], front)
        else:
            raise self.error("cannot compose this value: the static parser cannot see a "
                             "circuit or gate in it")
        return None if inplace else target

    def _qc_if_test(self, circuit, args, kwargs):
        bound, _ = self.bind("if_test", ["condition", "true_body", "qubits", "clbits"], args,
                             kwargs, {"true_body": None, "qubits": None, "clbits": None})
        if bound["true_body"] is not None:
            raise self.error("if_test with an explicit body is not supported; "
                             "use `with qc.if_test(...):`")
        condition = bound["condition"]
        if isinstance(condition, Unknown):
            raise self.error(f"the condition `{condition.text}` cannot be determined statically",
                             cause=condition)
        if not (isinstance(condition, tuple) and len(condition) == 2):
            raise self.error("only (clbit, value) and (register, value) conditions are supported")
        target, value = condition
        if isinstance(target, Unknown):
            raise self.error(f"the condition `{target.text}` cannot be determined statically",
                             cause=target)
        if isinstance(target, StaticRegister):
            if target.kind != "clbit":
                raise CircuitError("a condition must test classical bits")
            bits = [self._one_bit(circuit, b, "clbit") for b in target.bits]
        else:
            bit = self._one_bit(circuit, target, "clbit")
            target = bit
            bits = [bit]
        return _IfContext(circuit, (target, value), bits, self.here())

    def _qc_to_gate(self, circuit, args, kwargs):
        return circuit.to_gate(kwargs.get("label"))

    def _qc_to_instruction(self, circuit, args, kwargs):
        return circuit.to_instruction(kwargs.get("label"))

    def _qc_inverse(self, circuit, args, kwargs):
        return circuit.inverse()

    def _qc_copy(self, circuit, args, kwargs):
        bound, _ = self.bind("copy", ["name"], args, kwargs, {"name": None})
        return circuit.copy(bound["name"])

    def _qc_copy_empty_like(self, circuit, args, kwargs):
        bound, _ = self.bind("copy_empty_like", ["name"], args, kwargs, {"name": None})
        return circuit.copy_empty(bound["name"])

    def _qc_decompose(self, circuit, args, kwargs):
        bound, _ = self.bind("decompose", ["gates_to_decompose", "reps"], args, kwargs,
                             {"gates_to_decompose": None, "reps": 1})
        names = bound["gates_to_decompose"]
        if isinstance(names, str):
            names = [names]
        elif names is not None:
            names = [n.name if isinstance(n, StaticGate) else n for n in self.iterable(names)]
        return circuit.decompose(names, int(bound["reps"]))

    def _qc_add_register(self, circuit, args, kwargs):
        for register in args:
            if isinstance(register, StaticRegister):
                circuit.add_register(register)
            elif isinstance(register, (list, tuple)):
                circuit.add_bits(register)
            else:
                raise self.error("add_register() needs registers the static parser can see")

    def _qc_size(self, circuit, args, kwargs):
        return len(circuit.data)

    def _qc_width(self, circuit, args, kwargs):
        return circuit.num_qubits + circuit.num_clbits

    def _qc_count_ops(self, circuit, args, kwargs):
        counts = {}
        for instruction in circuit.data:
            counts[instruction.operation.name] = counts.get(instruction.operation.name, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))

    def _qc_assign_parameters(self, circuit, args, kwargs):
        bound, _ = self.bind("assign_parameters", ["parameters", "inplace"], args, kwargs,
                             {"inplace": False})
        values = bound["parameters"]
        if isinstance(values, Unknown):
            raise self.error("the parameter values cannot be determined statically", cause=values)
        mapping = {}
        if isinstance(values, dict):
            for key, value in values.items():
                if isinstance(key, Symbol):
                    mapping[key] = value
                elif isinstance(key, (list, tuple)):          # a ParameterVector
                    mapping.update(zip(key, self.iterable(value)))
        else:
            values = self.bounded(self.iterable(values))
            free = circuit.parameters
            if len(values) != len(free):
                raise ValueError(f"expected {len(free)} parameter values, got {len(values)}")
            mapping = dict(zip(free, values))
        inplace = self.flag(bound["inplace"], "inplace")
        target = circuit if inplace else circuit.copy()
        for instruction in target.data:
            params = instruction.operation.params
            if any(isinstance(p, Symbol) for p in params):
                new = [p.bind(mapping) if isinstance(p, Symbol) else p for p in params]
                instruction.operation = instruction.operation.with_params(
                    [self.gate_param(p) if not isinstance(p, Symbol) else p for p in new])
        return None if inplace else target

    # -- gate object methods ------------------------------------------------

    def gate_method(self, gate, name, args, kwargs):
        if name == "inverse":
            return gate.inverse()
        if name == "control":
            bound, _ = self.bind("control", ["num_ctrl_qubits", "label", "ctrl_state"], args,
                                 kwargs, {"num_ctrl_qubits": 1, "label": None, "ctrl_state": None})
            count = bound["num_ctrl_qubits"]
            if not isinstance(count, numbers.Integral):
                raise self.error("the number of control qubits cannot be determined statically",
                                 cause=count)
            return gate.control(int(count))
        if name == "copy":
            return gate.with_params(list(gate.params))
        if name == "to_matrix":
            return Unknown(self.call_text())
        raise self.error(f"`Gate.{name}()` is not supported by the static parser")

    # -- random numbers -----------------------------------------------------

    def random_call(self, source, name, args, kwargs):
        sizes = _RANDOM_METHODS[source.kind]
        if name not in sizes:
            raise self.error(f"random method `{name}` is not supported by the static parser")
        if source.impl is None:
            self.warn(f"`{self.call_text()}` draws from an unseeded random generator, "
                      "so its value is unknown")
            return Unknown(self.call_text())
        spec = sizes[name]
        if spec == "dims":
            dims = list(args)
        elif spec == "perm":
            dims = [args[0]] if args and isinstance(args[0], numbers.Integral) else []
        elif spec is None:
            dims = []
        else:
            size = kwargs.get("size", args[spec] if len(args) > spec else None)
            dims = [] if size is None else (list(size) if isinstance(size, (list, tuple)) else [size])
        total = 1
        for dim in dims:
            if isinstance(dim, Unknown):
                raise _NeedsUnknown(dim)
            total *= max(int(dim), 0)
        if total > MAX_COLLECTION:
            raise self.error("this random sample is too large to analyse")
        return self.call_python(getattr(source.impl, name), args, kwargs)

    # -- modules ------------------------------------------------------------

    def module_attr(self, path, name):
        """The modelled value of ``path.name``, or None if the parser does not model it."""
        root = path.split(".")[0]
        if root == "qiskit":
            return _QISKIT_MODEL.get(name)
        if root == "numpy":
            if path == "numpy" and name == "random":
                return _ModuleRef("numpy.random")
            if path == "numpy.random":
                return self._numpy_random_attr(name)
            if path == "numpy":
                return _NUMPY_MODEL.get(name)
            return None
        if path in ("math", "cmath"):
            return _MATH_MODEL.get(name)
        if path == "random":
            return self._python_random_attr(name)
        if path == "itertools":
            return _ITERTOOLS_MODEL.get(name)
        if path == "functools":
            return _FUNCTOOLS_MODEL.get(name)
        if path == "copy":
            return _COPY_MODEL.get(name)
        if path == "argparse":
            return _ARGPARSE_MODEL.get(name)
        return None

    def _seed(self, value, kind):
        if value is None:
            return None
        if isinstance(value, numbers.Integral) or (kind == "python" and isinstance(value, str)):
            return value if isinstance(value, str) else int(value)
        self.warn(f"`{self.call_text()}` uses a seed the parser cannot determine")
        return _MISSING

    def _numpy_random_attr(self, name):
        def legacy_method(interp, args, kwargs):
            return interp.random_call(interp.np_legacy, name, args, kwargs)

        def seed(interp, args, kwargs):
            value = interp._seed(args[0] if args else kwargs.get("seed"), "np")
            impl = (None if value is None or value is _MISSING or _np is None
                    else _np.random.RandomState(value))
            interp.np_legacy = _RandomSource("np-legacy", impl)

        def generator(kind, factory):
            def make(interp, args, kwargs):
                value = interp._seed(args[0] if args else kwargs.get("seed"), "np")
                if value is None or value is _MISSING or _np is None:
                    return _RandomSource(kind, None)
                return _RandomSource(kind, factory(value))
            return make

        if name == "seed":
            return _Native("numpy.random.seed", seed)
        if name == "default_rng":
            return _Native("default_rng", generator(
                "np-generator", lambda s: _np.random.default_rng(s)))
        if name == "RandomState":
            return _Native("RandomState", generator(
                "np-legacy", lambda s: _np.random.RandomState(s)))
        if name in _RANDOM_METHODS["np-legacy"]:
            return _Native(f"numpy.random.{name}", legacy_method)
        return None

    def _python_random_attr(self, name):
        def method(interp, args, kwargs):
            return interp.random_call(interp.py_random, name, args, kwargs)

        def seed(interp, args, kwargs):
            value = interp._seed(args[0] if args else kwargs.get("a"), "python")
            impl = None if value is None or value is _MISSING else _random.Random(value)
            interp.py_random = _RandomSource("python", impl)

        def make(interp, args, kwargs):
            value = interp._seed(args[0] if args else kwargs.get("x"), "python")
            impl = None if value is None or value is _MISSING else _random.Random(value)
            return _RandomSource("python", impl)

        if name == "seed":
            return _Native("random.seed", seed)
        if name == "Random":
            return _Native("random.Random", make)
        if name in _RANDOM_METHODS["python"]:
            return _Native(f"random.{name}", method)
        return None

    # -- Qiskit constructors ------------------------------------------------

    def new_circuit(self, args, kwargs):
        name = kwargs.pop("name", None)
        for ignored in ("global_phase", "metadata", "inputs", "captures", "declarations"):
            kwargs.pop(ignored, None)
        if kwargs:
            raise self.error(f"QuantumCircuit({', '.join(kwargs)}=...) is not supported")
        if isinstance(name, Unknown):
            name = name.text
        circuit = StaticCircuit(self.next_name("circuit-") if name is None else str(name))
        for arg in args:
            if isinstance(arg, Unknown):
                raise self.error(f"the circuit size `{arg.text}` cannot be determined "
                                 "without running the program", cause=arg)
        if args and all(isinstance(a, numbers.Integral) for a in args):
            if len(args) > 2:
                raise CircuitError("QuantumCircuit takes at most two integer sizes")
            sizes = [int(a) for a in args] + [0]
            if max(sizes) > MAX_BITS or min(sizes) < 0:
                raise self.error("circuit size out of range for static analysis")
            if sizes[0]:
                circuit.add_register(StaticRegister("qubit", "q", sizes[0]))
            if sizes[1]:
                circuit.add_register(StaticRegister("clbit", "c", sizes[1]))
            return circuit
        for arg in args:
            if isinstance(arg, StaticRegister):
                circuit.add_register(arg)
            elif isinstance(arg, (list, tuple)) and all(isinstance(b, StaticBit) for b in arg):
                circuit.add_bits(arg)
            else:
                raise CircuitError(f"invalid QuantumCircuit argument: {arg!r}")
        return circuit

    def new_register(self, kind, prefix, args, kwargs):
        bound, _ = self.bind("Register", ["size", "name", "bits"], args, kwargs,
                             {"size": None, "name": None, "bits": None})
        if bound["bits"] is not None:
            raise self.error("registers built from existing bits are not supported")
        size = bound["size"]
        if isinstance(size, Unknown):
            raise self.error(f"the register size `{size.text}` cannot be determined "
                             "without running the program", cause=size)
        if not isinstance(size, numbers.Integral) or not 0 <= int(size) <= MAX_BITS:
            raise CircuitError(f"invalid register size: {size!r}")
        name = bound["name"]
        name = self.next_name(prefix) if name is None or isinstance(name, Unknown) else str(name)
        return StaticRegister(kind, name, int(size))

    def new_qft(self, args, kwargs, as_gate):
        if as_gate:
            bound, _ = self.bind("QFTGate", ["num_qubits"], args, kwargs)
            n = self._size_arg(bound["num_qubits"])
            return StaticGate("qft", n, 0, [], _qft_definition(n))
        bound, _ = self.bind("QFT", ["num_qubits", "approximation_degree", "do_swaps", "inverse",
                                     "insert_barriers", "name"], args, kwargs,
                             {"num_qubits": None, "approximation_degree": 0, "do_swaps": True,
                              "inverse": False, "insert_barriers": False, "name": None})
        n = self._size_arg(bound["num_qubits"])
        inverse = self.flag(bound["inverse"], "inverse")
        name = bound["name"] or ("IQFT" if inverse else "QFT")
        definition = _qft_definition(n, int(bound["approximation_degree"]),
                                     self.flag(bound["do_swaps"], "do_swaps"), inverse,
                                     self.flag(bound["insert_barriers"], "insert_barriers"))
        circuit = StaticCircuit(str(name))
        circuit.add_register(StaticRegister("qubit", "q", n))
        circuit.data.append(StaticInstruction(StaticGate(str(name), n, 0, [], definition),
                                              circuit.qubits, [], self.here()))
        return circuit

    def _size_arg(self, value):
        if isinstance(value, Unknown) or value is None:
            raise self.error("the number of qubits cannot be determined statically", cause=value)
        if not isinstance(value, numbers.Integral) or not 0 <= int(value) <= MAX_BITS:
            raise CircuitError(f"invalid number of qubits: {value!r}")
        return int(value)


# --------------------------------------------------------------------------
# Natives: the parts of Python, numpy and Qiskit the interpreter implements
# --------------------------------------------------------------------------

def _pure(name, fn, pytype=None):
    """A native wrapping a side-effect-free Python callable."""
    return _Native(name, lambda interp, args, kwargs: interp.call_python(fn, args, kwargs),
                   pytype=pytype)


def _collection(name, pytype):
    def make(interp, args, kwargs):
        if args and not isinstance(args[0], (dict, Unknown)):
            args = [interp.bounded(interp.iterable(args[0]))] + list(args[1:])
        return interp.call_python(pytype, args, kwargs)
    return _Native(name, make, pytype=pytype)


def _b_range(interp, args, kwargs):
    result = interp.call_python(range, args, kwargs)
    if len(result) > MAX_COLLECTION:
        raise interp.error(f"range of {len(result)} elements is too long to analyse")
    return result


def _b_sorted(interp, args, kwargs):
    if args:
        args = [interp.bounded(interp.iterable(args[0]))] + list(args[1:])
    return interp.call_python(sorted, args, kwargs)


def _b_pow(interp, args, kwargs):
    if len(args) == 2:
        return interp.binop(ast.Pow(), args[0], args[1])
    return interp.call_python(pow, args, kwargs)


def _b_format(interp, args, kwargs):
    if len(args) > 1 and isinstance(args[1], str):
        interp.check_format_spec(args[1])
    return interp.call_python(format, args, kwargs)


def _b_isinstance(interp, args, kwargs):
    if len(args) != 2:
        raise TypeError("isinstance expected 2 arguments")
    value, kinds = args
    if isinstance(value, Unknown):
        raise _NeedsUnknown(value)

    def as_type(kind):
        if isinstance(kind, tuple):
            return tuple(as_type(k) for k in kind)
        if isinstance(kind, _Native) and kind.cls is not None:
            return kind.cls
        if isinstance(kind, type):
            return kind
        raise _NeedsUnknown(repr(kind))
    return isinstance(value, as_type(kinds))


def _b_print(interp, args, kwargs):
    return None


def _b_iter(interp, args, kwargs):
    if len(args) != 1:
        raise interp.error("iter() with a sentinel is not supported")
    return iter(interp.iterable(args[0]))


def _b_getattr(interp, args, kwargs):
    if len(args) not in (2, 3) or not isinstance(args[1], str):
        raise interp.error("getattr() needs an attribute name the static parser can see")
    try:
        return interp.getattr(args[0], args[1])
    except StaticParseError:
        if len(args) == 3:
            return args[2]
        raise


def _b_hasattr(interp, args, kwargs):
    try:
        _b_getattr(interp, args[:2], {})
        return True
    except StaticParseError:
        return False


def _b_dynamic_code(interp, args, kwargs):
    raise interp.error("code built at run time (eval/exec/compile) cannot be analysed statically")


def _b_callable(interp, args, kwargs):
    return isinstance(args[0], (_UserFunction, _Native, _BoundMethod)) or (
        isinstance(args[0], type) and issubclass(args[0], BaseException))


_SAFE_EXCEPTIONS = (
    Exception, BaseException, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError,
    ArithmeticError, LookupError, RuntimeError, NotImplementedError, AssertionError,
    AttributeError, NameError, StopIteration, OverflowError,
)


def _make_builtins():
    table = {cls.__name__: cls for cls in _SAFE_EXCEPTIONS}
    for name in ("len", "abs", "round", "divmod", "min", "max", "sum", "any", "all",
                 "enumerate", "zip", "reversed", "map", "filter", "hex", "bin", "oct",
                 "chr", "ord", "repr", "ascii", "next", "hash"):
        table[name] = _pure(name, getattr(_builtins, name))
    for pytype in (int, float, complex, str, bool, slice):
        table[pytype.__name__] = _pure(pytype.__name__, pytype, pytype=pytype)
    for pytype in (list, tuple, set, frozenset, dict):
        table[pytype.__name__] = _collection(pytype.__name__, pytype)
    table.update({
        "range": _Native("range", _b_range, pytype=range),
        "sorted": _Native("sorted", _b_sorted),
        "pow": _Native("pow", _b_pow),
        "format": _Native("format", _b_format),
        "isinstance": _Native("isinstance", _b_isinstance),
        "print": _Native("print", _b_print),
        "iter": _Native("iter", _b_iter),
        "callable": _Native("callable", _b_callable),
        "getattr": _Native("getattr", _b_getattr),
        "hasattr": _Native("hasattr", _b_hasattr),
        "eval": _Native("eval", _b_dynamic_code),
        "exec": _Native("exec", _b_dynamic_code),
        "compile": _Native("compile", _b_dynamic_code),
    })
    return table


def _numeric(name, fn, guard=None):
    """A math/numpy function: symbolic on Parameters, real otherwise."""
    def call(interp, args, kwargs):
        if any(isinstance(a, Symbol) for a in args):
            if len(args) == 1 and name in _SYMBOL_FUNCS:
                return Symbol("call", (name, args[0]))
            raise TypeError(f"{name}() does not accept circuit parameters")
        if guard is not None:
            for value in itertools.chain(args, kwargs.values()):
                if isinstance(value, Unknown):
                    raise _NeedsUnknown(value)
            guard(interp, args, kwargs)
        return interp.call_python(fn, args, kwargs, deep=True)
    return _Native(name, call)


def _size_guard(count_fn):
    def guard(interp, args, kwargs):
        try:
            count = count_fn(*args, **kwargs)
        except TypeError:
            count = 0
        if count > MAX_COLLECTION:
            raise interp.error("this array is too large to analyse")
    return guard


def _shape_count(shape, *_args, **_kwargs):
    if isinstance(shape, numbers.Integral):
        return int(shape)
    return math.prod(int(s) for s in shape)


_MATH_NAMES = {
    "sqrt": "sqrt", "sin": "sin", "cos": "cos", "tan": "tan", "asin": "asin", "acos": "acos",
    "atan": "atan", "atan2": "atan2", "sinh": "sinh", "cosh": "cosh", "tanh": "tanh",
    "exp": "exp", "log": "log", "log2": "log2", "log10": "log10", "floor": "floor",
    "ceil": "ceil", "radians": "radians", "degrees": "degrees", "fabs": "fabs",
    "hypot": "hypot", "isclose": "isclose", "gcd": "gcd", "trunc": "trunc",
    "copysign": "copysign", "fmod": "fmod", "isqrt": "isqrt",
}

_MATH_MODEL = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan}
_MATH_MODEL.update({name: _numeric(name, getattr(math, attr)) for name, attr in _MATH_NAMES.items()})


def _numpy_model():
    if _np is None:
        # Without numpy, the scalar functions still work through math.
        aliases = {"arcsin": "asin", "arccos": "acos", "arctan": "atan", "arctan2": "atan2",
                   "deg2rad": "radians", "rad2deg": "degrees", "abs": "fabs",
                   "absolute": "fabs"}
        model = {"pi": math.pi, "e": math.e, "inf": math.inf, "nan": math.nan,
                 "float64": _pure("float64", float), "int64": _pure("int64", int)}
        for name in list(_MATH_NAMES) + list(aliases):
            attr = _MATH_NAMES.get(aliases.get(name, name))
            if attr:
                model[name] = _numeric(name, getattr(math, attr))
        return model
    model = {"pi": _np.pi, "e": _np.e, "inf": _np.inf, "nan": _np.nan}
    for name in ("sqrt", "sin", "cos", "tan", "arcsin", "arccos", "arctan", "arctan2", "sinh",
                 "cosh", "tanh", "exp", "log", "log2", "log10", "floor", "ceil", "abs",
                 "absolute", "radians", "degrees", "deg2rad", "rad2deg", "sign", "mod", "power",
                 "round", "real", "imag", "conj", "conjugate", "angle", "hypot", "maximum",
                 "minimum", "isclose", "allclose", "sum", "prod", "mean", "max", "min",
                 "argmax", "argmin", "cumsum", "sort", "flip", "transpose", "trace",
                 "concatenate", "array", "asarray", "float64", "float32", "int64", "int32",
                 "complex128"):
        model[name] = _numeric(name, getattr(_np, name))
    model["arange"] = _numeric("arange", _np.arange, _size_guard(
        lambda *a, **k: len(range(*[int(math.ceil(x)) for x in a[:3]])) if a else 0))
    model["linspace"] = _numeric("linspace", _np.linspace, _size_guard(
        lambda start, stop, num=50, *a, **k: int(num)))
    for name in ("zeros", "ones", "empty"):
        model[name] = _numeric(name, getattr(_np, name), _size_guard(_shape_count))
    model["full"] = _numeric("full", _np.full, _size_guard(_shape_count))
    model["eye"] = _numeric("eye", _np.eye, _size_guard(
        lambda n, m=None, *a, **k: int(n) * int(n if m is None else m)))
    model["identity"] = _numeric("identity", _np.identity, _size_guard(
        lambda n, *a, **k: int(n) ** 2))
    return model


_NUMPY_MODEL = _numpy_model()


def _bounded_iter(fn):
    def call(interp, args, kwargs):
        return interp.bounded(interp.call_python(fn, args, kwargs))
    return call


_ITERTOOLS_MODEL = {
    name: _Native(f"itertools.{name}", _bounded_iter(getattr(itertools, name)))
    for name in ("product", "combinations", "permutations", "combinations_with_replacement",
                 "chain", "zip_longest", "islice", "accumulate", "starmap")
}
def _memoizer(interp, args, kwargs):
    """lru_cache(maxsize=...) returns the decorator; used bare it is the decorator."""
    return _FUNCTOOLS_MODEL["lru_cache"]


_FUNCTOOLS_MODEL = {"reduce": _pure("reduce", functools.reduce),
                    "lru_cache": _Native("lru_cache", _memoizer),
                    "cache": _Native("cache", _memoizer)}


def _copy_value(deep):
    def call(interp, args, kwargs):
        value = args[0]
        if isinstance(value, StaticCircuit):
            return value.copy()
        if isinstance(value, _STATIC_TYPES + (Unknown,)):
            return value
        return interp.call_python(_copy.deepcopy if deep else _copy.copy, args, kwargs)
    return call


_COPY_MODEL = {"copy": _Native("copy", _copy_value(False)),
               "deepcopy": _Native("deepcopy", _copy_value(True))}


def _gate_class(class_name, gate_name, num_params, num_qubits):
    def make(interp, args, kwargs):
        label = kwargs.pop("label", None)
        for ignored in ("ctrl_state", "duration", "unit"):
            kwargs.pop(ignored, None)
        params = list(args) + list(kwargs.values())
        if len(params) != num_params:
            raise TypeError(f"{class_name}() takes {num_params} parameters, got {len(params)}")
        return StaticGate(gate_name, num_qubits, 0, [interp.gate_param(p) for p in params],
                          label=label)
    return _Native(class_name, make, qiskit=True)


def _q_mcx_gate(interp, args, kwargs):
    bound, _ = interp.bind("MCXGate", ["num_ctrl_qubits", "label", "ctrl_state"], args, kwargs,
                           {"label": None, "ctrl_state": None})
    n = interp._size_arg(bound["num_ctrl_qubits"])
    return StaticGate(_mcx_name(n), n + 1)


def _q_mcphase_gate(interp, args, kwargs):
    bound, _ = interp.bind("MCPhaseGate", ["lam", "num_ctrl_qubits", "label", "ctrl_state"],
                           args, kwargs, {"label": None, "ctrl_state": None})
    n = interp._size_arg(bound["num_ctrl_qubits"])
    return StaticGate("mcphase", n + 1, 0, [interp.gate_param(bound["lam"])])


def _q_barrier(interp, args, kwargs):
    bound, _ = interp.bind("Barrier", ["num_qubits", "label"], args, kwargs, {"label": None})
    return StaticGate("barrier", interp._size_arg(bound["num_qubits"]), label=bound["label"])


def _q_unitary_gate(interp, args, kwargs):
    bound, _ = interp.bind("UnitaryGate", ["data", "label"], args, kwargs, {"label": None})
    data = bound["data"]
    if isinstance(data, Unknown):
        raise interp.error("the matrix of this UnitaryGate cannot be determined statically")
    size = len(data)
    num_qubits = size.bit_length() - 1
    if size < 2 or 2 ** num_qubits != size:
        raise CircuitError("a unitary matrix must be 2^n x 2^n")
    return StaticGate("unitary", num_qubits, 0, [data], label=bound["label"])


def _q_pauli_gate(interp, args, kwargs):
    bound, _ = interp.bind("PauliGate", ["label"], args, kwargs)
    label = bound["label"]
    if not isinstance(label, str):
        raise interp.error("the Pauli string cannot be determined statically")
    return StaticGate("pauli", len(label), 0, [label])


def _q_parameter_vector(interp, args, kwargs):
    bound, _ = interp.bind("ParameterVector", ["name", "length"], args, kwargs, {"length": 0})
    name, length = str(bound["name"]), bound["length"]
    if not isinstance(length, numbers.Integral) or not 0 <= int(length) <= MAX_BITS:
        raise interp.error("the ParameterVector length cannot be determined statically")
    return [Symbol.leaf(f"{name}[{i}]", name, i) for i in range(int(length))]


def _q_parameter(interp, args, kwargs):
    bound, _ = interp.bind("Parameter", ["name"], args, kwargs)
    name = bound["name"]
    return Symbol.leaf(name.text if isinstance(name, Unknown) else name)


_QISKIT_MODEL = {
    "QuantumCircuit": _Native("QuantumCircuit", lambda i, a, k: i.new_circuit(list(a), dict(k)),
                              qiskit=True, cls=StaticCircuit),
    "QuantumRegister": _Native("QuantumRegister",
                               lambda i, a, k: i.new_register("qubit", "q", a, k), qiskit=True,
                               cls=StaticRegister),
    "AncillaRegister": _Native("AncillaRegister",
                               lambda i, a, k: i.new_register("qubit", "a", a, k), qiskit=True,
                               cls=StaticRegister),
    "ClassicalRegister": _Native("ClassicalRegister",
                                 lambda i, a, k: i.new_register("clbit", "c", a, k), qiskit=True,
                                 cls=StaticRegister),
    "Parameter": _Native("Parameter", _q_parameter, qiskit=True, cls=Symbol),
    "ParameterVector": _Native("ParameterVector", _q_parameter_vector, qiskit=True),
    "QFT": _Native("QFT", lambda i, a, k: i.new_qft(a, k, False), qiskit=True),
    "QFTGate": _Native("QFTGate", lambda i, a, k: i.new_qft(a, k, True), qiskit=True),
    "MCXGate": _Native("MCXGate", _q_mcx_gate, qiskit=True),
    "MCPhaseGate": _Native("MCPhaseGate", _q_mcphase_gate, qiskit=True),
    "Measure": _Native("Measure", lambda i, a, k: StaticGate("measure", 1, 1), qiskit=True),
    "Reset": _Native("Reset", lambda i, a, k: StaticGate("reset", 1), qiskit=True),
    "Barrier": _Native("Barrier", _q_barrier, qiskit=True),
    "UnitaryGate": _Native("UnitaryGate", _q_unitary_gate, qiskit=True),
    "PauliGate": _Native("PauliGate", _q_pauli_gate, qiskit=True),
    "CircuitError": CircuitError,
    "QiskitError": QiskitError,
}
_QISKIT_MODEL.update({cls: _gate_class(cls, *spec) for cls, spec in _GATE_CLASSES.items()})

def _argparse_parser(interp, args, kwargs):
    return _ArgParser()


def _argparse_namespace(interp, args, kwargs):
    return _Namespace(kwargs)


_ARGPARSE_MODEL = {
    "ArgumentParser": _Native("ArgumentParser", _argparse_parser, cls=_ArgParser),
    "Namespace": _Native("Namespace", _argparse_namespace, cls=_Namespace),
}

_MODELLED_ROOTS = {"qiskit", "numpy", "math", "cmath", "random", "itertools", "functools", "copy",
                   "argparse"}

# Circuit methods whose result stands in for the circuit they are called on.
_DERIVING_METHODS = {"to_gate", "to_instruction", "inverse", "copy", "decompose",
                     "assign_parameters", "copy_empty_like", "reverse_ops", "power", "repeat",
                     "control"}

# Random methods and where their output size comes from: an argument position,
# "dims" (every argument is a dimension), "perm" (an int argument is the size) or None.
_RANDOM_METHODS = {
    "np-generator": {"uniform": 2, "random": 0, "normal": 2, "integers": 2,
                     "standard_normal": 0, "choice": 1, "exponential": 1,
                     "permutation": "perm", "shuffle": None},
    "np-legacy": {"uniform": 2, "random": 0, "random_sample": 0, "rand": "dims",
                  "randn": "dims", "normal": 2, "randint": 2, "choice": 1,
                  "standard_normal": 0, "permutation": "perm", "shuffle": None},
    "python": {"random": None, "uniform": None, "randint": None, "randrange": None,
               "choice": None, "gauss": None, "normalvariate": None, "shuffle": None,
               "sample": None},
}


# --------------------------------------------------------------------------
# Operator and method tables
# --------------------------------------------------------------------------

_BINARY_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.LShift: operator.lshift, ast.RShift: operator.rshift,
    ast.BitOr: operator.or_, ast.BitXor: operator.xor, ast.BitAnd: operator.and_,
    ast.MatMult: operator.matmul,
}
_INPLACE_OPS = {
    ast.BitOr: operator.ior, ast.BitAnd: operator.iand, ast.BitXor: operator.ixor,
    ast.Sub: operator.isub,
}
_SYMBOLIC_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "**"}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}
_COMPARE_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}

_STR_METHODS = {
    "join", "split", "rsplit", "splitlines", "upper", "lower", "title", "capitalize",
    "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "find", "rfind",
    "index", "rindex", "count", "isdigit", "isalpha", "isalnum", "isspace", "isupper",
    "islower", "zfill", "ljust", "rjust", "center", "format", "partition", "rpartition",
    "removeprefix", "removesuffix", "swapcase", "casefold",
}
_INT_METHODS = {"bit_length", "bit_count", "conjugate"}
_PLAIN_METHODS = {
    list: {"append", "extend", "insert", "pop", "remove", "reverse", "sort", "index",
           "count", "copy", "clear"},
    tuple: {"index", "count"},
    dict: {"get", "items", "keys", "values", "update", "setdefault", "pop", "copy", "clear",
           "popitem"},
    set: {"add", "update", "discard", "remove", "pop", "union", "intersection", "difference",
          "symmetric_difference", "copy", "issubset", "issuperset", "isdisjoint", "clear"},
    frozenset: {"union", "intersection", "difference", "symmetric_difference", "copy",
                "issubset", "issuperset", "isdisjoint"},
    str: _STR_METHODS,
    int: _INT_METHODS,
    bool: _INT_METHODS,
    float: {"is_integer", "conjugate"},
    complex: {"conjugate"},
    range: {"index", "count"},
}
# Methods that store their arguments rather than inspect them: unknown arguments are fine.
_STORING_METHODS = {"append", "insert", "extend", "add", "update", "setdefault", "copy",
                    "clear", "reverse"}
_NUMPY_ATTRS = {"shape", "size", "ndim", "T", "real", "imag", "dtype"}
_NUMPY_METHODS = {"tolist", "sum", "max", "min", "mean", "copy", "flatten", "item", "reshape",
                  "conj", "conjugate", "transpose", "round", "argmax", "argmin", "any", "all"}


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

class StaticQiskitParser(AbstractParser):
    """
    Parser for Qiskit programs given as Python source, without executing them.
    The source is interpreted statically (see the module docstring) to rebuild
    the QuantumCircuit it would create.

    Expected source type:
        str (Python source code)

    Attributes after parsing:
        program:  the QuantumProgram IR
        circuit:  the StaticCircuit the IR was built from
        warnings: what the parser skipped or approximated, as "line N: ..." strings
        notes:    the same, as (lineno, message) pairs
    """

    def __init__(self, source: str = None, filename: str = "<source>",
                 circuit_name: str = None, run_name: str = "__main__",
                 time_limit: float = None, bindings: dict = None,
                 circuit_index: int = 0) -> None:
        """Initialize the parser, optionally parsing the source immediately.

        ``circuit_name`` picks a module-level variable instead of the largest
        circuit; ``run_name`` is the ``__name__`` the file sees. The default,
        ``"__main__"``, follows ``if __name__ == "__main__":`` blocks; any other
        name skips them.
        ``time_limit`` caps the wall-clock seconds a parse may take, for
        callers that read many files and cannot wait out MAX_STEPS on each.

        ``bindings`` maps ``(line, name)`` to a value, for a value only running
        the program could give: the variable ``name`` gets it where it is
        assigned on ``line`` (or the command-line argument ``name`` declared
        there). A StaticParseError's ``needs`` says which one stopped a parse.

        ``circuit_index`` picks one of ``circuits`` (see ``parse``); 0 is the
        main circuit.
        """
        super().__init__(source)
        self.filename = filename
        self.circuit_name = circuit_name
        self.run_name = run_name
        self.time_limit = time_limit
        self.bindings = dict(bindings or {})
        self.circuit_index = circuit_index
        self.circuit = None
        self.circuits = []
        self._candidate_circuits = []
        self.used_bindings = []
        self.warnings = []
        self.notes = []
        if source is not None:
            self.parse()

    def parse(self) -> QuantumProgram:
        """Interpret the contained source and build the QuantumProgram IR."""
        self.preprocess()
        if not isinstance(self.source, str):
            raise ValueError("StaticQiskitParser expects Python source code as a string")
        try:
            tree = ast.parse(self.source, filename=self.filename)
        except SyntaxError as exc:
            raise StaticParseError(f"syntax error: {exc.msg}", exc.lineno) from None

        interpreter = _Interpreter(self.filename, self.run_name, self.time_limit, self.bindings)
        try:
            interpreter.run(tree)
        finally:
            self.warnings = interpreter.warnings
            self.notes = interpreter.notes
            self.used_bindings = sorted(interpreter.used_bindings)

        candidates = self._candidates(interpreter)
        self._candidate_circuits = [circuit for circuit, _ in candidates]
        self.circuits = [info for _, info in candidates]
        return self.select(self.circuit_index)

    def select(self, index: int) -> QuantumProgram:
        """Build the IR of ``circuits[index]`` (the main one if out of range), after parse()."""
        if not 0 <= index < len(self._candidate_circuits):
            index = 0
        self.circuit_index = index
        self.circuit = self._candidate_circuits[index]
        self.program = self._build_program(self.circuit)
        self.validate()
        return self.program

    def parse_file(self, path: str) -> QuantumProgram:
        """Read a Python file and build the IR of the circuit it creates, without running it."""
        with open(path, encoding="utf-8-sig") as handle:
            self.source = handle.read()
        self.filename = path
        return self.parse()

    MAX_CIRCUITS = 30       # circuits offered besides the main one

    def _candidates(self, interpreter):
        """The circuits worth showing, main one first, as (circuit, info) pairs.

        The main circuit is the one QiskitParser would pick: the module-level
        circuit with the most instructions. A program that keeps no circuit at
        module level -- everything built inside functions, as in a ``main()`` --
        gets the largest circuit that did not go into another one instead.

        The others follow: module-level circuits, then the rest that stand on
        their own, then the ones used inside another (a subroutine composed into
        the main circuit, the gate a circuit was turned into ...). Empty
        circuits and exact repeats (a helper called twice with the same
        arguments) are left out.
        """
        namespace = interpreter.globals
        if self.circuit_name is not None:
            circuit = namespace.get(self.circuit_name)
            if not isinstance(circuit, StaticCircuit):
                raise ValueError(f"No QuantumCircuit assigned to '{self.circuit_name}'")
            return [(circuit, self._describe(circuit, interpreter, 0))]

        names = {}
        for key, value in namespace.items():
            if isinstance(value, StaticCircuit):
                names.setdefault(id(value), key)
        module_level = [v for v in namespace.values() if isinstance(v, StaticCircuit)]
        made = [c for c, _ in interpreter.circuits]
        standalone = [c for c in made if id(c) not in interpreter.consumed]
        if module_level:
            # The most complex circuit is taken to be the main one, as QiskitParser does.
            main = max(module_level, key=lambda c: len(c.data))
        elif any(c.data for c in standalone):
            main = max(standalone, key=lambda c: len(c.data))
        elif any(c.data for c in made):
            main = max(made, key=lambda c: len(c.data))
        else:
            # Same wording as QiskitParser, which callers match on.
            raise ValueError("No QuantumCircuit assignment found in script")

        ordered = [main] + module_level + standalone + made
        seen_ids, seen_shapes, out = set(), set(), []
        for circuit in ordered:
            if id(circuit) in seen_ids or (circuit is not main and not circuit.data):
                continue
            seen_ids.add(id(circuit))
            shape = _circuit_shape(circuit)
            if shape in seen_shapes:
                continue
            seen_shapes.add(shape)
            out.append((circuit, self._describe(circuit, interpreter, len(out), names)))
            if len(out) > self.MAX_CIRCUITS:
                break
        return out

    @staticmethod
    def _describe(circuit, interpreter, index, names=None):
        lines = {id(c): line for c, line in interpreter.circuits}
        variable = (names or {}).get(id(circuit))
        return {
            "index": index,
            "name": circuit.name,
            "variable": variable,
            "line": lines.get(id(circuit)),
            "num_qubits": circuit.num_qubits,
            "num_clbits": circuit.num_clbits,
            "size": len(circuit.data),
            "used_inside": id(circuit) in interpreter.consumed,
        }

    @staticmethod
    def _build_program(circuit) -> QuantumProgram:
        return circuit_to_program(circuit, "qiskit-static")


def _circuit_shape(circuit):
    """What tells two circuits apart for the list of circuits: their name and gates."""
    return (circuit.name, circuit.num_qubits, circuit.num_clbits, tuple(
        (i.operation.name, tuple(circuit.find(q) for q in i.qubits),
         tuple(circuit.find(c) for c in i.clbits), tuple(map(repr, i.operation.params)))
        for i in circuit.data))


def circuit_to_program(circuit, frontend) -> QuantumProgram:
    """The QuantumProgram IR of a StaticCircuit, source locations included.

    An ``if_else`` becomes a predicate instruction on no qubit that reads the
    condition bits, followed by the instructions of its branches. Each of those
    carries ``guards``: the ``(predicate index, branch)`` pairs it sits under,
    outermost first, branch 0 the true body and 1 the else. It also inherits
    the predicate's ``condition`` (not its ``condition_clbits``: the predicate
    is what reads them), so it reads as classically conditioned.
    """
    program = QuantumProgram()
    program.set_registers(num_qubits=circuit.num_qubits, num_clbits=circuit.num_clbits)

    def emit(instructions, guards, inherited):
        for instruction in instructions:
            condition_clbits = []
            for bit in instruction.condition_bits:
                index = circuit.find(bit)
                if index not in condition_clbits:
                    condition_clbits.append(index)
            condition = instruction.condition
            metadata = {
                "frontend": frontend,
                "condition": inherited if condition is None else condition,
                "condition_clbits": condition_clbits,
                "guards": guards,
            }
            if instruction.operation.name == "if_else":
                predicate = len(program)
                program.add_instruction(QuantumInstruction(
                    name="if_else", qubits=[], clbits=[], params=[], metadata=metadata,
                    location=instruction.location))
                for branch, body in enumerate(instruction.operation.params):
                    if body is not None:
                        emit(body.data, guards + ((predicate, branch),), metadata["condition"])
                continue
            program.add_instruction(QuantumInstruction(
                name=instruction.operation.name,
                qubits=[circuit.find(q) for q in instruction.qubits],
                clbits=[circuit.find(c) for c in instruction.clbits],
                params=list(instruction.operation.params),
                metadata=metadata,
                location=instruction.location,
            ))

    emit(circuit.data, (), None)
    return program
