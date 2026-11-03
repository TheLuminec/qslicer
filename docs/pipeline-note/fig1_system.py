"""Figure 1: the system overview, with the web app as a layer on the left.

One request enters from the browser; analyze_source runs the lint path first,
then the analysis path, and the JSON response goes back along the top lane. A
slice request goes from slice_program along the second top lane to the cached
QDG. Imported by gen_figs.py, which owns the drawing helpers.
"""


def system(Fig):
    f = Fig("f1")
    W, H = 1910, 1134
    # column x positions and widths
    WEB = (40, 210)
    FE = (290, 380)
    MOD = (700, 215)
    IR = (945, 225)
    QDG = (1200, 250)
    SL = (1480, 200)
    OUTC = (1710, 185)
    A_TOP, A_BOT = 76, 640          # analysis band
    L_TOP, L_BOT = 652, 964         # lint band
    B_TOP, B_BOT = 976, 1124        # benchmark band

    # ---------------- bands and headings ----------------
    f.rect(14, A_TOP, 248, B_BOT - A_TOP, "band band-w", 10)
    f.rect(276, A_TOP, W - 290, A_BOT - A_TOP, "band band-a", 10)
    f.rect(276, L_TOP, W - 290, L_BOT - L_TOP, "band band-l", 10)
    f.rect(276, B_TOP, W - 290, B_BOT - B_TOP, "band band-b", 10)
    f.text(28, A_TOP + 18, "WEB APP", "bandt")
    f.text(28, A_TOP + 32, "Flask · webapp/", "bands")
    f.text(FE[0], A_TOP + 18, "ANALYSIS PATH", "bandt")
    f.text(FE[0] + 104, A_TOP + 18, "builds the QDG, answers slices", "bands")
    f.text(FE[0], L_TOP + 18, "LINT PATH", "bandt")
    f.text(FE[0] + 72, L_TOP + 18, "runs first, on its own parse (linting on)", "bands")
    f.text(FE[0], B_TOP + 18, "BENCHMARK PATH", "bandt")
    f.text(FE[0] + 118, B_TOP + 18, "grades the same linters on buggy and fixed programs", "bands")
    for (x, _), s in ((WEB, "WEB LAYER"), (FE, "STATIC FRONT ENDS"), (MOD, "CIRCUIT MODEL"),
                      (IR, "IR"), (QDG, "QDG CONSTRUCTION"), (SL, "SLICING"), (OUTC, "RESPONSE")):
        f.text(x, 20, s, "colh")

    # ---------------- web layer ----------------
    wx, ww = WEB
    f.box(wx, 118, ww, 116, "Browser · /slicer page", [
        "editor: source, language",
        "and lint-rule selection",
        "graph: typed edges, clbit",
        "wires, lint marks, slices",
    ], "box box-key", sub="slicer.js · graph.js")
    f.box(wx, 258, ww, 100, "app.py · Flask routes", [
        "`POST /api/analyze",
        "`POST /api/slice",
        "`POST /api/benchmark",
        "checks types and size",
    ], "box")
    f.arrow([(wx + 105, 234), (wx + 105, 258)], "ar")
    f.text(wx + 112, 250, "fetch", "al", "start")
    sx = wx + 75                     # slice_program box
    f.box(sx, 382, ww - 75, 62, "slice_program", [
        "token → cached QDG",
        "miss → stale_token",
    ], "box")
    f.arrow([(sx + 70, 358), (sx + 70, 382)], "ar")
    f.box(wx, 466, ww, 168, "analyze_source", [
        "1  linters.run(selection)",
        "2  QS000? stop, no graph",
        "3  _parse_source, 20 s",
        "4  QDG(program, rules)",
        "5  cache under a token",
        "6  nodes, edges, entanglement",
        "7  attach_lint_nodes",
    ], "box box-key", sub="analysis.py · in this order")
    f.arrow([(wx + 22, 358), (wx + 22, 466)], "ar")
    f.box(wx, 1006, ww, 92, "benchmark.run", [
        "selection + dataset;",
        "one run at a time",
    ], "box", sub="benchmark.py")
    f.arrow([(wx, 330), (26, 330), (26, 1060), (wx, 1060)], "ar")

    # ---------------- top lanes: response and slice request ----------------
    ox, ow = OUTC
    f.arrow([(ox + ow - 30, 118), (ox + ow - 30, 44), (wx + 170, 44), (wx + 170, 118)], "ar-r")
    f.text(1000, 38, "JSON response: the analysis payload, or a slice", "al", "middle")
    slx, slw = SL
    f.arrow([(sx + ww - 75, 413), (268, 413), (268, 62), (slx + 70, 62), (slx + 70, 118)], "ar")
    f.text(1000, 56, "slice request: token, node, direction, dep_types", "al", "middle")

    # ---------------- static front ends ----------------
    fx, fw = FE
    f.rect(fx, 104, fw, 526, "box box-group", 8)
    f.text(fx + 12, 122, "parse() never imports or runs the program", "sub")
    bx = fx + 24                     # left column of boxes, right of the dispatch bus
    f.box(bx, 136, 150, 112, "ast.parse(source)", [
        "Python syntax tree",
        "",
        "a SyntaxError →",
        "`StaticParseError",
        "with its line",
    ], "box")
    ix, iw = bx + 164, fx + fw - 10 - (bx + 164)
    f.box(ix, 134, iw, 200, "_Interpreter", [
        "walks the tree over values",
        "it controls:",
        "`Unknown Symbol",
        "`_ModuleRef StaticGate",
        "`StaticCircuit ...",
        "imports recorded, not run;",
        "loops and helpers run",
        "concretely, so they unroll",
        "~≤2M steps · depth 40",
    ], "box box-key")
    f.arrow([(bx + 150, 190), (ix, 190)], "ar")
    f.box(bx, 356, 150, 78, "openqasm3.parse", [
        "ANTLR reference",
        "grammar; spans",
    ], "box")
    f.box(ix, 350, iw, 180, "_QasmInterpreter", [
        "for → unrolled",
        "def → inlined",
        "known branch → taken",
        "branch on measured bits",
        "→ one if_else, switch_case",
        "or while_loop instruction",
        "measured copy → store",
        "~≤2M steps · depth 40",
    ], "box box-key")
    f.arrow([(bx + 150, 395), (ix, 395)], "ar")
    # dispatcher and its bus
    f.box(bx, 552, fw - 34, 66, "_parse_source · parse()", [
        "the request's language picks one front",
        "end; parse() returns a QuantumProgram",
    ], "box box-key")
    f.badge(bx + fw - 34 - 14, 566, 3)
    f.add(f'<path class="ar" d="M{bx+16},{552} L{bx+16},{542} L{fx+10},{542} L{fx+10},{190} L{bx-2},{190}" '
          f'marker-end="url(#f1-ah)"/>')
    f.arrow([(fx + 10, 395), (bx - 2, 395)], "ar")
    # analyze_source -> dispatcher
    f.arrow([(wx + ww, 586), (bx, 586)], "ar")

    # ---------------- circuit model ----------------
    mx, mw = MOD
    f.rect(mx, 104, mw, 526, "box box-group", 8)
    f.text(mx + 10, 122, "staticqiskitparser.py", "sub")
    f.box(mx + 10, 134, mw - 20, 112, "StaticCircuit", [
        "`data: [StaticInstruction]",
        "`qregs cregs qubits clbits",
        "`_scopes: open if blocks",
        "~broadcast() as Qiskit does",
    ], "box")
    f.box(mx + 10, 258, mw - 20, 82, "StaticInstruction", [
        "`operation qubits clbits",
        "`location condition",
        "`condition_bits",
    ], "box")
    f.box(mx + 10, 352, mw - 20, 80, "_choose_circuit", [
        "Qiskit only: picks the",
        "module-level circuit with",
        "the most instructions",
    ], "box")
    f.box(mx + 10, 444, mw - 20, 78, "circuit_to_program", [
        "bits → flat int indices",
        "`condition_bits →",
        "`  condition_clbits",
    ], "box box-key")
    f.arrow([(mx + mw / 2, 432), (mx + mw / 2, 444)], "ar")
    f.arrow([(ix + iw, 230), (mx + 10, 200)], "ar")
    f.text(ix + iw + 16, 206, "places", "al", "middle")
    f.arrow([(ix + iw, 450), (mx + 10, 300)], "ar")

    # ---------------- IR ----------------
    rx, rw = IR
    f.rect(rx, 104, rw, 526, "box box-group", 8)
    f.text(rx + 10, 122, "qprogram.py · qinstruction.py", "sub")
    f.box(rx + 10, 134, rw - 20, 78, "QuantumProgram", [
        "`num_qubits num_clbits",
        "`instructions[0 .. n-1]",
    ], "box")
    f.box(rx + 10, 222, rw - 20, 150, "QuantumInstruction", [
        "`name",
        "`qubits: [int]",
        "`clbits: [int]",
        "`params  if_else bodies",
        "`        stay nested here",
        "`metadata.condition_clbits",
        "`location",
    ], "box box-key")
    f.box(rx + 10, 384, rw - 20, 176, "SourceLocation", [
        "`line column",
        "`end_line end_column",
        "`via: call-site lines,",
        "`     outermost first",
        "",
        "~used by the lint path:",
        "~finding lines and call",
        "~chains, span → node",
    ], "box box-loc")
    f.arrow([(mx + mw - 10, 484), (mx + mw + 15, 484), (mx + mw + 15, 172), (rx + 10, 172)], "ar")

    # ---------------- QDG ----------------
    qx, qw = QDG
    f.rect(qx, 104, qw, 526, "box box-group", 8)
    f.text(qx + 10, 122, "QDG(program, rules) · qdg.py", "sub")
    f.text(qx + 10, 144, "node i = instruction i; each rule", "t")
    f.text(qx + 10, 159, "makes one pass in program order", "t")
    f.text(qx + 10, 174, "with a last-writer map", "t")
    f.badge(qx + qw - 16, 118, 4)
    y = 188
    for name, state, dep, k in (("DataQubitRule", "last_def[q]", "DATA_QUBIT", "k-dq"),
                                ("MeasurementRule", "last_measured[c]", "MEASUREMENT", "k-me"),
                                ("DataClassicalRule", "last_write[c]", "DATA_CLASSICAL", "k-dc")):
        f.rect(qx + 10, y, qw - 20, 40, "box", 5)
        f.add(f'<rect class="{k}" x="{qx+10}" y="{y}" width="5" height="40" rx="2"/>')
        f.text(qx + 22, y + 16, name, "t b")
        f.text(qx + 22, y + 32, state, "m")
        f.text(qx + qw - 18, y + 16, dep, "kl " + k + "t", "end")
        y += 46
    # the rules that emit nothing, grouped
    gy = y + 6
    f.rect(qx + 10, gy, qw - 20, 166, "box box-off", 6)
    f.text(qx + 20, gy + 18, "Unused in the baseline profile", "t b")
    f.text(qx + 20, gy + 33, "no edges come from these", "sub")
    y = gy + 42
    for name, note in (("ControlClassicalRule", "in profile, empty"),
                       ("EntanglementRule", "commented out"),
                       ("PhaseRule", "commented out")):
        f.rect(qx + 18, y, qw - 36, 34, "box", 4)
        f.add(f'<rect class="k-off" x="{qx+18}" y="{y}" width="4" height="34" rx="2"/>')
        f.text(qx + 28, y + 21, name, "t")
        f.text(qx + qw - 24, y + 21, note, "kl k-offt", "end")
        y += 40
    f.text(qx + 10, 560, "nx.MultiDiGraph; every edge u → v", "t")
    f.text(qx + 10, 575, "has u < v, a type and its rule:", "t")
    f.text(qx + 10, 590, "a DAG in program order", "t")
    f.arrow([(rx + rw, 172), (qx, 172)], "ar")

    # ---------------- cache and slicer ----------------
    f.box(slx, 118, slw, 104, "LRU cache", [
        "`token → (qdg, program)",
        "24 entries, in memory",
        "token = sha256(file, profile,",
        "front end, source)",
    ], "box")
    f.badge(slx + slw - 14, 132, 5)
    f.arrow([(qx + qw, 160), (slx, 160)], "ar")
    f.text(qx + qw + 15, 154, "put", "al", "middle")
    f.box(slx, 248, slw, 150, "QuantumSlicer", [
        "`SlicingCriterion(",
        "`  node_idx, dep_types)",
        "backward: in-edges",
        "forward: out-edges",
        "skip edge types not in",
        "dep_types; O(V + E)",
    ], "box box-key")
    f.arrow([(slx + 70, 222), (slx + 70, 248)], "ar")
    f.text(slx + 78, 239, "qdg", "al", "start")

    # ---------------- response ----------------
    f.box(ox, 118, ow, 352, "JSON response", [
        "/api/analyze",
        "`nodes[]: line, span",
        "`edges[]: type, rule",
        "`dependency_types[]",
        "`entanglement",
        "`diagnostics[] + nodes",
        "`token",
        "",
        "/api/slice",
        "`slice, size, total",
        "",
        "~the browser highlights",
        "~the nodes and lines",
    ], "box box-key")
    f.badge(ox + ow - 14, 132, 6)
    f.arrow([(slx + slw, 330), (ox, 330)], "ar")
    f.text(slx + slw + 15, 324, "slice", "al", "middle")
    f.arrow([(qx + qw, 440), (ox, 440)], "ar")
    f.text(slx + slw / 2, 434, "nodes (from the IR), edges", "al", "middle")

    # ---------------- lint path ----------------
    f.box(fx, 684, fw, 104, "LintContext(source, kind, profile)", [
        "program() asks the same front ends for",
        "the IR, lazily and at most once (5 s);",
        "failure() keeps the exception, which",
        "is where QS014 comes from",
    ], "box box-key")
    f.badge(fx + fw - 14, 698, 1)
    f.arrow([(wx + ww, 620), (272, 620), (272, 736), (fx, 736)], "ar")
    f.arrow([(fx + 150, 684), (fx + 150, 618)], "ar")
    f.text(fx + 156, 656, "parse, 5 s", "al", "start")
    f.arrow([(fx + 280, 618), (fx + 280, 684)], "ar-r")
    f.text(fx + 286, 656, "program or failure", "al", "start")

    # the linters, grouped
    gx, gw = MOD[0], SL[0] + SL[1] - MOD[0]
    f.rect(gx, 680, gw, 252, "box box-group box-lint", 8)
    f.text(gx + 12, 698, "Linters", "bh")
    f.text(gx + 70, 698, "registered in linters.py; only the selected rules run, each gets the same context",
           "sub")
    LW = (gw - 24 - 3 * 12) / 4
    lx = [gx + 12 + k * (LW + 12) for k in range(4)]
    ly, lh = 710, 210
    linters = [
        ("quick · QS000–QS016", "on · Python only", [
            "lint.check_source: one",
            "ast.NodeVisitor pass; literal",
            "indices against literal sizes",
            "QS005, QS012, QS013 look",
            "names up in installed Qiskit",
            "+ rejected_operation(failure)",
            "  → QS014 on CircuitError",
        ]),
        ("lintq · LQ01–LQ10", "off by default", [
            "LintQ's ten queries rebuilt on",
            "the syntax tree: scopes, CFG,",
            "reaching definitions, API",
            "paths (mirrors LintQ's CodeQL",
            "library and its limits)",
            "same 190 warnings as the",
            "original on 239 programs",
        ]),
        ("dependence · QD01–QD10", "on · Python and OpenQASM", [
            "reads the rebuilt program;",
            "builds its own QDG",
            "QD01: gates outside every",
            "measurement's backward slice",
            "QD05: qubits still |0⟩",
            "QD10: Pauli eigenstates",
            "findings name node indices",
            "and take lines from locations",
        ]),
        ("lintq-codeql · LQ codes", "off · needs CodeQL", [
            "the original queries:",
            "database init, trace-command,",
            "finalize, analyze",
            "LintQ-all.qls → SARIF",
            "QSLICER_CODEQL,",
            "QSLICER_LINTQ",
            "batch mode for benchmarks",
        ]),
    ]
    for k, (title, sub, lines) in enumerate(linters):
        f.box(lx[k], ly, LW, lh, title, lines, "box", sub=sub)
    f.arrow([(fx + fw, 736), (gx, 736)], "ar")
    # dependence checks reuse the QDG and slicer
    f.arrow([(lx[2] + LW - 30, ly), (lx[2] + LW - 30, 630)], "ar-l")
    f.text(lx[2] + LW - 24, 674, "reuses QDG + backward_slice", "al", "start")
    # SourceLocation feeds the dependence checks
    f.arrow([(rx + 110, 560), (rx + 110, 664), (lx[2] + 40, 664), (lx[2] + 40, ly)], "ar-loc")
    f.text(rx + 118, 658, "line, via", "al loc", "start")

    # results of the lint path
    f.box(ox, 684, ow, 100, "attach_lint_nodes", [
        "named nodes if one",
        "covers the line, else",
        "every node whose span",
        "covers the line(s)",
    ], "box box-key")
    f.badge(ox + ow - 14, 698, 7)
    f.box(ox, 812, ow, 112, "linters.run", [
        "keep selected ids,",
        "add tool and rule;",
        "a crash → one error;",
        "sort by line",
    ], "box", sub="linters.py")
    f.arrow([(gx + gw, 866), (ox, 866)], "ar")
    f.arrow([(ox + 90, 812), (ox + 90, 784)], "ar")
    f.text(ox + 96, 803, "diagnostics", "al", "start")
    f.arrow([(ox + ow - 30, 684), (ox + ow - 30, 470)], "ar")
    f.text(ox + ow - 36, 540, "diagnostics", "al", "end")
    f.text(ox + ow - 36, 553, "+ nodes", "al", "end")
    f.arrow([(ox + 40, 470), (ox + 40, 684)], "ar-loc")
    f.text(ox + 34, 600, "node spans", "al loc", "end")
    f.text(ox + 34, 613, "(SourceLocation)", "al loc", "end")
    # diagnostics back to analyze_source for the QS000 gate
    f.arrow([(ox + 150, 924), (ox + 150, 942), (wx + 120, 942), (wx + 120, 634)], "ar-alt")
    f.badge(wx + 120, 900, 2)
    f.text(1000, 957, "diagnostics back to analyze_source: a QS000 syntax error ends the request "
                      "with these and no graph", "al alt", "middle")

    # ---------------- benchmark band ----------------
    BY, BH = 1006, 92
    widths = [300, 280, 300, 260, 330]
    bxs = [fx]
    for wdt in widths[:-1]:
        bxs.append(bxs[-1] + wdt + 30)
    f.box(bxs[0], BY, widths[0], BH, "Dataset", [
        "`benchmarks/<name>/manifest.json",
        "bugs: key, buggy, fixed",
        "Bugs4Q: 62 Qiskit bugs, Apache-2.0",
    ], "box")
    f.box(bxs[1], BY, widths[1], BH, "linters.run, same selection", [
        "the Linters above, on the buggy",
        "and the fixed program;",
        "CodeQL runs once, in batch",
    ], "box")
    f.box(bxs[2], BY, widths[2], BH, "ground_truth(buggy, fixed)", [
        "difflib on whitespace-stripped lines",
        "fault lines: code lines the fix",
        "deletes or rewrites",
    ], "box")
    f.box(bxs[3], BY, widths[3], BH, "grade_bug", [
        "warned ⊇ hit ⊇ detected",
        "detected: a hit whose rule",
        "fires less often on the fix",
    ], "box box-key")
    f.box(bxs[4], BY, widths[4], BH, "Report → /benchmark page", [
        "per linter, combined, and recorded",
        "runs of other tools (the original",
        "LintQ; SARIF import)",
    ], "box")
    f.arrow([(wx + ww, 1052), (bxs[0], 1052)], "ar")
    for k in range(4):
        f.arrow([(bxs[k] + widths[k], BY + BH / 2), (bxs[k + 1], BY + BH / 2)], "ar")

    return f.svg(W, H, "System overview of the Quantum Slicer. The web layer on the left receives one "
                       "request from the browser. analyze_source runs the linters first on their own "
                       "static parse, stops on a syntax error, then parses the program through one of two "
                       "static front ends into the shared circuit model and the IR, builds the QDG, caches "
                       "it under a token and maps the lint findings onto graph nodes using source "
                       "locations; the response goes back to the browser. A slice request reaches the "
                       "cached QDG through slice_program. The benchmark grades the same linters on buggy "
                       "and fixed programs.")
