from qprogram import *
from abc import ABC, abstractmethod

class AbstractParser(ABC):
    """
    Abstract base class for all quantum language parsers.

    Responsibilities:
        - Parse frontend-specific source/code objects
        - Convert them into a QuantumProgram IR
        - Provide hooks for validation and preprocessing
    """

    def __init__(self, source) -> None:
        self.source = source
        self.program = QuantumProgram()

    @abstractmethod
    def parse(self) -> QuantumProgram:
        """
        Parse the source into a QuantumProgram.
        Must be implemented by subclasses.
        """
        pass

    def validate(self) -> None:
        """
        Optional validation hook.
        Subclasses may override.
        """
        return

    def preprocess(self) -> None:
        """
        Optional preprocessing hook.
        Subclasses may override.
        """
        return