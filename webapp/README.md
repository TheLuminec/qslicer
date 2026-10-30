# Web front end (mock-up)

A Flask front end over the analysis code in `scripts/`. It is a working
mock-up: the pages, the interactions and the API are real, and the graph it
draws comes from the actual parser, QDG and slicer rather than from canned
data. What is *mocked* is called out under [Mocked or partial](#mocked-or-partial)
below.

## Running it

From the repository root:

```bash
pip install -r requirements.txt
pip install -r webapp/requirements.txt
python webapp/app.py
```

Then open <http://127.0.0.1:5000/>.

## How submitted circuits are handled

The page offers two front ends, one per language, and neither executes what
you submit: both read the file and interpret it themselves.

Which front end runs is an explicit choice, not a guess. The page has an
**Interpreter** dropdown next to Example and Rules, and sends it as `language`
on every request. Loading an example or uploading a file sets the dropdown from
the file's extension; nothing else moves it.

If the text and the choice disagree, the error says so -- analyzing OpenQASM as
Qiskit reports a Python syntax error with a hint to switch the dropdown -- but
the switch is yours to make.

Both parsers bound their own work: a step limit, and a wall-clock limit of
`QSLICER_PARSE_TIMEOUT` seconds (default 20).

### Qiskit (`.py`) -- `language="qiskit-static"`

`StaticQiskitParser` walks the syntax tree with a small interpreter of its own
and never runs the file. It keeps every instruction's source line. Anything
only a real run could decide stops it with a `StaticParseError` naming the
line; what it skipped or approximated comes back as `QS200` warnings. Its
docstring in `scripts/Parsers/staticqiskitparser.py` lists what it models.

### OpenQASM 2 and 3 (`.qasm`, `.qasm3`) -- `language="qasm-static"`

`StaticOpenQASMParser` reads the text with the OpenQASM reference parser and
interprets the syntax tree without Qiskit, keeping every instruction's source
line. It covers the language rather than the subset `qiskit.qasm3` can import:
classical variables and arrays, subroutines, `extern`, physical qubits, and
`if`/`while`/`switch` on measured values, each of which becomes one
`if_else`/`while_loop`/`switch_case` node reading the bits it depends on. A copy
of a measured bit becomes a `store` node, so the dependency survives it. Its
docstring lists the details. Export a circuit from Qiskit with
`qiskit.qasm3.dumps(qc)`.

### Nothing is executed

The site used to offer a route that ran a submitted `.py` file in a worker
process to reach its circuit. That bounded accidents but not attacks: any code
in the file ran on the server. It has been removed. Asking for it by name
(`language="qiskit"`, `"python"` or `"py"`) gets a 400 with
`kind: "execution_disabled"`, so a caller relying on it finds out instead of
being quietly rerouted. `QiskitParser` still exists in `scripts/` for the
tests and the desktop tools, but nothing in `webapp/` calls it on submitted
text.

`language="qasm"` (OpenQASM through `qiskit.qasm3.load()`) is still accepted
from the API. It parses text and runs nothing, but it is not on the page.

## Pages

| Route      | What it is                                                              |
|------------|-------------------------------------------------------------------------|
| `/`        | Landing page: what the tool does, quantum programming in brief           |
| `/slicer`  | The application: source on the left, graph and analysis on the right     |
| `/quantum` | Background on quantum programs, on the QDG, and on the tools we use     |
| `/benchmark` | Grades the selected lint rules on the Bugs4Q bugs                     |
| `/team`    | Team, course, vision and mission                                         |

## JSON API

| Endpoint                      | Method | Body / params                                       |
|-------------------------------|--------|-----------------------------------------------------|
| `/api/examples`               | GET    | —                                                    |
| `/api/examples/<key>`         | GET    | —                                                    |
| `/api/analyze`                | POST   | `{source, filename, profile, language, lint_rules}`  |
| `/api/slice`                  | POST   | `{token, node_idx, direction, dep_types}`            |
| `/api/linters`                | GET    | —                                                    |
| `/api/benchmark`              | GET    | —                                                    |
| `/api/benchmark/bugs/<key>`   | GET    | —                                                    |
| `/api/benchmark`              | POST   | `{lint_rules, profile}`                              |

`language` is `"qiskit-static"` or `"qasm-static"` (and, off the page,
`"qasm"`; `"qiskit"` is refused). It is optional for API callers -- without it the
filename extension picks one of the two static routes -- but the page always
sends it. `lint_rules` is a list of rule ids such as
`"quick/QS002"`; leave it out for the default selection, which
`/api/linters` reports.

`/api/analyze` returns nodes, edges, entanglement groups, lint diagnostics and
a `token`. The token names the QDG cached server-side, so slicing never
re-executes the submitted program. The cache holds the 24 most recent analyses
in memory; after a server restart the client is told to analyze again.

## Layout

```
webapp/
├── app.py          Flask routes and the JSON API
├── wsgi.py         entry point for running behind gunicorn
├── analysis.py     bridge to scripts/ -- route, parse, build QDG, slice, serialize
├── linters.py      linter registry: runs the selected rules across every linter
├── lint.py         the quick checks: AST-based static checks
├── linter_plugins/ add-on linters: dependence checks, LintQ (port and CodeQL)
├── benchmark.py    grades the selected rules on the Bugs4Q bugs
├── gates.py        gate reference used by the hover cards and the glossary
├── examples.py     reads the example catalog (module, not the directory)
├── examples/       the example circuits themselves, plus manifest.json
├── team.py         team page content
├── tools_make_hero_figure.py   regenerates the landing page's graph figure
├── templates/      Jinja templates, one per page plus a base layout
└── static/
    ├── css/style.css
    └── js/
        ├── swatches.js  shared dependency colors
        ├── highlight.js the editor's syntax highlighter (Python and OpenQASM)
        ├── mathfmt.js   renders the gate math markup
        ├── graph.js     the SVG graph: pan, zoom, drag, hover, PNG export
        ├── lintrules.js the Lint rule picker shared by Slicer and Benchmark
        ├── slicer.js    page controller
        └── benchmark.js Benchmark page controller
```

No build step and no CDN: the graph is hand-rolled SVG, so the site works
offline.

## Example circuits

`webapp/examples/` holds 22 Qiskit circuits and 2 OpenQASM files, described by
`manifest.json` alongside them. They are grouped in the dropdown as Basics,
Algorithms, Communication protocols, Analysis showcases, and OpenQASM.

To add one: drop a `.py` file in the directory and add an entry to the
manifest (`key`, `file`, `category`, `title`, `blurb`, `highlight`). A file
with no manifest entry still appears, under "Unsorted". A `.py` file needs a
module-level `QuantumCircuit`, since that is what the parser looks for; a
`.qasm` file needs to be OpenQASM 3.

Every claim in a docstring or blurb was checked against what the analyzer
actually returns — including the two that turned out to be wrong on the first
pass — so if you change a circuit, re-check its text.

Do not add an `__init__.py` to `webapp/examples/`: the directory and
`examples.py` share a name, and a package would shadow the module.

For bulk circuits, [QASMBench](https://github.com/pnnl/QASMBench) and
[MQT Bench](https://quantum-journal.org/papers/q-2023-07-20-1062/) publish
large collections, but both ship OpenQASM rather than Python, so they would
come in through the OpenQASM interpreters -- the static one for OpenQASM 2
files, since `qiskit.qasm3.load()` reads only OpenQASM 3.

The circuits under `Attachments/Sample Code` are untouched and still drive the
command-line visualizer. The site no longer lists them: two are named after
the opposite of what they contain (`entanglement.py` has no two-qubit gate,
`independence.py` is a Bell pair) and `test.py` is a pytest module with no
module-level circuit.

## The landing page figure

The graph on the landing page is not a drawing of a circuit: it is the slicer's
own output for `examples/superdense_coding.py`, with a backward slice from the
final measurement. `tools_make_hero_figure.py` runs the real analysis, lays the
nodes out with the same constants as `static/js/graph.js`, and prints the SVG:

```bash
python webapp/tools_make_hero_figure.py > hero.svg
```

The result is pasted inline into the hero `<figure>` in `templates/index.html`,
so the page stays static. Regenerate and paste rather than editing the paths by
hand -- otherwise the illustration and the tool drift apart, and the picture
stops being a promise the app can keep.

## Editor highlighting

The editor is two layers that have to agree to the pixel: a highlighted copy of
the text in `#highlight`, and the real `<textarea>` on top with transparent text
so only its caret and selection show. Every property that decides where a glyph
lands -- font, size, line height, padding, wrapping -- is set once on
`.editor-body` as a custom property and used by both.

`static/js/highlight.js` is the tokenizer: about 180 lines, two languages, no
Prism or highlight.js, for the same reason there is no KaTeX. It knows comments,
strings (including triple-quoted ones spanning lines), numbers, keywords,
builtins, decorators, definitions, and **gate names**, which get their own color
because in a circuit file they are what you are reading for. The gate list is
kept in step with `gates.py` by hand; a name missing from it is simply colored
as an ordinary call.

Which tokenizer runs follows the Interpreter dropdown, and re-coloring happens
immediately when you change it -- coloring is a reading aid, so it does not
wait for Analyze the way the Follow chips do.

The gutter renders one element per line and matches each number's height to its
line, so the numbers stay aligned when a line wraps. Heights are measured as
fractions, not `offsetHeight`: rounding each line drifts the gutter by several
pixels by the bottom of a file.

Two guards keep typing responsive, since each render is one DOM element per line:
files over 20 KB re-color on a pause in typing rather than on every keystroke
(about 2 ms a keystroke in a 2000-line file, against 400 ms without it), and
files over 120 KB are not colored at all.

## Math notation

Gate descriptions and matrices are written in a small LaTeX-shaped markup and
rendered by `static/js/mathfmt.js` — about 200 lines, no KaTeX, no CDN, so the
site still works offline.

- `|0>` and `<0|` -- ket and bra, with proper angle brackets
- `\sqrt{2}` -- radical with the bar drawn over the radicand
- `\frac{1}{2}` -- stacked fraction with a rule between
- `^{2}` and `_{k}` -- super- and subscript
- `\pi`, `\theta`, `\lambda`, ... -- Greek letters; `\i` is the imaginary unit
- `\matrix{a & b \\ c & d}` -- matrix with brackets that stretch to fit
- `\cos`, `\sin`, `\exp`, `\text{...}` -- set upright rather than italic

In prose, wrap math in dollar signs:

```
Maps $|0>$ to $\frac{|0> + |1>}{\sqrt{2}}$.
```

The gate entries in `gates.py` use Python raw strings so the backslashes
survive. In a template, put the markup in `<span data-math>` for an expression
or `<span data-math="text">` for prose; the formatter fills those in on load.

Unknown commands are printed as written rather than swallowed, so a typo is
visible instead of silent.

## The graph view

* **Hover** a node for the gate's name, what it does, its matrix, parameters and
  flags (entangling / phase / self-inverse).
* **Click** a node to slice from it. Backward and forward are a toggle in the
  toolbar.
* **Click a wire label** (`q0`, `q1`, …) or a chip on the Instructions tab to
  highlight everything touching that qubit.
* **Follow** chips choose which dependency kinds the traversal may use. They are
  passed to `SlicingCriterion.dep_types`, so this is the real filter, not a
  display effect. The bar is empty until you analyze something, and is then
  built from the rules that actually ran: the `baseline` profile offers four
  chips, not six, because it never runs the entanglement or phase rules. Each
  chip carries the number of edges its rule contributed, and a rule that ran
  and produced nothing (`control_classical` today) shows a dashed chip with a
  zero rather than being hidden.

  The bar is rebuilt per analysis, not when the Rules dropdown changes: the
  dropdown says what the next run will use, the chips say what the last run
  produced.
* **Range** narrows the drawing to a window of instruction indices, which is how
  you make a large circuit readable.
* **Drag** a node to pull it out of a pile; **Relayout** puts everything back.
* **Zoom, fit and relayout** sit on the canvas itself, top right, the way map
  controls do. **PNG** stays in the toolbar, since it exports rather than
  changes the view, and saves what you are looking at at 2x scale.
* **Drag the divider** between the two panes to give the editor or the graph
  more room. Double-click it to even them up, or move it with the arrow keys
  when it has focus (shift for bigger steps). The position is remembered in
  `localStorage` per browser, and the graph refits itself when the pane
  changes width.

## Mocked or partial

Everything here is deliberate, and each item is a place the mock-up stops short
of the real thing:

* **Horizontal (qubit) slices.** Computed in the browser from the instruction
  list. `SlicingCriterion` has a `qubits` field, but `QuantumSlicer` does not
  consume it yet, so there is nothing server-side to call. Also waiting on the
  research; when the slicer grows real vertical and horizontal criteria, the UI
  hooks are already here.
* **Quick checks** (`QS000`–`QS016`, `lint.py`): errors Python or Qiskit would
  raise. Most read the Python AST, so they see only literal arguments; QS012
  and QS013 look names and keyword arguments up in the installed Qiskit
  (importing the library's modules, never the checked file), and QS014 reports
  what Qiskit rejects while the static parser rebuilds the circuit.
* **Dependence checks** (`QD01`–`QD08`, `linter_plugins/dependence_checks.py`):
  quantum mistakes in the rebuilt circuit (loops unrolled, helpers followed)
  and its dependence graph. Quick and dependence checks are on by default;
  LintQ (the port, and the original through CodeQL where it is installed) is
  an add-on picked from the **Lint** menu.
* **Rule profiles.** The dropdown lists whatever `qdg.list_rule_profiles()`
  returns, which today is just `baseline`. Entanglement and phase rules exist in
  `scripts/Dependencies/rules.py` but ship disabled, so a profile that enables
  them is the thing that makes their Follow chips appear.

  Which dependency type a rule emits is not something the rules declare -- it is
  baked into each `build()` -- so `analysis._RULE_DEPENDENCIES` maps it by rule
  name. A rule missing from that map is still picked up from the edges it
  emitted, so a new rule appears either way; add a line only for a rule that can
  legitimately produce nothing.
* **No persistence.** Nothing is written to disk and nothing is shared between
  browser sessions.
* **Console output.** Nothing is run, so a submitted program never prints
  anything; the Console tab shows the last error.
* **Static checks on OpenQASM.** Only the dependence checks run -- the quick
  checks and LintQ read a Python AST. When quick checks are selected, a `.qasm`
  analysis reports one informational note (`QS100`) saying they were skipped.
  Errors in the QASM itself come from the parser.

## Known limits

* A parse stops after `QSLICER_PARSE_TIMEOUT` seconds (default 20). Loops are
  unrolled, so a program whose unrolled size grows exponentially (deeply nested
  loops, say) reaches that before anything else.
* Very large circuits (several hundred instructions) draw slowly, because every
  node and edge is a live DOM element. The Range control is the workaround.
* Library circuits the Python interpreter models (`QFT`, `decompose()`) arrive
  as Qiskit builds them, so gate names may not match the source.
