#change
from qiskit.opflow import X, Z, Suzuki

U1 = Suzuki(1, order=1).convert(time * H)

U2 = Suzuki(1, order=1).convert(t * H)


#to
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.synthesis import SuzukiTrotter
from qiskit.opflow import X, Z

U1 = PauliEvolutionGate(H, time=time, synthesis=suzuki).definition

U2 = PauliEvolutionGate(H, time=t, synthesis=suzuki).definition

#add
suzuki = SuzukiTrotter(order=2, reps=1)