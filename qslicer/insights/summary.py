"""Everything the insight passes found about a program, as JSON-safe data.

The web page and ``qslicer viz`` both draw from this one summary, so they
label the same things.
"""

from typing import Any, Dict

from ..ir.qprogram import QuantumProgram
from ..slicer import QuantumSlicer
from .mirrors import find_mirror_pairs
from .state import track_states

__all__ = ["summarize"]


def summarize(program: QuantumProgram) -> Dict[str, Any]:
    """The insights for ``program``. Keys are node (instruction) indices unless noted.

    - ``measurements``: qubit -> its measurement nodes, in order; the last one
      is what "slice on this qubit" means.
    - ``mirrors``: ``[first, second, depth]`` per mirrored pair of gates.
    - ``kickback``: node -> the control qubits that receive the phase.
    - ``no_effect``: node -> why the gate provably does nothing.
    - ``predetermined``: measurement node -> the outcome known before running.
    - ``correlations``: measurement node -> ``measurements`` it may be
      correlated with, the ``qubits`` those read, and ``unmeasured`` qubits it
      may still be entangled with. Only measurements with something to say.
    - ``guards``: node -> the predicate node of the if_test it sits directly in.
    """
    states = track_states(program)
    correlations = {}
    measurements = QuantumSlicer.measurements_of(program)
    for nodes in measurements.values():
        for idx in nodes:
            found = states.correlation(idx)
            if found is not None and (found.measurements or found.unmeasured):
                correlations[idx] = {
                    "measurements": found.measurements,
                    "qubits": found.qubits,
                    "unmeasured": found.unmeasured,
                }
    guards = {}
    for idx in range(len(program)):
        path = program[idx].metadata.get("guards")
        if path:
            guards[idx] = path[-1][0]
    return {
        "measurements": measurements,
        "mirrors": [list(pair) for pair in find_mirror_pairs(program)],
        "kickback": dict(states.kickback),
        "no_effect": dict(states.no_effect),
        "predetermined": dict(states.predetermined),
        "correlations": correlations,
        "guards": guards,
    }
