/* ==========================================================================
   Benchmark page: pick lint rules, run Bugs4Q, show the graded report.
   ========================================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    picker: null,
    report: null,
    filter: 'all',
    busy: false,
    sources: {}        // bug key -> sources, fetched when a row is opened
  };

  document.addEventListener('DOMContentLoaded', function () {
    $('btn-run').addEventListener('click', run);
    document.querySelectorAll('[data-filter]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        document.querySelectorAll('[data-filter]').forEach(function (other) {
          other.classList.toggle('active', other === chip);
        });
        state.filter = chip.dataset.filter;
        renderBugs();
      });
    });

    window.LintRules.load()
      .then(function (catalog) {
        state.picker = window.LintRules.mountPicker($('lint-picker'), catalog, function (selection) {
          if (state.report) {
            setStatus(selection.length ? 'Selection changed: run again to grade it.'
              : 'Select at least one rule.');
          }
        });
        run();
      })
      .catch(function (error) {
        $('lint-picker').innerHTML = '<p class="placeholder">' + escapeHtml(error.message) + '</p>';
      });
  });

  function setStatus(text) { $('bench-status').textContent = text; }

  function run() {
    if (state.busy || !state.picker) return;
    var selection = state.picker.selection();
    if (!selection.length) {
      setStatus('Select at least one rule.');
      return;
    }
    state.busy = true;
    $('btn-run').disabled = true;
    setStatus('Grading ' + selection.length + ' rule' + (selection.length === 1 ? '' : 's') +
      ' on every bug, buggy and fixed…');
    fetch('/api/benchmark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lint_rules: selection, profile: $('profile-select').value })
    })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok || !body.ok) {
            throw new Error((body.error && body.error.message) || 'The benchmark failed.');
          }
          return body;
        });
      })
      .then(function (report) {
        state.report = report;
        render();
        setStatus('Graded ' + report.selection.length + ' rules in ' + report.elapsed + ' s.');
        openFromHash();
      })
      .catch(function (error) { setStatus(error.message); })
      .finally(function () {
        state.busy = false;
        $('btn-run').disabled = false;
      });
  }

  /* ---------------------------------------------------------- render ---- */

  function pct(value) { return value === null || value === undefined ? '–' : Math.round(value * 100) + '%'; }

  function render() {
    $('bench-results').hidden = false;
    renderStats();
    renderCompare();
    renderRules();
    renderBugs();
  }

  function renderStats() {
    var report = state.report;
    var t = report.totals.selection;
    var tiles = [
      ['Detected', t.detected + ' / ' + t.gradable, pct(t.detection_rate) + ' of the bugs'],
      ['Detected, no rewrites', String(t.detected_no_rewrites), 'fixes that keep most of the program'],
      ['Warned on a fault line', String(t.hit), pct(t.hit_rate) + ' of the bugs'],
      ['Warnings, buggy / fixed', t.warnings_buggy + ' / ' + t.warnings_fixed,
        pct(t.warning_precision) + ' of buggy-program warnings on a fault line']
    ];
    if (report.front_end) {
      tiles.push(['Static front end', report.front_end.read + ' / ' + report.front_end.attempted,
        'programs the IR-based linters could read']);
    }
    $('stat-grid').innerHTML = tiles.map(function (tile) {
      return '<div class="stat"><span class="stat-label">' + escapeHtml(tile[0]) + '</span>' +
        '<strong class="stat-value">' + escapeHtml(tile[1]) + '</strong>' +
        '<span class="stat-sub">' + escapeHtml(tile[2]) + '</span></div>';
    }).join('');
  }

  function renderCompare() {
    var report = state.report;
    var html = '<thead><tr><th>Tool</th><th>Detected</th><th>No rewrites</th><th>Hit</th>' +
      '<th>Programs warned</th><th>Warnings buggy / fixed</th><th>On a fault line</th></tr></thead><tbody>';
    report.configs.forEach(function (config) {
      var t = report.totals[config.id];
      var label = escapeHtml(config.label);
      if (config.kind === 'selection') label = '<strong>' + label + '</strong>';
      if (config.kind === 'external') {
        label += ' <span class="tag">recorded' + (config.version ? ' ' + escapeHtml(config.version) : '') + '</span>';
      }
      var sub = config.kind === 'external' ? escapeHtml(config.notes || '')
        : config.rules.length + ' rule' + (config.rules.length === 1 ? '' : 's');
      html += '<tr class="cfg-' + config.kind + '"><td>' + label +
        (sub ? '<span class="cell-sub">' + sub + '</span>' : '') + '</td>' +
        '<td class="num-cell"><strong>' + t.detected + '</strong> / ' + t.gradable +
        ' <span class="cell-sub">' + pct(t.detection_rate) + '</span></td>' +
        '<td class="num-cell">' + t.detected_no_rewrites + '</td>' +
        '<td class="num-cell">' + t.hit + '</td>' +
        '<td class="num-cell">' + t.warned + '</td>' +
        '<td class="num-cell">' + t.warnings_buggy + ' / ' + t.warnings_fixed + '</td>' +
        '<td class="num-cell">' + pct(t.warning_precision) + '</td></tr>';
    });
    $('compare-table').innerHTML = html + '</tbody>';
  }

  function renderRules() {
    var html = '<thead><tr><th>Rule</th><th>Linter</th><th>Fires on buggy</th><th>Fires on fixed</th>' +
      '<th>On a fault line</th><th>Bugs detected</th></tr></thead><tbody>';
    var linterLabels = {};
    state.report.linters.forEach(function (l) { linterLabels[l.id] = l.label; });
    state.report.rules.forEach(function (rule) {
      var quiet = !rule.buggy && !rule.fixed;
      html += '<tr' + (quiet ? ' class="is-quiet"' : '') + '><td><code>' + escapeHtml(rule.code) +
        '</code> ' + escapeHtml(rule.title) + '</td><td>' + escapeHtml(linterLabels[rule.tool] || rule.tool) +
        '</td><td class="num-cell">' + rule.buggy + '<span class="cell-sub">' + rule.programs +
        ' program' + (rule.programs === 1 ? '' : 's') + '</span></td>' +
        '<td class="num-cell">' + rule.fixed + '</td><td class="num-cell">' + rule.hits + '</td>' +
        '<td class="num-cell">' + (rule.detected ? '<strong>' + rule.detected + '</strong>' : '0') + '</td></tr>';
    });
    $('rule-table').innerHTML = html + '</tbody>';
  }

  function verdict(grade) {
    if (!grade.gradable) return ['none', 'no code change'];
    if (grade.detected) return ['detected', 'detected'];
    if (grade.hit) return ['hit', 'on a fault line'];
    if (grade.warned) return ['warned', 'warned elsewhere'];
    return ['missed', 'missed'];
  }

  function keep(grade) {
    switch (state.filter) {
      case 'detected': return grade.detected;
      case 'hit': return grade.hit;
      case 'warned': return grade.warned;
      case 'missed': return grade.gradable && !grade.hit;
      default: return true;
    }
  }

  function renderBugs() {
    var report = state.report;
    var others = report.configs.filter(function (c) { return c.kind !== 'selection'; });
    var html = '<thead><tr><th>Bug</th><th>Type</th><th>Fix</th><th>Selected rules</th>' +
      (others.length ? '<th>By tool</th>' : '') +
      '<th>Warnings buggy / fixed</th><th>Open in slicer</th></tr></thead><tbody>';
    var shown = 0;
    report.bugs.forEach(function (bug) {
      var grade = bug.grades.selection;
      if (!keep(grade)) return;
      shown += 1;
      var v = verdict(grade);
      var byTool = others.map(function (config) {
        var g = verdict(bug.grades[config.id]);
        return '<span class="verdict verdict-' + g[0] + '" title="' + escapeHtml(config.label + ': ' + g[1]) +
          '">' + escapeHtml(shortLabel(config.label)) + '</span>';
      }).join(' ');
      var ref = bug.url ? '<a href="' + escapeHtml(bug.url) + '" target="_blank" rel="noopener">' +
        escapeHtml(bug.reference) + '</a>' : escapeHtml(bug.reference);
      html += '<tr class="bug-row" id="bug-' + escapeHtml(bug.key) + '" data-key="' + escapeHtml(bug.key) + '">' +
        '<td><button type="button" class="row-toggle" aria-expanded="false" data-key="' +
        escapeHtml(bug.key) + '">' + escapeHtml(bug.key) + '</button>' +
        '<span class="cell-sub">' + escapeHtml(bug.source) + ' ' + ref + '</span></td>' +
        '<td>' + escapeHtml(bug.type) + '</td>' +
        '<td><span class="tag tag-' + bug.truth.kind + '">' + escapeHtml(bug.truth.kind) + '</span></td>' +
        '<td><span class="verdict verdict-' + v[0] + '">' + escapeHtml(v[1]) + '</span>' +
        (grade.detected_by.length ? '<span class="cell-sub">' + grade.detected_by.map(escapeHtml).join(', ') + '</span>' : '') +
        '</td>' + (others.length ? '<td class="verdicts">' + byTool + '</td>' : '') +
        '<td class="num-cell">' + grade.warnings.buggy + ' / ' + grade.warnings.fixed + '</td>' +
        '<td class="nowrap"><a href="/slicer?bugs4q=' + encodeURIComponent(bug.key) + '&version=buggy">buggy</a> · ' +
        '<a href="/slicer?bugs4q=' + encodeURIComponent(bug.key) + '&version=fixed">fixed</a></td></tr>';
    });
    if (!shown) {
      html += '<tr><td colspan="7" class="placeholder">No bug matches this filter.</td></tr>';
    }
    $('bug-table').innerHTML = html + '</tbody>';
    $('bug-count').textContent = shown + ' of ' + report.bugs.length;
    $('bug-table').querySelectorAll('.row-toggle').forEach(function (button) {
      button.addEventListener('click', function () { toggleBug(button.dataset.key); });
    });
  }

  function shortLabel(label) {
    return label.replace(/ checks?$/i, '').replace(/\s*\(.*\)$/, '');
  }

  /* ------------------------------------------------------ bug detail ---- */

  function toggleBug(key) {
    var row = document.getElementById('bug-' + key);
    if (!row) return;
    var button = row.querySelector('.row-toggle');
    var next = row.nextElementSibling;
    if (next && next.classList.contains('bug-detail')) {
      next.remove();
      button.setAttribute('aria-expanded', 'false');
      return;
    }
    button.setAttribute('aria-expanded', 'true');
    var detail = document.createElement('tr');
    detail.className = 'bug-detail';
    detail.innerHTML = '<td colspan="7"><p class="placeholder">Loading the programs…</p></td>';
    row.after(detail);
    loadSources(key).then(function (bug) {
      detail.firstChild.innerHTML = renderDetail(key, bug);
      // Start each program at its first fault line or warning.
      detail.querySelectorAll('.source-view').forEach(function (view) {
        var first = view.querySelector('.is-fault, .has-warning');
        if (first) view.scrollTop = Math.max(0, first.offsetTop - 60);
      });
    }).catch(function (error) {
      detail.firstChild.innerHTML = '<p class="placeholder">' + escapeHtml(error.message) + '</p>';
    });
  }

  function loadSources(key) {
    if (state.sources[key]) return Promise.resolve(state.sources[key]);
    return fetch('/api/benchmark/bugs/' + encodeURIComponent(key)).then(function (response) {
      if (!response.ok) throw new Error('Could not load ' + key + '.');
      return response.json();
    }).then(function (bug) {
      state.sources[key] = bug;
      return bug;
    });
  }

  function renderDetail(key, bug) {
    var row = state.report.bugs.filter(function (b) { return b.key === key; })[0];
    var faults = {};
    row.truth.lines.forEach(function (line) { faults[line] = true; });
    var html = '<div class="bug-detail-grid">';
    ['buggy', 'fixed'].forEach(function (version) {
      var warnings = row.warnings[version];
      var byLine = {};
      warnings.forEach(function (w) { (byLine[w.line] = byLine[w.line] || []).push(w); });
      var lines = bug.sources[version].split('\n');
      html += '<div class="bug-source"><h4>' + version + ' <span class="cell-sub">' +
        escapeHtml(row.paths[version]) + '</span></h4><pre class="source-view">';
      lines.forEach(function (text, index) {
        var n = index + 1;
        var cls = (version === 'buggy' && faults[n] ? ' is-fault' : '') + (byLine[n] ? ' has-warning' : '');
        html += '<span class="src-line' + cls + '"><span class="src-no">' + n + '</span>' +
          escapeHtml(text) + '</span>';
        (byLine[n] || []).forEach(function (w) {
          html += '<span class="src-warning sev-' + escapeHtml(w.severity || 'warning') + '">' +
            escapeHtml(w.code) + ' ' + escapeHtml(w.message) + '</span>';
        });
      });
      html += '</pre>';
      var unplaced = warnings.filter(function (w) { return !w.line || w.line > lines.length; });
      if (unplaced.length) {
        html += '<ul class="unplaced">' + unplaced.map(function (w) {
          return '<li><code>' + escapeHtml(w.code) + '</code> ' + escapeHtml(w.message) + '</li>';
        }).join('') + '</ul>';
      }
      var fe = row.front_end && row.front_end[version];
      if (fe) {
        html += '<p class="cell-sub">Static front end: ' + (fe.ok ? 'read, ' + fe.instructions + ' instructions'
          : 'could not read it (' + escapeHtml(fe.error) + ')') + '</p>';
      }
      html += '</div>';
    });
    html += '</div><p class="cell-sub">Highlighted lines are fault lines: what the fix changed' +
      (row.truth.kind === 'omission' ? ' (here, the lines around the code the fix adds)' : '') + '.</p>';
    return html;
  }

  function openFromHash() {
    var match = /^#bug-(.+)$/.exec(window.location.hash);
    if (!match) return;
    var key = decodeURIComponent(match[1]);
    var row = document.getElementById('bug-' + key);
    if (!row) return;
    toggleBug(key);
    row.scrollIntoView({ block: 'center' });
  }

  function escapeHtml(text) {
    return String(text === undefined || text === null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
})();
