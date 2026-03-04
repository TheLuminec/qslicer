from parser import *

class QiskitParser(AbstractParser):
    """
    Parser for Qiskit QuantumCircuit objects.

    Expected source type:
        qiskit.QuantumCircuit
    """

    def parse(self) -> QuantumProgram:
        self.preprocess()

        circuit = self.source

        # Extract register information
        self.program.set_registers(
            num_qubits=circuit.num_qubits,
            num_clbits=circuit.num_clbits,
        )

        # Iterate over instructions
        for instruction, qargs, cargs in circuit.data:
            name = instruction.name
            qubits = [circuit.qubits.index(q) for q in qargs]
            clbits = [circuit.clbits.index(c) for c in cargs]
            params = list(instruction.params)

            ir_instr = QuantumInstruction(
                name=name,
                qubits=qubits,
                clbits=clbits,
                params=params,
                metadata={"frontend": "qiskit"},
            )

            self.program.add_instruction(ir_instr)

        self.validate()
        return self.program