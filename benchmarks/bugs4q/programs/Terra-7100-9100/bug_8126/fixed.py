from qiskit.circuit import Parameter
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.synthesis import SuzukiTrotter
from qiskit.opflow import X, Z

suzuki = SuzukiTrotter(order=2, reps=1)  # for order=1 you should use LieTrotter

time = 11.0
H = (X^X^X) + (Z^Z^Z)
U1 = PauliEvolutionGate(H, time=time, synthesis=suzuki).definition
print(U1)

t = Parameter('t')
U2 = PauliEvolutionGate(H, time=t, synthesis=suzuki).definition
U2_t = U2.bind_parameters({t:time})
print(U2_t)