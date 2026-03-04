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
        qubits: List[int],
        clbits: Optional[List[int]] = None,
        params: Optional[List[Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.qubits = qubits
        self.clbits = clbits or []
        self.params = params or []
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return (
            f"QuantumInstruction(name={self.name}, "
            f"qubits={self.qubits}, "
            f"clbits={self.clbits}, "
            f"params={self.params})"
        )