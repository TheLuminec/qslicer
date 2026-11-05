"""Reference information for the gates the mock-up knows how to describe.

Used for the hover cards in the graph view and for the gate glossary on the
quantum info page. Anything not listed here falls back to a generic entry.

Math notation
-------------
``summary`` and ``detail`` are prose with inline math between dollar signs;
``matrix`` is a bare expression. Both are rendered by
``static/js/mathfmt.js``, whose docstring lists the markup it understands:

    |0>                  ket
    \\sqrt{2}             radical with the bar over the radicand
    \\frac{1}{2}          stacked fraction
    ^{2}  _{k}           super- and subscript
    \\pi \\theta ...       Greek letters
    \\i                   the imaginary unit
    \\matrix{a & b \\\\ c & d}   matrix with brackets

Keep these as raw strings so the backslashes survive.
"""

from typing import Any, Dict

# Categories drive the node color in the graph view.
SINGLE = "single-qubit"
MULTI = "multi-qubit"
MEASURE = "measurement"
BARRIER = "structural"
CLASSICAL = "classical"

_GATES: Dict[str, Dict[str, Any]] = {
    "h": {
        "title": "Hadamard",
        "category": SINGLE,
        "qubits": 1,
        "summary": r"Puts a basis state into an equal superposition.",
        "detail": r"Maps $|0>$ to $\frac{|0> + |1>}{\sqrt{2}}$ and $|1>$ to "
                  r"$\frac{|0> - |1>}{\sqrt{2}}$. Two Hadamards in a row cancel, so the "
                  r"dependence checks flag the second one as an uncomputation of the first "
                  r"(QD08).",
        "matrix": r"\frac{1}{\sqrt{2}}\matrix{1 & 1 \\ 1 & -1}",
        "self_inverse": True,
    },
    "x": {
        "title": "Pauli-X (NOT)",
        "category": SINGLE,
        "qubits": 1,
        "summary": r"Flips $|0>$ and $|1>$.",
        "detail": r"A rotation of $\pi$ radians about the X axis of the Bloch sphere.",
        "matrix": r"\matrix{0 & 1 \\ 1 & 0}",
        "self_inverse": True,
    },
    "y": {
        "title": "Pauli-Y",
        "category": SINGLE,
        "qubits": 1,
        "summary": r"Bit flip plus phase flip.",
        "detail": r"A rotation of $\pi$ radians about the Y axis.",
        "matrix": r"\matrix{0 & -\i \\ \i & 0}",
        "self_inverse": True,
    },
    "z": {
        "title": "Pauli-Z",
        "category": SINGLE,
        "qubits": 1,
        "summary": r"Flips the phase of $|1>$.",
        "detail": r"Leaves Z-basis measurement probabilities untouched, which is why phase "
                  r"dependencies need a rule of their own in the QDG.",
        "matrix": r"\matrix{1 & 0 \\ 0 & -1}",
        "self_inverse": True,
        "phase": True,
    },
    "s": {
        "title": "S (phase)", "category": SINGLE, "qubits": 1,
        "summary": r"Quarter turn about Z.",
        "detail": r"Adds a phase of $\i$ to $|1>$. Its inverse is $\text{sdg}$.",
        "matrix": r"\matrix{1 & 0 \\ 0 & \i}", "phase": True,
    },
    "sdg": {
        "title": "S-dagger", "category": SINGLE, "qubits": 1,
        "summary": r"Inverse of S, written $S^{\dagger}$.",
        "detail": r"Adds a phase of $-\i$ to $|1>$. Paired with H it rotates a Y-basis "
                  r"measurement into the computational basis.",
        "matrix": r"\matrix{1 & 0 \\ 0 & -\i}", "phase": True,
    },
    "t": {
        "title": "T (pi/8)", "category": SINGLE, "qubits": 1,
        "summary": r"Eighth turn about Z.",
        "detail": r"Adds a phase of $e^{\i\pi/4}$ to $|1>$. Its inverse is $T^{\dagger}$.",
        "matrix": r"\matrix{1 & 0 \\ 0 & e^{\i\pi/4}}", "phase": True,
    },
    "tdg": {
        "title": "T-dagger", "category": SINGLE, "qubits": 1,
        "summary": r"Inverse of T.",
        "detail": r"Adds a phase of $e^{-\i\pi/4}$ to $|1>$.",
        "matrix": r"\matrix{1 & 0 \\ 0 & e^{-\i\pi/4}}", "phase": True,
    },
    "sx": {
        "title": "Square root of X", "category": SINGLE, "qubits": 1,
        "summary": r"Half a bit flip.",
        "detail": r"Applied twice it is equivalent to X. A native gate on much IBM hardware.",
        "matrix": r"\frac{1}{2}\matrix{1+\i & 1-\i \\ 1-\i & 1+\i}",
    },
    "rx": {
        "title": "RX(theta)", "category": SINGLE, "qubits": 1,
        "summary": r"Rotation of $\theta$ about the X axis.",
        "detail": r"The angle comes from the first instruction parameter.",
        "matrix": r"\matrix{\cos\frac{\theta}{2} & -\i\sin\frac{\theta}{2} \\ "
                  r"-\i\sin\frac{\theta}{2} & \cos\frac{\theta}{2}}",
       
    },
    "ry": {
        "title": "RY(theta)", "category": SINGLE, "qubits": 1,
        "summary": r"Rotation of $\theta$ about the Y axis.",
        "detail": r"The angle comes from the first instruction parameter.",
        "matrix": r"\matrix{\cos\frac{\theta}{2} & -\sin\frac{\theta}{2} \\ "
                  r"\sin\frac{\theta}{2} & \cos\frac{\theta}{2}}",
       
    },
    "rz": {
        "title": "RZ(theta)", "category": SINGLE, "qubits": 1,
        "summary": r"Rotation of $\theta$ about the Z axis.",
        "detail": r"A pure phase rotation, invisible to a Z-basis measurement on its own but "
                  r"very visible once it sits inside an interference pattern.",
        "matrix": r"\matrix{e^{-\i\theta/2} & 0 \\ 0 & e^{\i\theta/2}}", "phase": True,
    },
    "p": {
        "title": "Phase(lambda)", "category": SINGLE, "qubits": 1,
        "summary": r"Adds a relative phase $\lambda$ to $|1>$.",
        "detail": r"Generalizes S and T to an arbitrary angle.",
        "matrix": r"\matrix{1 & 0 \\ 0 & e^{\i\lambda}}", "phase": True,
    },
    "u": {
        "title": "U(theta, phi, lambda)", "category": SINGLE, "qubits": 1,
        "summary": r"General single-qubit rotation.",
        "detail": r"Every one-qubit gate can be written in this form, up to a global phase. "
                  r"Transpiled circuits are often expressed in u / u1 / u2 / u3, so a circuit "
                  r"you transpiled yourself will show these names.",
        "matrix": r"\matrix{\cos\frac{\theta}{2} & -e^{\i\lambda}\sin\frac{\theta}{2} \\ "
                  r"e^{\i\phi}\sin\frac{\theta}{2} & e^{\i(\phi+\lambda)}\cos\frac{\theta}{2}}",
       
    },
    "u1": {
        "title": "U1(lambda)", "category": SINGLE, "qubits": 1,
        "summary": r"Legacy phase gate from older transpiler output.",
        "detail": r"Equivalent to $p(\lambda)$.",
        "matrix": r"\matrix{1 & 0 \\ 0 & e^{\i\lambda}}", "phase": True,
    },
    "u2": {
        "title": "U2(phi, lambda)", "category": SINGLE, "qubits": 1,
        "summary": r"Legacy gate from older transpiler output.",
        "detail": r"A Hadamard-like rotation carrying two phase angles.",
        "matrix": r"\frac{1}{\sqrt{2}}\matrix{1 & -e^{\i\lambda} \\ "
                  r"e^{\i\phi} & e^{\i(\phi+\lambda)}}",
       
    },
    "u3": {
        "title": "U3(theta, phi, lambda)", "category": SINGLE, "qubits": 1,
        "summary": r"Legacy general rotation.",
        "detail": r"The same operation as u.",
        "matrix": r"\matrix{\cos\frac{\theta}{2} & -e^{\i\lambda}\sin\frac{\theta}{2} \\ "
                  r"e^{\i\phi}\sin\frac{\theta}{2} & e^{\i(\phi+\lambda)}\cos\frac{\theta}{2}}",
       
    },
    "id": {
        "title": "Identity", "category": SINGLE, "qubits": 1,
        "summary": r"Does nothing.",
        "detail": r"Sometimes inserted as a timing placeholder.",
        "matrix": r"\matrix{1 & 0 \\ 0 & 1}",
    },

    "cx": {
        "title": "CNOT (controlled-X)",
        "category": MULTI,
        "qubits": 2,
        "summary": r"Flips the target when the control is $|1>$.",
        "detail": r"The workhorse entangling gate. With the control in $\frac{|0> + |1>}{\sqrt{2}}$ "
                  r"and the target in $|0>$ it produces $\frac{|00> + |11>}{\sqrt{2}}$, a Bell pair, "
                  r"which is where most entanglement groups are born.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 0 & 0 & 1 \\ 0 & 0 & 1 & 0 \\ 0 & 1 & 0 & 0}",
        "entangling": True,
        "self_inverse": True,
    },
    "cy": {
        "title": "Controlled-Y", "category": MULTI, "qubits": 2,
        "summary": r"Applies Y to the target when the control is $|1>$.",
        "detail": r"Entangling.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 0 & 0 & -\i \\ 0 & 0 & 1 & 0 \\ 0 & \i & 0 & 0}",
        "entangling": True, "self_inverse": True,
    },
    "cz": {
        "title": "Controlled-Z", "category": MULTI, "qubits": 2,
        "summary": r"Flips the phase of $|11>$.",
        "detail": r"Symmetric in its two qubits: control and target are interchangeable.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 1 & 0 & 0 \\ 0 & 0 & 1 & 0 \\ 0 & 0 & 0 & -1}",
        "entangling": True, "phase": True, "self_inverse": True,
    },
    "ch": {
        "title": "Controlled-H", "category": MULTI, "qubits": 2,
        "summary": r"Hadamard on the target when the control is $|1>$.",
        "detail": r"Entangling. The Hadamard acts on the amplitudes where the control is $|1>$.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ "
                  r"0 & \frac{1}{\sqrt{2}} & 0 & \frac{1}{\sqrt{2}} \\ "
                  r"0 & 0 & 1 & 0 \\ "
                  r"0 & \frac{1}{\sqrt{2}} & 0 & -\frac{1}{\sqrt{2}}}",
        "entangling": True, "self_inverse": True,
    },
    "crx": {
        "title": "Controlled-RX", "category": MULTI, "qubits": 2,
        "summary": r"Conditional rotation of $\theta$ about X.",
        "detail": r"Entangling. Identity where the control is $|0>$, $RX(\theta)$ where it is $|1>$.",
        "matrix": r"I \otimes |0><0| + RX(\theta) \otimes |1><1|", "entangling": True,
    },
    "cry": {
        "title": "Controlled-RY", "category": MULTI, "qubits": 2,
        "summary": r"Conditional rotation of $\theta$ about Y.",
        "detail": r"Entangling.",
        "matrix": r"I \otimes |0><0| + RY(\theta) \otimes |1><1|", "entangling": True,
    },
    "crz": {
        "title": "Controlled-RZ", "category": MULTI, "qubits": 2,
        "summary": r"Conditional rotation of $\theta$ about Z.",
        "detail": r"Entangling and phase-carrying.",
        "matrix": r"\text{diag}(1, e^{-\i\theta/2}, 1, e^{\i\theta/2})", "entangling": True, "phase": True,
    },
    "cp": {
        "title": "Controlled-phase", "category": MULTI, "qubits": 2,
        "summary": r"Adds $e^{\i\lambda}$ to the $|11>$ amplitude only.",
        "detail": r"Central to the quantum Fourier transform.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 1 & 0 & 0 \\ 0 & 0 & 1 & 0 \\ "
                  r"0 & 0 & 0 & e^{\i\lambda}}",
        "entangling": True, "phase": True,
    },
    "csx": {
        "title": "Controlled-sqrt(X)", "category": MULTI, "qubits": 2,
        "summary": r"Conditional half bit flip.",
        "detail": r"Entangling.",
        "matrix": r"I \otimes |0><0| + \sqrt{X} \otimes |1><1|", "entangling": True,
    },
    "ccx": {
        "title": "Toffoli (CCX)", "category": MULTI, "qubits": 3,
        "summary": r"Flips the target when both controls are $|1>$.",
        "detail": r"A reversible AND: $|a,b,c> \mapsto |a,b,c \oplus ab>$. Very common inside "
                  r"oracles, and the classic place where an ancilla qubit has to be uncomputed "
                  r"afterwards.",
        "matrix": r"\text{8}\times\text{8 permutation}",
        "entangling": True, "self_inverse": True,
    },
    "c3x": {
        "title": "3-controlled X", "category": MULTI, "qubits": 4,
        "summary": r"X with three controls.",
        "detail": r"Entangling.", "matrix": r"\text{permutation}", "entangling": True,
        "self_inverse": True,
    },
    "c4x": {
        "title": "4-controlled X", "category": MULTI, "qubits": 5,
        "summary": r"X with four controls.",
        "detail": r"Entangling.", "matrix": r"\text{permutation}", "entangling": True,
        "self_inverse": True,
    },
    "mcx": {
        "title": "Multi-controlled X", "category": MULTI, "qubits": 3,
        "summary": r"X with an arbitrary number of controls.",
        "detail": r"Decomposes into a ladder of Toffolis, often with ancilla qubits.",
        "matrix": r"\text{permutation}", "entangling": True, "self_inverse": True,
    },
    "mcphase": {
        "title": "Multi-controlled phase", "category": MULTI, "qubits": 3,
        "summary": r"Adds $e^{\i\lambda}$ to the amplitude where every qubit is $|1>$.",
        "detail": r"What qc.mcp, MCPhaseGate and PhaseGate($\lambda$).control(n) build; with "
                  r"one control it is cp. Diagonal, so it only changes phases, and symmetric: "
                  r"any of its qubits can be read as the target. Fourier-space adders, such as "
                  r"the doubly controlled ones in Shor's algorithm, are made of these. The "
                  r"matrix shown is for two controls.",
        "matrix": r"\text{diag}(1, 1, 1, 1, 1, 1, 1, e^{\i\lambda})",
        "entangling": True, "phase": True,
    },
    "swap": {
        "title": "SWAP", "category": MULTI, "qubits": 2,
        "summary": r"Exchanges two qubits: $|ab> \mapsto |ba>$.",
        "detail": r"Not entangling by itself, but it moves entanglement between wires, so the "
                  r"data-qubit rule still chains both wires through it.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 0 & 1 & 0 \\ 0 & 1 & 0 & 0 \\ 0 & 0 & 0 & 1}",
        "self_inverse": True,
    },
    "iswap": {
        "title": "iSWAP", "category": MULTI, "qubits": 2,
        "summary": r"Swap with a phase of $\i$ on the swapped amplitudes.",
        "detail": r"Entangling.",
        "matrix": r"\matrix{1 & 0 & 0 & 0 \\ 0 & 0 & \i & 0 \\ 0 & \i & 0 & 0 \\ 0 & 0 & 0 & 1}",
        "entangling": True,
    },
    "cswap": {
        "title": "Fredkin (CSWAP)", "category": MULTI, "qubits": 3,
        "summary": r"Swaps two qubits when the control is $|1>$.",
        "detail": r"Entangling; the core of the swap test for state overlap.",
        "matrix": r"\text{8}\times\text{8 permutation}",
        "entangling": True, "self_inverse": True,
    },
    "dcx": {
        "title": "Double CNOT", "category": MULTI, "qubits": 2,
        "summary": r"Two back-to-back CNOTs in opposite directions.",
        "detail": r"Entangling.", "matrix": r"\text{permutation}", "entangling": True,
    },
    "ecr": {
        "title": "Echoed cross-resonance", "category": MULTI, "qubits": 2,
        "summary": r"Hardware-native entangling gate.",
        "detail": r"The basis two-qubit gate on several IBM devices.",
        "matrix": r"\frac{1}{\sqrt{2}}(IX - XY)", "entangling": True, "self_inverse": True,
    },
    "rxx": {
        "title": "RXX(theta)", "category": MULTI, "qubits": 2,
        "summary": r"XX interaction of angle $\theta$.",
        "detail": r"Entangling for most angles.",
        "matrix": r"e^{-\i\theta(X \otimes X)/2}", "entangling": True,
    },
    "ryy": {
        "title": "RYY(theta)", "category": MULTI, "qubits": 2,
        "summary": r"YY interaction of angle $\theta$.",
        "detail": r"Entangling for most angles.",
        "matrix": r"e^{-\i\theta(Y \otimes Y)/2}", "entangling": True,
    },
    "rzz": {
        "title": "RZZ(theta)", "category": MULTI, "qubits": 2,
        "summary": r"ZZ interaction of angle $\theta$.",
        "detail": r"Entangling for most angles, and diagonal, so it only changes phases. The "
                  r"cost layer of QAOA is built from these.",
        "matrix": r"e^{-\i\theta(Z \otimes Z)/2}", "entangling": True, "phase": True,
    },

    "measure": {
        "title": "Measurement",
        "category": MEASURE,
        "qubits": 1,
        "summary": r"Collapses a qubit and writes the outcome to a classical bit.",
        "detail": r"Irreversible. A state $\alpha|0> + \beta|1>$ reads 0 with probability "
                  r"$|\alpha|^{2}$ and 1 with probability $|\beta|^{2}$, and whatever it reads "
                  r"is what the qubit now is. Everything downstream that reads the classical "
                  r"bit picks up a measurement dependency.",
        "matrix": r"\text{not unitary}",
    },
    "measure_all": {
        "title": "Measure all", "category": MEASURE, "qubits": 0,
        "summary": r"Measures every qubit into a fresh classical register.",
        "detail": r"Expanded into individual measurements before it reaches the IR.",
        "matrix": r"\text{not unitary}",
    },
    "reset": {
        "title": "Reset", "category": MEASURE, "qubits": 1,
        "summary": r"Forces a qubit back to $|0>$.",
        "detail": r"Also irreversible; treated as a write to the wire.",
        "matrix": r"\text{not unitary}",
    },
    "if_else": {
        "title": "Classical conditional", "category": CLASSICAL, "qubits": 1,
        "summary": r"Runs a block of gates only when a classical bit matches.",
        "detail": r"Produced by a $\text{with qc.if_test((clbit, value))}$ block, or an "
                  r"OpenQASM $\text{if}$ on a measured value. The body is carried as a "
                  r"parameter, so the graph shows the branch as one node; the classical bits "
                  r"it reads are what give it a measurement dependency.",
        "matrix": r"\text{not unitary}",
    },
    "while_loop": {
        "title": "Run-time loop", "category": CLASSICAL, "qubits": 1,
        "summary": r"Repeats a block for as long as a classical condition holds.",
        "detail": r"Produced by an OpenQASM $\text{while}$ on a measured value. How many times "
                  r"it runs is only known at run time, so the body is one node over every "
                  r"qubit it touches; the bits its condition reads give it a measurement "
                  r"dependency.",
        "matrix": r"\text{not unitary}",
    },
    "for_loop": {
        "title": "Loop with a run-time exit", "category": CLASSICAL, "qubits": 1,
        "summary": r"A counted loop that a measured value can end early.",
        "detail": r"OpenQASM $\text{for}$ loops are unrolled into separate nodes. One with a "
                  r"$\text{break}$ or $\text{continue}$ under a run-time condition cannot be, "
                  r"so it becomes one node over everything any iteration does.",
        "matrix": r"\text{not unitary}",
    },
    "switch_case": {
        "title": "Classical switch", "category": CLASSICAL, "qubits": 1,
        "summary": r"Runs one of several blocks, chosen by a classical value.",
        "detail": r"Produced by an OpenQASM $\text{switch}$ on a measured value. Every case is "
                  r"carried as a parameter, and the bits the target reads give it a "
                  r"measurement dependency.",
        "matrix": r"\text{not unitary}",
    },
    "store": {
        "title": "Classical copy", "category": CLASSICAL, "qubits": 0,
        "summary": r"Copies a classical value into bits.",
        "detail": r"Produced by an OpenQASM assignment such as $\text{b = c[0];}$. It acts on "
                  r"no qubit; reading one bit and writing another is what carries the "
                  r"measurement dependency on to whatever later reads the copy.",
        "matrix": r"\text{not unitary}",
    },
    "c_if": {
        "title": "Classical conditional (legacy)", "category": CLASSICAL, "qubits": 1,
        "summary": r"Pre-2.0 form of a classically conditioned gate.",
        "detail": r"Removed in Qiskit 2.0. The static checks flag it, because a circuit using "
                  r"it will not build at all on a current Qiskit.",
        "matrix": r"\text{not unitary}",
    },
    "barrier": {
        "title": "Barrier", "category": BARRIER, "qubits": 0,
        "summary": r"Optimization fence and visual divider.",
        "detail": r"Carries no physics, but it does touch every qubit it spans, so it appears "
                  r"in the data-qubit chain and quietly links wires that never interact.",
        "matrix": r"\text{none}",
    },
    "init": {
        "title": "Wire initialization", "category": BARRIER, "qubits": 1,
        "summary": r"Synthetic node for the $|0>$ state of a wire.",
        "detail": r"Created by the analysis rather than by your program.",
        "matrix": r"\text{none}",
    },
}

# Flags use the standard physics meaning, not the dependency rules' gate sets
# (those live in qslicer/dependencies and are expected to change):
#   entangling    can create entanglement between the qubits it acts on
#   phase         diagonal in the computational basis, so it only changes phases
#   self_inverse  applying it twice is the identity
# Multi-qubit matrices use Qiskit's little-endian ordering: the first qubit
# argument is the least significant bit of the basis-state index.
ENTANGLING = frozenset(name for name, info in _GATES.items() if info.get("entangling"))
PHASE = frozenset(name for name, info in _GATES.items() if info.get("phase"))
SELF_INVERSE = frozenset(name for name, info in _GATES.items() if info.get("self_inverse"))


def describe(name: str) -> Dict[str, Any]:
    """Return a hover-card description for a gate name, with a sane fallback."""
    info = _GATES.get(name)
    if info is None:
        return {
            "name": name,
            "title": name.upper(),
            "category": SINGLE,
            "qubits": 0,
            "summary": "No reference entry for this operation yet.",
            "detail": "The parser took this name straight from the circuit. It is analyzed "
                      "normally; only the description is missing.",
            "matrix": r"\text{unknown}",
            "entangling": False,
            "phase": False,
            "self_inverse": False,
            "known": False,
        }
    return {
        "name": name,
        "title": info["title"],
        "category": info["category"],
        "qubits": info.get("qubits", 1),
        "summary": info["summary"],
        "detail": info["detail"],
        "matrix": info.get("matrix", ""),
        "entangling": bool(info.get("entangling")),
        "phase": bool(info.get("phase")),
        "self_inverse": bool(info.get("self_inverse")),
        "known": True,
    }


def glossary():
    """Every known gate, ordered for the reference table on the info page."""
    order = {SINGLE: 0, MULTI: 1, MEASURE: 2, CLASSICAL: 3, BARRIER: 4}
    return sorted(
        (describe(name) for name in _GATES),
        key=lambda g: (order.get(g["category"], 9), g["name"]),
    )
