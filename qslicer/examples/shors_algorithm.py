"""
Shor's algorithm, end to end, as a hybrid quantum-classical program.

In the slicer this is the stress test for the static parser and the viewer.
The parser follows main() through argparse, the retry loop, an lru_cache and
a dozen helper functions, and rebuilds an order-finding circuit of 12,776
instructions on 13 qubits (N=21, a=2), with 10 more circuits -- each c-U(a),
each cmult(a) and its inverse -- under the Circuit picker. Three changes from
the plain program keep that deterministic: `--a` defaults to 2 (0 picks a
random base, as the original did), and `qc` below is the same circuit kept at
module level, so the tests can check the static rebuild against Qiskit's.

Factors a small odd composite N (15, 21, 35, ...) using:
  * Beauregard's 2n+3 qubit order-finding circuit (quant-ph/0205095):
    Draper Fourier-space adders, a doubly controlled modular adder,
    a controlled modular multiplier, and a controlled-U_a built from them.
  * A semiclassical QFT: one control qubit is measured, reset and reused
    2n times, with phase corrections conditioned on earlier measurement
    results (Griffiths-Niu / Kitaev iterative phase estimation).
  * The classical wrapper Shor's algorithm actually needs: even / perfect
    power checks, random base selection, a gcd shortcut, continued
    fractions, and a retry loop on bad orders.

Qubit layout (N has n bits):
  ctrl : 1 qubit     recycled phase-estimation control
  x    : n qubits    work register, holds a^k mod N
  b    : n+1 qubits  Fourier-space accumulator (extra bit catches overflow)
  anc  : 1 qubit     sign flag for the modular adder
  c    : 2n clbits   phase estimate, c[0] is the least significant bit

Where each dependency kind shows up (useful as slicing targets):
  quantum data         phi_add / qft / iqft chains on register b
  entanglement         controlled_ua: ctrl entangles with x and b via the
                       controlled multiplier and the cswap block
  measurement          order_finding_circuit: each round measures ctrl
                       into one clbit, then resets ctrl
  classical -> quantum
    (feedforward)      order_finding_circuit: if_test blocks apply phase
                       corrections conditioned on earlier clbits
    (gate parameters)  a_k = a^(2^k) mod N and its modular inverse are
                       computed classically and baked into rotation angles
    (circuit shape)    rounds where a_k == 1 skip controlled-U entirely
  quantum -> classical recover_order consumes the measured integer;
                       shor() branches and retries on the result
  compute / uncompute  phi_add_mod raises and lowers anc; controlled_ua
                       runs the inverse multiplier to clear b back to 0
"""

import argparse
import math
import random
import time
from fractions import Fraction
from functools import lru_cache

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit.circuit.library import PhaseGate
from qiskit_aer import AerSimulator


# ---------------------------------------------------------------------------
# Quantum arithmetic
# ---------------------------------------------------------------------------

def qft(qc, reg):
    """QFT with no final swaps.

    For an input integer v, output qubit k ends in |0> + e^{2 pi i v / 2^(k+1)} |1>.
    Skipping the swaps just means the adder below indexes angles accordingly.
    """
    m = len(reg)
    for j in reversed(range(m)):
        qc.h(reg[j])
        for i in reversed(range(j)):
            qc.cp(math.pi / 2 ** (j - i), reg[i], reg[j])


def iqft(qc, reg):
    """Exact inverse of qft()."""
    m = len(reg)
    for j in range(m):
        for i in range(j):
            qc.cp(-math.pi / 2 ** (j - i), reg[i], reg[j])
        qc.h(reg[j])


def phi_add(qc, b, c, controls=()):
    """Add the classical constant c to b, where b is already in Fourier space.

    c may be negative (subtraction). Up to two control qubits.
    """
    for k in range(len(b)):
        theta = 2 * math.pi * c / 2 ** (k + 1)
        if len(controls) == 0:
            qc.p(theta, b[k])
        elif len(controls) == 1:
            qc.cp(theta, controls[0], b[k])
        else:
            qc.append(PhaseGate(theta).control(len(controls)), [*controls, b[k]])


def phi_add_mod(qc, b, anc, a, N, controls):
    """b <- (b + a) mod N when every control is 1, else b unchanged.

    b is in Fourier space on entry and exit, and must satisfy b < N, a < N.
    anc starts and ends in |0>: it is computed from the sign of b + a - N,
    used to conditionally add N back, then uncomputed by comparing again.
    """
    msb = b[len(b) - 1]

    phi_add(qc, b, a, controls)          # b + a
    phi_add(qc, b, -N)                   # b + a - N
    iqft(qc, b)
    qc.cx(msb, anc)                      # anc = 1 iff b + a - N < 0
    qft(qc, b)
    phi_add(qc, b, N, [anc])             # undo the -N if it went negative

    # Uncompute anc: (result - a) is negative exactly when anc should be 0.
    phi_add(qc, b, -a, controls)
    iqft(qc, b)
    qc.x(msb)
    qc.cx(msb, anc)
    qc.x(msb)
    qft(qc, b)
    phi_add(qc, b, a, controls)


def cmult_circuit(n, a, N):
    """Controlled modular multiply-accumulate: |c>|x>|b> -> |c>|x>|b + c*a*x mod N>."""
    ctrl = QuantumRegister(1, "ctrl")
    x = QuantumRegister(n, "x")
    b = QuantumRegister(n + 1, "b")
    anc = QuantumRegister(1, "anc")
    qc = QuantumCircuit(ctrl, x, b, anc, name=f"cmult({a})")

    qft(qc, b)
    for i in range(n):
        # Classical precompute: the constant for bit i of x is a * 2^i mod N.
        phi_add_mod(qc, b, anc[0], (a * 2 ** i) % N, N, [ctrl[0], x[i]])
    iqft(qc, b)
    return qc


def controlled_ua(n, a, N):
    """Controlled-U_a: |1>|x>|0> -> |1>|a*x mod N>|0>, and identity when ctrl is 0.

    Requires gcd(a, N) == 1 so that a has an inverse mod N.
    """
    ctrl = QuantumRegister(1, "ctrl")
    x = QuantumRegister(n, "x")
    b = QuantumRegister(n + 1, "b")
    anc = QuantumRegister(1, "anc")
    qc = QuantumCircuit(ctrl, x, b, anc, name=f"c-U({a})")

    qc.compose(cmult_circuit(n, a, N), inplace=True)            # b = a*x mod N
    for i in range(n):
        qc.cswap(ctrl[0], x[i], b[i])                            # x <-> b
    a_inv = pow(a, -1, N)
    qc.compose(cmult_circuit(n, a_inv, N).inverse(), inplace=True)  # b = b - a^-1 * (a*x) = 0
    return qc


# ---------------------------------------------------------------------------
# Order finding: semiclassical phase estimation with one recycled control
# ---------------------------------------------------------------------------

def order_finding_circuit(a, N):
    """Dynamic circuit estimating s/r, where r is the order of a mod N.

    Writes the 2n-bit estimate y into c, so that s/r ~= y / 2^(2n).
    """
    n = N.bit_length()
    t = 2 * n

    ctrl = QuantumRegister(1, "ctrl")
    x = QuantumRegister(n, "x")
    b = QuantumRegister(n + 1, "b")
    anc = QuantumRegister(1, "anc")
    c = ClassicalRegister(t, "c")
    qc = QuantumCircuit(ctrl, x, b, anc, c, name=f"order_finding(a={a}, N={N})")
    work = [*ctrl, *x, *b, *anc]

    qc.x(x[0])  # x = 1, a superposition of U_a eigenstates

    # Phase is 0.m_1 m_2 ... m_t in binary. Measure m_t first, m_1 last.
    for j in range(t, 0, -1):
        k = j - 1
        a_k = pow(a, 2 ** k, N)  # U^(2^k) is multiplication by a^(2^k) mod N

        qc.reset(ctrl)
        qc.h(ctrl)
        if a_k != 1:  # U^(2^k) is the identity otherwise, so skip it
            qc.compose(controlled_ua(n, a_k, N), qubits=work, inplace=True)

        # Remove the already-known low bits m_{j+1..t} from the phase.
        for l in range(j + 1, t + 1):
            with qc.if_test((c[t - l], 1)):
                qc.p(-math.pi / 2 ** (l - j), ctrl)

        qc.h(ctrl)
        qc.measure(ctrl, c[t - j])  # m_j has weight 2^(t-j) in y

    return qc


@lru_cache(maxsize=None)
def compile_order_finding(a, N, sim):
    """The circuit depends only on (a, N), so build and transpile it once per base."""
    qc = order_finding_circuit(a, N)
    return qc, transpile(qc, sim, optimization_level=1)


def run_order_finding(a, N, sim, shots=1, seed=None):
    """Build, transpile and simulate the order-finding circuit. Returns {y: count}.

    Each call needs its own seed: reusing one makes every "random"
    measurement identical, which silently breaks the retry loop in shor().
    """
    qc, tqc = compile_order_finding(a, N, sim)
    counts = sim.run(tqc, shots=shots, seed_simulator=seed).result().get_counts()
    return {int(bits, 2): cnt for bits, cnt in counts.items()}, qc


# ---------------------------------------------------------------------------
# Classical parts
# ---------------------------------------------------------------------------

def perfect_power_base(N):
    """Return p if N == p^k for some k >= 2, else None."""
    for k in range(2, N.bit_length() + 1):
        root = round(N ** (1 / k))
        for p in (root - 1, root, root + 1):
            if p > 1 and p ** k == N:
                return p
    return None


def recover_order(y, t, a, N):
    """Continued fractions on y / 2^t, then check small multiples of the denominator.

    The multiples cover the case where s and r shared a factor, so the
    convergent's denominator is a proper divisor of r.
    """
    if y == 0:
        return None
    r0 = Fraction(y, 2 ** t).limit_denominator(N).denominator
    r = r0
    while r <= N:
        if pow(a, r, N) == 1:
            return r
        r += r0
    return None


def shor(N, rng, sim, max_attempts=20, fixed_a=None, coprime_only=False, verbose=True):
    """Return a nontrivial factor pair (p, q) of N, or None after max_attempts.

    coprime_only draws random bases from those coprime to N, so the run always
    goes through the quantum path instead of the gcd shortcut.
    """
    log = print if verbose else (lambda *args, **kwargs: None)

    if N % 2 == 0:
        log("N is even: classical shortcut.")
        return 2, N // 2
    p = perfect_power_base(N)
    if p is not None:
        log(f"N is a perfect power of {p}: classical shortcut.")
        return p, N // p

    n = N.bit_length()
    t = 2 * n
    for attempt in range(1, max_attempts + 1):
        a = fixed_a if fixed_a is not None else rng.randrange(2, N)
        while coprime_only and fixed_a is None and math.gcd(a, N) > 1:
            a = rng.randrange(2, N)
        g = math.gcd(a, N)
        if g > 1:
            log(f"[{attempt}] a={a}: gcd(a, N)={g}, classical shortcut.")
            return g, N // g

        start = time.time()
        counts, _ = run_order_finding(a, N, sim, shots=1, seed=rng.randrange(2 ** 31))
        y = next(iter(counts))
        r = recover_order(y, t, a, N)
        log(f"[{attempt}] a={a}: measured y={y} (y/2^{t} = {y / 2 ** t:.5f}), "
            f"order r={r}  ({time.time() - start:.1f}s)")

        # A bad sample (e.g. y = 0) is worth resampling with the same base.
        # Odd r or a^(r/2) = -1 are properties of the base itself, so only a
        # new random base can help; with a fixed base we stop.
        if r is None:
            log("      no valid order from this sample, retrying")
            continue
        h = pow(a, r // 2, N)
        if r % 2 == 1 or h == N - 1:
            reason = "r is odd" if r % 2 == 1 else "a^(r/2) = -1 mod N"
            if fixed_a is not None:
                log(f"      {reason}: a={a} is a bad base for N={N}, stopping")
                return None
            log(f"      {reason}, picking a new base")
            continue

        p = math.gcd(h - 1, N)
        if 1 < p < N:
            return p, N // p
        p = math.gcd(h + 1, N)
        if 1 < p < N:
            return p, N // p
        log("      trivial factors, retrying")

    return None


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Hybrid Shor's algorithm on a simulator.")
    ap.add_argument("N", type=int, nargs="?", default=21, help="odd composite to factor")
    ap.add_argument("--a", type=int, default=2,
                    help="the base to use (default 2); 0 picks one at random each attempt")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--attempts", type=int, default=20)
    ap.add_argument("--coprime-only", action="store_true",
                    help="skip bases that share a factor with N, forcing the quantum path")
    ap.add_argument("--stats", action="store_true",
                    help="print circuit size and the phase distribution for --a, then exit")
    ap.add_argument("--shots", type=int, default=256, help="shots for --stats")
    args = ap.parse_args()

    N = args.N
    rng = random.Random(args.seed)
    sim = AerSimulator(method="statevector")

    if args.stats:
        a = args.a or 2
        counts, qc = run_order_finding(a, N, sim, shots=args.shots, seed=args.seed)
        ops = qc.decompose(reps=2).count_ops()
        print(f"N={N}, a={a}: {qc.num_qubits} qubits, {qc.num_clbits} clbits, "
              f"{sum(ops.values())} ops after 2 decompositions")
        t = 2 * N.bit_length()
        for y, cnt in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
            f = Fraction(y, 2 ** t).limit_denominator(N)
            print(f"  y={y:5d}  ~{f}   {cnt}")
        return

    n = N.bit_length()
    print(f"Factoring N={N} with {2 * n + 3} qubits ({2 * n} semiclassical QFT rounds)")
    result = shor(N, rng, sim, max_attempts=args.attempts, fixed_a=args.a or None,
                  coprime_only=args.coprime_only)
    if result:
        print(f"{N} = {result[0]} x {result[1]}")
    else:
        print("No factors found.")


# The circuit main() builds with the default arguments, kept at module level
# for anything that reads the file without running main().
qc = order_finding_circuit(2, 21)


if __name__ == "__main__":
    main()
