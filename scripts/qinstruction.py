from typing import *

class SourceLocation:
    """
    Where an instruction came from in the submitted source.

    Lines are 1-based and columns 0-based, the convention of Python's ``ast``
    and of the OpenQASM reference parser. ``end_line``/``end_column`` close the
    span (exclusive column). ``via`` lists the lines of the calls that led
    there, outermost first -- for a gate added inside a helper function, the
    line that called the helper.
    """

    __slots__ = ("line", "column", "end_line", "end_column", "via")

    def __init__(
        self,
        line: int,
        column: int = 0,
        end_line: Optional[int] = None,
        end_column: Optional[int] = None,
        via: Sequence[int] = (),
    ) -> None:
        """Initialize a location; the span defaults to the start position."""
        self.line = line
        self.column = column
        self.end_line = line if end_line is None else end_line
        self.end_column = column if end_column is None else end_column
        self.via = tuple(via)

    @classmethod
    def of(cls, node: Any, via: Sequence[int] = ()) -> Optional["SourceLocation"]:
        """The location of anything with Python-``ast``-style position attributes."""
        line = getattr(node, "lineno", None)
        if line is None:
            return None
        return cls(line, getattr(node, "col_offset", 0) or 0,
                   getattr(node, "end_lineno", None), getattr(node, "end_col_offset", None), via)

    def with_via(self, line: int) -> "SourceLocation":
        """The same span, reached through one more call at ``line`` (added outermost)."""
        return SourceLocation(self.line, self.column, self.end_line, self.end_column,
                              (line,) + self.via)

    def to_dict(self) -> Dict[str, Any]:
        """A JSON-safe copy."""
        return {
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "via": list(self.via),
        }

    def __eq__(self, other) -> bool:
        """Locations are equal when their span and call path are."""
        if not isinstance(other, SourceLocation):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        """Return ``line:column`` with the call path, if any."""
        via = "".join(f" via {line}" for line in self.via)
        return f"SourceLocation({self.line}:{self.column}{via})"


class QuantumInstruction:
    """
    Represents a single quantum operation in the internal representation.

    Attributes:
        name: Name of the gate/operation
        qubits: List of qubit indices this instruction acts on
        clbits: Optional classical bits (for measurement)
        params: Optional gate parameters (e.g., rotation angles)
        metadata: Additional frontend-specific information
        location: Where in the source the instruction came from, when the
            frontend can tell (None otherwise)
    """

    def __init__(
        self,
        name: str,
        qubits: Set[int],
        clbits: Optional[Set[int]] = None,
        params: Optional[List[Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        location: Optional[SourceLocation] = None,
    ) -> None:
        """Initialize a new quantum instruction with its associated bits and parameters."""
        self.name = name
        self.qubits = qubits
        self.clbits = clbits or []
        self.params = params or []
        self.metadata = metadata or {}
        self.location = location

    def __eq__(self, other) -> bool:
        """Check equality based on name, qubits, clbits, and params (not location)."""
        if not isinstance(other, QuantumInstruction):
            return NotImplemented
        return (
            self.name == other.name and
            self.qubits == other.qubits and
            self.clbits == other.clbits and
            self.params == other.params
        )

    def __repr__(self) -> str:
        """Return a string representation of the quantum instruction."""
        return (
            f"QuantumInstruction(name={self.name}, "
            f"qubits={self.qubits}, "
            f"clbits={self.clbits}, "
            f"params={self.params})"
        )