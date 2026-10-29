/* ==========================================================================
   QDG graph view.

   Hand-rolled SVG rather than a charting library: the graph is small, the
   layout is fixed (instruction index across, qubit lane down), and this keeps
   the page dependency-free and working offline.

   Supports: pan, zoom, per-node dragging, hover cards, click-to-slice,
   click-a-wire-to-filter, narrowing to a range of instructions, and PNG export.
   ========================================================================== */

(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  var LANE_H = 66;      // vertical distance between qubit wires
  var TOP_PAD = 46;     // room above the first wire
  var LEFT_PAD = 60;    // room for the wire labels
  var NODE_H = 26;
  var NODE_GAP = 18;    // horizontal gap between consecutive instructions
  var MIN_NODE_W = 34;
  var CHAR_W = 6.3;
  var READABLE_SCALE = 0.55; // auto-fit never zooms out past this

  var CATEGORY_FILL = {
    'single-qubit': '#9AA6CF',
    'multi-qubit': '#C6A0F6',
    'measurement': '#A6E3A1',
    'classical': '#F9E2AF',
    'structural': '#6C7086'
  };

  function el(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
    }
    return node;
  }

  function nodeWidth(label) {
    return Math.max(MIN_NODE_W, Math.round(label.length * CHAR_W) + 16);
  }

  function QGraph(svg, options) {
    this.svg = svg;
    this.options = options || {};
    this.data = null;
    this.layout = {};          // id -> {x, y, w, h}
    this.offsets = {};         // id -> {dx, dy} from user dragging
    this.transform = { x: 0, y: 0, k: 1 };
    this.slice = null;         // Set of ids, or null
    this.criterion = null;
    this.selectedQubit = null;
    this.activeDeps = null;    // Set of dependency names, or null for all
    this.range = null;         // [from, to] inclusive, or null
    this.hoveredId = null;
    this.marked = null;        // Set of ids picked from the source, or null
    this.lint = {};            // id -> the lint diagnostics about that node
    this.showLint = true;      // draw lint markers on the nodes

    this.root = el('g', { class: 'graph-root' });
    this.defs = el('defs');
    this.svg.appendChild(this.defs);
    this.svg.appendChild(this.root);
    this.layerEdges = el('g', { class: 'edges' });
    this.layerLanes = el('g', { class: 'lanes' });
    this.layerNodes = el('g', { class: 'nodes' });
    this.root.appendChild(this.layerLanes);
    this.root.appendChild(this.layerEdges);
    this.root.appendChild(this.layerNodes);

    this._installArrowheads();
    this._installPanZoom();
  }

  QGraph.prototype._installArrowheads = function () {
    var self = this;
    Object.keys(window.DEP_COLORS).forEach(function (dep) {
      var marker = el('marker', {
        id: 'arrow-' + dep,
        viewBox: '0 0 10 10',
        refX: '9', refY: '5',
        markerWidth: '5', markerHeight: '5',
        orient: 'auto-start-reverse',
        markerUnits: 'strokeWidth'
      });
      marker.appendChild(el('path', {
        d: 'M 0 0 L 10 5 L 0 10 z',
        fill: window.DEP_COLORS[dep]
      }));
      self.defs.appendChild(marker);
    });
  };

  /* ------------------------------------------------------------ data ---- */

  QGraph.prototype.setData = function (data) {
    this.data = data;
    this.offsets = {};
    this.slice = null;
    this.criterion = null;
    this.selectedQubit = null;
    this.range = null;
    this.marked = null;
    this.lint = lintByNode(data.diagnostics);
    this._computeLayout();
    this.render();
    this.fit(READABLE_SCALE);
  };

  QGraph.prototype.clear = function () {
    this.data = null;
    this.layerEdges.textContent = '';
    this.layerNodes.textContent = '';
    this.layerLanes.textContent = '';
  };

  QGraph.prototype.setSlice = function (ids, criterion) {
    this.slice = ids ? new Set(ids) : null;
    this.criterion = (criterion === undefined) ? this.criterion : criterion;
    this.render();
  };

  /* Nodes that came from the source line the user picked in the editor. */
  QGraph.prototype.setMarked = function (ids) {
    this.marked = ids && ids.length ? new Set(ids) : null;
    this.render();
  };

  /* Lint markers on the nodes the diagnostics are about, or none. */
  QGraph.prototype.setShowLint = function (on) {
    this.showLint = !!on;
    this.render();
  };

  /* Pan so the given nodes are in view, keeping the zoom. */
  QGraph.prototype.reveal = function (ids) {
    var self = this;
    var boxes = (ids || []).map(function (id) { return self._pos(id); }).filter(Boolean);
    if (!boxes.length) return;
    var xs = boxes.map(function (b) { return b.x; });
    var ys = boxes.map(function (b) { return b.y; });
    var cx = (Math.min.apply(null, xs) + Math.max.apply(null, xs)) / 2;
    var cy = (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
    var rect = this.svg.getBoundingClientRect();
    this.transform.x = rect.width / 2 - cx * this.transform.k;
    this.transform.y = rect.height / 2 - cy * this.transform.k;
    this._applyTransform();
  };

  QGraph.prototype.setActiveDeps = function (names) {
    this.activeDeps = names ? new Set(names) : null;
    this.render();
  };

  QGraph.prototype.setRange = function (from, to) {
    this.range = (from === null || to === null) ? null : [from, to];
    this.render();
  };

  QGraph.prototype.setSelectedQubit = function (qubit) {
    this.selectedQubit = qubit;
    this.render();
  };

  QGraph.prototype.resetLayout = function () {
    this.offsets = {};
    this.render();
    this.fit();
  };

  /* Instructions run left to right; each node sits on the average of the qubit
     lanes it touches, the same rule the matplotlib viewer uses. */
  QGraph.prototype._computeLayout = function () {
    var self = this;
    var x = LEFT_PAD;
    this.layout = {};
    this.data.nodes.forEach(function (node) {
      var w = nodeWidth(node.short || node.label);
      self.layout[node.id] = {
        x: x + w / 2,
        y: TOP_PAD + node.lane * LANE_H,
        w: w,
        h: NODE_H
      };
      x += w + NODE_GAP;
    });
    this.contentWidth = x + LEFT_PAD;
    this.contentHeight = TOP_PAD + Math.max(1, this.data.program.num_qubits) * LANE_H;
  };

  QGraph.prototype._pos = function (id) {
    var base = this.layout[id];
    var offset = this.offsets[id];
    if (!offset) return base;
    return { x: base.x + offset.dx, y: base.y + offset.dy, w: base.w, h: base.h };
  };

  QGraph.prototype._inRange = function (id) {
    if (!this.range) return true;
    return id >= this.range[0] && id <= this.range[1];
  };

  QGraph.prototype._depActive = function (dep) {
    return !this.activeDeps || this.activeDeps.has(dep);
  };

  /* ---------------------------------------------------------- render ---- */

  QGraph.prototype.render = function () {
    if (!this.data) return;
    this._renderLanes();
    this._renderEdges();
    this._renderNodes();
    this._applyTransform();
  };

  QGraph.prototype._renderLanes = function () {
    var self = this;
    var group = this.layerLanes;
    group.textContent = '';
    var qubits = this.data.program.num_qubits;

    for (var q = 0; q < qubits; q++) {
      var y = TOP_PAD + q * LANE_H;
      group.appendChild(el('line', {
        class: 'lane-line',
        x1: LEFT_PAD - 30, y1: y, x2: this.contentWidth - LEFT_PAD + 20, y2: y
      }));

      var hit = el('rect', {
        class: 'lane-hit' + (this.selectedQubit === q ? ' selected' : ''),
        x: LEFT_PAD - 40, y: y - LANE_H / 2 + 4,
        width: this.contentWidth, height: LANE_H - 8
      });
      var label = el('text', {
        class: 'lane-label' + (this.selectedQubit === q ? ' selected' : ''),
        x: LEFT_PAD - 38, y: y + 4
      });
      label.textContent = 'q' + q;

      (function (qubit) {
        function pick(event) {
          event.stopPropagation();
          if (self.options.onQubitPick) self.options.onQubitPick(qubit);
        }
        hit.addEventListener('click', pick);
        label.addEventListener('click', pick);
      })(q);

      group.appendChild(hit);
      group.appendChild(label);
    }
  };

  QGraph.prototype._renderEdges = function () {
    var self = this;
    var group = this.layerEdges;
    group.textContent = '';

    /* Count parallel edges so multi-edges between the same pair fan out. */
    var seen = {};
    this.data.edges.forEach(function (edge) {
      if (!self._inRange(edge.source) || !self._inRange(edge.target)) return;

      var pairKey = edge.source + '->' + edge.target;
      seen[pairKey] = (seen[pairKey] || 0) + 1;
      var rank = seen[pairKey] - 1;

      var a = self._pos(edge.source);
      var b = self._pos(edge.target);
      if (!a || !b) return;

      var x1 = a.x + a.w / 2;
      var y1 = a.y;
      var x2 = b.x - b.w / 2;
      var y2 = b.y;
      var bow = (rank % 2 === 0 ? 1 : -1) * (12 + 14 * Math.floor(rank / 2));
      var mx = (x1 + x2) / 2;
      var my = (y1 + y2) / 2 - bow;

      var active = self._depActive(edge.dependency);
      var inSlice = active && self.slice &&
        self.slice.has(edge.source) && self.slice.has(edge.target);

      var classes = ['edge'];
      if (!active) classes.push('dimmed');
      else if (self.slice && !inSlice) classes.push('dimmed');
      if (inSlice) classes.push('in-slice');

      var path = el('path', {
        class: classes.join(' '),
        d: 'M ' + x1 + ' ' + y1 + ' Q ' + mx + ' ' + my + ' ' + x2 + ' ' + y2,
        stroke: window.depColor(edge.dependency),
        'marker-end': 'url(#arrow-' + edge.dependency + ')'
      });
      path.appendChild(el('title')).textContent =
        edge.dependency + '  ' + edge.source + ' → ' + edge.target +
        (edge.rule ? '  (rule: ' + edge.rule + ')' : '');
      group.appendChild(path);
    });
  };

  QGraph.prototype._renderNodes = function () {
    var self = this;
    var group = this.layerNodes;
    group.textContent = '';

    this.data.nodes.forEach(function (node) {
      if (!self._inRange(node.id)) return;
      var pos = self._pos(node.id);

      var classes = ['node', 'cat-' + node.category.replace('-qubit', '')];
      if (self.slice) {
        if (self.slice.has(node.id)) classes.push('in-slice');
        else classes.push('dimmed');
      }
      if (self.criterion === node.id) classes.push('criterion');
      if (self.hoveredId === node.id) classes.push('hovered');
      if (self.marked && self.marked.has(node.id)) classes.push('marked');
      if (self.selectedQubit !== null && node.qubits.indexOf(self.selectedQubit) !== -1) {
        classes.push('qubit-selected');
      }
      var lint = self.showLint ? self.lint[node.id] : null;
      if (lint) classes.push('lint-' + worstSeverity(lint));

      var g = el('g', {
        class: classes.join(' '),
        transform: 'translate(' + pos.x + ',' + pos.y + ')',
        'data-id': String(node.id)
      });

      /* Vertical tick through every wire the instruction touches, so a
         multi-qubit gate reads like a circuit diagram. */
      if (node.qubits.length > 1) {
        var lanes = node.qubits.map(function (q) { return TOP_PAD + q * LANE_H - pos.y; });
        var lo = Math.min.apply(null, lanes);
        var hi = Math.max.apply(null, lanes);
        g.appendChild(el('line', {
          class: 'node-span', x1: 0, y1: lo, x2: 0, y2: hi,
          stroke: CATEGORY_FILL[node.category] || '#9AA6CF',
          'stroke-width': 2, opacity: 0.55
        }));
        lanes.forEach(function (ly) {
          g.appendChild(el('circle', {
            class: 'node-tick', cx: 0, cy: ly, r: 3.2,
            fill: CATEGORY_FILL[node.category] || '#9AA6CF', opacity: 0.8
          }));
        });
      }

      g.appendChild(el('rect', {
        class: 'node-shape',
        x: -pos.w / 2, y: -pos.h / 2,
        width: pos.w, height: pos.h, rx: 6
      }));

      var text = el('text', { class: 'node-text', x: 0, y: 4 });
      text.textContent = node.short || node.label;
      g.appendChild(text);

      var index = el('text', { class: 'node-index', x: 0, y: -pos.h / 2 - 5 });
      index.textContent = '#' + node.id;
      g.appendChild(index);

      // Where the instruction was written (FR-07), when the front end knows.
      if (node.line) {
        var where = el('text', { class: 'node-line', x: 0, y: pos.h / 2 + 11 });
        where.textContent = 'L' + node.line;
        g.appendChild(where);
      }

      // A badge on the corner of a node some lint rule is about.
      if (lint) {
        var severity = worstSeverity(lint);
        var badge = el('g', { class: 'lint-badge sev-' + severity,
                              transform: 'translate(' + (pos.w / 2) + ',' + (-pos.h / 2) + ')' });
        badge.appendChild(el('circle', { r: 6.5 }));
        var mark = el('text', { x: 0, y: 3.2 });
        mark.textContent = severity === 'info' ? 'i' : '!';
        badge.appendChild(mark);
        badge.appendChild(el('title')).textContent = lint.map(function (d) {
          return d.code + ': ' + d.message;
        }).join('\n');
        g.appendChild(badge);
      }

      self._wireNode(g, node);
      group.appendChild(g);
    });
  };

  /* ------------------------------------------------- node interaction --- */

  QGraph.prototype._wireNode = function (g, node) {
    var self = this;
    var dragging = false;
    var moved = false;
    var start = null;

    g.addEventListener('mouseenter', function (event) {
      self.hoveredId = node.id;
      g.classList.add('hovered');
      if (self.options.onHover) self.options.onHover(node, event, self._tooltipHtml(node));
    });

    g.addEventListener('mousemove', function (event) {
      if (!dragging && self.options.onHoverMove) self.options.onHoverMove(event);
    });

    g.addEventListener('mouseleave', function () {
      self.hoveredId = null;
      g.classList.remove('hovered');
      if (self.options.onHoverEnd) self.options.onHoverEnd();
    });

    g.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      event.stopPropagation();
      dragging = true;
      moved = false;
      var offset = self.offsets[node.id] || { dx: 0, dy: 0 };
      start = { x: event.clientX, y: event.clientY, dx: offset.dx, dy: offset.dy };
      g.setPointerCapture(event.pointerId);
    });

    g.addEventListener('pointermove', function (event) {
      if (!dragging) return;
      var dx = (event.clientX - start.x) / self.transform.k;
      var dy = (event.clientY - start.y) / self.transform.k;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
      self.offsets[node.id] = { dx: start.dx + dx, dy: start.dy + dy };
      var pos = self._pos(node.id);
      g.setAttribute('transform', 'translate(' + pos.x + ',' + pos.y + ')');
      self._renderEdges();
    });

    function endDrag(event) {
      if (!dragging) return;
      dragging = false;
      try { g.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
      if (!moved && self.options.onNodePick) self.options.onNodePick(node);
      self.render();
    }

    g.addEventListener('pointerup', endDrag);
    g.addEventListener('pointercancel', endDrag);
  };

  /* "line 4", "lines 8-10", with the calls that led there: "line 3 (via 7)". */
  function describeSource(source) {
    var text = source.end_line > source.line
      ? 'lines ' + source.line + '–' + source.end_line
      : 'line ' + source.line;
    if (source.via && source.via.length) text += ' (via ' + source.via.join(' → ') + ')';
    return text;
  }
  window.describeSource = describeSource;

  QGraph.prototype._tooltipHtml = function (node) {
    var gate = node.gate;
    var parts = [];
    parts.push('<h4>' + escapeHtml(gate.title) + '</h4>');
    parts.push('<p class="tip-sub">#' + node.id + ' &middot; ' + escapeHtml(node.name) +
      (node.qubits.length ? ' on q' + node.qubits.join(', q') : '') + '</p>');
    parts.push('<p>' + math(gate.summary) + '</p>');
    parts.push('<p>' + math(gate.detail) + '</p>');

    var rows = '';
    if (gate.matrix) {
      rows += '<dt>matrix</dt><dd class="tip-matrix">' + mathExpr(gate.matrix) + '</dd>';
    }
    if (node.params.length) rows += '<dt>params</dt><dd>' + escapeHtml(node.params.join(', ')) + '</dd>';
    if (node.clbits.length) rows += '<dt>clbits</dt><dd>' + node.clbits.join(', ') + '</dd>';
    if (node.condition_clbits.length) {
      rows += '<dt>reads</dt><dd>c' + node.condition_clbits.join(', c') + '</dd>';
    }
    if (node.source) rows += '<dt>source</dt><dd>' + escapeHtml(describeSource(node.source)) + '</dd>';
    if (rows) parts.push('<dl>' + rows + '</dl>');

    var flags = [];
    if (gate.entangling) flags.push('<span class="flag flag-ent">entangling</span>');
    if (gate.phase) flags.push('<span class="flag flag-phase">phase</span>');
    if (gate.self_inverse) flags.push('<span class="flag flag-inv">self-inverse</span>');
    if (flags.length) parts.push('<div class="tip-flags">' + flags.join('') + '</div>');

    if (!gate.known) {
      parts.push('<div class="tip-note">No reference entry for this gate yet.</div>');
    }
    var lint = this.showLint ? this.lint[node.id] : null;
    if (lint) {
      parts.push('<div class="tip-lint">' + lint.map(function (d) {
        return '<p class="sev-' + escapeHtml(d.severity) + '"><strong>' + escapeHtml(d.code) +
          '</strong> ' + escapeHtml(d.message) + '</p>';
      }).join('') + '</div>');
    }
    return parts.join('');
  };

  /* id -> diagnostics, from each diagnostic's ``nodes`` (set by the analysis). */
  function lintByNode(diagnostics) {
    var byNode = {};
    (diagnostics || []).forEach(function (d) {
      (d.nodes || []).forEach(function (id) { (byNode[id] = byNode[id] || []).push(d); });
    });
    return byNode;
  }

  var SEVERITY_RANK = { error: 3, warning: 2, info: 1 };

  function worstSeverity(diagnostics) {
    return diagnostics.reduce(function (worst, d) {
      return (SEVERITY_RANK[d.severity] || 0) > (SEVERITY_RANK[worst] || 0) ? d.severity : worst;
    }, 'info');
  }

  /* ------------------------------------------------------- pan / zoom --- */

  QGraph.prototype._installPanZoom = function () {
    var self = this;
    var panning = false;
    var start = null;

    this.svg.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      panning = true;
      start = { x: event.clientX, y: event.clientY, tx: self.transform.x, ty: self.transform.y };
      self.svg.classList.add('panning');
      self.svg.setPointerCapture(event.pointerId);
    });

    this.svg.addEventListener('pointermove', function (event) {
      if (!panning) return;
      self.transform.x = start.tx + (event.clientX - start.x);
      self.transform.y = start.ty + (event.clientY - start.y);
      self._applyTransform();
    });

    function stop(event) {
      panning = false;
      self.svg.classList.remove('panning');
      try { self.svg.releasePointerCapture(event.pointerId); } catch (err) { /* fine */ }
    }
    this.svg.addEventListener('pointerup', stop);
    this.svg.addEventListener('pointercancel', stop);
    this.svg.addEventListener('pointerleave', stop);

    this.svg.addEventListener('wheel', function (event) {
      event.preventDefault();
      var rect = self.svg.getBoundingClientRect();
      var factor = Math.pow(1.0015, -event.deltaY);
      self.zoomAround(event.clientX - rect.left, event.clientY - rect.top, factor);
    }, { passive: false });
  };

  QGraph.prototype.zoomAround = function (px, py, factor) {
    var k = Math.min(4, Math.max(0.15, this.transform.k * factor));
    var ratio = k / this.transform.k;
    this.transform.x = px - (px - this.transform.x) * ratio;
    this.transform.y = py - (py - this.transform.y) * ratio;
    this.transform.k = k;
    this._applyTransform();
  };

  QGraph.prototype.zoomBy = function (factor) {
    var rect = this.svg.getBoundingClientRect();
    this.zoomAround(rect.width / 2, rect.height / 2, factor);
  };

  /* ``minScale`` keeps an auto-fit from shrinking a long circuit into an
     unreadable strip: past that point the view anchors on the left of the graph
     and the user pans. The Fit button passes nothing and really does fit. */
  QGraph.prototype.fit = function (minScale) {
    if (!this.data || !this.data.nodes.length) return;
    var rect = this.svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    var ids = this.data.nodes.map(function (n) { return n.id; }).filter(this._inRange, this);
    if (!ids.length) return;

    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var self = this;
    ids.forEach(function (id) {
      var pos = self._pos(id);
      minX = Math.min(minX, pos.x - pos.w / 2 - 50);
      maxX = Math.max(maxX, pos.x + pos.w / 2 + 20);
      minY = Math.min(minY, pos.y - 40);
      maxY = Math.max(maxY, pos.y + 40);
    });
    minY = Math.min(minY, TOP_PAD - 30);
    maxY = Math.max(maxY, TOP_PAD + (this.data.program.num_qubits - 1) * LANE_H + 30);

    var k = Math.min(rect.width / (maxX - minX), rect.height / (maxY - minY));
    k = Math.min(1.6, Math.max(0.15, k * 0.94));

    var anchorLeft = false;
    if (minScale && k < minScale) {
      k = minScale;
      anchorLeft = true;
    }

    this.transform.k = k;
    this.transform.x = anchorLeft
      ? 16 - minX * k
      : (rect.width - (maxX - minX) * k) / 2 - minX * k;
    this.transform.y = (rect.height - (maxY - minY) * k) / 2 - minY * k;
    this._applyTransform();
  };

  QGraph.prototype._applyTransform = function () {
    this.root.setAttribute('transform',
      'translate(' + this.transform.x + ',' + this.transform.y + ') scale(' + this.transform.k + ')');
  };

  /* ----------------------------------------------------- png download --- */

  /* The live SVG is styled by the page stylesheet and by CSS variables, neither
     of which survives serialization, so the clone gets its own literal
     stylesheet before it is rasterized. */
  var EXPORT_CSS = [
    '.lane-line { stroke: #313244; stroke-width: 1; stroke-dasharray: 3 5; }',
    '.lane-label { fill: #7f849c; font-size: 10px; font-family: monospace; }',
    '.lane-label.selected { fill: #ff6b35; font-weight: 700; }',
    '.lane-hit { fill: transparent; }',
    '.edge { fill: none; stroke-width: 1.4; opacity: 0.55; }',
    '.edge.dimmed { opacity: 0.08; }',
    '.edge.in-slice { opacity: 1; stroke-width: 2.4; }',
    '.node .node-shape { fill: #45475a; stroke: #11111b; stroke-width: 1.5; }',
    '.node .node-text { fill: #11111b; font-size: 11px; font-family: monospace; text-anchor: middle; }',
    '.node .node-index { fill: #7f849c; font-size: 9px; font-family: monospace; text-anchor: middle; }',
    '.node.cat-single .node-shape { fill: #9AA6CF; }',
    '.node.cat-multi .node-shape { fill: #C6A0F6; }',
    '.node.cat-measurement .node-shape { fill: #A6E3A1; }',
    '.node.cat-classical .node-shape { fill: #F9E2AF; }',
    '.node.cat-structural .node-shape { fill: #6C7086; }',
    '.node.in-slice .node-shape { fill: #4a90d9; stroke: #cdd6f4; }',
    '.node.criterion .node-shape { fill: #ff6b35; stroke: #ffffff; stroke-width: 2.5; }',
    '.node.dimmed { opacity: 0.22; }',
    '.node.lint-error .node-shape { stroke: #f38ba8; stroke-width: 2.5; stroke-dasharray: 5 3; }',
    '.node.lint-warning .node-shape { stroke: #f9e2af; stroke-width: 2.5; stroke-dasharray: 5 3; }',
    '.node.lint-info .node-shape { stroke: #89dceb; stroke-width: 2; stroke-dasharray: 5 3; }',
    '.lint-badge circle { stroke: #11111b; stroke-width: 1.5; }',
    '.lint-badge.sev-error circle { fill: #f38ba8; }',
    '.lint-badge.sev-warning circle { fill: #f9e2af; }',
    '.lint-badge.sev-info circle { fill: #89dceb; }',
    '.lint-badge text { fill: #11111b; font-size: 9px; font-weight: 700; font-family: sans-serif; text-anchor: middle; }'
  ].join('\n');

  QGraph.prototype.toPngBlob = function (filename, callback) {
    var rect = this.svg.getBoundingClientRect();
    var width = Math.max(320, Math.round(rect.width));
    var height = Math.max(240, Math.round(rect.height));
    var scale = 2; // retina-ish output

    var clone = this.svg.cloneNode(true);
    clone.setAttribute('xmlns', SVG_NS);
    clone.setAttribute('width', width);
    clone.setAttribute('height', height);
    clone.setAttribute('viewBox', '0 0 ' + width + ' ' + height);

    var style = document.createElementNS(SVG_NS, 'style');
    style.textContent = EXPORT_CSS;
    clone.insertBefore(style, clone.firstChild);

    var background = el('rect', { x: 0, y: 0, width: width, height: height, fill: '#181825' });
    clone.insertBefore(background, style.nextSibling);

    var markup = new XMLSerializer().serializeToString(clone);
    var url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(markup);

    var image = new Image();
    image.onload = function () {
      var canvas = document.createElement('canvas');
      canvas.width = width * scale;
      canvas.height = height * scale;
      var ctx = canvas.getContext('2d');
      ctx.scale(scale, scale);
      ctx.drawImage(image, 0, 0, width, height);
      canvas.toBlob(function (blob) { callback(null, blob); }, 'image/png');
    };
    image.onerror = function () { callback(new Error('Could not rasterize the graph.')); };
    image.src = url;
  };

  /* Prose with $...$ spans, and bare expressions. Both fall back to plain
     escaped text if mathfmt.js has not loaded. */
  function math(value) {
    return window.formatText ? window.formatText(value) : escapeHtml(value);
  }

  function mathExpr(value) {
    return window.formatMath ? window.formatMath(value) : escapeHtml(value);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  QGraph.READABLE_SCALE = READABLE_SCALE;
  window.QGraph = QGraph;
})();
