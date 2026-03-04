from parser import *

class OpenQASMParser(AbstractParser):
    """
    Basic OpenQASM 2.0 text parser skeleton.

    Expected source type:
        str (OpenQASM source code)
    NOTE:
        This is a minimal structural pass.
        A full implementation should use a formal grammar or AST.
    """

    def parse(self) -> QuantumProgram:
        self.preprocess()

        lines = self.source.splitlines()

        for line in lines:
            line = line.strip()

            if not line or line.startswith("//"):
                continue

            if line.startswith("qreg"):
                # Example: qreg q[3];
                name, size = self._parse_register(line)
                self.program.set_registers(num_qubits=size)

            elif line.startswith("creg"):
                name, size = self._parse_register(line)
                self.program.num_clbits = size

            else:
                instr = self._parse_instruction(line)
                if instr:
                    self.program.add_instruction(instr)

        self.validate()
        return self.program

    def _parse_register(self, line: str):
        # Very minimal parsing logic
        left = line.split()[1]
        name = left.split("[")[0]
        size = int(left.split("[")[1].split("]")[0])
        return name, size

    def _parse_instruction(self, line: str) -> Optional[QuantumInstruction]:
        # Very naive instruction parsing
        # Example: cx q[0],q[1];

        if line.endswith(";"):
            line = line[:-1]

        tokens = line.split()
        if not tokens:
            return None

        name = tokens[0]
        args = tokens[1] if len(tokens) > 1 else ""

        qubits = []
        if args:
            parts = args.split(",")
            for part in parts:
                idx = int(part.split("[")[1].split("]")[0])
                qubits.append(idx)

        return QuantumInstruction(
            name=name,
            qubits=qubits,
            metadata={"frontend": "openqasm"},
        )
