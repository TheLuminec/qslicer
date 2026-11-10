# Debugging-centric slicing: changes to the original proposal

This lists only what differs from the original proposal. Anything not mentioned here is
built as proposed.

## Corrections (the original rule is wrong)

1. **Which gates can correlate qubits.** Original: a qubit in a basis state during a
   multi-qubit gate is not added to the group. That leaves qubit 1 out of a Bell pair
   (`h(0); cx(0,1)`: qubit 1 is |0> at the `cx`). Instead, per gate:
   - A controlled gate with any control known to be |0> does nothing.
   - A controlled gate whose controls are all known to be |1> is its base gate on the
     target (so `ch` can put the target in superposition). No grouping.
   - `cx`/`ccx`/`mcx` with the target known to be |+> or |-> leaves the target alone and
     out of the group (|-> is kickback, see 9).
   - A diagonal gate (`cz`, `cp`, `ccz`, ...) ignores operands known to be |0> or |1>.
   - `swap` exchanges the two wires' state and group.
   - Everything else groups all its qubits.

   "Basis state" always means a *known* computational basis state, |0> or |1>.

2. **Resetting a partner.** Original: a qubit entangled with a qubit that is then reset is
   back in a basis state. It is not: it is left mixed (reset half a Bell pair, the other
   half still measures randomly). The reset qubit leaves the group and becomes |0>; the
   remaining qubit stays "unknown".

3. **Uncomputed means basis state.** Original: if every operation on a qubit was
   uncomputed, it is no longer in superposition. Only true with nothing entangling in
   between (`h x cz x h` does not return to a basis state). The tracker follows the actual
   state gate by gate instead; mirror links (7) are a visual only and prove nothing.

## Clarifications (the original is underspecified)

4. **"Slice on a qubit"** means a backward slice from a measurement node. Picking a qubit
   picks its last measurement; an earlier one can be picked directly. An unmeasured qubit
   cannot be sliced this way.

5. **Barriers are kept, but carry no dependence.** They stay in the IR and on screen
   (QD08 uses a barrier to mark a deliberate pair) and the dependency rules skip them, and
   `delay`. This gives the clean per-qubit separation the proposal asks for.

6. **Resets.** An unconditional `reset` (and `initialize`, which resets first) gets no
   incoming qubit edge. A reset inside an `if_test` body does not sever: it only may run.
   Side effect: QD01 (dead gate) now reports gates that only feed a reset.

7. **`if_test` bodies.** The `if_else` instruction becomes a predicate node on no qubit
   that reads the condition bits; each body instruction is its own node with a
   `CONTROL_CLASSICAL` edge from the predicate. After the block, a wire's reaching
   definitions are the union of both branches (or of the body and what came before, with
   no `else`). OpenQASM `if` is treated the same, and so is `switch`: every case reaches
   past it, and what came before does too unless there is a `default` and every case
   wrote. `while_loop` and `for_loop` stay single nodes for now: they need loop-carried
   dependences.

8. **Mirror links.** A new detection, not QD08 (which only finds adjacent pairs). A gate
   links to the nearest earlier unmatched gate on exactly its qubits that is its inverse
   (`h`/`h`, `s`/`sdg`, `rz(a)`/`rz(-a)`), nested like brackets, never across a
   measurement, reset or conditional. The link says "mirrors", never "cancels". Drawn on
   hover/selection only, so large circuits stay responsive.

9. **Correlation** is between measurement outcomes, not qubits. One forward pass keeps
   groups whose members are live qubits and recorded measurements:
   - a gate that can correlate (see 1) merges its qubits' groups;
   - a measurement of a qubit not known to be |0>/|1> joins the group and stays in it,
     even if the qubit is reset afterwards;
   - a reset removes the qubit but not its earlier measurements;
   - a gate under a classical condition joins the group of the measurement that wrote
     the condition (teleportation-style feed-forward).

   Slicing a measurement shows the other measurements in its group as "may be correlated"
   and, separately, unmeasured qubits still in it as "may still be entangled with
   unmeasured qubit k" (the leftover-ancilla case).

10. **Kickback** uses the same state tracker, so |-> is recognized however it was reached
    (`x h`, `h z`, ...), and the target stays |-> afterwards, so every oracle call in a
    Grover loop is labelled, not just the first. A target in |+> is reported as "gate has
    no effect" instead.

11. **Predetermined measurement** is a mark on the node ("always 0" / "always 1"), not a
    lint warning. It shows only when the outcome is known before running (qubit known |0>
    or |1>). A warning would fire on every output of Bernstein-Vazirani and Deutsch-Jozsa,
    whose outcomes are deterministic by design (the tracker proves BV's secret string).
    QD03 (measured before any gate, always 0) stays as the warning.

12. **Diagonal ("phase only") label** comes from one gate set, `qslicer.gates.PHASE`. The
    `_PHASE_GATES` set in the dependency rules wrongly lists `rx`, `ry`, `u2`, `u3`, `u`
    and is corrected. On a multi-qubit diagonal gate the label reads "phase only, can
    still entangle".

13. **Multi-qubit selection colours.** One colour per selected measurement; a node in
    several slices shows a stripe per slice rather than a blended colour, which stays
    readable past two selections and for colour-blind users.

14. **Chop** is as proposed. An empty chop says "this gate cannot affect that
    measurement" instead of showing an empty graph.

## Order of work

Barriers, resets, chop and per-qubit slices first; then `if_test` bodies; then the state
tracker (items 1-3, 9-11); then the visuals, each built for both the web page and
`qslicer viz`.
