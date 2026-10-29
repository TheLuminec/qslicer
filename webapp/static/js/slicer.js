/* ==========================================================================
   Slicer page controller: editor, analysis request, and everything that hangs
   off the resulting graph payload.
   ========================================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    payload: null,      // last successful /api/analyze response
    token: null,
    // Kept here rather than in a field on the page: it only matters for error
    // messages and the PNG filename. The Interpreter dropdown decides the
    // front end, not this.
    filename: '',
    direction: 'backward',
    criterion: null,
    selectedQubit: null,
    activeDeps: null,   // Set of dependency names
    busy: false,
    sliceSeq: 0,       // guards against an older slice response landing last
    // FR-07: which source lines are linked to the graph right now. Cleared
    // whenever the text changes, since the payload no longer describes it.
    source: { linked: false, hover: [], slice: [], criterion: [], picked: null }
  };

  var graph = null;
  var tooltip = null;
  var lintPicker = null;   // the Lint dropdown, once /api/linters has answered
  var linterLabels = {};   // linter id -> label, for the badge on each diagnostic

  document.addEventListener('DOMContentLoaded', function () {
    tooltip = $('tooltip');
    graph = new window.QGraph($('graph'), {
      onNodePick: pickCriterion,
      onQubitPick: pickQubit,
      onHover: function (node, event, html) {
        showTooltip(node, event, html);
        hoverSource(node);
      },
      onHoverMove: moveTooltip,
      onHoverEnd: function () {
        hideTooltip();
        hoverSource(null);
      }
    });

    wireEditor();
    wireSplitter();
    wireDiagnosticsSplitter();
    wireToolbar();
    wireTabs();
    wireLintPicker();
    updateGutter();
    loadFromQuery();

    window.addEventListener('resize', debounce(function () {
      if (state.payload) graph.fit(window.QGraph.READABLE_SCALE);
      reclampDiagnostics();
      updateGutter();
    }, 180));
  });

  /* ---------------------------------------------------------- editor ---- */

  function wireEditor() {
    var code = $('code');

    code.addEventListener('input', function () {
      unlinkSource();
      scheduleEditorRender();
      // What is in the editor is no longer the example or file that was loaded,
      // so it goes back to being a pasted circuit. The interpreter stays as it
      // is -- switching that is the user's call.
      $('example-select').value = '';
      state.filename = '';
    });
    code.addEventListener('scroll', syncEditorScroll);
    $('gutter').addEventListener('click', function (event) {
      var row = event.target.closest('.gutter-line');
      if (!row) return;
      var line = Array.prototype.indexOf.call($('gutter').children, row) + 1;
      if (line > 0) pickLine(line);
    });
    code.addEventListener('keydown', function (event) {
      if (event.key === 'Tab') {
        event.preventDefault();
        insertAtCursor(code, '    ');
        scheduleEditorRender();
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        analyze();
      }
    });

    $('btn-analyze').addEventListener('click', analyze);

    $('btn-clear').addEventListener('click', function () {
      code.value = '';
      state.filename = '';
      $('example-select').value = '';
      $('example-blurb').textContent = '';
      updateGutter();
      resetOutput();
    });

    $('file-input').addEventListener('change', function (event) {
      var file = event.target.files && event.target.files[0];
      if (file) readFile(file);
      event.target.value = '';
    });

    $('interpreter-select').addEventListener('change', function () {
      // Nothing re-runs on its own: say what will be used next time, and let
      // the user press Analyze. The coloring does follow immediately, since
      // it is only a reading aid.
      updateGutter();
      announceInterpreter(false);
    });

    $('example-select').addEventListener('change', function (event) {
      var key = event.target.value;
      if (!key) { $('example-blurb').textContent = ''; return; }
      loadExample(key);
    });

    var shell = $('editor-shell');
    var overlay = $('drop-overlay');
    var depth = 0;

    ['dragenter', 'dragover'].forEach(function (name) {
      shell.addEventListener(name, function (event) {
        event.preventDefault();
        if (name === 'dragenter') depth++;
        overlay.hidden = false;
      });
    });
    shell.addEventListener('dragleave', function () {
      depth = Math.max(0, depth - 1);
      if (depth === 0) overlay.hidden = true;
    });
    shell.addEventListener('drop', function (event) {
      event.preventDefault();
      depth = 0;
      overlay.hidden = true;
      var file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) readFile(file);
    });
  }

  function readFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      $('code').value = reader.result;
      state.filename = file.name;
      setInterpreter(interpreterForFilename(file.name));
      $('example-select').value = '';
      $('example-blurb').textContent = 'Loaded from ' + file.name + '.';
      updateGutter();
      analyze();
    };
    reader.onerror = function () {
      setStatus('Could not read ' + file.name + '.');
    };
    reader.readAsText(file);
  }

  function loadExample(key) {
    fetch('/api/examples/' + encodeURIComponent(key))
      .then(function (response) {
        if (!response.ok) throw new Error('Example not found.');
        return response.json();
      })
      .then(function (data) {
        $('code').value = data.source;
        state.filename = data.filename;
        setInterpreter(data.interpreter || interpreterForFilename(data.filename));
        var blurb = data.blurb || '';
        $('example-blurb').innerHTML = escapeHtml(blurb) +
          (data.highlight ? ' <span class="highlight">' + escapeHtml(data.highlight) + '</span>' : '');
        updateGutter();
        analyze();
      })
      .catch(function (error) {
        setStatus(error.message);
      });
  }

  /* A benchmark program, when the Benchmark page linked here with
     ?dataset=<id>&bug=<key>&version=buggy|fixed. */
  function loadFromQuery() {
    var params = new URLSearchParams(window.location.search);
    var key = params.get('bug');
    if (!key) return;
    var dataset = params.get('dataset') || '';
    var version = params.get('version') === 'fixed' ? 'fixed' : 'buggy';
    fetch('/api/benchmark/bugs/' + encodeURIComponent(key) + '?dataset=' + encodeURIComponent(dataset))
      .then(function (response) {
        if (!response.ok) throw new Error('No benchmark bug named ' + key + '.');
        return response.json();
      })
      .then(function (bug) {
        $('code').value = bug.sources[version];
        state.filename = bug[version].split('/').pop();
        setInterpreter(interpreterForFilename(state.filename));
        var other = version === 'buggy' ? 'fixed' : 'buggy';
        var faults = bug.truth.lines.length
          ? ' Fault lines (from the fix): ' + bug.truth.lines.join(', ') + '.' : '';
        var query = '?dataset=' + encodeURIComponent(dataset);
        $('example-blurb').innerHTML = escapeHtml(bug.key) +
          (bug.source || bug.reference ? ' (' + escapeHtml([bug.source, bug.reference].filter(Boolean).join(' ')) + ')' : '') +
          ', ' + version + ' program' +
          (version === 'buggy' ? escapeHtml(faults) : '') + ' <a href="' + query + '&bug=' +
          encodeURIComponent(key) + '&version=' + other + '">Open the ' + other + ' program</a> · ' +
          '<a href="/benchmark' + query + '#bug-' + encodeURIComponent(key) + '">Back to the benchmark</a>';
        updateGutter();
        analyze();
      })
      .catch(function (error) { setStatus(error.message); });
  }

  /* The Interpreter dropdown is the single source of truth for the front end.
     Uploads and examples set it; nothing else changes it behind the user. */
  function interpreter() {
    return $('interpreter-select').value || 'qiskit-static';
  }

  function setInterpreter(value) {
    var select = $('interpreter-select');
    if (value && select.value !== value) {
      select.value = value;
      announceInterpreter(true);
    }
  }

  function interpreterForFilename(name) {
    return /\.qasm3?$/i.test(name || '') ? 'qasm-static' : 'qiskit-static';
  }

  function isQasm(value) {
    return String(value || '').indexOf('qasm') === 0;
  }

  function defaultFilename() {
    return isQasm(interpreter()) ? 'pasted_circuit.qasm' : 'pasted_circuit.py';
  }

  function announceInterpreter(switched) {
    var label = $('interpreter-select').selectedOptions[0].textContent;
    setSide((switched ? 'Interpreter switched to ' : 'Interpreter: ') + label);
  }

  function insertAtCursor(field, text) {
    var start = field.selectionStart;
    var end = field.selectionEnd;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    field.selectionStart = field.selectionEnd = start + text.length;
  }

  /* Rebuild the highlighted copy of the source and the line numbers.
     ``errorLine`` marks one gutter number red, for a reported syntax error. */
  var HIGHLIGHT_LIMIT = 120000;   // characters; past this, skip the tokenizer
  var LINE_LIMIT = 800;           // lines; past this, skip the per-line sync
  var INSTANT_LIMIT = 20000;      // characters; past this, re-render on a pause

  /* Re-rendering is one DOM element per line, and laying out a few thousand of
     those costs more than a keystroke should. Small files -- every example
     here is one -- redraw immediately; a large one redraws once the typing
     stops. The text itself never lags, because that is the real textarea. */
  var renderTimer = null;

  function scheduleEditorRender(errorLine) {
    if (renderTimer) {
      clearTimeout(renderTimer);
      renderTimer = null;
    }
    if ($('code').value.length <= INSTANT_LIMIT) {
      updateGutter(errorLine);
      return;
    }
    // Keep the line count honest even while the coloring waits.
    var lines = $('code').value.split('\n').length;
    $('editor-meta').textContent = lines + (lines === 1 ? ' line' : ' lines');
    renderTimer = setTimeout(function () {
      renderTimer = null;
      updateGutter(errorLine);
    }, 90);
  }

  var lastLineCount = -1;         // so the gutter is only rebuilt when it changes
  var lastErrorLine = 0;

  function updateGutter(errorLine) {
    var code = $('code');
    var highlight = $('highlight');
    var text = code.value;
    var sourceLines = text.split('\n');

    // Wrapping has to match the textarea exactly or the two layers drift, and
    // the textarea's own scrollbar changes how much room the text has.
    highlight.style.width = code.clientWidth + 'px';

    var rendered;
    if (text.length > HIGHLIGHT_LIMIT || !window.highlightCode) {
      rendered = sourceLines.map(escapeHtml);       // too big to color; still readable
    } else {
      rendered = window.highlightCode(text, highlightLanguage());
    }

    highlight.innerHTML = rendered
      .map(function (line) { return '<div class="hl-line">' + line + '</div>'; })
      .join('');

    renderGutter(sourceLines.length, errorLine || 0);
    applySourceMarks();
    syncGutterHeights(sourceLines.length);
    syncEditorScroll();
    $('editor-meta').textContent =
      sourceLines.length + (sourceLines.length === 1 ? ' line' : ' lines');
  }

  /* Typing inside a line does not change the numbers, and rebuilding a few
     thousand of them on every keystroke is most of what made this slow. */
  function renderGutter(count, errorLine) {
    var gutter = $('gutter');
    if (count !== lastLineCount) {
      var markup = '';
      for (var i = 1; i <= count; i++) {
        markup += '<div class="gutter-line">' + i + '</div>';
      }
      gutter.innerHTML = markup;
      lastLineCount = count;
      lastErrorLine = 0;
    }
    if (errorLine !== lastErrorLine) {
      if (lastErrorLine && gutter.children[lastErrorLine - 1]) {
        gutter.children[lastErrorLine - 1].classList.remove('err-line');
      }
      if (errorLine && gutter.children[errorLine - 1]) {
        gutter.children[errorLine - 1].classList.add('err-line');
      }
      lastErrorLine = errorLine;
    }
  }

  /* A line that wraps is taller than one line, so its number has to be too --
     otherwise the gutter drifts out of step further down the file.

     Measuring every line is expensive, so first ask one question: does the
     whole block come to exactly one line-height per line? If it does, nothing
     wrapped and there is nothing to do. */
  function syncGutterHeights(count) {
    var highlightLines = $('highlight').children;
    var gutterLines = $('gutter').children;
    if (!highlightLines.length) return;

    var first = highlightLines[0].getBoundingClientRect().height;
    var total = $('highlight').getBoundingClientRect().height;
    var padding = 2 * parseFloat(getComputedStyle($('highlight')).paddingTop || 0);
    var nothingWrapped = Math.abs((total - padding) - first * count) < 1;

    if (nothingWrapped || count > LINE_LIMIT) {
      if (gutterLines.length && gutterLines[0].style.height) {
        for (var k = 0; k < gutterLines.length; k++) gutterLines[k].style.height = '';
      }
      return;
    }

    // Fractional heights, not offsetHeight: a line box is rarely a whole
    // number of pixels, and rounding each one drifts the gutter further out of
    // step with every line down the file.
    var heights = [];
    for (var i = 0; i < highlightLines.length; i++) {
      heights.push(highlightLines[i].getBoundingClientRect().height);
    }
    for (var j = 0; j < gutterLines.length; j++) {
      gutterLines[j].style.height = heights[j] ? heights[j].toFixed(3) + 'px' : '';
    }
  }

  function syncEditorScroll() {
    var code = $('code');
    $('highlight').style.transform =
      'translate(' + -code.scrollLeft + 'px, ' + -code.scrollTop + 'px)';
    $('gutter').scrollTop = code.scrollTop;
  }

  /* The Interpreter dropdown decides which tokenizer runs. */
  function highlightLanguage() {
    return isQasm(interpreter()) ? 'qasm' : 'python';
  }

  /* --------------------------------------------------------- toolbar ---- */

  function wireToolbar() {
    $('dir-backward').addEventListener('click', function () { setDirection('backward'); });
    $('dir-forward').addEventListener('click', function () { setDirection('forward'); });

    $('range-from').addEventListener('change', applyRange);
    $('range-to').addEventListener('change', applyRange);
    $('range-reset').addEventListener('click', function () {
      if (!state.payload) return;
      $('range-from').value = 0;
      $('range-to').value = state.payload.nodes.length - 1;
      applyRange();
    });

    $('zoom-in').addEventListener('click', function () { graph.zoomBy(1.25); });
    $('zoom-out').addEventListener('click', function () { graph.zoomBy(0.8); });
    $('zoom-fit').addEventListener('click', function () { graph.fit(); });
    $('layout-reset').addEventListener('click', function () { graph.resetLayout(); });
    $('download-png').addEventListener('click', downloadPng);
  }

  function setDirection(direction) {
    state.direction = direction;
    $('dir-backward').classList.toggle('active', direction === 'backward');
    $('dir-forward').classList.toggle('active', direction === 'forward');
    if (state.criterion !== null) requestSlice(state.criterion);
  }

  function applyRange() {
    if (!state.payload) return;
    var last = state.payload.nodes.length - 1;
    var from = clamp(parseInt($('range-from').value, 10) || 0, 0, last);
    var to = clamp(parseInt($('range-to').value, 10) || 0, 0, last);
    if (to < from) { var swap = from; from = to; to = swap; }
    $('range-from').value = from;
    $('range-to').value = to;
    graph.setRange(from, to);
    graph.fit(window.QGraph.READABLE_SCALE);
    setSide(from === 0 && to === last
      ? ''
      : 'showing instructions ' + from + '–' + to + ' of ' + last);
  }

  /* The chips are whatever the rules that ran can emit -- nothing else. They
     are rebuilt on every analysis, and deliberately not when the Rules
     dropdown changes: the dropdown says what the next run will use, the chips
     say what the last run produced. */
  function renderDepFilter(data) {
    var container = $('dep-filter');
    container.textContent = '';

    var label = document.createElement('span');
    label.className = 'tool-label';
    label.textContent = 'Follow';
    container.appendChild(label);

    var offered = data && data.dependency_types ? data.dependency_types : [];
    if (!offered.length) {
      var placeholder = document.createElement('span');
      placeholder.className = 'placeholder';
      placeholder.textContent = data
        ? 'this rule set emits no dependency types'
        : 'the dependency types the rules produce appear here';
      container.appendChild(placeholder);
      return;
    }

    offered.forEach(function (dep) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip chip-dep active' + (dep.count ? '' : ' chip-empty');
      chip.dataset.dep = dep.name;
      chip.title = dep.description +
        '\n\nRule: ' + (dep.rules || []).join(', ') +
        '\n' + dep.count + ' edge' + (dep.count === 1 ? '' : 's') + ' in this circuit';

      var swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = window.depColor(dep.name);
      chip.appendChild(swatch);

      chip.appendChild(document.createTextNode(
        dep.name.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, function (c) {
          return c.toUpperCase();
        })
      ));

      var count = document.createElement('span');
      count.className = 'chip-count';
      count.textContent = dep.count;
      chip.appendChild(count);

      chip.addEventListener('click', function () {
        chip.classList.toggle('active');
        syncDepFilter();
      });
      container.appendChild(chip);
    });
  }

  function syncDepFilter() {
    var active = [];
    document.querySelectorAll('.chip-dep.active').forEach(function (on) {
      active.push(on.dataset.dep);
    });
    var all = document.querySelectorAll('.chip-dep').length;
    // All of them selected means "follow everything", which the slicer takes
    // as dep_types: null.
    state.activeDeps = (active.length === all) ? null : active;
    graph.setActiveDeps(state.activeDeps);
    if (state.criterion !== null) requestSlice(state.criterion);
  }

  function wireTabs() {
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        document.querySelectorAll('.tab').forEach(function (other) {
          var on = other === tab;
          other.classList.toggle('active', on);
          other.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('.tab-panel').forEach(function (panel) {
          panel.classList.toggle('active', panel.dataset.panel === tab.dataset.tab);
        });
        if (tab.dataset.tab === 'graph' && state.payload) graph.fit(window.QGraph.READABLE_SCALE);
      });
    });
  }

  /* ------------------------------------------------------ lint rules --- */

  function wireLintPicker() {
    if (!window.LintRules) return;
    window.LintRules.load()
      .then(function (catalog) {
        catalog.linters.forEach(function (linter) { linterLabels[linter.id] = linter.label; });
        lintPicker = window.LintRules.mountPicker($('lint-picker'), catalog, function () {
          $('lint-summary').textContent = lintPicker.summary();
        });
        $('lint-summary').textContent = lintPicker.summary();
      })
      .catch(function () {
        $('lint-picker').innerHTML = '<p class="placeholder">The linter list did not load; ' +
          'the default rules run.</p>';
      });
    // Close the dropdown on an outside click, as a select would. The click's
    // path, not its target: a "?" re-renders the picker while the click is
    // still bubbling, so by now its target may no longer be in the page.
    document.addEventListener('click', function (event) {
      var dropdown = $('lint-dropdown');
      var inside = event.composedPath ? event.composedPath().indexOf(dropdown) !== -1
        : dropdown.contains(event.target);
      if (dropdown.open && !inside) dropdown.open = false;
    });
  }

  /* -------------------------------------------------------- analysis ---- */

  function analyze() {
    if (state.busy) return;
    var source = $('code').value;
    if (!source.trim()) {
      setStatus('Nothing to analyze yet.');
      return;
    }

    state.busy = true;
    var button = $('btn-analyze');
    button.disabled = true;
    button.textContent = 'Analyzing…';
    setStatus('Running the parser and building the graph…');

    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: source,
        filename: state.filename || defaultFilename(),
        profile: $('profile-select').value || 'baseline',
        language: interpreter(),
        lint_rules: lintPicker ? lintPicker.selection()
          : (window.LintRules ? window.LintRules.stored() : null)
      })
    })
      .then(function (response) { return response.json(); })
      .then(handleAnalysis)
      .catch(function (error) {
        renderDiagnostics([], {
          kind: 'network_error',
          message: 'Could not reach the analysis endpoint.',
          detail: String(error),
          hint: 'Is the Flask server still running?'
        });
        setStatus('Request failed.');
      })
      .finally(function () {
        state.busy = false;
        button.disabled = false;
        button.textContent = 'Analyze';
      });
  }

  function handleAnalysis(data) {
    renderDiagnostics(data.diagnostics || [], data.error);
    $('console-out').textContent = data.stdout ? data.stdout : '(no output)';
    $('console-error').textContent = data.error
      ? formatErrorForConsole(data.error)
      : '(none)';

    resetSourceLinks(data.ok && data.nodes.some(function (node) { return node.line; }));

    if (!data.ok) {
      state.payload = null;
      state.token = null;
      state.criterion = null;
      graph.clear();
      $('graph-empty').hidden = false;
      renderInstructions(null);
      renderEntanglement(null);
      renderQubitFilter(null);
      renderDepFilter(null);
      updateGutter(data.error && data.error.line);
      setStatus(data.error ? data.error.message : 'Analysis failed.');
      return;
    }

    state.payload = data;
    state.token = data.token;
    state.criterion = null;
    state.selectedQubit = null;
    // A new rule set may offer different dependency types, so the filter goes
    // back to following everything it now has.
    state.activeDeps = null;
    renderDepFilter(data);
    updateGutter();

    $('graph-empty').hidden = true;
    graph.setData(data);
    graph.setActiveDeps(state.activeDeps);

    $('range-from').value = 0;
    $('range-to').value = data.nodes.length - 1;
    $('range-from').max = data.nodes.length - 1;
    $('range-to').max = data.nodes.length - 1;

    renderInstructions(data);
    renderEntanglement(data);
    renderQubitFilter(data);

    var program = data.program;
    setStatus(program.num_instructions + ' instructions, ' + program.num_qubits + ' qubits, ' +
      program.num_clbits + ' classical bits, ' + data.edges.length + ' dependency edges. ' +
      'Click a gate to slice from it.');
    setSide((data.route ? data.route + ' · ' : '') + 'rules: ' + data.rules.join(', '));
  }

  function formatErrorForConsole(error) {
    var lines = [error.message];
    if (error.detail) lines.push('', error.detail);
    if (error.traceback && error.traceback.length) lines.push('', error.traceback.join('\n'));
    if (error.hint) lines.push('', 'Hint: ' + error.hint);
    return lines.join('\n');
  }

  /* ------------------------------------------------------ diagnostics --- */

  function renderDiagnostics(diagnostics, error) {
    var list = $('diagnostics-list');
    var summary = $('diagnostics-summary');
    list.textContent = '';

    var items = [];
    if (error) {
      items.push({
        code: 'RUN',
        severity: 'error',
        line: error.line || 0,
        column: 0,
        message: error.message,
        hint: error.hint || '',
        detail: error.detail || '',
        traceback: error.traceback || null
      });
    }
    items = items.concat(diagnostics);

    if (!items.length) {
      var clean = document.createElement('li');
      clean.className = 'placeholder';
      clean.textContent = 'No problems found by the selected lint rules.';
      list.appendChild(clean);
      summary.textContent = 'clean';
      summary.className = 'diagnostics-summary clean';
      return;
    }

    var errors = items.filter(function (d) { return d.severity === 'error'; }).length;
    var warnings = items.filter(function (d) { return d.severity === 'warning'; }).length;
    summary.textContent = [
      errors ? errors + ' error' + (errors === 1 ? '' : 's') : '',
      warnings ? warnings + ' warning' + (warnings === 1 ? '' : 's') : '',
      (items.length - errors - warnings) ? (items.length - errors - warnings) + ' note(s)' : ''
    ].filter(Boolean).join(', ');
    summary.className = 'diagnostics-summary ' +
      (errors ? 'has-errors' : (warnings ? 'has-warnings' : ''));

    items.forEach(function (item) {
      var li = document.createElement('li');
      li.className = 'diag sev-' + item.severity;

      var head = document.createElement('div');
      head.className = 'diag-head';
      head.innerHTML =
        (item.tool ? '<span class="diag-tool">' + escapeHtml(linterLabels[item.tool] || item.tool) +
          '</span>' : '') +
        '<span class="diag-code">' + escapeHtml(item.code) + '</span>' +
        (item.line ? '<span class="diag-loc">line ' + item.line +
          (item.column ? ':' + item.column : '') + '</span>' : '') +
        '<span class="diag-message">' + escapeHtml(item.message) + '</span>';
      li.appendChild(head);

      if (item.detail) {
        var detail = document.createElement('span');
        detail.className = 'diag-hint';
        detail.textContent = item.detail;
        li.appendChild(detail);
      }
      if (item.hint) {
        var hint = document.createElement('span');
        hint.className = 'diag-hint';
        hint.textContent = item.hint;
        li.appendChild(hint);
      }
      if (item.traceback && item.traceback.length) {
        var trace = document.createElement('pre');
        trace.className = 'diag-trace';
        trace.textContent = item.traceback.join('\n');
        li.appendChild(trace);
      }
      if (item.line) {
        li.addEventListener('click', function () { jumpToLine(item.line); });
      }
      list.appendChild(li);
    });
  }

  function jumpToLine(line) {
    var code = $('code');
    var lines = code.value.split('\n');
    var offset = 0;
    for (var i = 0; i < Math.min(line - 1, lines.length); i++) offset += lines[i].length + 1;
    code.focus();
    code.setSelectionRange(offset, offset + (lines[line - 1] || '').length);

    // The highlight layer has one element per line and the same metrics as the
    // textarea, so it knows where the line really is -- including when earlier
    // lines have wrapped.
    var target = $('highlight').children[line - 1];
    code.scrollTop = target ? Math.max(0, target.offsetTop - 60) : 0;
  }

  /* ---------------------------------------------------- source links ---- */

  /* FR-07: every node the static front ends produce knows the span of source
     it came from. These keep the editor and the graph pointing at each other:
     hovering a node lights its line, a slice paints every line it covers, and
     clicking a line number marks the nodes that line produced. */

  function spanLines(node) {
    var lines = [];
    if (!node || !node.source) return lines;
    for (var line = node.source.line; line <= node.source.end_line; line++) lines.push(line);
    return lines;
  }

  function resetSourceLinks(linked) {
    state.source = { linked: !!linked, hover: [], slice: [], criterion: [], picked: null };
    $('gutter').classList.toggle('linked', !!linked);
    $('gutter').title = linked ? 'Click a line number to find the instructions it produced.' : '';
    applySourceMarks();
  }

  function unlinkSource() {
    if (!state.source.linked) return;
    resetSourceLinks(false);
    graph.setMarked(null);
    markPickedInTable([]);
  }

  function hoverSource(node) {
    if (!state.source.linked) return;
    state.source.hover = spanLines(node);
    applySourceMarks();
  }

  /* Paint the lines of a slice; returns them, sorted, for the status line. */
  function sliceSource(ids, criterion) {
    if (!state.source.linked || !state.payload) return [];
    var nodes = state.payload.nodes;
    var lines = {};
    ids.forEach(function (id) {
      spanLines(nodes[id]).forEach(function (line) { lines[line] = true; });
    });
    state.source.slice = Object.keys(lines).map(Number).sort(function (a, b) { return a - b; });
    state.source.criterion = spanLines(nodes[criterion]);
    applySourceMarks();
    if (state.source.criterion.length) revealLine(state.source.criterion[0]);
    return state.source.slice;
  }

  function pickLine(line) {
    if (!state.source.linked || !state.payload) return;
    if (state.source.picked === line) {
      state.source.picked = null;
      graph.setMarked(null);
      markPickedInTable([]);
      applySourceMarks();
      setStatus('Click a gate to slice from it.');
      return;
    }
    var ids = state.payload.nodes
      .filter(function (node) { return spanLines(node).indexOf(line) !== -1; })
      .map(function (node) { return node.id; });
    state.source.picked = line;
    graph.setMarked(ids);
    markPickedInTable(ids);
    applySourceMarks();
    setStatus(ids.length
      ? 'Line ' + line + ' produced ' + ids.length + ' instruction' + (ids.length === 1 ? '' : 's') +
        ': #' + ids.join(', #') + '. Click one to slice from it; click the line number again to clear.'
      : 'Line ' + line + ' produced no instructions.');
  }

  function markPickedInTable(ids) {
    var picked = new Set(ids);
    $('instr-body').querySelectorAll('tr').forEach(function (row) {
      row.classList.toggle('marked', picked.has(parseInt(row.dataset.id, 10)));
    });
  }

  var MARK_CLASSES = ['src-slice', 'src-criterion', 'src-picked', 'src-hover'];
  var markedRows = [];          // line numbers currently carrying a mark

  /* Re-applied after every editor render, which rebuilds the line elements. */
  function applySourceMarks() {
    var highlight = $('highlight').children;
    var gutter = $('gutter').children;
    markedRows.forEach(function (line) {
      [highlight[line - 1], gutter[line - 1]].forEach(function (row) {
        if (row) row.classList.remove.apply(row.classList, MARK_CLASSES);
      });
    });
    markedRows = [];

    var marks = {};
    function add(lines, cls) {
      lines.forEach(function (line) { (marks[line] = marks[line] || []).push(cls); });
    }
    add(state.source.slice, 'src-slice');
    add(state.source.criterion, 'src-criterion');
    if (state.source.picked) add([state.source.picked], 'src-picked');
    add(state.source.hover, 'src-hover');

    Object.keys(marks).forEach(function (key) {
      var line = Number(key);
      var row = highlight[line - 1];
      if (row) row.classList.add.apply(row.classList, marks[key]);
      var number = gutter[line - 1];
      var gutterMarks = marks[key].filter(function (cls) { return cls !== 'src-hover'; });
      if (number && gutterMarks.length) number.classList.add.apply(number.classList, gutterMarks);
      markedRows.push(line);
    });
  }

  /* Scroll the editor so a line is in view, without moving the caret. */
  function revealLine(line) {
    var code = $('code');
    var target = $('highlight').children[line - 1];
    if (!target) return;
    var top = target.offsetTop;
    if (top < code.scrollTop + 20 || top > code.scrollTop + code.clientHeight - 40) {
      code.scrollTop = Math.max(0, top - 60);
      syncEditorScroll();
    }
  }

  /* [3, 5, 6, 7, 10] -> "lines 3, 5-7, 10" */
  function formatLines(lines) {
    var parts = [];
    for (var i = 0; i < lines.length; i++) {
      var start = lines[i];
      while (i + 1 < lines.length && lines[i + 1] === lines[i] + 1) i++;
      parts.push(start === lines[i] ? String(start) : start + '\u2013' + lines[i]);
    }
    return (lines.length === 1 ? 'line ' : 'lines ') + parts.join(', ');
  }

  /* ----------------------------------------------------------- slice ---- */

  function pickCriterion(node) {
    requestSlice(node.id);
  }

  function requestSlice(nodeIdx) {
    if (!state.token) return;
    state.criterion = nodeIdx;
    var seq = ++state.sliceSeq;

    fetch('/api/slice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.token,
        node_idx: nodeIdx,
        direction: state.direction,
        dep_types: state.activeDeps
      })
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (seq !== state.sliceSeq) return;  // a newer request has overtaken this one
        if (!data.ok) {
          var problem = data.error || {};
          var text = typeof problem === 'string' ? problem : (problem.message || 'Slicing failed.');
          if (problem.hint) text += ' ' + problem.hint;
          setStatus(text);
          return;
        }
        graph.setSlice(data.slice, nodeIdx);
        markSliceInTable(data.slice, nodeIdx);
        var node = state.payload.nodes[nodeIdx];
        var lines = sliceSource(data.slice, nodeIdx);
        var percent = Math.round((data.size / data.total) * 100);
        setStatus(
          data.direction + ' slice from #' + nodeIdx + ' (' + (node.short || node.label) + '): ' +
          data.size + ' of ' + data.total + ' instructions (' + percent + '%)' +
          (data.dep_types ? ' following ' + data.dep_types.join(', ') : ' following all dependencies') +
          (lines.length ? '; source ' + formatLines(lines) : '')
        );
      })
      .catch(function (error) {
        if (seq === state.sliceSeq) setStatus('Slice request failed: ' + error);
      });
  }

  /* ------------------------------------------------ instruction table --- */

  function renderInstructions(data) {
    var body = $('instr-body');
    body.textContent = '';

    if (!data) {
      body.innerHTML = '<tr><td colspan="7" class="placeholder">No instructions yet.</td></tr>';
      return;
    }

    data.nodes.forEach(function (node) {
      var row = document.createElement('tr');
      row.dataset.id = String(node.id);
      row.innerHTML =
        '<td>' + node.id + '</td>' +
        '<td>' + escapeHtml(node.name) + '</td>' +
        '<td>' + (node.qubits.length ? 'q' + node.qubits.join(', q') : '—') + '</td>' +
        '<td>' + (node.clbits.length ? 'c' + node.clbits.join(', c') : '—') + '</td>' +
        '<td>' + (node.params.length ? escapeHtml(node.params.join(', ')) : '—') + '</td>' +
        '<td class="line-cell">' + (node.source
          ? '<span class="line-link" title="Show in the editor">' +
            escapeHtml(window.describeSource(node.source)) + '</span>'
          : '—') + '</td>' +
        '<td class="slice-cell"></td>';
      row.addEventListener('click', function () { requestSlice(node.id); });
      row.addEventListener('mouseenter', function () { hoverSource(node); });
      row.addEventListener('mouseleave', function () { hoverSource(null); });
      var link = row.querySelector('.line-link');
      if (link) {
        link.addEventListener('click', function (event) {
          event.stopPropagation();
          jumpToLine(node.source.line);
        });
      }
      body.appendChild(row);
    });
  }

  function markSliceInTable(sliceIds, criterion) {
    var inSlice = new Set(sliceIds);
    $('instr-body').querySelectorAll('tr').forEach(function (row) {
      var id = parseInt(row.dataset.id, 10);
      row.classList.toggle('in-slice', inSlice.has(id));
      row.classList.toggle('criterion', id === criterion);
      var cell = row.querySelector('.slice-cell');
      if (cell) {
        cell.innerHTML = id === criterion
          ? '<span class="tick">criterion</span>'
          : (inSlice.has(id) ? '<span class="tick">✓</span>' : '');
      }
    });
  }

  /* ----------------------------------------------- qubit (horizontal) --- */

  function renderQubitFilter(data) {
    var container = $('qubit-filter');
    container.textContent = '';

    var label = document.createElement('span');
    label.className = 'tool-label';
    label.textContent = 'Qubit slice';
    container.appendChild(label);

    if (!data) {
      var placeholder = document.createElement('span');
      placeholder.className = 'placeholder';
      placeholder.textContent = 'Analyze a circuit to pick a wire.';
      container.appendChild(placeholder);
      return;
    }

    for (var q = 0; q < data.program.num_qubits; q++) {
      (function (qubit) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = 'q' + qubit;
        chip.addEventListener('click', function () { pickQubit(qubit); });
        container.appendChild(chip);
      })(q);
    }

    var clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'chip';
    clear.textContent = 'clear';
    clear.addEventListener('click', function () { pickQubit(null); });
    container.appendChild(clear);
  }

  /* A horizontal slice: every instruction that touches one wire. The Python
     slicer reserves SlicingCriterion.qubits for this but does not consume it
     yet, so it is computed here from the instruction list. */
  function pickQubit(qubit) {
    if (!state.payload) return;
    if (state.selectedQubit === qubit) qubit = null;
    state.selectedQubit = qubit;
    graph.setSelectedQubit(qubit);

    $('qubit-filter').querySelectorAll('.chip').forEach(function (chip) {
      chip.classList.toggle('active', qubit !== null && chip.textContent === 'q' + qubit);
    });

    if (qubit === null) {
      setStatus('Qubit filter cleared.');
      return;
    }

    var touching = state.payload.nodes
      .filter(function (node) { return node.qubits.indexOf(qubit) !== -1; })
      .map(function (node) { return node.id; });

    setStatus('q' + qubit + ' is touched by ' + touching.length + ' of ' +
      state.payload.nodes.length + ' instructions' +
      (touching.length ? ': #' + touching.join(', #') : '.'));
  }

  /* ---------------------------------------------------- entanglement ---- */

  function renderEntanglement(data) {
    var container = $('entanglement-body');
    container.textContent = '';

    if (!data) {
      container.innerHTML = '<p class="placeholder">Analyze a circuit to see its groups.</p>';
      return;
    }

    var intro = document.createElement('p');
    intro.className = 'console-note';
    intro.textContent = data.entanglement.num_groups + ' groups, of which ' +
      data.entanglement.entangled_groups + ' were created by an entangling gate. ' +
      'Every wire starts in a group of its own.';
    container.appendChild(intro);

    // Newest group first: the measurement and classical groups that close a
    // program are usually what you came to look at, and the per-wire init
    // groups that open it are the least interesting rows on the panel.
    data.entanglement.groups.slice().reverse().forEach(function (group) {
      var card = document.createElement('div');
      card.className = 'group-card';

      var title = document.createElement('h4');
      title.innerHTML = 'Group ' + group.id +
        ' <span class="group-kind kind-' + group.kind + '">' + group.kind + '</span>' +
        (group.measured ? ' <span class="group-kind kind-measurement">measured</span>' : '') +
        (group.active ? '' : ' <span class="group-kind kind-init">closed</span>');
      card.appendChild(title);

      var dl = document.createElement('dl');
      dl.innerHTML =
        '<dt>qubits</dt><dd>' + (group.qubits.length ? 'q' + group.qubits.join(', q') : '—') + '</dd>' +
        '<dt>origin</dt><dd>' + escapeHtml(group.origin) + '</dd>' +
        '<dt>depth</dt><dd>' + group.depth + '</dd>' +
        '<dt>parents</dt><dd>' + (group.parents.length ? group.parents.join(', ') : '—') + '</dd>';
      card.appendChild(dl);

      card.appendChild(indexRow('computed', group.computed));
      card.appendChild(indexRow('uncomputed', group.uncomputed));
      card.appendChild(indexRow('measurements', group.measurements));
      container.appendChild(card);
    });
  }

  function indexRow(label, indices) {
    var wrapper = document.createElement('dl');
    var terms = document.createElement('dt');
    terms.textContent = label;
    var values = document.createElement('dd');
    if (!indices.length) {
      values.textContent = '—';
    } else {
      indices.forEach(function (index, position) {
        var link = document.createElement('span');
        link.className = 'idx-link';
        link.textContent = '#' + index;
        link.title = 'Slice from this instruction';
        link.addEventListener('click', function () {
          requestSlice(index);
          document.querySelector('.tab[data-tab="graph"]').click();
        });
        values.appendChild(link);
        if (position < indices.length - 1) values.appendChild(document.createTextNode(', '));
      });
    }
    wrapper.appendChild(terms);
    wrapper.appendChild(values);
    return wrapper;
  }


  /* ---------------------------------------------------------- splitter -- */

  var SPLIT_KEY = 'qslicer.split';
  var SPLIT_MIN = 22;     // percent; below this the editor stops being usable
  var SPLIT_MAX = 78;

  function wireSplitter() {
    var splitter = $('splitter');
    var workbench = document.querySelector('.workbench');
    if (!splitter || !workbench) return;

    applySplit(readSplit());

    var dragging = false;

    splitter.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      dragging = true;
      document.body.classList.add('splitting');
      splitter.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    splitter.addEventListener('pointermove', function (event) {
      if (!dragging) return;
      var rect = workbench.getBoundingClientRect();
      applySplit(((event.clientX - rect.left) / rect.width) * 100);
    });

    function stop(event) {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('splitting');
      try { splitter.releasePointerCapture(event.pointerId); } catch (err) { /* fine */ }
      saveSplit();
      refitGraph();
    }
    splitter.addEventListener('pointerup', stop);
    splitter.addEventListener('pointercancel', stop);

    // Double-click evens the two panes up again.
    splitter.addEventListener('dblclick', function () {
      applySplit(50);
      saveSplit();
      refitGraph();
    });

    // A separator should be movable from the keyboard too.
    splitter.addEventListener('keydown', function (event) {
      var step = event.shiftKey ? 10 : 2;
      if (event.key === 'ArrowLeft') applySplit(currentSplit() - step);
      else if (event.key === 'ArrowRight') applySplit(currentSplit() + step);
      else if (event.key === 'Home') applySplit(SPLIT_MIN);
      else if (event.key === 'End') applySplit(SPLIT_MAX);
      else if (event.key === 'Enter' || event.key === ' ') applySplit(50);
      else return;
      event.preventDefault();
      saveSplit();
      refitGraph();
    });
  }

  function currentSplit() {
    return parseFloat(document.querySelector('.workbench').style.getPropertyValue('--split')) || 50;
  }

  function applySplit(percent) {
    var value = clamp(percent, SPLIT_MIN, SPLIT_MAX);
    var workbench = document.querySelector('.workbench');
    workbench.style.setProperty('--split', value.toFixed(2) + '%');
    var splitter = $('splitter');
    splitter.setAttribute('aria-valuenow', Math.round(value));
    splitter.setAttribute('aria-valuemin', SPLIT_MIN);
    splitter.setAttribute('aria-valuemax', SPLIT_MAX);
  }

  /* The pane changed width, so what fitted before may not fit now. */
  var refitGraph = debounce(function () {
    if (state.payload) graph.fit(window.QGraph.READABLE_SCALE);
    updateGutter();   // the editor is narrower or wider, so lines re-wrap
  }, 120);

  function readSplit() {
    try {
      var saved = parseFloat(window.localStorage.getItem(SPLIT_KEY));
      if (!isNaN(saved)) return saved;
    } catch (err) { /* private window, blocked storage: use the default */ }
    return 50;
  }

  function saveSplit() {
    try {
      window.localStorage.setItem(SPLIT_KEY, String(currentSplit()));
    } catch (err) { /* not worth telling anyone about */ }
  }

  /* --------------------------------------------- diagnostics height -- */

  /* The divider between the editor and the diagnostics panel. Until it is
     dragged the panel sizes to its contents (up to 36% of the pane); after,
     it keeps the height it was given, remembered like the column split. */

  var DIAG_KEY = 'qslicer.diagnostics';
  var DIAG_MIN = 64;       // px: the heading and the first line of the list
  var EDITOR_MIN = 120;    // px the editor keeps however tall the panel gets

  function wireDiagnosticsSplitter() {
    var handle = $('diag-splitter');
    if (!handle) return;

    applyDiagHeight(readDiagHeight());

    var dragging = false;
    var startY = 0;
    var startHeight = 0;

    handle.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      dragging = true;
      startY = event.clientY;
      startHeight = $('diagnostics').getBoundingClientRect().height;
      document.body.classList.add('splitting-rows');
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    handle.addEventListener('pointermove', function (event) {
      if (!dragging) return;
      // Dragging up makes the panel taller.
      applyDiagHeight(startHeight + (startY - event.clientY));
    });

    function stop(event) {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('splitting-rows');
      try { handle.releasePointerCapture(event.pointerId); } catch (err) { /* fine */ }
      saveDiagHeight();
      updateGutter();
    }
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);

    // Double-click hands the height back to the contents.
    handle.addEventListener('dblclick', function () {
      applyDiagHeight(null);
      saveDiagHeight();
      updateGutter();
    });

    handle.addEventListener('keydown', function (event) {
      var step = event.shiftKey ? 64 : 16;
      var height = $('diagnostics').getBoundingClientRect().height;
      if (event.key === 'ArrowUp') applyDiagHeight(height + step);
      else if (event.key === 'ArrowDown') applyDiagHeight(height - step);
      else if (event.key === 'Home') applyDiagHeight(diagMax());
      else if (event.key === 'End') applyDiagHeight(DIAG_MIN);
      else if (event.key === 'Enter' || event.key === ' ') applyDiagHeight(null);
      else return;
      event.preventDefault();
      saveDiagHeight();
      updateGutter();
    });
  }

  /* The tallest the panel may be: everything but EDITOR_MIN of the editor. */
  function diagMax() {
    var panel = $('diagnostics').getBoundingClientRect().height;
    var editor = $('editor-shell').getBoundingClientRect().height;
    return Math.max(DIAG_MIN, panel + editor - EDITOR_MIN);
  }

  /* `height` in px, or null to size to the contents again. */
  function applyDiagHeight(height) {
    var panel = $('diagnostics');
    var handle = $('diag-splitter');
    if (height === null || isNaN(height)) {
      panel.style.removeProperty('height');
      panel.classList.remove('sized');
      handle.removeAttribute('aria-valuenow');
      return;
    }
    var value = Math.round(clamp(height, DIAG_MIN, diagMax()));
    panel.style.height = value + 'px';
    panel.classList.add('sized');
    handle.setAttribute('aria-valuenow', value);
    handle.setAttribute('aria-valuemin', DIAG_MIN);
  }

  /* A smaller window may leave a dragged panel taller than now fits. */
  function reclampDiagnostics() {
    var panel = $('diagnostics');
    if (panel && panel.classList.contains('sized')) {
      applyDiagHeight(panel.getBoundingClientRect().height);
    }
  }

  function readDiagHeight() {
    try {
      var saved = parseFloat(window.localStorage.getItem(DIAG_KEY));
      if (!isNaN(saved)) return saved;
    } catch (err) { /* private window, blocked storage: size to contents */ }
    return null;
  }

  function saveDiagHeight() {
    try {
      var panel = $('diagnostics');
      if (panel.classList.contains('sized')) {
        window.localStorage.setItem(DIAG_KEY, String(panel.getBoundingClientRect().height));
      } else {
        window.localStorage.removeItem(DIAG_KEY);
      }
    } catch (err) { /* not worth telling anyone about */ }
  }

  /* --------------------------------------------------------- tooltip ---- */

  function showTooltip(node, event, html) {
    tooltip.innerHTML = html;
    tooltip.hidden = false;
    moveTooltip(event);
  }

  function moveTooltip(event) {
    if (tooltip.hidden) return;
    var stage = $('graph-stage').getBoundingClientRect();
    var x = event.clientX - stage.left + 16;
    var y = event.clientY - stage.top + 16;
    var box = tooltip.getBoundingClientRect();
    if (x + box.width > stage.width) x = Math.max(8, x - box.width - 32);
    if (y + box.height > stage.height) y = Math.max(8, y - box.height - 32);
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  /* ------------------------------------------------------------- png ---- */

  function downloadPng() {
    if (!state.payload) {
      setStatus('Nothing to export yet.');
      return;
    }
    hideTooltip();
    graph.toPngBlob(null, function (error, blob) {
      if (error) {
        setStatus('PNG export failed: ' + error.message);
        return;
      }
      var base = (state.filename || 'circuit').replace(/\.(py|qasm3?)$/i, '');
      var suffix = state.criterion === null
        ? 'qpdg'
        : state.direction + '-slice-' + state.criterion;
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = base + '_' + suffix + '.png';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      setStatus('Saved ' + link.download + '.');
    });
  }

  /* ----------------------------------------------------------- misc ----- */

  function resetOutput() {
    state.payload = null;
    state.token = null;
    state.criterion = null;
    state.selectedQubit = null;
    resetSourceLinks(false);
    graph.clear();
    $('graph-empty').hidden = false;
    renderInstructions(null);
    renderEntanglement(null);
    renderQubitFilter(null);
    renderDepFilter(null);
    state.activeDeps = null;
    $('console-out').textContent = '(nothing yet)';
    $('console-error').textContent = '(none)';
    $('diagnostics-list').innerHTML =
      '<li class="placeholder">The linters picked under <strong>Lint</strong> run on every analysis.</li>';
    $('diagnostics-summary').textContent = 'not run yet';
    $('diagnostics-summary').className = 'diagnostics-summary';
    setStatus('Nothing analyzed yet.');
    setSide('');
  }

  function setStatus(text) { $('status-main').textContent = text; }
  function setSide(text) { $('status-side').textContent = text; }

  function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, wait);
    };
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
