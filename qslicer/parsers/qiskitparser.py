from ..ir.qinstruction import QuantumInstruction
from ..ir.qprogram import QuantumProgram
from .base import AbstractParser
import runpy
import io
from contextlib import redirect_stdout
from qiskit import QuantumCircuit
from qiskit.circuit import CASE_DEFAULT, ClassicalRegister, Clbit
from qiskit.circuit.classical import expr

def _condition_clbits(circuit: QuantumCircuit, condition, index_of=None) -> list:
    """Indices of the classical bits a condition reads.

    A condition is ``(Clbit, value)``, ``(ClassicalRegister, value)``, or a
    classical expression; only the bits it actually names are returned.
    ``index_of`` maps a bit to its index when the bit belongs to a block
    rather than to ``circuit`` itself.
    """
    if index_of is None:
        index_of = lambda bit: circuit.find_bit(bit).index
    if condition is None:
        return []
    if isinstance(condition, tuple):
        targets = [condition[0]]
    else:
        targets = [var.var for var in expr.iter_vars(condition)]

    indices = []
    for target in targets:
        bits = list(target) if isinstance(target, ClassicalRegister) else [target]
        for bit in bits:
            if isinstance(bit, Clbit):
                index = index_of(bit)
                if index not in indices:
                    indices.append(index)
    return indices


class QiskitParser(AbstractParser):
    """
    Parser for Qiskit QuantumCircuit objects.
    File parsing is done by executing the file and extracting the QuantumCircuit object.

    Expected source type:
        qiskit.QuantumCircuit
    """
    def __init__(self, source: QuantumCircuit = None) -> None:
        """Initialize the parser, optionally parsing the QuantumCircuit immediately."""
        super().__init__(source)
        if source is not None:
            self.parse()

    def parse(self) -> QuantumProgram:
        """Parse the contained QuantumCircuit source into a QuantumProgram IR."""
        self.preprocess()

        circuit = self.source

        # Extract register information
        self.program.set_registers(
            num_qubits=circuit.num_qubits,
            num_clbits=circuit.num_clbits,
        )

        self._emit(circuit, circuit.data, (), None,
                   lambda q: circuit.find_bit(q).index, lambda c: circuit.find_bit(c).index)

        self.validate()
        return self.program
    
    def _emit(self, circuit, instructions, guards, inherited, qubit_of, clbit_of) -> None:
        """Add ``instructions`` to the program, the branches of an if_else included.

        An ``if_else`` becomes a predicate instruction on no qubit that reads
        the condition bits, followed by the instructions of its branches. Each
        of those carries ``guards``: the ``(predicate index, branch)`` pairs it
        sits under, outermost first, branch 0 the true body and 1 the else. It
        also inherits the predicate's ``condition`` (not its
        ``condition_clbits``), so it reads as classically conditioned.
        """
        for instruction in instructions:
            op = instruction.operation
            # Classical condition, e.g. from `with qc.if_test((clbit, value))`
            condition_raw = getattr(op, 'condition', None)
            metadata = {
                "frontend": "qiskit",
                "condition": inherited if condition_raw is None else condition_raw,
                "condition_clbits": _condition_clbits(circuit, condition_raw, clbit_of),
                "guards": guards,
            }
            if op.name in ("if_else", "switch_case"):
                predicate = len(self.program)
                blocks = list(op.params)
                if op.name == "if_else":
                    metadata["arms"] = 2
                else:
                    # A switch reads its target; with a default its cases cover every value.
                    blocks = list(op.blocks)
                    target = op.target
                    metadata["condition"] = target
                    metadata["condition_clbits"] = _condition_clbits(
                        circuit, target if isinstance(target, expr.Expr) else (target, None),
                        clbit_of)
                    has_default = any(CASE_DEFAULT in values
                                      for values, _ in op.cases_specifier())
                    metadata["arms"] = len(blocks) if has_default else None
                self.program.add_instruction(QuantumInstruction(
                    name=op.name, qubits=[], clbits=[], params=[], metadata=metadata))
                outer_qubits = [qubit_of(q) for q in instruction.qubits]
                outer_clbits = [clbit_of(c) for c in instruction.clbits]
                for branch, block in enumerate(blocks):
                    if block is None:
                        continue
                    # A block's bits stand for the instruction's, position by position.
                    qmap = dict(zip(block.qubits, outer_qubits))
                    cmap = dict(zip(block.clbits, outer_clbits))
                    self._emit(circuit, block.data, guards + ((predicate, branch),),
                               metadata["condition"], qmap.__getitem__, cmap.__getitem__)
                continue
            self.program.add_instruction(QuantumInstruction(
                name=op.name,
                qubits=[qubit_of(q) for q in instruction.qubits],
                clbits=[clbit_of(c) for c in instruction.clbits],
                params=list(op.params),
                metadata=metadata,
            ))

    def parse_file(self, path: str) -> QuantumProgram:
        """Dynamically execute a python script and extract the generated QuantumCircuit."""
        # Suppress stdout to avoid unwanted print statements (like circuit.draw) during parsing
        with io.StringIO() as buf, redirect_stdout(buf):
            try:
                # Execute the file to get its namespace
                result = runpy.run_path(path)
            except Exception as e:
                raise ValueError(f"Failed to execute the script for parsing: {e}") from e
                
        # Find all QuantumCircuit objects in the script's namespace
        circuits = [v for k, v in result.items() if isinstance(v, QuantumCircuit)]
        
        if not circuits:
            raise ValueError("No QuantumCircuit assignment found in script")
            
        # Select the most complex circuit if multiple exist (assumes the main one has the most components)
        circuit = max(circuits, key=lambda c: len(c.data))
        
        self.source = circuit
        return self.parse()