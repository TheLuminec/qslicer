from qiskit.transpiler import PassManager, InstructionDurations
from qiskit.transpiler.passes import ALAPSchedule, DynamicalDecoupling
from qiskit.circuit.library import XGate
from qiskit import QuantumCircuit, transpile, execute, IBMQ

IBMQ.load_account()
provider = IBMQ.get_provider(hub='ibm-q-internal' , group='performance', project='demos')
realbackend = provider.get_backend('ibmq_mumbai')

circ = QuantumCircuit(4)
circ.cx(0, 1)
circ.cx(2, 3)
circ.h(0) # comment out to run without errors
circ.cx(1, 2)
circ.measure_all()

circ_t = transpile(circ, backend=realbackend,scheduling_method="alap")
dd_sequence = [XGate()] * 2
spacing = []
durations = InstructionDurations.from_backend(realbackend)
pm = PassManager([ALAPSchedule(durations),
                DynamicalDecoupling(durations, dd_sequence, qubits=None, spacing=None)])

circuits = pm.run(circuit_t)
job = realbackend.run(transpile(circuits, realbackend, scheduling_method="alap"), shots=10)