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
    direction: 'backward',   // or 'forward', or 'chop'
    // The slice criteria: one node, or (backward, Ctrl/Shift-click) up to
    // MAX_CRITERIA of them, each its own slice. Empty when there is no slice.
    criteria: [],
    // In Chop: the operation and the measurement to stop at, as they are picked.
    chop: { from: null, to: null },
    sideText: '',      // the side status the analysis left, shown again on Clear
    selectedQubit: null,
    activeDeps: null,   // Set of dependency names
    busy: false,
    sliceSeq: 0,       // guards against an older slice response landing last
    // Values the user picked for what only a run could decide, as the API
    // takes them: {name, line, value}. Cleared when another file is loaded.
    bindings: [],
    circuit: 0,        // which of the file's circuits the output shows
    requestKey: null,  // the analyze request the output came from (see analysisCache)
    retrySlice: null,  // {criteria, chop} to slice again once a forgotten analysis is redone
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
      onQubitSlice: sliceOnQubit,
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
    wireInstructionTable();
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
      drawTableRows(true);
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
    ['mouseup', 'keyup', 'select', 'input'].forEach(function (type) {
      code.addEventListener(type, dropLintMarkIfMoved);
    });
    document.addEventListener('selectionchange', dropLintMarkIfMoved);
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
      forgetProgramChoices();
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

    $('circuit-select').addEventListener('change', function (event) {
      state.circuit = parseInt(event.target.value, 10) || 0;
      analyze();
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
      forgetProgramChoices();
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
        forgetProgramChoices();
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
        forgetProgramChoices();
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

    // The gutter goes first: it is as wide as its longest number, and the
    // textarea gets what is left. Measured before a paste takes it from 1 digit
    // to 3, the colored layer comes out wider than the text, wraps less, and
    // every line number and the caret drift from what is drawn.
    renderGutter(sourceLines.length, errorLine || 0);

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
    $('dir-chop').addEventListener('click', function () { setDirection('chop'); });
    $('slice-clear').addEventListener('click', function () { clearSlice(); });
    $('slice-qubit').addEventListener('change', function (event) {
      var value = event.target.value;
      event.target.value = '';      // an action, not a setting: ready to pick again
      if (value !== '') sliceOnQubit(Number(value), null);
    });
    $('mirror-links').addEventListener('click', function () {
      var on = !this.classList.contains('active');
      this.classList.toggle('active', on);
      this.setAttribute('aria-pressed', String(on));
      graph.setShowMirrors(on);
    });
    // Esc clears the slice, unless the key is meant for the editor or a field.
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' || !hasSlice()) return;
      var tag = event.target && event.target.tagName;
      if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'SELECT') return;
      clearSlice();
    });

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
    $('lint-markers').addEventListener('click', function () {
      var on = !this.classList.contains('active');
      this.classList.toggle('active', on);
      this.setAttribute('aria-pressed', String(on));
      graph.setShowLint(on);
    });
  }

  /* Switching keeps what it can: a single criterion becomes Chop's operation,
     Chop's operation becomes the criterion again, and several criteria going
     Forward keep only the first (several at once are backward only). */
  function setDirection(direction) {
    var before = state.direction;
    state.direction = direction;
    setDirectionChips(direction);
    if (!state.payload || before === direction) {
      if (hasSlice()) requestSlice();
      return;
    }
    if (direction === 'chop') {
      var from = state.criteria.length === 1 ? state.criteria[0] : null;
      clearSlice(true);
      state.chop = { from: from, to: null };
      showChopEnds();
      return;
    }
    var start = before === 'chop' ? state.chop.from : null;
    var criteria = before === 'chop' ? (start === null ? [] : [start]) : state.criteria.slice();
    var dropped = direction === 'forward' && criteria.length > 1;
    if (dropped) criteria = criteria.slice(0, 1);
    if (before === 'chop') clearSlice(true);   // otherwise the new slice simply replaces the old
    state.criteria = criteria;
    if (criteria.length) requestSlice(dropped ? ' (Forward slices one criterion at a time: kept the first.)' : '');
    else setStatus('Click a gate to slice from it.');
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
    if (hasSlice()) requestSlice();
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
        if (tab.dataset.tab === 'instructions') drawTableRows(true);
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

  /* Recent analyses, kept in this tab so that switching back to a circuit, or
     analyzing code that has not changed, needs no request. Keyed by the whole
     request (source, settings, circuit, picked values); sized by how many
     nodes and edges the entries hold, oldest dropped first. Shor's main
     circuit is ~37,000 of them, about 15 MB once parsed. */
  var CACHE_BUDGET = 150000;     // nodes + edges across all entries, ~60 MB
  var analysisCache = new Map(); // request body -> {data, cost}, oldest first
  var cachedCost = 0;

  function cacheGet(key) {
    var entry = analysisCache.get(key);
    if (!entry) return null;
    analysisCache.delete(key);   // move to the newest end
    analysisCache.set(key, entry);
    return entry.data;
  }

  function cachePut(key, data) {
    if (!data || !data.ok) return;
    var cost = (data.nodes ? data.nodes.length : 0) + (data.edges ? data.edges.length : 0);
    if (cost > CACHE_BUDGET) return;
    cacheDrop(key);
    analysisCache.set(key, { data: data, cost: cost });
    cachedCost += cost;
    while (cachedCost > CACHE_BUDGET && analysisCache.size) {
      cacheDrop(analysisCache.keys().next().value);
    }
  }

  function cacheDrop(key) {
    var entry = analysisCache.get(key);
    if (!entry) return;
    cachedCost -= entry.cost;
    analysisCache.delete(key);
  }

  /* ``fresh`` skips the tab's cache, for when the server has forgotten the
     analysis a cached copy points at. */
  function analyze(fresh) {
    if (state.busy) return;
    var source = $('code').value;
    if (!source.trim()) {
      setStatus('Nothing to analyze yet.');
      return;
    }

    var rules = lintPicker ? lintPicker.selection()
      : (window.LintRules ? window.LintRules.stored() : null);
    var key = JSON.stringify({
      source: source,
      filename: state.filename || defaultFilename(),
      profile: $('profile-select').value || 'baseline',
      language: interpreter(),
      // Sorted, so the same selection makes the same cache key whether it came
      // from the Lint picker or, before that has loaded, from storage.
      lint_rules: Array.isArray(rules) ? rules.slice().sort() : rules,
      bindings: state.bindings,
      circuit: state.circuit
    });
    var cached = fresh === true ? null : cacheGet(key);
    if (cached) {
      state.requestKey = key;
      handleAnalysis(cached, true);
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
      body: key
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        cachePut(key, data);
        state.requestKey = key;
        handleAnalysis(data, false);
      })
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

  function handleAnalysis(data, fromCache) {
    state.lintMark = null;
    renderDiagnostics(data.diagnostics || [], data.error);
    renderCircuitBar(data);
    $('console-out').textContent = data.stdout ? data.stdout : '(no output)';
    $('console-error').textContent = data.error
      ? formatErrorForConsole(data.error)
      : '(none)';

    resetSourceLinks(data.ok && data.nodes.some(function (node) { return node.line; }));

    if (!data.ok) {
      state.payload = null;
      state.token = null;
      forgetSlice();
      graph.clear();
      $('graph-empty').hidden = false;
      renderInstructions(null);
      renderQubitFilter(null);
      renderQubitSlicePicker(null);
      renderDepFilter(null);
      updateGutter(data.error && data.error.line);
      setStatus(data.error ? data.error.message : 'Analysis failed.');
      return;
    }

    state.payload = data;
    state.token = data.token;
    forgetSlice();
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
    renderQubitFilter(data);
    renderQubitSlicePicker(data);

    var program = data.program;
    setStatus(program.num_instructions + ' instructions, ' + program.num_qubits + ' qubits, ' +
      program.num_clbits + ' classical bits, ' + data.edges.length + ' dependency edges. ' +
      (state.direction === 'chop' ? chopPrompt()
        : 'Click a gate to slice from it, or "slice" under a wire name for its last measurement.') +
      (fromCache ? ' (Kept from earlier: no request.)' : ''));

    state.sideText = (data.route ? data.route + ' · ' : '') + 'rules: ' + data.rules.join(', ');
    setSide(state.sideText);

    // The server had forgotten the analysis a slice was asked of; it has just
    // been redone, so the slice can go ahead.
    if (state.retrySlice !== null) {
      var retry = state.retrySlice;
      state.retrySlice = null;
      var known = function (id) { return id === null || id < data.nodes.length; };
      if (retry.criteria.every(known) && known(retry.chop.from) && known(retry.chop.to)) {
        state.criteria = retry.criteria;
        state.chop = retry.chop;
        requestSlice();
      }
    }
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
        traceback: error.traceback || null,
        needs: error.needs || null
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
        // The code links to the rule's explanation on the Lint page, in a new
        // tab so the analysis stays.
        (item.tool && item.code ? '<a class="diag-code" target="_blank" rel="noopener" href="/lint#' +
          escapeHtml(item.tool + '/' + item.code) + '" title="What ' + escapeHtml(item.code) +
          ' checks, with an example">' + escapeHtml(item.code) + '</a>'
          : '<span class="diag-code">' + escapeHtml(item.code) + '</span>') +
        (item.line ? '<span class="diag-loc">line ' + item.line +
          (item.column ? ':' + item.column : '') + '</span>' : '') +
        (item.nodes && item.nodes.length ? '<span class="diag-nodes" title="Graph nodes this is about">#' +
          item.nodes.join(', #') + '</span>' : '') +
        '<span class="diag-message">' + escapeHtml(item.message) + '</span>';
      li.appendChild(head);
      var ruleLink = head.querySelector('a.diag-code');
      if (ruleLink) ruleLink.addEventListener('click', function (event) { event.stopPropagation(); });

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
      if (item.needs) li.appendChild(pickValueForm(item.needs));
      if (item.traceback && item.traceback.length) {
        var trace = document.createElement('pre');
        trace.className = 'diag-trace';
        trace.textContent = item.traceback.join('\n');
        li.appendChild(trace);
      }
      var aboutGraph = (item.nodes && item.nodes.length) || (item.qubits && item.qubits.length);
      if (item.line || aboutGraph) {
        li.addEventListener('click', function () {
          if (item.line) jumpToLine(item.line);
          if (aboutGraph) markLintNodes(item);
        });
      }
      list.appendChild(li);
    });
  }

  /* ------------------------------------------- run-time values ---- */

  /* The form under a needs_value error: the parser stopped on something only a
     run could decide (an unseeded random draw, a required command-line
     argument), and names the variable it came from. Whatever the user types is
     read as a Python literal where that variable is assigned. */
  function pickValueForm(needs) {
    var form = document.createElement('form');
    form.className = 'pick-value';
    var id = 'pick-' + needs.name + '-' + needs.line;
    var label = document.createElement('label');
    label.htmlFor = id;
    label.textContent = needs.name + ' =';
    var input = document.createElement('input');
    input.id = id;
    input.type = 'text';
    input.required = true;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.placeholder = 'a Python value';
    input.title = 'A Python literal: a number, a quoted string, True/False/None, or a list';
    var button = document.createElement('button');
    button.type = 'submit';
    button.className = 'btn btn-small btn-primary';
    button.textContent = 'Use and analyze';
    form.appendChild(label);
    form.appendChild(input);
    form.appendChild(button);
    // Typing or clicking here must not count as clicking the finding.
    form.addEventListener('click', function (event) { event.stopPropagation(); });
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var value = input.value.trim();
      if (!value) return;
      setBinding(needs.name, needs.line, value);
      analyze();
    });
    // Focus without scrolling the page; the diagnostics list scrolls itself.
    setTimeout(function () {
      try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
    }, 0);
    return form;
  }

  function setBinding(name, line, value) {
    state.bindings = state.bindings.filter(function (b) {
      return !(b.name === name && b.line === line);
    });
    state.bindings.push({ name: name, line: line, value: value });
    renderBindings();
  }

  /* The picked values, as chips under the example line; x removes one. */
  function renderBindings() {
    var box = $('bindings');
    box.textContent = '';
    box.hidden = !state.bindings.length;
    if (!state.bindings.length) return;
    var label = document.createElement('span');
    label.textContent = 'Run-time values:';
    box.appendChild(label);
    state.bindings.forEach(function (binding, index) {
      var chip = document.createElement('span');
      chip.className = 'chip';
      chip.title = 'Used where ' + binding.name + ' is assigned on line ' + binding.line;
      chip.textContent = binding.name + ' = ' + binding.value + '  (line ' + binding.line + ')';
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '\u00d7';
      remove.setAttribute('aria-label', 'Forget the value of ' + binding.name);
      remove.addEventListener('click', function () {
        state.bindings.splice(index, 1);
        renderBindings();
        analyze();
      });
      chip.appendChild(remove);
      box.appendChild(chip);
    });
  }

  /* A different program: its values and circuits are not this one's. */
  function forgetProgramChoices() {
    state.bindings = [];
    state.circuit = 0;
    renderBindings();
    renderCircuitBar(null);
  }

  /* The circuit picker, shown when the file builds more than one circuit. */
  function renderCircuitBar(data) {
    var circuits = (data && data.ok && data.circuits) || [];
    var bar = $('circuit-bar');
    bar.hidden = circuits.length < 2;
    if (circuits.length < 2) return;
    var select = $('circuit-select');
    select.textContent = '';
    circuits.forEach(function (info) {
      var option = document.createElement('option');
      option.value = String(info.index);
      option.textContent = circuitLabel(info);
      select.appendChild(option);
    });
    state.circuit = data.circuit || 0;
    select.value = String(state.circuit);
    var inside = circuits.filter(function (info) { return info.used_inside; }).length;
    $('circuit-count').textContent = circuits.length + ' circuits' +
      (inside ? ', ' + inside + ' used inside another' : '');
  }

  function circuitLabel(info) {
    var name = info.variable && info.variable !== info.name
      ? info.variable + ' (' + info.name + ')' : info.name;
    return (info.index === 0 ? 'Main: ' : '') + name + ' \u2014 ' +
      info.num_qubits + ' qubit' + (info.num_qubits === 1 ? '' : 's') + ', ' +
      info.size + ' instruction' + (info.size === 1 ? '' : 's') +
      (info.line ? ', line ' + info.line : '') +
      (info.index !== 0 && info.used_inside ? ', used inside another' : '');
  }

  /* A finding's nodes: marked in the graph and the instruction table, and
     brought into view, as clicking a line number marks the nodes it made.
     A finding about wires (QD07's idle qubits) marks those lanes instead. */
  function markLintNodes(item) {
    if (!state.payload) return;
    var nodes = item.nodes || [];
    var qubits = item.qubits || [];
    state.source.picked = null;
    graph.setMarked(nodes, qubits);
    if (nodes.length) graph.reveal(nodes);
    markPickedInTable(nodes);
    applySourceMarks();
    // The marks last as long as the line the finding selected stays selected.
    var code = $('code');
    state.lintMark = { start: code.selectionStart, end: code.selectionEnd };
    setStatus(nodes.length
      ? item.code + ' is about node' + (nodes.length === 1 ? '' : 's') + ' #' +
        nodes.join(', #') + ', marked in the graph. Click a gate to slice from it.'
      : item.code + ' is about wire' + (qubits.length === 1 ? '' : 's') + ' q' +
        qubits.join(', q') + ', marked red in the graph.');
  }

  /* Once the editor's selection moves off the finding's line (a click in the
     editor, typing, the arrow keys), its node marks go too. */
  function dropLintMarkIfMoved() {
    var mark = state.lintMark;
    var code = $('code');
    if (!mark || (code.selectionStart === mark.start && code.selectionEnd === mark.end)) return;
    state.lintMark = null;
    graph.setMarked(null);
    markPickedInTable([]);
    setStatus('Click a gate to slice from it.');
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

  /* Paint the lines of a slice; returns them, sorted, for the status line.
     ``criteria`` is the criterion's id, or a list of them (several slices,
     or a chop's two ends). */
  function sliceSource(ids, criteria) {
    if (!state.source.linked || !state.payload) return [];
    var nodes = state.payload.nodes;
    var lines = {};
    ids.forEach(function (id) {
      spanLines(nodes[id]).forEach(function (line) { lines[line] = true; });
    });
    state.source.slice = Object.keys(lines).map(Number).sort(function (a, b) { return a - b; });
    var ends = {};
    [].concat(criteria).forEach(function (id) {
      if (id !== null && id !== undefined) spanLines(nodes[id]).forEach(function (line) { ends[line] = true; });
    });
    state.source.criterion = Object.keys(ends).map(Number).sort(function (a, b) { return a - b; });
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
    state.lintMark = null;          // a picked line owns the marks now
    graph.setMarked(ids);
    markPickedInTable(ids);
    applySourceMarks();
    setStatus(ids.length
      ? 'Line ' + line + ' produced ' + ids.length + ' instruction' + (ids.length === 1 ? '' : 's') +
        ': #' + ids.join(', #') + '. Click one to slice from it; click the line number again to clear.'
      : 'Line ' + line + ' produced no instructions.');
  }

  function markPickedInTable(ids) {
    table.picked = ids && ids.length ? new Set(ids) : null;
    drawTableRows(true);
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

  var MAX_CRITERIA = 16;   // the most node_indices /api/slice takes at once

  function additive(event) {
    return !!(event && (event.ctrlKey || event.metaKey || event.shiftKey));
  }

  /* Anything picked: a criterion, or either end of a chop. */
  function hasSlice() {
    return state.criteria.length > 0 || state.chop.from !== null || state.chop.to !== null;
  }

  function insights() {
    return (state.payload && state.payload.insights) || {};
  }

  /* "#12 (cx)" */
  function nodeName(id) {
    var node = state.payload && state.payload.nodes[id];
    return '#' + id + (node ? ' (' + (node.short || node.label) + ')' : '');
  }

  /* A click on a node, in the graph or the table. A plain click slices from it
     alone, and clicking the only criterion again clears. Backward, Ctrl/Cmd-
     or Shift-click adds it to the criteria (each its own slice) or takes it
     out. In Chop, clicks pick the two ends. */
  function pickCriterion(node, event) {
    if (!state.payload) return;
    if (state.direction === 'chop') {
      pickChopEnd(node.id, null);
      return;
    }
    if (additive(event)) {
      if (state.direction !== 'backward') {
        setStatus('Several slices at once go backward only: switch to Backward, then Ctrl- or Shift-click.');
        return;
      }
      toggleCriterion(node.id);
      return;
    }
    if (state.criteria.length === 1 && state.criteria[0] === node.id) {
      clearSlice();
      return;
    }
    state.criteria = [node.id];
    requestSlice();
  }

  function toggleCriterion(id) {
    var at = state.criteria.indexOf(id);
    if (at !== -1) {
      state.criteria.splice(at, 1);
    } else if (state.criteria.length >= MAX_CRITERIA) {
      setStatus('At most ' + MAX_CRITERIA + ' slices at once. Remove one first: its × in the ' +
        'list above the graph, or Ctrl-click it.');
      return;
    } else {
      state.criteria.push(id);
    }
    if (state.criteria.length) requestSlice();
    else clearSlice();
  }

  /* Slice on a qubit: a backward slice from its last measurement. Ctrl/Cmd- or
     Shift-click adds it to the slices; in Chop it is the end to stop at. */
  function sliceOnQubit(qubit, event) {
    if (!state.payload) return;
    var found = (insights().measurements || {})[String(qubit)] || [];
    if (!found.length) {
      setStatus('q' + qubit + ' is never measured, so there is no measurement to slice from.');
      return;
    }
    var last = found[found.length - 1];
    graph.reveal([last]);
    if (state.direction === 'chop') {
      pickChopEnd(null, last);
      return;
    }
    if (state.direction !== 'backward') {
      // A measurement's slice is what led to it: backward, whatever was on.
      setDirectionChips('backward');
      state.direction = 'backward';
    }
    if (additive(event)) {
      if (state.criteria.indexOf(last) === -1) toggleCriterion(last);
      else requestSlice();
      return;
    }
    state.criteria = [last];
    requestSlice();
  }

  function setDirectionChips(direction) {
    ['backward', 'forward', 'chop'].forEach(function (name) {
      var chip = $('dir-' + name);
      chip.classList.toggle('active', direction === name);
      chip.setAttribute('aria-pressed', String(direction === name));
    });
  }

  /* Chop picks two ends: the operation (a node click), then the measurement to
     stop at (a second node click, or a qubit, which gives its last
     measurement). The qubit may come first; the next node click is then the
     operation. Once both are there, the next node click starts a new chop. */
  function pickChopEnd(nodeId, measurementId) {
    var chop = state.chop;
    if (nodeId === null) {
      chop.to = measurementId;
    } else if (chop.from === null) {
      chop.from = nodeId;
    } else if (chop.to === null) {
      if (nodeId === chop.from) {
        clearSlice();
        return;
      }
      chop.to = nodeId;
    } else {
      chop.from = nodeId;
      chop.to = null;
    }
    if (chop.from !== null && chop.to !== null) {
      graph.setChop(chop.from, chop.to);
      requestSlice();
    } else {
      showChopEnds();
    }
  }

  /* The chop's ends so far, with no slice, and what the next click will do. */
  function showChopEnds() {
    state.sliceSeq++;                 // a chop still on its way is no longer wanted
    graph.setSlice(null, null);
    graph.setLaneMarks(null);
    graph.setChop(state.chop.from, state.chop.to);
    renderSliceLegend(null);
    markSliceInTable([], [], state.chop, null);
    sliceSource([], [state.chop.from, state.chop.to]);
    $('slice-clear').disabled = !hasSlice();
    setSide(state.sideText);
    setStatus(chopPrompt());
  }

  function chopPrompt() {
    var chop = state.chop;
    if (chop.from === null && chop.to === null) {
      return 'Chop: click the operation to start from.';
    }
    if (chop.to === null) {
      return 'Chop from ' + nodeName(chop.from) + '. Next click: the measurement to stop at ' +
        '(or a wire\'s "slice", for its last measurement). Click ' + nodeName(chop.from) +
        ' again or Clear to start over.';
    }
    return 'Chop to ' + nodeName(chop.to) + '. Next click: the operation to start from.';
  }

  /* ``quiet`` leaves the status line alone, for a caller about to set it. */
  function clearSlice(quiet) {
    if (!hasSlice() && quiet !== true) return;
    forgetSlice();
    state.sliceSeq++;                 // a slice still on its way is no longer wanted
    graph.setSlice(null, null);
    graph.setChop(null);
    graph.setLaneMarks(null);
    markSliceInTable([], [], null, null);
    state.source.slice = [];
    state.source.criterion = [];
    applySourceMarks();
    setSide(state.sideText);
    if (quiet === true) return;
    setStatus(state.direction === 'chop'
      ? 'Chop cleared. ' + chopPrompt()
      : 'Slice cleared. Click a gate to slice from it.');
  }

  /* The page's record of the slice, for a new analysis (the graph resets itself). */
  function forgetSlice() {
    state.criteria = [];
    state.chop = { from: null, to: null };
    renderSliceLegend(null);
    $('slice-clear').disabled = true;
  }

  /* Ask for the slice the state describes: one criterion, several (one
     request with node_indices), or a chop with both ends picked. ``note`` is
     added to the status line once the answer is in. */
  function requestSlice(note) {
    if (!state.token) return;
    var body = { token: state.token, dep_types: state.activeDeps };
    var chop = state.direction === 'chop';
    if (chop) {
      if (state.chop.from === null || state.chop.to === null) return;
      body.direction = 'chop';
      body.node_idx = state.chop.from;
      body.target_idx = state.chop.to;
    } else if (state.criteria.length > 1) {
      body.direction = 'backward';
      body.node_indices = state.criteria.slice();
    } else if (state.criteria.length === 1) {
      body.direction = state.direction;
      body.node_idx = state.criteria[0];
    } else {
      return;
    }
    var seq = ++state.sliceSeq;
    var asked = { criteria: state.criteria.slice(), chop: { from: state.chop.from, to: state.chop.to } };
    $('slice-clear').disabled = false;

    fetch('/api/slice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (seq !== state.sliceSeq) return;  // a newer request has overtaken this one
        if (!data.ok && data.stale && state.requestKey !== null) {
          // The server restarted or dropped this analysis (it keeps a few dozen),
          // while this tab still had it. Analyze again, then slice.
          cacheDrop(state.requestKey);
          state.retrySlice = asked;
          setStatus('The server no longer had this analysis; analyzing again…');
          analyze(true);
          return;
        }
        if (!data.ok) {
          var problem = data.error || {};
          var text = typeof problem === 'string' ? problem : (problem.message || 'Slicing failed.');
          if (problem.hint) text += ' ' + problem.hint;
          setStatus(text);
          return;
        }
        showSlice(data, note || '');
      })
      .catch(function (error) {
        if (seq === state.sliceSeq) setStatus('Slice request failed: ' + error);
      });
  }

  function following(data) {
    return data.dep_types ? ' following ' + data.dep_types.join(', ') : ' following all dependencies';
  }

  function sliceSummary(data) {
    var percent = data.total ? Math.round((data.size / data.total) * 100) : 0;
    return data.size + ' of ' + data.total + ' instructions (' + percent + '%)';
  }

  function showSlice(data, note) {
    var lines;
    var correlated = null;
    if (data.slices) {
      // Several criteria: each its own color and number, the union in the table.
      state.criteria = data.node_indices.slice();
      graph.setChop(null);
      graph.setSlices(data.slices);
      graph.setLaneMarks(null);
      renderSliceLegend(data.slices);
      markSliceInTable(data.slice, state.criteria, null, graph);
      lines = sliceSource(data.slice, state.criteria);
      setStatus('Backward slices from ' + data.slices.length + ' criteria (' +
        data.slices.map(function (entry, k) { return (k + 1) + ': ' + nodeName(entry.node_idx); }).join(', ') +
        '): ' + sliceSummary(data) + ' in all,' + following(data) +
        (lines.length ? '; source ' + formatLines(lines) : '') + '.' + note);
    } else if (data.direction === 'chop') {
      var ends = 'Chop from ' + nodeName(data.node_idx) + ' to ' + nodeName(data.target_idx);
      renderSliceLegend(null);
      graph.setLaneMarks(null);
      graph.setChop(data.node_idx, data.target_idx);
      if (data.empty_reason) {
        // Nothing connects them: both ends stay marked, no slice is shown.
        graph.setSlice(null, null);
        markSliceInTable([], [], state.chop, null);
        sliceSource([], [data.node_idx, data.target_idx]);
        setStatus(ends + ': ' + data.empty_reason + ' Click a node to start a new chop, or Clear.' + note);
      } else {
        graph.setSlice(data.slice, null);
        markSliceInTable(data.slice, [], state.chop, null);
        lines = sliceSource(data.slice, [data.node_idx, data.target_idx]);
        setStatus(ends + ': ' + sliceSummary(data) + following(data) +
          (lines.length ? '; source ' + formatLines(lines) : '') +
          '. Click a node to start a new chop, or Clear.' + note);
      }
    } else {
      var nodeIdx = data.node_idx;
      state.criteria = [nodeIdx];
      renderSliceLegend(null);
      graph.setChop(null);
      graph.setSlice(data.slice, nodeIdx);
      markSliceInTable(data.slice, [nodeIdx], null, null);
      lines = sliceSource(data.slice, nodeIdx);
      setStatus(
        data.direction + ' slice from ' + nodeName(nodeIdx) + ': ' + sliceSummary(data) + following(data) +
        (lines.length ? '; source ' + formatLines(lines) : '') + note
      );
      correlated = (insights().correlations || {})[String(nodeIdx)] || null;
    }
    // A measurement as the single criterion: what it may be correlated with,
    // beside the status and on the wire labels.
    if (correlated) {
      graph.setLaneMarks(correlated.qubits, correlated.unmeasured);
      setSide(window.QGraph.insightText.correlation(correlated).join(' '));
    } else {
      graph.setLaneMarks(null);
      setSide(state.sideText);
    }
    $('slice-clear').disabled = false;
  }

  /* The criteria of several slices: number and color, the node, the size, and
     a button that takes it out. Clicking the node brings it into view. */
  function renderSliceLegend(slices) {
    var box = $('slice-legend');
    box.textContent = '';
    box.hidden = !slices || slices.length < 2;
    if (box.hidden) return;
    var label = document.createElement('span');
    label.className = 'tool-label';
    label.textContent = 'Slices';
    box.appendChild(label);
    slices.forEach(function (entry, k) {
      var item = document.createElement('span');
      item.className = 'slice-item';

      var go = document.createElement('button');
      go.type = 'button';
      go.className = 'slice-go';
      go.title = 'Show ' + nodeName(entry.node_idx) + ' in the graph';
      var swatch = document.createElement('span');
      swatch.className = 'slice-swatch ' + window.QGraph.slices.sliceClass(k);
      swatch.textContent = String(k + 1);
      go.appendChild(swatch);
      go.appendChild(document.createTextNode(' ' + nodeName(entry.node_idx)));
      go.addEventListener('click', function () { graph.reveal([entry.node_idx]); });
      item.appendChild(go);

      var size = document.createElement('span');
      size.className = 'slice-size';
      size.textContent = entry.size + ' instruction' + (entry.size === 1 ? '' : 's');
      item.appendChild(size);

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'slice-remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', 'Remove slice ' + (k + 1) + ', from ' + nodeName(entry.node_idx));
      remove.title = 'Remove this slice';
      remove.addEventListener('click', function () { toggleCriterion(entry.node_idx); });
      item.appendChild(remove);
      box.appendChild(item);
    });
  }

  /* The Slice picker's qubits, each slicing from that wire's last measurement. */
  function renderQubitSlicePicker(data) {
    var select = $('slice-qubit');
    select.textContent = '';
    var first = document.createElement('option');
    first.value = '';
    first.textContent = 'on qubit…';
    select.appendChild(first);
    select.disabled = !data;
    if (!data) return;
    var measured = (data.insights && data.insights.measurements) || {};
    for (var q = 0; q < data.program.num_qubits; q++) {
      var option = document.createElement('option');
      option.value = String(q);
      var count = (measured[String(q)] || []).length;
      option.textContent = 'q' + q + (count ? '' : ' (never measured)');
      select.appendChild(option);
    }
  }

  /* ------------------------------------------------ instruction table --- */

  /* Only the rows in view exist (plus a screenful either side), between two
     spacer rows that keep the scrollbar honest, so a circuit of any size
     scrolls, slices and marks at the same speed. Rows are one line high.
     Which rows are in the slice, the criterion or marked lives here, and rows
     are drawn from it. */
  var TABLE_OVERSCAN = 30;     // rows drawn beyond the view, each way
  var table = { nodes: [], rowH: 0, start: -1, end: -1,
                slice: null, criteria: [], chop: null, member: null,
                picked: null, frame: null, hovered: null };

  function renderInstructions(data) {
    var body = $('instr-body');
    table.nodes = data ? data.nodes : [];
    table.slice = null;
    table.criteria = [];
    table.chop = null;
    table.member = null;
    table.picked = null;
    table.start = table.end = -1;
    if (!data) {
      body.innerHTML = '<tr><td colspan="7" class="placeholder">No instructions yet.</td></tr>';
      return;
    }
    var scroller = body.closest('.table-scroll');
    if (scroller) scroller.scrollTop = 0;
    body.textContent = '';        // drawn now, or when the tab is shown
    drawTableRows(true);
  }

  /* ``criteria`` is a list (several slices number theirs), ``chop`` a chop's
     ends or null, and ``member`` the graph, which knows which of several
     slices a node is in, or null. */
  function markSliceInTable(sliceIds, criteria, chop, member) {
    table.slice = sliceIds && sliceIds.length ? new Set(sliceIds) : null;
    table.criteria = criteria || [];
    table.chop = chop ? { from: chop.from, to: chop.to } : null;
    table.member = member;
    drawTableRows(true);
  }

  function tableRowHtml(node) {
    var inSlice = table.slice && table.slice.has(node.id);
    var number = table.criteria.indexOf(node.id);
    var end = !table.chop ? '' : (table.chop.from === node.id ? 'from'
      : (table.chop.to === node.id ? 'to' : ''));
    var classes = [];
    if (inSlice) classes.push('in-slice');
    if (number !== -1 || end) classes.push('criterion');
    if (table.picked && table.picked.has(node.id)) classes.push('marked');
    return '<tr data-id="' + node.id + '"' +
      (classes.length ? ' class="' + classes.join(' ') + '"' : '') + '>' +
      '<td>' + node.id + '</td>' +
      '<td>' + escapeHtml(node.name) + '</td>' +
      '<td>' + (node.qubits.length ? 'q' + node.qubits.join(', q') : '—') + '</td>' +
      '<td>' + (node.clbits.length ? 'c' + node.clbits.join(', c') : '—') + '</td>' +
      '<td>' + (node.params.length ? escapeHtml(node.params.join(', ')) : '—') + '</td>' +
      '<td class="line-cell">' + (node.source
        ? '<span class="line-link" title="Show in the editor">' +
          escapeHtml(window.describeSource(node.source)) + '</span>'
        : '—') + '</td>' +
      '<td class="slice-cell">' + sliceCell(node.id, inSlice, number, end) + '</td></tr>';
  }

  /* "criterion", "criterion 2", "from", "to", a tick, or a tick and the
     numbers of the slices a node is in, while there are several. */
  function sliceCell(id, inSlice, number, end) {
    var several = table.criteria.length > 1;
    if (end) return '<span class="tick">' + end + '</span>';
    if (number !== -1) return '<span class="tick">criterion' + (several ? ' ' + (number + 1) : '') + '</span>';
    if (!inSlice) return '';
    var positions = several && table.member ? table.member.sliceMember(id) : [];
    return '<span class="tick">✓</span>' + (positions.length ? ' ' + positions.map(function (k) {
      return '<span class="slice-swatch ' + window.QGraph.slices.sliceClass(k) + '">' + (k + 1) + '</span>';
    }).join(' ') : '');
  }

  function spacerRow(height) {
    return height > 0
      ? '<tr class="spacer" aria-hidden="true"><td colspan="7" style="height:' + height + 'px"></td></tr>'
      : '';
  }

  /* Draw the rows the scroll position shows; ``force`` redraws them even when
     the same rows are in view (their state changed). */
  function drawTableRows(force) {
    var body = $('instr-body');
    var count = table.nodes.length;
    if (!count) return;
    var scroller = body.closest('.table-scroll');
    var viewH = scroller ? scroller.clientHeight : 0;
    if (!viewH) {
      // The tab is hidden: nothing to measure, so draw when it is shown.
      table.start = table.end = -1;
      return;
    }
    if (!table.rowH) {
      body.innerHTML = tableRowHtml(table.nodes[0]);
      table.rowH = body.firstChild.getBoundingClientRect().height || 28;
    }
    var headH = body.offsetTop || 0;
    var top = Math.max(0, scroller.scrollTop - headH);
    var start = Math.max(0, Math.floor(top / table.rowH) - TABLE_OVERSCAN);
    var end = Math.min(count, Math.ceil((top + viewH) / table.rowH) + TABLE_OVERSCAN);
    if (!force && start === table.start && end === table.end) return;
    table.start = start;
    table.end = end;
    var html = spacerRow(start * table.rowH);
    for (var i = start; i < end; i++) html += tableRowHtml(table.nodes[i]);
    html += spacerRow((count - end) * table.rowH);
    body.innerHTML = html;
  }

  function wireInstructionTable() {
    var body = $('instr-body');
    var scroller = body.closest('.table-scroll');
    if (scroller) {
      scroller.addEventListener('scroll', function () {
        if (table.frame) return;
        table.frame = requestAnimationFrame(function () {
          table.frame = null;
          drawTableRows(false);
        });
      }, { passive: true });
    }
    function rowNode(event) {
      var row = event.target.closest ? event.target.closest('tr[data-id]') : null;
      return row ? table.nodes[Number(row.getAttribute('data-id'))] : null;
    }
    body.addEventListener('click', function (event) {
      var node = rowNode(event);
      if (!node) return;
      if (event.target.closest('.line-link') && node.source) {
        jumpToLine(node.source.line);
        return;
      }
      pickCriterion(node, event);
    });
    body.addEventListener('mouseover', function (event) {
      var node = rowNode(event);
      if (!node || node === table.hovered) return;
      table.hovered = node;
      hoverSource(node);
    });
    body.addEventListener('mouseleave', function () {
      table.hovered = null;
      hoverSource(null);
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

    var SHOWN = 40;
    setStatus('q' + qubit + ' is touched by ' + touching.length + ' of ' +
      state.payload.nodes.length + ' instructions' +
      (touching.length ? ': #' + touching.slice(0, SHOWN).join(', #') +
        (touching.length > SHOWN ? ', … and ' + (touching.length - SHOWN) + ' more' : '') : '.'));
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
      var suffix = 'qdg';
      if (state.direction === 'chop' && state.chop.from !== null && state.chop.to !== null) {
        suffix = 'chop-' + state.chop.from + '-' + state.chop.to;
      } else if (state.criteria.length) {
        suffix = state.direction + '-slice-' + state.criteria.join('-');
      }
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
    forgetSlice();
    state.selectedQubit = null;
    resetSourceLinks(false);
    graph.clear();
    $('graph-empty').hidden = false;
    renderInstructions(null);
    renderQubitFilter(null);
    renderQubitSlicePicker(null);
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
