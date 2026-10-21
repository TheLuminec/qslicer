from qiskit.providers.fake_provider import FakeBelem
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

backend = FakeBelem()
pass_manager = generate_preset_pass_manager(
    optimization_level=3, backend=backend, unitary_synthesis_method="a"
)