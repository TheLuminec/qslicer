// The editor's Tab, Shift+Tab and bracket-wrapping edits (editkeys.js), loaded
// in a vm with a stub window. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(
  path.join(__dirname, '..', '..', 'webapp', 'static', 'js', 'editkeys.js'), 'utf8'), context);
const keys = context.window.EditKeys;

// Apply an edit the way the page does; '|' marks the selection's ends.
function run(fn, marked, ...rest) {
  const start = marked.indexOf('|');
  const end = marked.lastIndexOf('|') - (marked.lastIndexOf('|') > start ? 1 : 0);
  const text = marked.replace(/\|/g, '');
  const change = fn(text, start, end, ...rest);
  if (!change) return null;
  const out = text.slice(0, change.start) + change.text + text.slice(change.end);
  return out.slice(0, change.selStart) + '|' +
    out.slice(change.selStart, change.selEnd) + '|' + out.slice(change.selEnd);
}

test('each opening bracket and quote wraps the selection and keeps it selected', () => {
  assert.strictEqual(run(keys.wrap, 'qc.h|0, 1|', '('), 'qc.h(|0, 1|)');
  assert.strictEqual(run(keys.wrap, 'x = |a|', '['), 'x = [|a|]');
  assert.strictEqual(run(keys.wrap, 'x = |a|', '{'), 'x = {|a|}');
  assert.strictEqual(run(keys.wrap, 'x = |a|', '"'), 'x = "|a|"');
  assert.strictEqual(run(keys.wrap, 'x = |a|', "'"), "x = '|a|'");
  assert.strictEqual(run(keys.wrap, '|a\nb|', '('), '(|a\nb|)');
});

test('nothing is wrapped without a selection or for another key', () => {
  assert.strictEqual(keys.wrap('abc', 1, 1, '('), null);
  assert.strictEqual(keys.wrap('abc', 0, 3, ')'), null);
  assert.strictEqual(keys.wrap('abc', 0, 3, 'a'), null);
  assert.strictEqual(keys.wrap('abc', 0, 3, 'toString'), null);
});

test('Tab indents every line the selection touches', () => {
  assert.strictEqual(run(keys.indent, 'a\n|b\nc|\nd'), 'a\n    |b\n    c|\nd');
  // From the middle of the first line to the middle of the last.
  assert.strictEqual(run(keys.indent, 'ab|c\nde|f'), '    ab|c\n    de|f');
  // A line the selection only reaches the start of stays put; blank lines stay blank.
  assert.strictEqual(run(keys.indent, '|a\n\nb\n|c'), '    |a\n\n    b\n|c');
});

test('Tab at a caret or inside one line pads to the next multiple of four', () => {
  assert.strictEqual(run(keys.indent, '||x'), '    ||x');
  assert.strictEqual(run(keys.indent, 'ab||'), 'ab  ||');
  // A selection inside one line is replaced, as typing would.
  assert.strictEqual(run(keys.indent, 'a\nqc.|h|(0)'), 'a\nqc. ||(0)');
});

test('Shift+Tab takes one level off the selected lines', () => {
  assert.strictEqual(run(keys.outdent, '|    a\n        b\n  c|'), '|a\n    b\nc|');
  assert.strictEqual(run(keys.outdent, '    a||b'), 'a||b');
  assert.strictEqual(run(keys.outdent, '\tx||'), 'x||');
  // The caret inside the indentation ends at the line's start, not before it.
  assert.strictEqual(run(keys.outdent, 'a\n  ||  b'), 'a\n||b');
  assert.strictEqual(keys.outdent('a\nb', 0, 3), null);
});

test('indent then outdent gives the text and selection back', () => {
  const text = 'def f():\n    qc.h(0)\n    qc.cx(0, 1)\n';
  const a = keys.indent(text, 9, text.length);
  const mid = text.slice(0, a.start) + a.text + text.slice(a.end);
  const b = keys.outdent(mid, a.selStart, a.selEnd);
  assert.strictEqual(mid.slice(0, b.start) + b.text + mid.slice(b.end), text);
  assert.deepStrictEqual([b.selStart, b.selEnd], [9, text.length]);
});
