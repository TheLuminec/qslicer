#change
circuits = pm.run(circ_t)
job = execute(circuits, realbackend, shots=10)

#to
circuits = pm.run(circuit_t)
job = realbackend.run(transpile(circuits, realbackend, scheduling_method="alap"), shots=10)