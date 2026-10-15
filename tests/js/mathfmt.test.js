// Unit tests for webapp/static/js/mathfmt.js. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const { formatMath, formatText, renderMathIn } =
  require(path.join(__dirname, '..', '..', 'webapp', 'static', 'js', 'mathfmt.js'));

const count = (haystack, needle) => haystack.split(needle).length - 1;

test('exports the three entry points', () => {
  assert.strictEqual(typeof formatMath, 'function');
  assert.strictEqual(typeof formatText, 'function');
  assert.strictEqual(typeof renderMathIn, 'function');
});

test('output is wrapped in a math span', () => {
  assert.strictEqual(formatMath('x'), '<span class="math">x</span>');
  assert.strictEqual(formatMath(null), '<span class="math"></span>');
});

test('ket |0> uses a bar and a right angle bracket', () => {
  const html = formatMath('|0>');
  assert.match(html, /<span class="math-ket">\|<span class="math">0<\/span>\u27e9<\/span>/);
});

test('bra <1| uses a left angle bracket and a bar', () => {
  const html = formatMath('<1|');
  assert.match(html, /<span class="math-ket">\u27e8<span class="math">1<\/span>\|<\/span>/);
});

test('\\frac renders numerator and denominator', () => {
  const html = formatMath(String.raw`\frac{1}{2}`);
  assert.match(html, /class="math-frac"/);
  assert.match(html, /<span class="math-num"><span class="math">1<\/span><\/span>/);
  assert.match(html, /<span class="math-den"><span class="math">2<\/span><\/span>/);
});

test('\\sqrt renders a radical, nested inside \\frac', () => {
  const html = formatMath(String.raw`\frac{1}{\sqrt{2}}`);
  assert.match(html, /<span class="math-sqrt">\u221a<span class="math-radicand"><span class="math">2<\/span>/);
  assert.ok(html.indexOf('math-den') < html.indexOf('math-sqrt'));
});

test('super- and subscripts, with and without braces', () => {
  assert.match(formatMath('x^2'), /x<sup><span class="math">2<\/span><\/sup>/);
  assert.match(formatMath('q_{10}'), /q<sub><span class="math">10<\/span><\/sub>/);
  const both = formatMath('a^2_k');
  assert.ok(both.includes('<sup>') && both.includes('<sub>'));
});

test('Greek letters and symbols', () => {
  assert.ok(formatMath(String.raw`\theta`).includes('\u03b8'));
  assert.ok(formatMath(String.raw`\pi`).includes('\u03c0'));
  assert.ok(formatMath(String.raw`\psi \phi`).includes('\u03c8 \u03c6'));
  assert.ok(formatMath(String.raw`|0> \otimes |1>`).includes('\u2297'));
  assert.ok(formatMath(String.raw`U^{\dagger}`).includes('\u2020'));
});

test('\\i is an italic imaginary unit, not \\in or similar', () => {
  assert.match(formatMath(String.raw`e^{\i\theta}`), /<sup><span class="math"><i>i<\/i>\u03b8<\/span><\/sup>/);
});

test('function names are set upright', () => {
  assert.match(formatMath(String.raw`\cos`), /<span class="math-text">cos<\/span>/);
});

test('\\text keeps its content literal and escaped', () => {
  const html = formatMath(String.raw`\text{if <b>}`);
  assert.match(html, /<span class="math-text">if &lt;b&gt;<\/span>/);
});

test('2x2 matrix has 2 columns and 4 cells', () => {
  const html = formatMath(String.raw`\matrix{0 & 1 \\ 1 & 0}`);
  assert.match(html, /repeat\(2, auto\)/);
  assert.strictEqual(count(html, 'class="math-cell"'), 4);
  assert.strictEqual(count(html, 'math-bracket'), 2 + 2); // two brackets, each with two classes
});

test('4x4 matrix has 4 columns and 16 cells', () => {
  const rows = [
    '1 & 0 & 0 & 0', '0 & 1 & 0 & 0', '0 & 0 & 0 & 1', '0 & 0 & 1 & 0',
  ].join(String.raw` \\ `);
  const html = formatMath(String.raw`\matrix{` + rows + '}');
  assert.match(html, /repeat\(4, auto\)/);
  assert.strictEqual(count(html, 'class="math-cell"'), 16);
});

test('matrix cells are themselves formatted', () => {
  const html = formatMath(String.raw`\matrix{\frac{1}{2} & 0 \\ 0 & \i}`);
  assert.ok(html.includes('math-frac'));
  assert.ok(html.includes('<i>i</i>'));
});

test('unknown commands are shown as written', () => {
  assert.ok(formatMath(String.raw`\foo`).includes('\\foo'));
});

test('escaped characters', () => {
  assert.ok(formatMath(String.raw`\{x\}`).includes('{x}'));
});

test('HTML is escaped: no injection through formatMath', () => {
  const html = formatMath('<script>alert(1)</script>');
  assert.ok(!html.includes('<script'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(formatMath('a" onload="x').includes('a&quot; onload=&quot;x'));
  assert.ok(formatMath('a & b').includes('&amp;'));
});

test('HTML is escaped: no injection through formatText', () => {
  const html = formatText('<img src=x onerror=alert(1)> $<script>$');
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script'));
});

test('formatText formats only the $...$ spans', () => {
  const html = formatText(String.raw`Maps $|0>$ to $\pi$, not \pi`);
  assert.ok(html.startsWith('Maps <span class="math">'));
  assert.strictEqual(count(html, '<span class="math">'), 3); // two spans + the nested ket body
  assert.ok(html.includes('\u03c0'));
  assert.ok(html.endsWith(', not \\pi'));
});

test('formatText of plain prose is just escaped text', () => {
  assert.strictEqual(formatText('a < b & c'), 'a &lt; b &amp; c');
  assert.strictEqual(formatText(undefined), '');
});

test('renderMathIn fills [data-math] nodes once', () => {
  const make = (mode, text) => ({ textContent: text, innerHTML: '', dataset: { math: mode } });
  const expr = make('', '|0>');
  const prose = make('text', 'state $|1>$');
  const root = { querySelectorAll: (sel) => (assert.strictEqual(sel, '[data-math]'), [expr, prose]) };
  renderMathIn(root);
  assert.strictEqual(expr.innerHTML, formatMath('|0>'));
  assert.strictEqual(prose.innerHTML, formatText('state $|1>$'));
  assert.strictEqual(expr.dataset.mathDone, '1');
  expr.innerHTML = 'untouched';
  renderMathIn(root);
  assert.strictEqual(expr.innerHTML, 'untouched');
});
