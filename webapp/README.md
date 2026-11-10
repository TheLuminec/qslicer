# Web front end (mock-up)

A Flask front end over the `qslicer` package. It is a working
mock-up: the pages, the interactions and the API are real, and the graph it
draws comes from the actual parser, QDG and slicer rather than from canned
data. What is *mocked* is called out under [Mocked or partial](#mocked-or-partial)
below.

## Running it

From the repository root:

```bash
pip install -r requirements.txt    # includes Flask
pip install -e .
python webapp/app.py
```

Without the install, `app.py` falls back to the `qslicer/` directory next to
`webapp/`, so a plain checkout still runs.

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
docstring in `qslicer/parsers/staticqiskitparser.py` lists what it models.

The file is read as a script: `if __name__ == "__main__":` blocks and the
`main()` they call are followed, and `argparse` arguments take their defaults,
as in a run with no command-line arguments.

**Values only a run decides.** When the circuit depends on one -- an unseeded
`random` draw, a required command-line argument -- and it came from a
variable, the error is `needs_value` and names it (`error.needs`: `name`,
`line`, `kind`, `expression`). The page asks for a value under the error; the
next request sends it in `bindings`, and the parser uses it where that variable
is assigned. Values are read with `ast.literal_eval`, never run. Picked values
show as chips under the example line; loading another file clears them.

**Several circuits.** Every circuit the program builds is a candidate,
including ones made inside functions. The main one is the module-level circuit
with the most instructions, as before, or, when the file keeps none at module
level, the largest circuit that is not used inside another. The others --
other module-level circuits, then subroutines composed into a bigger one,
their inverses and so on -- are listed in `circuits`, and the Circuit picker
above the graph switches between them (`circuit` in the request). The parse is
cached, so switching does not interpret the file again, and the linters read
the circuit on screen, so their node numbers match the graph.

### OpenQASM 2 and 3 (`.qasm`, `.qasm3`) -- `language="qasm-static"`

`StaticOpenQASMParser` reads the text with the OpenQASM reference parser and
interprets the syntax tree without Qiskit, keeping every instruction's source
line. It covers the language rather than the subset `qiskit.qasm3` can import:
classical variables and arrays, subroutines, `extern`, physical qubits, and
`if`/`while`/`switch` on measured values. A `while` becomes one `while_loop`
node reading the bits it depends on; an `if` or `switch` becomes an `if_else`
or `switch_case` node that reads them, followed by a node per gate of its
branches. A copy
of a measured bit becomes a `store` node, so the dependency survives it. Its
docstring lists the details. Export a circuit from Qiskit with
`qiskit.qasm3.dumps(qc)`.

### Nothing is executed

The site used to offer a route that ran a submitted `.py` file in a worker
process to reach its circuit. That bounded accidents but not attacks: any code
in the file ran on the server. It has been removed. Asking for it by name
(`language="qiskit"`, `"python"` or `"py"`) gets a 400 with
`kind: "execution_disabled"`, so a caller relying on it finds out instead of
being quietly rerouted. `QiskitParser` still exists in `qslicer.parsers` for the
tests, but nothing in `webapp/` calls it on submitted
text.

`language="qasm"` (OpenQASM through `qiskit.qasm3.load()`) is still accepted
from the API. It parses text and runs nothing, but it is not on the page.

## Pages

| Route      | What it is                                                              |
|------------|-------------------------------------------------------------------------|
| `/`        | Landing page: what the tool does, quantum programming in brief           |
| `/slicer`  | The application: source on the left, graph and analysis on the right     |
| `/quantum` | Background on quantum programs, on the QDG, and on the tools we use     |
| `/lint`      | The lint rules: pick them, read each one with an example, lint a program |
| `/benchmark` | Grades the selected lint rules on the Bugs4Q bugs                     |
| `/team`    | Team, course, vision and mission                                         |

## JSON API

| Endpoint                      | Method | Body / params                                       |
|-------------------------------|--------|-----------------------------------------------------|
| `/api/examples`               | GET    | —                                                    |
| `/api/examples/<key>`         | GET    | —                                                    |
| `/api/analyze`                | POST   | `{source, filename, profile, language, lint_rules, bindings, circuit}` |
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

`bindings` is a list of `{name, line, value}` (value as typed, a Python
literal) and `circuit` an index into the `circuits` a response lists; both are
optional and only read by the Qiskit route.

`/api/analyze` returns nodes, edges, lint diagnostics,
`circuits`, `gates` and a `token`. A node names its gate (`name`) and gives its
`category`; the gate's reference card (title, summary, matrix, whether it
entangles, ...) is not repeated on every node but given once per name in
`gates`, as `{name: card}` for the names the nodes use, so look a node's up as
`gates[node.name]`. The token names the QDG cached server-side, so slicing never
re-executes the submitted program. The cache holds the 24 most recent analyses
in memory; after a server restart the client is told to analyze again.

The analysis builds the QDG once: the dependence checks lint the same graph the
page draws and slices (through `LintContext.qdg()`), rather than one of their own.

The finished response is kept with its token's cache entry, so the same request
again -- the same source, filename, profile, interpreter, lint selection (after
normalizing: none means the default), `bindings` and `circuit` -- is answered
with the stored bytes, without linting or describing anything again. A stored
response goes when its token is evicted, so it never hands out a token that no
longer slices; all of them together are also kept under 64 MB. Failed analyses
are not stored. The `X-Analysis-Cache` header says `hit` or `miss`.

JSON API responses of 4 KB or more are gzipped (level 5) when the request's
`Accept-Encoding` allows it, with `Content-Encoding: gzip` and
`Vary: Accept-Encoding`; browsers decode this themselves. A large circuit's
analysis is several megabytes of very repetitive JSON (Shor's example, 12,821
instructions: about 5.8 MB, 0.28 MB gzipped).

## Layout

```
webapp/
├── app.py          Flask routes and the JSON API
├── wsgi.py         entry point for running behind gunicorn
├── analysis.py     bridge to qslicer -- route, parse, build QDG, slice, serialize
├── benchmark.py    grades the selected rules on the Bugs4Q bugs
├── examples.py     reads the example catalog in qslicer/examples/
├── rule_docs.py    what each lint rule checks, for the Lint menu's ? cards
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

The linters (`qslicer/lint/`: the registry, the quick checks, and the
dependence and LintQ add-ons in `plugins/`), the gate reference
(`qslicer/gates.py`) and the example circuits (`qslicer/examples/`) live in the
package, so the command line and the site share them.

## Example circuits

`qslicer/examples/` holds 23 Qiskit circuits and 2 OpenQASM files, described by
`manifest.json` alongside them. They are grouped in the dropdown as Basics,
Algorithms, Communication protocols, Analysis showcases, and OpenQASM.

To add one: drop a `.py` file in the directory and add an entry to the
manifest (`key`, `file`, `category`, `title`, `blurb`, `highlight`). A file
with no manifest entry still appears, under "Unsorted". A `.py` example needs a
module-level `QuantumCircuit`: the tests check every example against the
executing parser, which only sees module-level circuits. A `.qasm` file needs
to be OpenQASM 3.

Every claim in a docstring or blurb was checked against what the analyzer
actually returns — including the two that turned out to be wrong on the first
pass — so if you change a circuit, re-check its text.

For bulk circuits, [QASMBench](https://github.com/pnnl/QASMBench) and
[MQT Bench](https://quantum-journal.org/papers/q-2023-07-20-1062/) publish
large collections, but both ship OpenQASM rather than Python, so they would
come in through the OpenQASM interpreters -- the static one for OpenQASM 2
files, since `qiskit.qasm3.load()` reads only OpenQASM 3.

The circuits under `Attachments/Sample Code` are untouched. The site does not
list them: two are named after
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
  built from the rules that actually ran: the `baseline` profile offers three
  chips, not six, because it never runs the entanglement, phase or
  control-classical rules. Each chip carries the number of edges its rule
  contributed, and a rule that ran and produced nothing shows a dashed chip
  with a zero rather than being hidden.

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
* **The overview strip** under the graph appears when the circuit is wider than
  the view: every instruction as a mark on each wire it touches, the slice and
  marks in their colors, and the part on screen outlined. While **Lint
  markers** is on, a band along its top marks every instruction a finding is
  about, in the worst severity's color, and a wire a finding is about (an idle
  qubit) is tinted red, so findings off screen show too. Click or drag it to
  move there, or scroll over it to pan. The main view stops zooming out at
  0.15, so on a long circuit the strip is the only way to see all of it.

### Large circuits

The page stays responsive on circuits of tens of thousands of instructions
(the Shor example has 12,821 and 24,154 edges):

* **Only what is in view is drawn.** `graph.js` keeps in the DOM just the nodes
  and edges that overlap the view plus half a view on every side. Panning moves
  one transform; when the view leaves that window, what came into it is created
  and what left is removed. At the zoom limit that is a few hundred elements
  rather than the whole graph (about 140,000 for the Shor example).
* **State changes are class changes.** A slice, a mark, a filter or a hover
  updates classes on the elements that exist; nothing is rebuilt. Dragging a
  node redraws only its own edges. Events are handled once, on the node layer.
* **Small labels go when zoomed out.** Below 0.4 the index and line labels are
  hidden, below 0.25 the gate names too (`lod-mid`, `lod-far` on the root).
* **The Instructions tab draws only the rows in view**, between two spacer
  rows, so scrolling, slicing and marking cost the same at any size. Rows are
  one line high; find-in-page only sees the rows drawn.
* **Recent analyses are kept in the tab**, keyed by the whole request (source,
  settings, circuit, picked values) and limited by the nodes and edges they
  hold (150,000, about 60 MB), oldest first. Switching back to a circuit, or
  analyzing code that has not changed, needs no request. If the server has
  meanwhile forgotten that analysis (a restart, or its 24-entry cache moved
  on), the next slice notices, analyzes again and then slices.

## Mocked or partial

Everything here is deliberate, and each item is a place the mock-up stops short
of the real thing:

* **Horizontal (qubit) slices.** Computed in the browser from the instruction
  list. `SlicingCriterion` has a `qubits` field, but `QuantumSlicer` does not
  consume it yet, so there is nothing server-side to call. Also waiting on the
  research; when the slicer grows real vertical and horizontal criteria, the UI
  hooks are already here.
* **Quick checks** (`QS000`–`QS016`, `qslicer/lint/quick_checks.py`): errors Python or Qiskit would
  raise. Most read the Python AST, so they see only literal arguments; QS012
  and QS013 look names and keyword arguments up in the installed Qiskit
  (importing the library's modules, never the checked file), and QS014 reports
  what Qiskit rejects while the static parser rebuilds the circuit.
* **Dependence checks** (`QD01`–`QD03`, `QD05`–`QD10`, `qslicer/lint/plugins/dependence_checks.py`):
  quantum mistakes in the rebuilt circuit (loops unrolled, helpers followed)
  and its dependence graph. Quick and dependence checks are on by default;
  LintQ (the port, and the original through CodeQL where it is installed) is
  an add-on picked from the **Lint** menu.
* **Rule profiles.** The dropdown lists whatever `qdg.list_rule_profiles()`
  returns, which today is just `baseline`. Entanglement, phase and
  control-classical rules exist in `qslicer/dependencies/rules.py` but ship
  disabled, so a profile that enables
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
