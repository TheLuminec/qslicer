// Unit tests for webapp/static/js/highlight.js. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', '..', 'webapp', 'static', 'js', 'highlight.js'), 'utf8');

function load() {
  const window = {};
  vm.runInNewContext(SOURCE, { window });
  return window;
}

const { highlightCode: rawHighlight, HIGHLIGHT_GATES } = load();
// Arrays built inside the vm context have a foreign prototype; copy them out.
const highlightCode = (...args) => Array.from(rawHighlight(...args));
const span = (cls, text) => `<span class="${cls}">${text}</span>`;

test('installs highlightCode and the gate table on window', () => {
  assert.strictEqual(typeof highlightCode, 'function');
  assert.ok(HIGHLIGHT_GATES.h && HIGHLIGHT_GATES.cx && HIGHLIGHT_GATES.measure);
});

test('returns one string per line', () => {
  assert.deepStrictEqual(highlightCode('a\nb\n\nc').length, 4);
  assert.deepStrictEqual(highlightCode(''), ['']);
  assert.deepStrictEqual(highlightCode(null), ['']);
});

test('python keywords', () => {
  const [line] = highlightCode('from qiskit import QuantumCircuit');
  assert.strictEqual(line,
    span('tok-keyword', 'from') + ' qiskit ' + span('tok-keyword', 'import') + ' QuantumCircuit');
});

test('python def names, constants and builtins', () => {
  const [line] = highlightCode('def build(n=None): return len(n)');
  assert.ok(line.includes(span('tok-def', 'build')));
  assert.ok(line.includes(span('tok-constant', 'None')));
  assert.ok(line.includes(span('tok-builtin', 'len')));
});

test('python strings and comments', () => {
  const [line] = highlightCode("x = 'hi'  # note");
  assert.ok(line.includes(span('tok-string', "'hi'")));
  assert.ok(line.endsWith(span('tok-comment', '# note')));
});

test('a # inside a string is not a comment', () => {
  const [line] = highlightCode('s = "a # b"');
  assert.ok(line.includes(span('tok-string', '"a # b"')));
  assert.ok(!line.includes('tok-comment'));
});

test('gate method calls get tok-gate, other calls tok-call', () => {
  const [line] = highlightCode('qc.cx(0, 1); qc.draw()');
  assert.ok(line.includes('qc.' + span('tok-gate', 'cx')));
  assert.ok(line.includes('qc.' + span('tok-call', 'draw')));
  assert.ok(line.includes(span('tok-number', '0')));
});

test('triple-quoted strings are split across lines', () => {
  const lines = highlightCode('"""one\ntwo"""\nfoo');
  assert.deepStrictEqual(lines.slice(0, 2), [
    span('tok-string', '"""one'),
    span('tok-string', 'two"""'),
  ]);
  assert.strictEqual(lines[2], 'foo');
});

test('HTML in code is escaped', () => {
  const lines = highlightCode('# <script>alert(1)</script>\ny = "a<b&c"');
  assert.ok(!lines.join('').includes('<script'));
  assert.ok(lines[0].includes('&lt;script&gt;'));
  assert.ok(lines[1].includes('a&lt;b&amp;c'));
});

test('qasm mode: keywords, gates, numbers, constants', () => {
  const lines = highlightCode('OPENQASM 3.0;\nqubit[2] q;\nrz(pi/2) q[0];', 'qasm');
  assert.ok(lines[0].startsWith(span('tok-keyword', 'OPENQASM')));
  assert.ok(lines[0].includes(span('tok-number', '3.0')));
  assert.ok(lines[1].startsWith(span('tok-keyword', 'qubit')));
  assert.ok(lines[2].startsWith(span('tok-gate', 'rz')));
  assert.ok(lines[2].includes(span('tok-constant', 'pi')));
});

test('qasm mode: line and block comments', () => {
  const lines = highlightCode('h q[0]; // hi\n/* a\nb */ cx q[0], q[1];', 'qasm');
  assert.ok(lines[0].endsWith(span('tok-comment', '// hi')));
  assert.strictEqual(lines[1], span('tok-comment', '/* a'));
  assert.ok(lines[2].startsWith(span('tok-comment', 'b */')));
  assert.ok(lines[2].includes(span('tok-gate', 'cx')));
});

test('qasm mode: gate definitions name a tok-def', () => {
  const [line] = highlightCode('gate foo a { x a; }', 'qasm');
  assert.ok(line.startsWith(span('tok-keyword', 'gate') + ' ' + span('tok-def', 'foo')));
});

test('python comment syntax is not a comment in qasm and vice versa', () => {
  assert.ok(!highlightCode('# x', 'qasm')[0].includes('tok-comment'));
  assert.ok(!highlightCode('// x')[0].includes('tok-comment'));
});

test('highlighting never changes the text, only wraps it', () => {
  const code = 'from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)  # x\n';
  const strip = (html) => html.replace(/<[^>]+>/g, '');
  assert.strictEqual(highlightCode(code).map(strip).join('\n'), code);
});
