from typing import *

class QuantumInstruction:
    """
    Represents a single quantum operation in the internal representation.

    Attributes:
        name: Name of the gate/operation
        qubits: List of qubit indices this instruction acts on
        clbits: Optional classical bits (for measurement)
        params: Optional gate parameters (e.g., rotation angles)
        metadata: Additional frontend-specific information
    """

    def __init__(
        self,
        name: str,
        qubits: Set[int],
        clbits: Optional[Set[int]] = None,
        params: Optional[List[Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize a new quantum instruction with its associated bits and parameters."""
        self.name = name
        self.qubits = qubits
        self.clbits = clbits or []
        self.params = params or []
        self.metadata = metadata or {}

    def __eq__(self, other) -> bool:
        """Check equality based on name, qubits, clbits, and params."""
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