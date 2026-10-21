from qiskit.opflow import X, Z, Suzuki
from qiskit.circuit import Parameter

time = 11.0
H = (X^X^X) + (Z^Z^Z)
U1 = Suzuki(1, order=1).convert(time * H)
print(U1)

t = Parameter('t')
U2 = Suzuki(1, order=1).convert(t * H)
U2_t = U2.bind_parameters({t:time})
print(U2_t)

#Qiskit Terra version:0.36.0
#url：https://github.com/Qiskit/qiskit/issues/8126
#type：Runtime Error