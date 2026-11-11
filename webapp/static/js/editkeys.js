/* ==========================================================================
   Editing keys for the source editor: Tab and Shift+Tab indent or outdent
   every selected line, and an opening bracket or quote typed over a selection
   wraps it.

   These only work out the edit: {start, end, text, selStart, selEnd} says
   which range of the text to replace with what, and what to select after.
   slicer.js applies it, so the page's undo history stays the browser's own.
   ========================================================================== */

(function () {
  'use strict';

  var INDENT = '    ';
  var PAIRS = { '(': ')', '[': ']', '{': '}', '"': '"', "'": "'" };

  /* The selection [start, end) wrapped in the pair `open` begins, with the
     same text still selected inside it. Null for a key that opens no pair or
     an empty selection: the key is then typed as usual. */
  function wrap(text, start, end, open) {
    if (start === end || !Object.prototype.hasOwnProperty.call(PAIRS, open)) return null;
    return {
      start: start, end: end,
      text: open + text.slice(start, end) + PAIRS[open],
      selStart: start + 1, selEnd: end + 1
    };
  }

  /* Tab. With a selection that reaches a second line, every line it touches
     moves one level in; otherwise spaces go in at the caret, up to the next
     multiple of four columns. A line the selection only reaches the start of
     is not part of it, and blank lines are left blank. */
  function indent(text, start, end) {
    if (text.slice(start, end).indexOf('\n') === -1) {
      var column = start - lineStart(text, start);
      var pad = INDENT.slice(column % INDENT.length);
      return { start: start, end: end, text: pad, selStart: start + pad.length, selEnd: start + pad.length };
    }
    return shiftLines(text, start, end, function (line) {
      return line.trim() ? INDENT + line : line;
    });
  }

  /* Shift+Tab: the lines the selection touches (or the caret's line) lose up
     to one level of leading spaces, or one leading tab. */
  function outdent(text, start, end) {
    return shiftLines(text, start, end, function (line) {
      if (line.charAt(0) === '\t') return line.slice(1);
      var spaces = line.length - line.replace(/^ +/, '').length;
      return line.slice(Math.min(spaces, INDENT.length));
    });
  }

  function lineStart(text, at) { return text.lastIndexOf('\n', at - 1) + 1; }

  function shiftLines(text, start, end, change) {
    var from = lineStart(text, start);
    var last = end > start && text.charAt(end - 1) === '\n' ? end - 1 : end;
    var to = text.indexOf('\n', last);
    if (to === -1) to = text.length;
    var lines = text.slice(from, to).split('\n');
    var changed = lines.map(change);
    var first = changed[0].length - lines[0].length;
    var total = changed.join('\n').length - (to - from);
    if (!total) return null;
    // The ends of the selection move with their lines' text, never past the
    // start of a line that lost its indentation.
    var selStart = Math.max(from, start + first);
    var lastLine = changed[changed.length - 1];
    var newLastStart = to + total - lastLine.length;
    var selEnd = end > last ? end + total : Math.max(newLastStart, end + total);
    return { start: from, end: to, text: changed.join('\n'), selStart: selStart, selEnd: Math.max(selStart, selEnd) };
  }

  window.EditKeys = { wrap: wrap, indent: indent, outdent: outdent, PAIRS: PAIRS };
})();
