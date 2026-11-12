/* ==========================================================================
   QDG graph view.

   Hand-rolled SVG rather than a charting library: the layout is fixed
   (instruction index across, qubit lane down), and this keeps the page
   dependency-free and working offline.

   Supports: pan, zoom, per-node dragging, hover cards, click-to-slice,
   click-a-wire-to-filter, narrowing to a range of instructions, an overview
   strip of the whole circuit, and PNG export.

   Large circuits
   --------------
   A circuit can have tens of thousands of instructions, and the graph is one
   long strip of them: at the zoom limit only a few hundred are ever on screen.
   So only what is in view (plus half a view on every side) exists in the
   DOM. Panning moves one transform; when the view leaves the drawn window,
   nodes and edges that came into it are created and the ones that left are
   removed. A slice, a mark, a filter or a hover changes classes on the
   elements that exist -- nothing is rebuilt -- and elements created later get
   the current classes. Events are handled once, on the node layer.
   ========================================================================== */

(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  /* The desktop viewer (qslicer viz) draws the same layout: qslicer/viz/model.py
     copies these numbers and the category colors, so change them in both. */
  var LANE_H = 66;      // vertical distance between qubit wires
  var TOP_PAD = 46;     // room above the first wire
  var LEFT_PAD = 60;    // room for the wire labels
  var NODE_H = 26;
  var NODE_GAP = 18;    // horizontal gap between consecutive instructions
  var MIN_NODE_W = 34;
  var CHAR_W = 6.3;
  var READABLE_SCALE = 0.55; // auto-fit never zooms out past this
  var MIN_SCALE = 0.15;      // nor can the user: the overview strip shows the rest
  var MAX_SCALE = 4;
  var CL_GAP = 58;      // last qubit wire to the first classical wire
  var CL_H = 22;        // vertical distance between classical wires
  var CL_SHIFT = 12;    // MEASUREMENT legs run left of a node's line label, DATA_CLASSICAL right
  var CL_CORNER = 6;    // corner radius of a routed classical edge
  var OVERSCAN = 0.5;   // drawn beyond the view on each side, as a fraction of the view
  var EDGE_BOW = 70;    // how far a curved edge can stray above or below its ends
  var LOD_LABELS = 0.4; // below this zoom the index and line labels are hidden
  var LOD_TEXT = 0.25;  // below this the gate names too: they are unreadable by then

  /* Several backward slices at once: each gets one of these colors and a
     number, cycling after eight. style.css (.sl-0 ... .sl-7) has the same
     colors in the same order, so change both. */
  var SLICE_COLORS = ['#4e9fe6', '#f28e2b', '#59c26b', '#e15759',
                      '#b07aa1', '#edc948', '#76b7b2', '#ff9da7'];
  var MAX_STRIPES = 4;  // a node in more slices than this shows "+n" for the rest
  var STRIPE_H = 2;
  // Mirror links: arcs away from the crowded middle of the graph (above the nodes
  // on the top half of the qubits, below them on the bottom half), further out
  // for an outer pair, never past the cap.
  var MIRROR_RISE = 20;
  var MIRROR_STEP = 12;
  var MIRROR_CAP = 68;

  /* Edges whose dependency travels through a classical bit: drawn down to the
     bit's wire, along it, and up into the reader, as a circuit diagram would. */
  var CLASSICAL_DEPS = { MEASUREMENT: -1, DATA_CLASSICAL: 1 };

  var CATEGORY_FILL = {
    'single-qubit': '#9AA6CF',
    'multi-qubit': '#C6A0F6',
    'measurement': '#A6E3A1',
    'classical': '#F9E2AF',
    'structural': '#6C7086'
  };
  // The overview strip draws in the same colors as the graph's CSS.
  var OVERVIEW = {
    background: 'rgba(24, 24, 37, 0.9)',
    slice: '#4a90d9',
    criterion: '#ff6b35',
    marked: '#89b4fa',
    view: 'rgba(137, 180, 250, 0.22)',
    viewEdge: '#89b4fa',
    // Lint severities, as the node outlines and badges draw them.
    lint: { error: '#f38ba8', warning: '#f9e2af', info: '#89dceb' }
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

  /* ------------------------------------------- several slices at once --- */

  /* id -> the positions (0, 1, ...) of the slices it is in, ascending, for
     slices given as [{node_idx, slice}]. Built once per response, so drawing a
     node only looks itself up. */
  function sliceMembership(slices) {
    var member = new Map();
    (slices || []).forEach(function (entry, k) {
      (entry.slice || []).forEach(function (id) {
        var list = member.get(id);
        if (!list) member.set(id, [k]);
        else if (list[list.length - 1] !== k) list.push(k);
      });
    });
    return member;
  }

  function sliceClass(k) { return 'sl-' + (k % SLICE_COLORS.length); }
  function sliceColor(k) { return SLICE_COLORS[k % SLICE_COLORS.length]; }

  /* The stripes a node in several slices shows: the first ``max`` slices, and
     how many more there are. */
  function stripesFor(positions, max) {
    var cap = max || MAX_STRIPES;
    return { shown: positions.slice(0, cap), more: Math.max(0, positions.length - cap) };
  }

  /* --------------------------------------------------- mirror links ---- */

  /* id -> indices of the mirrored pairs ([first, second, depth]) it is an end of. */
  function mirrorIndex(pairs) {
    var byNode = new Map();
    (pairs || []).forEach(function (pair, index) {
      [pair[0], pair[1]].forEach(function (id) {
        var list = byNode.get(id);
        if (list) list.push(index);
        else byNode.set(id, [index]);
      });
    });
    return byNode;
  }

  /* The arc between two node boxes: from the inner top corner of each (clear
     of the index label), rising higher the deeper the pair is nested, capped.
     With ``down`` it leaves the bottom corners and hangs below instead. Both
     control points sit 4/3 of the rise out, so a level arc peaks exactly
     ``rise`` from its ends. Returns the path and its content-space box. */
  function mirrorArc(a, b, depth, down) {
    var left = a.x <= b.x ? a : b;
    var right = left === a ? b : a;
    var x1 = left.x + left.w / 2 - 4;
    var x2 = right.x - right.w / 2 + 4;
    if (x2 < x1) { x1 = left.x; x2 = right.x; }   // dragged on top of each other
    var side = down ? 1 : -1;
    var y1 = left.y + side * (left.h / 2 + 2);
    var y2 = right.y + side * (right.h / 2 + 2);
    var rise = Math.min(MIRROR_CAP, MIRROR_RISE + MIRROR_STEP * Math.max(0, depth || 0));
    var far = down ? Math.max(y1, y2) : Math.min(y1, y2);
    var cy = far + side * rise * 4 / 3;
    var peak = far + side * (rise + 2);
    return {
      d: 'M ' + x1 + ' ' + y1 + ' C ' + x1 + ' ' + cy + ' ' + x2 + ' ' + cy + ' ' + x2 + ' ' + y2,
      curve: [[x1, y1], [x1, cy], [x2, cy], [x2, y2]],
      rise: rise,
      box: { x0: x1 - 2, x1: x2 + 2,
             y0: Math.min(y1, y2, peak), y1: Math.max(y1, y2, peak) }
    };
  }

  /* Whether a pair's arc hangs below its nodes: when the pair sits on the bottom
     half of the qubit lanes. ``middle`` is the y halfway down the lanes; a pair
     exactly on it (the middle wire of an odd number) still arcs up. */
  function mirrorHangs(a, b, middle) {
    return (a.y + b.y) / 2 > middle;
  }

  /* ------------------------------------------------- insight wording ---- */

  function qubitList(qubits) { return 'q' + qubits.join(', q'); }

  function phaseSentence(multi) {
    return 'Phase only: this gate cannot change a measurement outcome until a ' +
      'non-diagonal gate follows on this wire.' + (multi ? ' It can still entangle.' : '');
  }

  function kickbackSentence(controls) {
    return 'Phase kickback: the target is left as it is and the phase lands on control' +
      (controls.length === 1 ? ' ' : 's ') + qubitList(controls) + '.';
  }

  function predeterminedSentence(value) {
    return 'This measurement always gives ' + value + ': the qubit is provably |' + value + '> here.';
  }

  function noEffectSentence(reason) {
    return 'No effect here: ' + String(reason).replace(/[.\s]+$/, '') + '.';
  }

  /* What a measurement's correlations entry says, as sentences. */
  function correlationSentences(entry) {
    var out = [];
    if (!entry) return out;
    var measurements = entry.measurements || [];
    var qubits = entry.qubits || [];
    var unmeasured = entry.unmeasured || [];
    if (measurements.length && qubits.length) {
      out.push('May be correlated with the measurement' + (measurements.length === 1 ? '' : 's') +
        ' of ' + qubitList(qubits) + '.');
    }
    if (unmeasured.length) out.push('May still be entangled with unmeasured ' + qubitList(unmeasured) + '.');
    return out;
  }

  /* The response's insights with every key present, so lookups need no guards. */
  function normalizeInsights(found) {
    var source = found || {};
    var out = {};
    ['measurements', 'kickback', 'no_effect', 'predetermined', 'correlations', 'guards'].forEach(function (key) {
      out[key] = source[key] || {};
    });
    out.mirrors = source.mirrors || [];
    return out;
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
    this.markedQubits = null;  // Set of wires a clicked lint finding is about, or null
    this.lint = {};            // id -> the lint diagnostics about that node
    this.lintQubits = {};      // wire -> the lint diagnostics about that wire (QD07)
    this.showLint = true;      // draw lint markers on the nodes
    this.criteria = null;      // several backward criteria, in order, or null
    this.member = null;        // with them: id -> the slice positions it is in
    this.chop = null;          // {from, to} ends of a chop (either may be null), or null
    this.laneMarks = { correlated: new Set(), unmeasured: new Set() };
    this.insights = normalizeInsights(null);
    this.mirrorPairs = [];     // [first, second, depth], from the insights
    this.mirrorsByNode = new Map();
    this.showMirrors = false;  // every mirror link in view, not just the focused ones
    this.mirrorsInWindow = []; // with showMirrors: pair indices overlapping the window
    this.hoverMirrors = null;  // pair indices of the hovered node
    this.mirrorPartners = null; // Set of ids at the other end of them

    this.edgeMeta = [];        // per edge: ends, dependency, parallel rank, classical bit
    this.edgesByNode = {};     // id -> indices into edgeMeta
    this.routed = {};          // "id:clbit" -> true for every bit an edge runs along
    this.mountedNodes = new Map();  // id -> <g>, the nodes that exist in the DOM
    this.mountedTicks = new Map();  // id -> [<g>], their classical-bit ticks
    this.mountedEdges = new Map();  // edge index -> <path>
    this.mountedMirrors = new Map(); // mirror pair index -> <path>
    this.window = null;       // the content-space box currently drawn
    this.viewW = 0;            // size of the svg, cached: reading it forces a layout
    this.viewH = 0;
    this.lod = '';
    this._drag = null;

    this.root = el('g', { class: 'graph-root' });
    this.defs = el('defs');
    this.svg.appendChild(this.defs);
    this.svg.appendChild(this.root);
    this.layerEdges = el('g', { class: 'edges' });
    this.layerLanes = el('g', { class: 'lanes' });
    this.layerMirrors = el('g', { class: 'mirrors' });
    this.layerNodes = el('g', { class: 'nodes' });
    this.root.appendChild(this.layerLanes);
    this.root.appendChild(this.layerEdges);
    this.root.appendChild(this.layerNodes);
    // Over the nodes: a gate between the pair (a cx between two lanes) would hide the arc.
    this.root.appendChild(this.layerMirrors);

    this._installArrowheads();
    this._installPanZoom();
    this._installNodeEvents();
    this._installOverview();
    this._watchSize();
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
    this.markedQubits = null;
    this.hoveredId = null;
    this.criteria = null;
    this.member = null;
    this.chop = null;
    this.laneMarks = { correlated: new Set(), unmeasured: new Set() };
    this.lint = lintByNode(data.diagnostics);
    this.lintQubits = lintByQubit(data.diagnostics);
    this._computeLayout();
    this.insights = normalizeInsights(data.insights);
    var byId = this.byId;
    this.mirrorPairs = this.insights.mirrors.filter(function (pair) {
      return byId[pair[0]] && byId[pair[1]];
    });
    this.mirrorsByNode = mirrorIndex(this.mirrorPairs);
    this.mirrorsInWindow = [];
    this.hoverMirrors = null;
    this.mirrorPartners = null;
    this.edgeMeta = prepareEdges(data.edges, this.byId);
    this.edgesByNode = edgesByNode(this.edgeMeta);
    this._computeRouted();
    this._clearMounted();
    this._renderLanes();
    this._overviewDirty = true;
    this.fit(READABLE_SCALE);
    if (!this.window) this._afterViewChange(true);
  };

  QGraph.prototype.clear = function () {
    this.data = null;
    this._clearMounted();
    this.layerLanes.textContent = '';
    this.edgeMeta = [];
    this.edgesByNode = {};
    this.mirrorPairs = [];
    this.mirrorsByNode = new Map();
    this.mirrorsInWindow = [];
    this.hoverMirrors = null;
    this.mirrorPartners = null;
    this._showOverview(false);
  };

  QGraph.prototype.setSlice = function (ids, criterion) {
    this.slice = ids ? new Set(ids) : null;
    this.criterion = (criterion === undefined) ? this.criterion : criterion;
    this.criteria = null;
    this.member = null;
    this._sliceChanged();
  };

  /* Several backward slices at once, as the response's ``slices`` gives them
     ([{node_idx, slice}], in order): each its own color and number. */
  QGraph.prototype.setSlices = function (slices) {
    var union = new Set();
    (slices || []).forEach(function (entry) {
      (entry.slice || []).forEach(function (id) { union.add(id); });
    });
    this.slice = union;
    this.criterion = null;
    this.criteria = (slices || []).map(function (entry) { return entry.node_idx; });
    this.member = sliceMembership(slices);
    this._sliceChanged();
  };

  /* The two ends of a chop, either still unpicked (null); or null for none. */
  QGraph.prototype.setChop = function (from, to) {
    var none = (from === null || from === undefined) && (to === null || to === undefined);
    var next = none ? null : { from: from === undefined ? null : from, to: to === undefined ? null : to };
    var was = this.chop;
    // Every slice clears the chop first; with none to clear, nothing changed
    // and nothing (the overview's image least of all) needs drawing again.
    if (was === next || (was && next && was.from === next.from && was.to === next.to)) return;
    this.chop = next;
    this._sliceChanged();
  };

  /* Wire labels for what the slice criterion may be correlated with: qubits
     whose measurements it may agree with, and qubits never measured that it may
     still be entangled with. */
  QGraph.prototype.setLaneMarks = function (correlated, unmeasured) {
    this.laneMarks = { correlated: new Set(correlated || []), unmeasured: new Set(unmeasured || []) };
    this._renderLanes();
  };

  /* Every mirror link in view, or only those of the hovered node and the criteria. */
  QGraph.prototype.setShowMirrors = function (on) {
    this.showMirrors = !!on;
    if (this.window) this._scanMirrors();
    this._syncMirrors();
  };

  /* The positions (0, 1, ...) of the slices ``id`` is in, while there are several. */
  QGraph.prototype.sliceMember = function (id) {
    return this.member ? (this.member.get(id) || []) : [];
  };

  QGraph.prototype._sliceChanged = function () {
    if (this.sliceOnly && this.data) { this._relayout(); return; }
    this._refreshClasses();
    this._refreshDecorations();
    this._syncMirrors();
    this._overviewChanged();
  };

  /* Show only the slice: everything outside it, and every wire it does not
     touch, is left out and the rest closes up. With no slice, all of it shows. */
  QGraph.prototype.setSliceOnly = function (on) {
    on = !!on;
    if (on === !!this.sliceOnly) return;
    this.sliceOnly = on;
    if (this.data) this._relayout();
  };

  /* Lay everything out again and keep the criterion (else the slice's start)
     where the eye is. */
  QGraph.prototype._relayout = function () {
    this._computeLayout();
    this._computeRouted();
    this._overviewDirty = true;
    this.render();
    var anchor = this.criterion;
    if (anchor === null || anchor === undefined) {
      anchor = this.criteria && this.criteria.length ? this.criteria[0]
        : (this.chop && this.chop.from !== null ? this.chop.from : null);
    }
    if (anchor !== null && anchor !== undefined && this._inRange(anchor)) this.reveal([anchor]);
    else if (this.slice && this.sliceOnly) this.fit(READABLE_SCALE);
  };

  /* Whether a node is drawn (in the range, and in the slice when only it shows). */
  QGraph.prototype.isShown = function (id) {
    return !!(this.data && this.byId[id] && this._inRange(id));
  };

  /* Bring one node to the middle of the view and ring it for a moment. */
  QGraph.prototype.jumpTo = function (id) {
    if (!this.isShown(id)) return false;
    if (this.transform.k < READABLE_SCALE) this.transform.k = READABLE_SCALE;
    this.reveal([id]);
    var g = this.mountedNodes.get(id);
    if (g) {
      g.classList.remove('jumped');
      g.getBoundingClientRect();          // restart the animation
      g.classList.add('jumped');
      setTimeout(function () { g.classList.remove('jumped'); }, 1600);
    }
    return true;
  };

  /* Nodes that came from the source line the user picked in the editor, and
     wires a lint finding is about (an idle qubit has no node to mark). */
  QGraph.prototype.setMarked = function (ids, qubits) {
    this.marked = ids && ids.length ? new Set(ids) : null;
    this.markedQubits = qubits && qubits.length ? new Set(qubits) : null;
    this._renderLanes();
    this._refreshClasses();
    this._overviewChanged();
  };

  /* Lint markers on the nodes the diagnostics are about, or none. */
  QGraph.prototype.setShowLint = function (on) {
    this.showLint = !!on;
    this._overviewDirty = true;
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
    var view = this._view();
    this.transform.x = view.width / 2 - cx * this.transform.k;
    this.transform.y = view.height / 2 - cy * this.transform.k;
    this._applyTransform();
  };

  QGraph.prototype.setActiveDeps = function (names) {
    this.activeDeps = names ? new Set(names) : null;
    this._refreshClasses();
  };

  QGraph.prototype.setRange = function (from, to) {
    this.range = (from === null || to === null) ? null : [from, to];
    this._computeRouted();
    this._overviewDirty = true;
    this.render();
  };

  QGraph.prototype.setSelectedQubit = function (qubit) {
    this.selectedQubit = qubit;
    this._renderLanes();
    this._refreshClasses();
  };

  QGraph.prototype.resetLayout = function () {
    this.offsets = {};
    this._overviewDirty = true;
    this.render();
    this.fit();
  };

  /* Instructions run left to right; each node sits on the average of the qubit
     lanes it touches, the same rule the desktop viewer uses. An instruction
     on no qubit at all (a store) sits on the classical wires it uses instead. */
  QGraph.prototype._computeLayout = function () {
    var self = this;
    var x = LEFT_PAD;
    this.layout = {};
    this.byId = {};
    this.laneRow = this._sliceLanes();
    this.data.nodes.forEach(function (node) {
      self.byId[node.id] = node;
      // Showing only the slice: the rest take no room, so the slice closes up.
      if (self._outsideSlice(node.id)) {
        self.layout[node.id] = { x: x, y: TOP_PAD, w: 0, h: NODE_H };
        return;
      }
      var w = nodeWidth(node.short || node.label);
      var bits = classicalOnly(node);
      var y;
      if (bits.length) {
        y = bits.reduce(function (sum, c) { return sum + self._clbitY(c); }, 0) / bits.length;
      } else if (self.laneRow) {
        var on = node.qubits || [];
        y = on.reduce(function (sum, q) { return sum + self._laneY(q); }, 0) / Math.max(1, on.length);
      } else {
        y = TOP_PAD + node.lane * LANE_H;
      }
      self.layout[node.id] = { x: x + w / 2, y: y, w: w, h: NODE_H };
      x += w + NODE_GAP;
    });
    this.contentWidth = x + LEFT_PAD;
    var clbits = this.data.program.num_clbits || 0;
    this.contentHeight = clbits
      ? this._clbitY(clbits - 1) + CL_H
      : TOP_PAD + this._laneCount() * LANE_H;
  };

  /* Showing only the slice: the row of each qubit a slice node touches, in
     qubit order, so wires the slice leaves out close up too. Otherwise null,
     and every qubit has its own row. */
  QGraph.prototype._sliceLanes = function () {
    if (!this.sliceOnly || !this.slice) return null;
    var self = this;
    var used = new Set();
    this.data.nodes.forEach(function (node) {
      if (self.slice.has(node.id)) (node.qubits || []).forEach(function (q) { used.add(q); });
    });
    var rows = new Map();
    Array.from(used).sort(function (a, b) { return a - b; })
      .forEach(function (q, row) { rows.set(q, row); });
    return rows;
  };

  QGraph.prototype._laneCount = function () {
    return Math.max(1, this.laneRow ? this.laneRow.size : this.data.program.num_qubits);
  };

  /* The y of a qubit's wire; a wire that is not shown sits on the first row. */
  QGraph.prototype._laneY = function (qubit) {
    var row = this.laneRow ? (this.laneRow.get(qubit) || 0) : qubit;
    return TOP_PAD + row * LANE_H;
  };

  QGraph.prototype._laneShown = function (qubit) {
    return !this.laneRow || this.laneRow.has(qubit);
  };

  QGraph.prototype._outsideSlice = function (id) {
    return !!(this.sliceOnly && this.slice && !this.slice.has(id));
  };

  /* The classical wires sit below the last qubit wire, one per clbit. */
  QGraph.prototype._clbitY = function (clbit) {
    return TOP_PAD + (this._laneCount() - 1) * LANE_H + CL_GAP + clbit * CL_H;
  };

  /* The clbits of an instruction that touches no qubit, else none. */
  function classicalOnly(node) {
    if ((node.qubits || []).length) return [];
    var bits = (node.clbits || []).concat(node.condition_clbits || []);
    return bits.filter(function (c, i) { return bits.indexOf(c) === i; });
  }

  QGraph.prototype._pos = function (id) {
    var base = this.layout[id];
    var offset = this.offsets[id];
    if (!offset) return base;
    return { x: base.x + offset.dx, y: base.y + offset.dy, w: base.w, h: base.h };
  };

  /* Whether a node is drawn at all: inside the range, and inside the slice
     while only the slice is shown. */
  QGraph.prototype._inRange = function (id) {
    if (this._outsideSlice(id)) return false;
    if (!this.range) return true;
    return id >= this.range[0] && id <= this.range[1];
  };

  QGraph.prototype._depActive = function (dep) {
    return !this.activeDeps || this.activeDeps.has(dep);
  };

  /* Per edge, what drawing it needs beyond its two ends: its rank among the
     parallel edges of the same pair (they fan out), and for an edge carried by
     a classical bit, which bit -- the k-th parallel edge of one type runs along
     the k-th bit both ends share. Worked out once per graph, in edge order. */
  function prepareEdges(edges, byId) {
    var seen = {};
    return (edges || []).map(function (edge) {
      var pairKey = edge.source + '->' + edge.target;
      seen[pairKey] = (seen[pairKey] || 0) + 1;
      var clbit = null;
      var side = CLASSICAL_DEPS[edge.dependency];
      if (side !== undefined) {
        var depKey = pairKey + ':' + edge.dependency;
        seen[depKey] = (seen[depKey] || 0) + 1;
        var shared = sharedClbits(byId[edge.source], byId[edge.target]);
        if (shared.length) clbit = shared[(seen[depKey] - 1) % shared.length];
      }
      return {
        source: edge.source, target: edge.target, dependency: edge.dependency,
        rule: edge.rule, rank: seen[pairKey] - 1, clbit: clbit,
        side: side === undefined ? 0 : side
      };
    });
  }

  function edgesByNode(meta) {
    var out = {};
    meta.forEach(function (edge, index) {
      (out[edge.source] = out[edge.source] || []).push(index);
      if (edge.target !== edge.source) (out[edge.target] = out[edge.target] || []).push(index);
    });
    return out;
  }

  /* Every bit an edge in range runs along, so a write or read it accounts for
     gets no tick of its own. */
  QGraph.prototype._computeRouted = function () {
    var self = this;
    this.routed = {};
    this.edgeMeta.forEach(function (edge) {
      if (edge.clbit === null || !self._inRange(edge.source) || !self._inRange(edge.target)) return;
      self.routed[edge.source + ':' + edge.clbit] = self.routed[edge.target + ':' + edge.clbit] = true;
    });
  };

  /* ------------------------------------------------- what is in view ---- */

  QGraph.prototype._watchSize = function () {
    var self = this;
    this._measure();
    if (window.ResizeObserver) {
      new window.ResizeObserver(function () {
        var before = self.viewW + 'x' + self.viewH;
        self._measure();
        if (before === self.viewW + 'x' + self.viewH) return;
        self._overviewDirty = true;
        self._afterViewChange(true);
      }).observe(this.svg);
    }
  };

  QGraph.prototype._measure = function () {
    var rect = this.svg.getBoundingClientRect();
    this.viewW = rect.width;
    this.viewH = rect.height;
  };

  /* The svg's size, measured again only when the cache says it has none. */
  QGraph.prototype._view = function () {
    if (!this.viewW || !this.viewH) this._measure();
    return { width: this.viewW, height: this.viewH };
  };

  /* The part of the content the view shows, in content coordinates. */
  QGraph.prototype._viewBox = function () {
    var t = this.transform;
    var view = this._view();
    return { x0: -t.x / t.k, y0: -t.y / t.k,
             x1: (view.width - t.x) / t.k, y1: (view.height - t.y) / t.k };
  };

  function contains(outer, inner) {
    return inner.x0 >= outer.x0 && inner.x1 <= outer.x1 &&
           inner.y0 >= outer.y0 && inner.y1 <= outer.y1;
  }

  function overlaps(box, win) {
    return box.x1 >= win.x0 && box.x0 <= win.x1 && box.y1 >= win.y0 && box.y0 <= win.y1;
  }

  /* The window to draw for a view: the view plus OVERSCAN of it on each side,
     so a short pan only moves the transform. */
  function drawWindow(view, overscan) {
    var mx = (view.x1 - view.x0) * overscan;
    var my = (view.y1 - view.y0) * overscan;
    return { x0: view.x0 - mx, y0: view.y0 - my, x1: view.x1 + mx, y1: view.y1 + my };
  }

  /* A node's extent: its box, the wires its span reaches, and the classical
     wires its ticks drop to. */
  QGraph.prototype._nodeBox = function (node) {
    var pos = this._pos(node.id);
    var y0 = pos.y - pos.h / 2 - 16;
    var y1 = pos.y + pos.h / 2 + 16;
    var qubits = node.qubits || [];
    for (var i = 0; i < qubits.length; i++) {
      var ly = this._laneY(qubits[i]);
      if (ly < y0) y0 = ly;
      if (ly > y1) y1 = ly;
    }
    var bits = (node.clbits || []).concat(node.condition_clbits || []);
    for (var j = 0; j < bits.length; j++) {
      var cy = this._clbitY(bits[j]);
      if (cy > y1) y1 = cy + 4;
    }
    return { x0: pos.x - pos.w / 2 - 8, x1: pos.x + pos.w / 2 + 8, y0: y0, y1: y1 };
  };

  QGraph.prototype._edgeBox = function (edge) {
    var a = this._pos(edge.source);
    var b = this._pos(edge.target);
    var box = {
      x0: Math.min(a.x - a.w / 2, b.x - b.w / 2) - CL_SHIFT - 4,
      x1: Math.max(a.x + a.w / 2, b.x + b.w / 2) + CL_SHIFT + 4,
      y0: Math.min(a.y, b.y) - EDGE_BOW,
      y1: Math.max(a.y, b.y) + EDGE_BOW
    };
    if (edge.clbit !== null) box.y1 = Math.max(box.y1, this._clbitY(edge.clbit) + 6);
    return box;
  };

  /* After any change to the transform or the view's size: the label level,
     the drawn window (only when the view has left it), the overview. */
  QGraph.prototype._afterViewChange = function (force) {
    if (!this.data) return;
    this._applyLod();
    var view = this._viewBox();
    if (force || !this.window || !contains(this.window, view)) this._cull(view);
    this._drawOverview();
  };

  QGraph.prototype._applyLod = function () {
    var k = this.transform.k;
    var lod = k < LOD_TEXT ? ' lod-far' : (k < LOD_LABELS ? ' lod-mid' : '');
    if (lod !== this.lod) {
      this.lod = lod;
      this.root.setAttribute('class', 'graph-root' + lod);
    }
  };

  /* Make the DOM hold exactly what overlaps the window around ``view``. */
  QGraph.prototype._cull = function (view) {
    var self = this;
    var win = drawWindow(view, OVERSCAN);
    this.window = win;

    var wantNodes = new Set();
    this.data.nodes.forEach(function (node) {
      if (self._inRange(node.id) && overlaps(self._nodeBox(node), win)) wantNodes.add(node.id);
    });
    this.mountedNodes.forEach(function (g, id) {
      if (!wantNodes.has(id)) self._unmountNode(id);
    });
    wantNodes.forEach(function (id) {
      if (!self.mountedNodes.has(id)) self._mountNode(self.byId[id]);
    });

    var wantEdges = new Set();
    this.edgeMeta.forEach(function (edge, index) {
      if (!self._inRange(edge.source) || !self._inRange(edge.target)) return;
      if (overlaps(self._edgeBox(edge), win)) wantEdges.add(index);
    });
    this.mountedEdges.forEach(function (path, index) {
      if (!wantEdges.has(index)) {
        path.remove();
        self.mountedEdges.delete(index);
      }
    });
    wantEdges.forEach(function (index) {
      var path = self.mountedEdges.get(index);
      // A routed edge kept from the last window is cut to this one.
      if (path && self.edgeMeta[index].clbit !== null) self._setEdgePath(path, self.edgeMeta[index]);
      if (!path) self._mountEdge(index);
    });

    this._scanMirrors();
    this.mountedMirrors.forEach(function (path, index) {
      path.setAttribute('d', self._mirrorArc(index).d);
    });
    this._syncMirrors();
  };

  QGraph.prototype._clearMounted = function () {
    this.layerEdges.textContent = '';
    this.layerMirrors.textContent = '';
    this.layerNodes.textContent = '';
    this.mountedNodes.clear();
    this.mountedTicks.clear();
    this.mountedEdges.clear();
    this.mountedMirrors.clear();
    this.window = null;
  };

  /* ---------------------------------------------------- mirror links ---- */

  QGraph.prototype._mirrorArc = function (index) {
    var pair = this.mirrorPairs[index];
    var a = this._pos(pair[0]);
    var b = this._pos(pair[1]);
    var middle = TOP_PAD + (this._laneCount() - 1) * LANE_H / 2;
    var arc = mirrorArc(a, b, pair[2], mirrorHangs(a, b, middle));
    // Dashed, so only the part in the drawn window (see clipRoute).
    var cut = this.window && arc.curve[0][0] < arc.curve[3][0]
      ? clipCubic(arc.curve, this.window.x0, this.window.x1) : null;
    if (cut) arc.d = cut;
    return arc;
  };

  QGraph.prototype._mirrorDrawable = function (index) {
    var pair = this.mirrorPairs[index];
    return this.window && this._inRange(pair[0]) && this._inRange(pair[1]) &&
      overlaps(this._mirrorArc(index).box, this.window);
  };

  /* With the Mirrors chip on: the pairs that reach into the drawn window, found
     with the same linear scan as nodes and edges, only when that window moves. */
  QGraph.prototype._scanMirrors = function () {
    this.mirrorsInWindow = [];
    if (!this.showMirrors) return;
    for (var i = 0; i < this.mirrorPairs.length; i++) {
      if (this._mirrorDrawable(i)) this.mirrorsInWindow.push(i);
    }
  };

  /* The pairs to draw without the chip: the hovered node's, and the criteria's. */
  QGraph.prototype._mirrorFocus = function () {
    var self = this;
    var focus = new Set(this.hoverMirrors || []);
    var ends = (this.criteria || []).slice();
    if (this.criterion !== null && this.criterion !== undefined) ends.push(this.criterion);
    if (this.chop) ends.push(this.chop.from, this.chop.to);
    ends.forEach(function (id) {
      (self.mirrorsByNode.get(id) || []).forEach(function (index) { focus.add(index); });
    });
    return focus;
  };

  /* Make the DOM hold exactly the mirror links wanted now: those in view with
     the chip on, plus the focused ones. Touches only links, never nodes. */
  QGraph.prototype._syncMirrors = function () {
    var self = this;
    if (!this.data) return;
    var focus = this._mirrorFocus();
    var want = new Set(this.mirrorsInWindow);
    focus.forEach(function (index) { if (self._mirrorDrawable(index)) want.add(index); });
    this.mountedMirrors.forEach(function (path, index) {
      if (!want.has(index)) {
        path.remove();
        self.mountedMirrors.delete(index);
      }
    });
    var hot = new Set(this.hoverMirrors || []);
    want.forEach(function (index) {
      var path = self.mountedMirrors.get(index);
      if (!path) path = self._mountMirror(index);
      path.setAttribute('class', 'mirror-arc' + (focus.has(index) ? ' focus' : '') +
        (hot.has(index) ? ' hot' : ''));
    });
  };

  QGraph.prototype._mountMirror = function (index) {
    var pair = this.mirrorPairs[index];
    var path = el('path', { class: 'mirror-arc', d: this._mirrorArc(index).d });
    path.appendChild(el('title')).textContent =
      this._nodeName(pair[0]) + ' mirrors ' + this._nodeName(pair[1]) +
      ': a gate and a later inverse on the same qubits';
    this.layerMirrors.appendChild(path);
    this.mountedMirrors.set(index, path);
    return path;
  };

  /* "#12 (cx)" */
  QGraph.prototype._nodeName = function (id) {
    var node = this.byId[id];
    return '#' + id + (node ? ' (' + (node.short || node.label) + ')' : '');
  };

  /* The hovered node's mirror links are drawn, and the nodes at their other
     ends lit, by class on just those elements. ``id`` null ends it. */
  QGraph.prototype._hoverMirrors = function (id) {
    var self = this;
    var pairs = id === null ? null : (this.mirrorsByNode.get(id) || null);
    if (!pairs && !this.hoverMirrors) return;
    (this.mirrorPartners || new Set()).forEach(function (other) {
      var g = self.mountedNodes.get(other);
      if (g) g.classList.remove('mirror-partner');
    });
    this.hoverMirrors = pairs;
    this.mirrorPartners = null;
    if (pairs) {
      this.mirrorPartners = new Set();
      pairs.forEach(function (index) {
        var pair = self.mirrorPairs[index];
        var other = pair[0] === id ? pair[1] : pair[0];
        self.mirrorPartners.add(other);
        var g = self.mountedNodes.get(other);
        if (g) g.classList.add('mirror-partner');
      });
    }
    this._syncMirrors();
  };

  /* --------------------------------------------------------- drawing ---- */

  /* Draw everything again from the data: the lanes, and what is in view. */
  QGraph.prototype.render = function () {
    if (!this.data) return;
    this._renderLanes();
    this._clearMounted();
    this._afterViewChange(true);
  };

  QGraph.prototype._renderLanes = function () {
    var self = this;
    var group = this.layerLanes;
    group.textContent = '';
    if (!this.data) return;
    var qubits = this.data.program.num_qubits;

    for (var q = 0; q < qubits; q++) {
      if (!this._laneShown(q)) continue;
      var y = this._laneY(q);
      var marked = this.markedQubits && this.markedQubits.has(q) ? ' lint-marked' : '';
      var lint = this.showLint ? this.lintQubits[q] : null;
      group.appendChild(el('line', {
        class: 'lane-line' + marked,
        x1: LEFT_PAD - 30, y1: y, x2: this.contentWidth - LEFT_PAD + 20, y2: y
      }));

      var hit = el('rect', {
        class: 'lane-hit' + (this.selectedQubit === q ? ' selected' : '') + marked,
        x: LEFT_PAD - 40, y: y - LANE_H / 2 + 4,
        width: this.contentWidth, height: LANE_H - 8
      });
      // What the slice criterion may be correlated with: a glyph as well as a color.
      var corr = this.laneMarks.correlated.has(q) ? 'measured'
        : (this.laneMarks.unmeasured.has(q) ? 'unmeasured' : '');
      var label = el('text', {
        class: 'lane-label' + (this.selectedQubit === q ? ' selected' : '') +
          (lint ? ' lint-flagged' : '') + marked + (corr ? ' corr-' + corr : ''),
        x: LEFT_PAD - 38, y: y + 4
      });
      label.textContent = 'q' + q;
      var notes = lint ? lint.map(function (d) { return d.code + ': ' + d.message; }) : [];
      if (corr) {
        label.appendChild(el('tspan', { class: 'lane-glyph' })).textContent = corr === 'measured' ? '≈' : '~';
        notes.push(corr === 'measured'
          ? 'The slice criterion may be correlated with the measurement of q' + q + '.'
          : 'The slice criterion may still be entangled with q' + q + ', which is never measured.');
      }
      if (notes.length) label.appendChild(el('title')).textContent = notes.join('\n');

      // "slice": a backward slice from the wire's last measurement.
      var measured = (this.insights.measurements[String(q)] || []).length > 0;
      var action = el('g', {
        class: 'lane-slice' + (measured ? '' : ' unmeasured'),
        transform: 'translate(' + (LEFT_PAD - 40) + ',' + (y + 9) + ')'
      });
      action.appendChild(el('rect', { x: 0, y: 0, width: 30, height: 12, rx: 3 }));
      action.appendChild(el('text', { x: 15, y: 9 })).textContent = 'slice';
      action.appendChild(el('title')).textContent = measured
        ? 'Slice on q' + q + ': a backward slice from its last measurement. ' +
          'Ctrl- or Shift-click to add it to the slices. In Chop, stop at that measurement.'
        : 'q' + q + ' is never measured, so there is no measurement to slice from.';

      (function (qubit) {
        function pick(event) {
          event.stopPropagation();
          if (self.options.onQubitPick) self.options.onQubitPick(qubit);
        }
        hit.addEventListener('click', pick);
        label.addEventListener('click', pick);
        // The view pans on pointerdown; a press on the button is not a pan.
        action.addEventListener('pointerdown', function (event) { event.stopPropagation(); });
        action.addEventListener('click', function (event) {
          event.stopPropagation();
          if (self.options.onQubitSlice) self.options.onQubitSlice(qubit, event);
        });
      })(q);

      group.appendChild(hit);
      group.appendChild(label);
      group.appendChild(action);
    }

    // Classical wires: a thin double line per clbit, as circuit diagrams draw them.
    var clbits = this.data.program.num_clbits || 0;
    for (var c = 0; c < clbits; c++) {
      var cy = this._clbitY(c);
      [-1.5, 1.5].forEach(function (d) {
        group.appendChild(el('line', {
          class: 'cwire-line',
          x1: LEFT_PAD - 30, y1: cy + d, x2: self.contentWidth - LEFT_PAD + 20, y2: cy + d
        }));
      });
      var clabel = el('text', { class: 'cwire-label', x: LEFT_PAD - 38, y: cy + 4 });
      clabel.textContent = 'c' + c;
      group.appendChild(clabel);
    }
  };

  QGraph.prototype._edgePath = function (edge) {
    var a = this._pos(edge.source);
    var b = this._pos(edge.target);
    if (edge.clbit !== null) {
      var points = classicalRoute(a, b, this._clbitY(edge.clbit) + edge.side * 2.5,
                                  edge.side * CL_SHIFT);
      var cut = this.window ? clipRoute(points, this.window.x0, this.window.x1) : null;
      this._cutEnd = !!(cut && cut.cutEnd);
      return roundedPath(cut ? cut.points : points, CL_CORNER);
    }
    this._cutEnd = false;
    var x1 = a.x + a.w / 2;
    var y1 = a.y;
    var x2 = b.x - b.w / 2;
    var y2 = b.y;
    var bow = (edge.rank % 2 === 0 ? 1 : -1) * (12 + 14 * Math.floor(edge.rank / 2));
    var mx = (x1 + x2) / 2;
    var my = (y1 + y2) / 2 - bow;
    return 'M ' + x1 + ' ' + y1 + ' Q ' + mx + ' ' + my + ' ' + x2 + ' ' + y2;
  };

  /* Draw (or redraw) an edge's path. An end cut off by the window has no
     arrowhead: the arrow belongs at the node, which is out of sight. */
  QGraph.prototype._setEdgePath = function (path, edge) {
    path.setAttribute('d', this._edgePath(edge));
    if (this._cutEnd) path.removeAttribute('marker-end');
    else path.setAttribute('marker-end', 'url(#arrow-' + edge.dependency + ')');
  };

  QGraph.prototype._edgeClass = function (edge) {
    var active = this._depActive(edge.dependency);
    var inSlice = active && this.slice &&
      this.slice.has(edge.source) && this.slice.has(edge.target);
    var classes = 'edge';
    if (edge.clbit !== null) classes += ' classical dep-' + edge.dependency.toLowerCase();
    if (!active || (this.slice && !inSlice)) classes += ' dimmed';
    if (inSlice) classes += ' in-slice';
    return classes;
  };

  QGraph.prototype._mountEdge = function (index) {
    var edge = this.edgeMeta[index];
    var path = el('path', {
      class: this._edgeClass(edge),
      stroke: window.depColor(edge.dependency)
    });
    this._setEdgePath(path, edge);
    path.appendChild(el('title')).textContent =
      edge.dependency + '  ' + edge.source + ' → ' + edge.target +
      (edge.clbit !== null ? '  via c' + edge.clbit : '') +
      (edge.rule ? '  (rule: ' + edge.rule + ')' : '');
    this.layerEdges.appendChild(path);
    this.mountedEdges.set(index, path);
  };

  /* A bit written with no edge leaving along it (no later reader, or the reader
     is outside the range) gets a dotted drop onto its wire ending in a dot; a
     bit read with no edge arriving (nothing wrote it: QD09) gets one rising
     from a ring. So every write and read stays visible. */
  QGraph.prototype._createTicks = function (node) {
    var self = this;
    var pos = this._pos(node.id);
    var reads = node.condition_clbits || [];
    var writes = (node.clbits || []).filter(function (c) { return reads.indexOf(c) === -1; });
    var ticks = [];
    [['write', writes], ['read', reads]].forEach(function (pair) {
      pair[1].forEach(function (c) {
        if (self.routed[node.id + ':' + c]) return;
        var wy = self._clbitY(c);
        var edge = wy > pos.y ? pos.y + pos.h / 2 : pos.y - pos.h / 2;
        if (Math.abs(wy - pos.y) <= pos.h / 2) return;  // the node sits on this wire
        var tx = pos.x - CL_SHIFT;   // where a MEASUREMENT edge would run, clear of the label
        var tick = el('g', { class: self._tickClass(node.id, pair[0]), 'data-kind': pair[0] });
        tick.appendChild(el('line', { x1: tx, y1: edge, x2: tx, y2: wy }));
        tick.appendChild(el('circle', { cx: tx, cy: wy, r: 2.8 }));
        tick.appendChild(el('title')).textContent =
          '#' + node.id + (pair[0] === 'write' ? ' writes c' : ' reads c') + c;
        self.layerEdges.appendChild(tick);
        ticks.push(tick);
      });
    });
    return ticks;
  };

  QGraph.prototype._tickClass = function (id, kind) {
    return 'clbit-tick ' + kind + (this.slice && !this.slice.has(id) ? ' dimmed' : '');
  };

  /* Bits the source writes that the target's condition reads, in the target's order. */
  function sharedClbits(source, target) {
    if (!source || !target) return [];
    var written = source.clbits || [];
    return (target.condition_clbits || []).filter(function (c) { return written.indexOf(c) !== -1; });
  }

  /* The corner points of a classical edge from box ``a`` to box ``b`` along the
     wire at ``wireY``: down (or up) from the source, along the wire, up (or
     down) into the target. ``shift`` moves the vertical legs off the node's
     centre so a MEASUREMENT and a DATA_CLASSICAL edge on one bit stay apart. A
     box that sits on the wire itself joins it from its side instead. */
  function classicalRoute(a, b, wireY, shift) {
    function leg(box, x, isTarget) {
      if (box.y + box.h / 2 < wireY - 3) return [[x, box.y + box.h / 2], [x, wireY]];
      if (box.y - box.h / 2 > wireY + 3) return [[x, box.y - box.h / 2], [x, wireY]];
      return [[box.x + (isTarget ? -1 : 1) * box.w / 2, wireY]];
    }
    var points = leg(a, a.x + shift, false).concat(leg(b, b.x + shift, true).reverse());
    return points.filter(function (p, i) {
      return i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1];
    });
  }

  /* A dashed stroke costs time for its whole length, drawn or not, so an edge
     far longer than the view is cut down to the drawn window before it becomes
     a path. DASH_PERIOD is the classical-data dash (5 3): a cut made on a
     multiple of it leaves the dashes where they were. */
  var DASH_PERIOD = 8;

  /* ``points`` (a classical route) with its long run along the wire kept only
     between ``lo`` and ``hi``. Returns null when nothing needs cutting, else
     the points and which ends were cut off. */
  function clipRoute(points, lo, hi) {
    var run = -1;
    var longest = 0;
    for (var i = 0; i < points.length - 1; i++) {
      var length = points[i + 1][0] - points[i][0];
      if (points[i][1] === points[i + 1][1] && length > longest) { run = i; longest = length; }
    }
    if (run < 0) return null;
    var xa = points[run][0];
    var xb = points[run + 1][0];
    var y = points[run][1];
    lo = Math.floor(lo / DASH_PERIOD) * DASH_PERIOD;
    var cutStart = xa < lo && lo < xb;
    var cutEnd = xb > hi && hi > xa;
    if (!cutStart && !cutEnd) return null;
    var head = cutStart ? [[lo, y]] : points.slice(0, run + 1);
    var tail = cutEnd ? [[hi, y]] : points.slice(run + 1);
    return { points: head.concat(tail), cutStart: cutStart, cutEnd: cutEnd };
  }

  /* The part of a cubic (four [x, y] points, x never decreasing along it)
     between x = ``lo`` and x = ``hi``, as a path; null when it is all inside. */
  function clipCubic(curve, lo, hi) {
    function at(points, t) {
      var out = [];
      for (var i = 0; i < points.length - 1; i++) {
        out.push([points[i][0] + (points[i + 1][0] - points[i][0]) * t,
                  points[i][1] + (points[i + 1][1] - points[i][1]) * t]);
      }
      return out;
    }
    // de Casteljau: the two halves of ``points`` at ``t``.
    function split(points, t) {
      var a = at(points, t), b = at(a, t), c = at(b, t);
      return [[points[0], a[0], b[0], c[0]], [c[0], b[1], a[2], points[3]]];
    }
    function solve(x) {
      var t0 = 0, t1 = 1;
      for (var i = 0; i < 40; i++) {
        var t = (t0 + t1) / 2;
        if (split(curve, t)[0][3][0] < x) t0 = t; else t1 = t;
      }
      return (t0 + t1) / 2;
    }
    var from = curve[0][0] < lo ? solve(lo) : 0;
    var to = curve[3][0] > hi ? solve(hi) : 1;
    if (from === 0 && to === 1) return null;
    var part = split(curve, to)[0];
    if (from > 0) part = split(part, from / to)[1];
    return 'M ' + part[0][0] + ' ' + part[0][1] + ' C ' + part[1][0] + ' ' + part[1][1] + ' ' +
      part[2][0] + ' ' + part[2][1] + ' ' + part[3][0] + ' ' + part[3][1];
  }

  /* An SVG path through ``points`` with each corner rounded to ``radius``. */
  function roundedPath(points, radius) {
    function toward(from, to, r) {
      var len = Math.hypot(to[0] - from[0], to[1] - from[1]);
      if (!len) return from;
      return [from[0] + (to[0] - from[0]) * r / len, from[1] + (to[1] - from[1]) * r / len];
    }
    var d = 'M ' + points[0][0] + ' ' + points[0][1];
    for (var i = 1; i < points.length - 1; i++) {
      var prev = points[i - 1], corner = points[i], next = points[i + 1];
      var r = Math.min(radius,
        Math.hypot(corner[0] - prev[0], corner[1] - prev[1]) / 2,
        Math.hypot(next[0] - corner[0], next[1] - corner[1]) / 2);
      var p = toward(corner, prev, r);
      var q = toward(corner, next, r);
      d += ' L ' + p[0] + ' ' + p[1] + ' Q ' + corner[0] + ' ' + corner[1] + ' ' + q[0] + ' ' + q[1];
    }
    var last = points[points.length - 1];
    return d + ' L ' + last[0] + ' ' + last[1];
  }

  /* What each of a node's qubits is to its gate, in argument order: 'control',
     'target' (a plain ring), 'target-x', 'target-swap', or 'plain' for a gate
     with no controls. The gate's card says how many leading qubits are
     controls (-1: all but the last) and what the rest get. */
  function qubitRoles(node, gate) {
    var qubits = node.qubits || [];
    var count = gate && gate.controls ? gate.controls : 0;
    if (count < 0) count = qubits.length - 1;
    if (count >= qubits.length) count = 0;
    var kind = gate && gate.target;
    var rest = kind === 'swap' ? 'target-swap' : !count ? 'plain'
      : kind === 'x' ? 'target-x' : 'target';
    return qubits.map(function (q, i) { return i < count ? 'control' : rest; });
  }

  /* The mark for one wire of a multi-qubit gate, drawn around (0, 0). */
  function roleMark(role, color) {
    var g = el('g', { class: 'node-tick role-' + role });
    var r = 3.2;
    if (role === 'control') {
      r = 4.4;
      g.appendChild(el('circle', { r: r, fill: color }));
    } else if (role === 'target-swap') {
      r = 4.6;
      g.appendChild(el('path', { d: 'M -4.6 -4.6 L 4.6 4.6 M -4.6 4.6 L 4.6 -4.6',
                                 stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round' }));
    } else if (role === 'target' || role === 'target-x') {
      r = 6;
      g.appendChild(el('circle', { r: r, fill: '#181825', stroke: color, 'stroke-width': 1.8 }));
      if (role === 'target-x') {
        g.appendChild(el('path', { d: 'M -6 0 L 6 0 M 0 -6 L 0 6', stroke: color, 'stroke-width': 1.6 }));
      }
    } else {
      g.appendChild(el('circle', { r: r, fill: color, opacity: 0.8 }));
    }
    return { el: g, r: r };
  }

  QGraph.prototype._nodeClass = function (node) {
    var classes = 'node cat-' + node.category.replace('-qubit', '');
    if (this.slice) classes += this.slice.has(node.id) ? ' in-slice' : ' dimmed';
    if (this.criterion === node.id) classes += ' criterion';
    if (this.member) {
      // Several slices: one slice's color, or neutral with a stripe per slice.
      var positions = this.member.get(node.id);
      if (positions) classes += positions.length === 1 ? ' ' + sliceClass(positions[0]) : ' sl-many striped';
      if (this.criteria.indexOf(node.id) !== -1) classes += ' criterion';
    }
    if (this.chop) {
      if (this.chop.from === node.id) classes += ' chop-from';
      if (this.chop.to === node.id) classes += ' chop-to';
    }
    if (this.mirrorPartners && this.mirrorPartners.has(node.id)) classes += ' mirror-partner';
    if (this.hoveredId === node.id) classes += ' hovered';
    if (this.marked && this.marked.has(node.id)) classes += ' marked';
    if (this.selectedQubit !== null && node.qubits.indexOf(this.selectedQubit) !== -1) {
      classes += ' qubit-selected';
    }
    var lint = this.showLint ? this.lint[node.id] : null;
    if (lint) classes += ' lint-' + worstSeverity(lint);
    return classes;
  };

  QGraph.prototype._mountNode = function (node) {
    var pos = this._pos(node.id);
    var g = el('g', {
      class: this._nodeClass(node),
      transform: 'translate(' + pos.x + ',' + pos.y + ')',
      'data-id': String(node.id)
    });

    /* Vertical tick through every wire the instruction touches, so a
       multi-qubit gate reads like a circuit diagram. */
    if (node.qubits.length > 1) {
      var graph = this;
      var lanes = node.qubits.map(function (q) { return graph._laneY(q) - pos.y; });
      var lo = Math.min.apply(null, lanes);
      var hi = Math.max.apply(null, lanes);
      g.appendChild(el('line', {
        class: 'node-span', x1: 0, y1: lo, x2: 0, y2: hi,
        stroke: CATEGORY_FILL[node.category] || '#9AA6CF',
        'stroke-width': 2, opacity: 0.55
      }));
      // What each wire is to the gate, as a circuit diagram draws it: a solid
      // dot on a control, a ring on a target (a circled plus for an X, a
      // cross for a swap). A wire that runs through the node's box has its
      // mark on the box's left edge, where the box would hide it.
      var roles = qubitRoles(node, this._gateInfo(node));
      var color = CATEGORY_FILL[node.category] || '#9AA6CF';
      lanes.forEach(function (ly, i) {
        var mark = roleMark(roles[i], color);
        var cx = Math.abs(ly) < pos.h / 2 + mark.r ? -pos.w / 2 - mark.r : 0;
        mark.el.setAttribute('transform', 'translate(' + cx + ',' + ly + ')');
        g.appendChild(mark.el);
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
    var lint = this.showLint ? this.lint[node.id] : null;
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

    // What the insight passes found: small square tags on the bottom-right
    // corner, a different shape from the round lint badge. Their sentences are
    // in the hover card rather than a <title> each: most of a phase-heavy
    // circuit (Shor) carries one.
    var right = pos.w / 2 + 4;
    this._insightMarks(node).forEach(function (mark) {
      var width = 5 + 5.4 * mark.text.length;
      var tag = el('g', { class: 'insight-mark ' + mark.cls,
                          transform: 'translate(' + (right - width) + ',' + (pos.h / 2 - 6) + ')' });
      tag.appendChild(el('rect', { width: width, height: 11, rx: 2 }));
      tag.appendChild(el('text', { x: width / 2, y: 8.2 })).textContent = mark.text;
      g.appendChild(tag);
      right -= width + 2;
    });

    this._decorate(g, node);
    this.layerNodes.appendChild(g);
    this.mountedNodes.set(node.id, g);
    this.mountedTicks.set(node.id, this._createTicks(node));
  };

  /* The badges a node gets from the insights: [{cls, text, title}]. */
  QGraph.prototype._insightMarks = function (node) {
    var key = String(node.id);
    var found = this.insights || normalizeInsights(null);
    var marks = [];
    var outcome = found.predetermined[key];
    if (outcome !== undefined && outcome !== null) {
      marks.push({ cls: 'mark-always', text: '=' + outcome, title: predeterminedSentence(outcome) });
    }
    if (found.kickback[key]) {
      marks.push({ cls: 'mark-kickback', text: 'kb', title: kickbackSentence(found.kickback[key]) });
    }
    if (this._gateInfo(node).phase) {
      marks.push({ cls: 'mark-phase', text: 'φ', title: phaseSentence(node.qubits.length > 1) });
    }
    return marks;
  };

  /* What a node shows for the slice state beyond its class: its number among
     several criteria, a stripe per slice it is in, a chop end's tag. Built on
     mounting, and again on the mounted nodes when that state changes (a slice
     response), never on hover. */
  QGraph.prototype._decorate = function (g, node) {
    if (g.qDeco) {
      g.qDeco.remove();
      g.qDeco = null;
    }
    var pos = this._pos(node.id);
    var deco = null;
    function add(child) {
      if (!deco) deco = el('g', { class: 'node-deco' });
      deco.appendChild(child);
      return child;
    }

    var positions = this.member ? this.member.get(node.id) : null;
    if (positions && positions.length > 1) {
      var stripes = stripesFor(positions, MAX_STRIPES);
      var n = stripes.shown.length;
      stripes.shown.forEach(function (k, i) {
        add(el('rect', { class: 'slice-stripe ' + sliceClass(k),
                         x: -pos.w / 2 + 4, y: pos.h / 2 - 2 - STRIPE_H * (n - i),
                         width: pos.w - 8, height: STRIPE_H }));
      });
      if (stripes.more) {
        add(el('text', { class: 'slice-more', x: -pos.w / 2 - 2, y: pos.h / 2 })).textContent =
          '+' + stripes.more;
      }
    }

    var number = this.criteria ? this.criteria.indexOf(node.id) : -1;
    if (number !== -1) {
      var badge = add(el('g', { class: 'slice-num ' + sliceClass(number),
                                transform: 'translate(' + (-pos.w / 2) + ',' + (-pos.h / 2) + ')' }));
      badge.appendChild(el('circle', { r: 7 }));
      badge.appendChild(el('text', { x: 0, y: 3.2 })).textContent = String(number + 1);
    }

    if (this.chop) {
      [['from', this.chop.from], ['to', this.chop.to]].forEach(function (end) {
        if (end[1] !== node.id) return;
        var tag = add(el('g', { class: 'chop-tag chop-' + end[0],
                                transform: 'translate(' + (-pos.w / 2 - 4) + ',' + (-pos.h / 2) + ')' }));
        tag.appendChild(el('rect', { x: -12, y: -6, width: 24, height: 12, rx: 6 }));
        tag.appendChild(el('text', { x: 0, y: 3 })).textContent = end[0];
      });
    }

    if (deco) {
      g.appendChild(deco);
      g.qDeco = deco;
    }
  };

  QGraph.prototype._refreshDecorations = function () {
    var self = this;
    if (!this.data) return;
    this.mountedNodes.forEach(function (g, id) { self._decorate(g, self.byId[id]); });
  };

  QGraph.prototype._unmountNode = function (id) {
    var g = this.mountedNodes.get(id);
    if (g) g.remove();
    this.mountedNodes.delete(id);
    (this.mountedTicks.get(id) || []).forEach(function (tick) { tick.remove(); });
    this.mountedTicks.delete(id);
    // A node that leaves the DOM under the pointer never gets its mouseout.
    if (this.hoveredId === id) {
      this.hoveredId = null;
      this._hoverMirrors(null);
      if (this.options.onHoverEnd) this.options.onHoverEnd();
    }
  };

  /* After a change of state (slice, marks, filters, the selected wire): new
     classes on what exists. What is created later gets them on creation. */
  QGraph.prototype._refreshClasses = function () {
    var self = this;
    if (!this.data) return;
    this.mountedNodes.forEach(function (g, id) {
      g.setAttribute('class', self._nodeClass(self.byId[id]));
    });
    this.mountedTicks.forEach(function (ticks, id) {
      ticks.forEach(function (tick) {
        tick.setAttribute('class', self._tickClass(id, tick.getAttribute('data-kind')));
      });
    });
    this.mountedEdges.forEach(function (path, index) {
      path.setAttribute('class', self._edgeClass(self.edgeMeta[index]));
    });
  };

  /* A dragged node: move it, its ticks and the edges at its ends. */
  QGraph.prototype._moveNode = function (id) {
    var self = this;
    var pos = this._pos(id);
    var g = this.mountedNodes.get(id);
    if (g) g.setAttribute('transform', 'translate(' + pos.x + ',' + pos.y + ')');
    (this.mountedTicks.get(id) || []).forEach(function (tick) { tick.remove(); });
    if (g) this.mountedTicks.set(id, this._createTicks(this.byId[id]));
    (this.edgesByNode[id] || []).forEach(function (index) {
      var path = self.mountedEdges.get(index);
      if (path) self._setEdgePath(path, self.edgeMeta[index]);
    });
    (this.mirrorsByNode.get(id) || []).forEach(function (index) {
      var path = self.mountedMirrors.get(index);
      if (path) path.setAttribute('d', self._mirrorArc(index).d);
    });
  };

  /* ------------------------------------------------- node interaction --- */

  /* One set of listeners on the node layer, for every node there will be. */
  QGraph.prototype._installNodeEvents = function () {
    var self = this;
    var layer = this.layerNodes;

    function nodeOf(event) {
      var target = event.target;
      var g = target && target.closest ? target.closest('.node') : null;
      return g && layer.contains(g) ? g : null;
    }

    layer.addEventListener('mouseover', function (event) {
      var g = nodeOf(event);
      if (!g || self._drag) return;
      var id = Number(g.getAttribute('data-id'));
      if (self.hoveredId === id) return;
      var previous = self.mountedNodes.get(self.hoveredId);
      if (previous) previous.classList.remove('hovered');
      self.hoveredId = id;
      g.classList.add('hovered');
      self._hoverMirrors(id);
      var node = self.byId[id];
      if (self.options.onHover) self.options.onHover(node, event, self._tooltipHtml(node));
    });

    layer.addEventListener('mouseout', function (event) {
      var g = nodeOf(event);
      if (!g || (event.relatedTarget && g.contains(event.relatedTarget))) return;
      if (self.hoveredId !== Number(g.getAttribute('data-id'))) return;
      self.hoveredId = null;
      g.classList.remove('hovered');
      self._hoverMirrors(null);
      if (self.options.onHoverEnd) self.options.onHoverEnd();
    });

    layer.addEventListener('mousemove', function (event) {
      if (!self._drag && self.hoveredId !== null && self.options.onHoverMove) {
        self.options.onHoverMove(event);
      }
    });

    layer.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      var g = nodeOf(event);
      if (!g) return;
      event.stopPropagation();          // a node is dragged, not the view
      var id = Number(g.getAttribute('data-id'));
      var offset = self.offsets[id] || { dx: 0, dy: 0 };
      self._drag = { id: id, g: g, pointerId: event.pointerId, moved: false,
                     x: event.clientX, y: event.clientY, dx: offset.dx, dy: offset.dy };
      g.setPointerCapture(event.pointerId);
    });

    layer.addEventListener('pointermove', function (event) {
      var drag = self._drag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      var dx = (event.clientX - drag.x) / self.transform.k;
      var dy = (event.clientY - drag.y) / self.transform.k;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) drag.moved = true;
      if (!drag.moved) return;
      self.offsets[drag.id] = { dx: drag.dx + dx, dy: drag.dy + dy };
      self._moveNode(drag.id);
    });

    function endDrag(event) {
      var drag = self._drag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      self._drag = null;
      try { drag.g.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
      if (!drag.moved) {
        // The event goes along: Ctrl/Cmd or Shift adds to the slices.
        if (self.options.onNodePick) self.options.onNodePick(self.byId[drag.id], event);
        return;
      }
      // Edges that now reach into or out of the view are drawn or dropped.
      self._overviewDirty = true;
      self._afterViewChange(true);
    }
    layer.addEventListener('pointerup', endDrag);
    layer.addEventListener('pointercancel', endDrag);
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

  /* A gate's reference card: one per gate name in the response (``gates``),
     or on the node itself in an older response. */
  QGraph.prototype._gateInfo = function (node) {
    var table = this.data && this.data.gates;
    return (table && table[node.name]) || node.gate || {
      title: node.name, summary: '', detail: '', matrix: '', known: false
    };
  };

  QGraph.prototype._tooltipHtml = function (node) {
    var gate = this._gateInfo(node);
    var parts = [];
    parts.push('<h4>' + escapeHtml(gate.title) + '</h4>');
    parts.push('<p class="tip-sub">#' + node.id + ' &middot; ' + escapeHtml(node.name) +
      (node.qubits.length ? ' on q' + node.qubits.join(', q') : '') + '</p>');
    if (gate.summary) parts.push('<p>' + math(gate.summary) + '</p>');
    if (gate.detail) parts.push('<p>' + math(gate.detail) + '</p>');

    var rows = '';
    if (gate.matrix) {
      rows += '<dt>matrix</dt><dd class="tip-matrix">' + mathExpr(gate.matrix) + '</dd>';
    }
    var roles = qubitRoles(node, gate);
    if (roles.indexOf('control') !== -1) {
      var held = function (want) {
        return node.qubits.filter(function (q, i) { return (roles[i] === 'control') === want; });
      };
      rows += '<dt>control</dt><dd>&#9679; ' + qubitList(held(true)) + '</dd>' +
        '<dt>target</dt><dd>' + (gate.target === 'x' ? '&oplus; ' : gate.target === 'swap' ? '&times; ' : '&#9675; ') +
        qubitList(held(false)) + '</dd>';
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
    var insight = this._insightHtml(node);
    if (insight) parts.push('<div class="tip-insight">' + insight + '</div>');
    var lint = this.showLint ? this.lint[node.id] : null;
    if (lint) {
      parts.push('<div class="tip-lint">' + lint.map(function (d) {
        return '<p class="sev-' + escapeHtml(d.severity) + '"><strong>' + escapeHtml(d.code) +
          '</strong> ' + escapeHtml(d.message) + '</p>';
      }).join('') + '</div>');
    }
    return parts.join('');
  };

  /* The hover card's lines from the insights and the slices: each badge's
     sentence (with the badge beside it), no effect, correlations, mirror
     partners, and which of several slices the node is in. */
  QGraph.prototype._insightHtml = function (node) {
    var self = this;
    var key = String(node.id);
    var found = this.insights || normalizeInsights(null);
    var lines = this._insightMarks(node).map(function (mark) {
      return '<p><span class="tip-mark ' + mark.cls + '">' + escapeHtml(mark.text) + '</span> ' +
        escapeHtml(mark.title) + '</p>';
    });
    if (found.no_effect[key]) lines.push('<p>' + escapeHtml(noEffectSentence(found.no_effect[key])) + '</p>');
    correlationSentences(found.correlations[key]).forEach(function (sentence) {
      lines.push('<p>' + escapeHtml(sentence) + '</p>');
    });
    ((this.mirrorsByNode && this.mirrorsByNode.get(node.id)) || []).forEach(function (index) {
      var pair = self.mirrorPairs[index];
      var other = pair[0] === node.id ? pair[1] : pair[0];
      lines.push('<p><span class="tip-mark mark-mirror">⌒</span> mirrors ' +
        escapeHtml(self._nodeName(other)) + '</p>');
    });
    var positions = this.sliceMember ? this.sliceMember(node.id) : [];
    if (positions.length) {
      lines.push('<p>In slice' + (positions.length === 1 ? ' ' : 's ') + positions.map(function (k) {
        return '<span class="tip-slice ' + sliceClass(k) + '">' + (k + 1) + '</span>';
      }).join(' ') + '</p>');
    }
    return lines.join('');
  };

  /* id -> diagnostics, from each diagnostic's ``nodes`` (set by the analysis). */
  function lintByNode(diagnostics) {
    var byNode = {};
    (diagnostics || []).forEach(function (d) {
      (d.nodes || []).forEach(function (id) { (byNode[id] = byNode[id] || []).push(d); });
    });
    return byNode;
  }

  /* Wires the diagnostics are about (QD07's idle qubits), as lintByNode. */
  function lintByQubit(diagnostics) {
    var byQubit = {};
    (diagnostics || []).forEach(function (d) {
      (d.qubits || []).forEach(function (q) { (byQubit[q] = byQubit[q] || []).push(d); });
    });
    return byQubit;
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
    var k = Math.min(MAX_SCALE, Math.max(MIN_SCALE, this.transform.k * factor));
    var ratio = k / this.transform.k;
    this.transform.x = px - (px - this.transform.x) * ratio;
    this.transform.y = py - (py - this.transform.y) * ratio;
    this.transform.k = k;
    this._applyTransform();
  };

  QGraph.prototype.zoomBy = function (factor) {
    var view = this._view();
    this.zoomAround(view.width / 2, view.height / 2, factor);
  };

  /* ``minScale`` keeps an auto-fit from shrinking a long circuit into an
     unreadable strip: past that point the view anchors on the left of the graph
     and the user pans. The Fit button passes nothing and fits as far as the
     zoom limit allows; the overview strip shows the whole circuit. */
  QGraph.prototype.fit = function (minScale) {
    if (!this.data || !this.data.nodes.length) return;
    var view = this._view();
    if (!view.width || !view.height) return;

    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var any = false;
    for (var i = 0; i < this.data.nodes.length; i++) {
      var id = this.data.nodes[i].id;
      if (!this._inRange(id)) continue;
      any = true;
      var pos = this._pos(id);
      minX = Math.min(minX, pos.x - pos.w / 2 - 50);
      maxX = Math.max(maxX, pos.x + pos.w / 2 + 20);
      minY = Math.min(minY, pos.y - 40);
      maxY = Math.max(maxY, pos.y + 40);
    }
    if (!any) return;
    minY = Math.min(minY, TOP_PAD - 30);
    maxY = Math.max(maxY, TOP_PAD + (this._laneCount() - 1) * LANE_H + 30);
    if (this.data.program.num_clbits) {
      maxY = Math.max(maxY, this._clbitY(this.data.program.num_clbits - 1) + 18);
    }

    var k = Math.min(view.width / (maxX - minX), view.height / (maxY - minY));
    k = Math.min(1.6, Math.max(MIN_SCALE, k * 0.94));

    var anchorLeft = false;
    if (minScale && k < minScale) {
      k = minScale;
      anchorLeft = true;
    }
    // A strip too long to fit even at the zoom limit starts at its left end too.
    if ((maxX - minX) * k > view.width) anchorLeft = true;

    this.transform.k = k;
    this.transform.x = anchorLeft
      ? 16 - minX * k
      : (view.width - (maxX - minX) * k) / 2 - minX * k;
    this.transform.y = (view.height - (maxY - minY) * k) / 2 - minY * k;
    this._applyTransform();
  };

  QGraph.prototype._applyTransform = function () {
    this.root.setAttribute('transform',
      'translate(' + this.transform.x + ',' + this.transform.y + ') scale(' + this.transform.k + ')');
    this._afterViewChange(false);
  };

  /* --------------------------------------------------- overview strip --- */

  /* The whole circuit in one canvas, under the graph: every instruction as a
     mark on each wire it touches, the slice and marks in their colors, and
     the part the view shows outlined. Click or drag it to move the view. It
     appears only when the circuit is wider than the view. */
  QGraph.prototype._installOverview = function () {
    var self = this;
    var stage = this.svg.parentNode;
    if (!stage || !document.createElement) return;
    var canvas = document.createElement('canvas');
    canvas.className = 'graph-overview';
    canvas.hidden = true;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Overview of the whole circuit');
    canvas.title = 'The whole circuit, with lint findings marked along the top. ' +
      'Click or drag to move the view there.';
    stage.appendChild(canvas);
    this.overview = canvas;
    this.overviewBase = document.createElement('canvas');
    this._overviewDirty = true;

    var dragging = false;
    function jump(event) {
      if (!self.data) return;
      var rect = canvas.getBoundingClientRect();
      if (!rect.width) return;
      var x = clamp((event.clientX - rect.left) / rect.width, 0, 1) * self.contentWidth;
      self.transform.x = self._view().width / 2 - x * self.transform.k;
      self._applyTransform();
    }
    canvas.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      event.preventDefault();
      dragging = true;
      jump(event);
      try { canvas.setPointerCapture(event.pointerId); } catch (err) { /* not a live pointer */ }
    });
    canvas.addEventListener('pointermove', function (event) { if (dragging) jump(event); });
    function stop(event) {
      dragging = false;
      try { canvas.releasePointerCapture(event.pointerId); } catch (err) { /* fine */ }
    }
    canvas.addEventListener('pointerup', stop);
    canvas.addEventListener('pointercancel', stop);
    canvas.addEventListener('wheel', function (event) {
      // Scrolling over the strip pans along the circuit.
      event.preventDefault();
      var delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      self.transform.x -= delta * 2;
      self._applyTransform();
    }, { passive: false });
  };

  QGraph.prototype._showOverview = function (on) {
    if (!this.overview) return;
    if (this.overview.hidden === !on) return;
    this.overview.hidden = !on;
    if (this.svg.parentNode) this.svg.parentNode.classList.toggle('has-overview', on);
    if (on) this._overviewDirty = true;
  };

  QGraph.prototype._overviewChanged = function () {
    this._overviewDirty = true;
    this._drawOverview();
  };

  QGraph.prototype._drawOverview = function () {
    var canvas = this.overview;
    if (!canvas || !this.data) return;
    var view = this._view();
    this._showOverview(this.contentWidth * this.transform.k > view.width + 1);
    if (canvas.hidden) return;

    var ratio = window.devicePixelRatio || 1;
    var width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    var height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      this._overviewDirty = true;
    }
    if (this._overviewDirty) {
      this._drawOverviewBase(width, height, ratio);
      this._overviewDirty = false;
    }

    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(this.overviewBase, 0, 0);
    var box = this._viewBox();
    var sx = width / this.contentWidth;
    var x0 = Math.max(0, box.x0 * sx);
    var x1 = Math.min(width, box.x1 * sx);
    var minWidth = 4 * ratio;
    if (x1 - x0 < minWidth) {
      var mid = (x0 + x1) / 2;
      x0 = mid - minWidth / 2;
      x1 = mid + minWidth / 2;
    }
    ctx.fillStyle = OVERVIEW.view;
    ctx.fillRect(x0, 0, x1 - x0, height);
    ctx.strokeStyle = OVERVIEW.viewEdge;
    ctx.lineWidth = Math.max(1, ratio);
    ctx.strokeRect(x0 + 0.5, 0.5, Math.max(1, x1 - x0 - 1), height - 1);
  };

  /* The instructions, drawn once per change of data, slice, marks or range,
     batched into one path per color. */
  QGraph.prototype._drawOverviewBase = function (width, height, ratio) {
    var base = this.overviewBase;
    base.width = width;
    base.height = height;
    var ctx = base.getContext('2d');
    ctx.fillStyle = OVERVIEW.background;
    ctx.fillRect(0, 0, width, height);

    var self = this;
    var pad = 4 * ratio;
    var qubits = Math.max(1, this.data.program.num_qubits);
    // With lint markers on, a band along the top carries one mark per
    // instruction a finding is about, so findings far off screen show.
    var lintIds = this.showLint ? Object.keys(this.lint) : [];
    var band = lintIds.length ? 5 * ratio : 0;
    var top = pad + band;
    var span = height - top - pad;
    function laneY(q) { return qubits === 1 ? top + span / 2 : top + q * span / (qubits - 1); }
    var sx = width / this.contentWidth;
    var batches = {};      // "color|alpha" -> Path2D
    var hasPath = typeof window.Path2D === 'function';

    // A mark on each wire an instruction touches, not a bar across the wires in
    // between: in a long circuit most gates span the register, and bars would
    // fill the strip.
    var tick = Math.max(ratio * 1.5, Math.min(3 * ratio, span / qubits * 0.7));

    // A wire a finding is about (QD07, an idle qubit) is tinted along its length.
    if (this.showLint) {
      ctx.globalAlpha = 0.35;
      ctx.fillStyle = OVERVIEW.lint.error;
      Object.keys(this.lintQubits).forEach(function (q) {
        if (Number(q) < qubits) ctx.fillRect(0, laneY(Number(q)) - tick / 2, width, tick);
      });
      ctx.globalAlpha = 1;
    }

    this.data.nodes.forEach(function (node) {
      var pos = self._pos(node.id);
      var w = Math.max(ratio, pos.w * sx);
      var ys = node.qubits.length ? node.qubits.map(laneY) : [height - pad];
      var color = CATEGORY_FILL[node.category] || CATEGORY_FILL['single-qubit'];
      var alpha = 0.75;
      var end = self.criterion === node.id ||
        (self.criteria && self.criteria.indexOf(node.id) !== -1) ||
        (self.chop && (self.chop.from === node.id || self.chop.to === node.id));
      if (self._outsideSlice(node.id)) return;
      if (!self._inRange(node.id)) alpha = 0.12;
      else if (end) { color = OVERVIEW.criterion; alpha = 1; }
      else if (self.marked && self.marked.has(node.id)) { color = OVERVIEW.marked; alpha = 1; }
      else if (self.slice) {
        // Several slices: the first one a node is in gives its color.
        var positions = self.member ? self.member.get(node.id) : null;
        if (positions) { color = sliceColor(positions[0]); alpha = 1; }
        else if (self.slice.has(node.id)) { color = OVERVIEW.slice; alpha = 1; }
        else alpha = 0.18;
      }
      var key = color + '|' + alpha;
      var x = pos.x * sx - w / 2;
      ys.forEach(function (y) {
        if (hasPath) {
          (batches[key] = batches[key] || new window.Path2D()).rect(x, y - tick / 2, w, tick);
        } else {
          ctx.globalAlpha = alpha;
          ctx.fillStyle = color;
          ctx.fillRect(x, y - tick / 2, w, tick);
        }
      });
    });
    // Dim first, so the slice and the marks draw over everything else.
    Object.keys(batches).sort(function (a, b) {
      return Number(a.split('|')[1]) - Number(b.split('|')[1]);
    }).forEach(function (key) {
      var parts = key.split('|');
      ctx.globalAlpha = Number(parts[1]);
      ctx.fillStyle = parts[0];
      ctx.fill(batches[key]);
    });
    ctx.globalAlpha = 1;

    // The lint marks, in the band: one per flagged instruction, in its worst
    // severity, at least two pixels wide so a single finding still shows.
    // Errors last, so they stay on top where marks crowd together.
    var marks = {};
    lintIds.forEach(function (key) {
      var id = Number(key);
      if (!self.layout[id] || !self._inRange(id)) return;
      var pos = self._pos(id);
      var w = Math.max(2 * ratio, pos.w * sx);
      var severity = worstSeverity(self.lint[id]);
      (marks[severity] = marks[severity] || []).push([pos.x * sx - w / 2, w]);
    });
    ['info', 'warning', 'error'].forEach(function (severity) {
      ctx.fillStyle = OVERVIEW.lint[severity];
      (marks[severity] || []).forEach(function (mark) {
        ctx.fillRect(mark[0], ratio, mark[1], band - ratio);
      });
    });
  };

  function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

  /* ----------------------------------------------------- png download --- */

  /* The live SVG is styled by the page stylesheet and by CSS variables, neither
     of which survives serialization, so the clone gets its own literal
     stylesheet before it is rasterized. It holds what is in and around the
     view, which is what the export shows. */
  var EXPORT_CSS = [
    '.lane-line { stroke: #313244; stroke-width: 1; stroke-dasharray: 3 5; }',
    '.lane-label { fill: #7f849c; font-size: 10px; font-family: monospace; }',
    '.lane-label.selected { fill: #ff6b35; font-weight: 700; }',
    '.lane-hit { fill: transparent; }',
    '.lane-hit.lint-marked { fill: rgba(243, 139, 168, 0.16); }',
    '.lane-line.lint-marked { stroke: #f38ba8; stroke-width: 1.5; }',
    '.lane-label.lint-flagged, .lane-label.lint-marked { fill: #f38ba8; font-weight: 700; }',
    '.edge { fill: none; stroke-width: 1.4; opacity: 0.55; }',
    '.edge.dimmed { opacity: 0.08; }',
    '.edge.in-slice { opacity: 1; stroke-width: 2.4; }',
    '.edge.dep-data_classical { stroke-dasharray: 5 3; }',
    '.cwire-line { stroke: #45475a; stroke-width: 0.9; }',
    '.cwire-label { fill: #7f849c; font-size: 10px; font-family: monospace; }',
    '.clbit-tick line { stroke: #7f849c; stroke-width: 1.1; stroke-dasharray: 2 3; }',
    '.clbit-tick.write circle { fill: #7f849c; }',
    '.clbit-tick.read circle { fill: #181825; stroke: #7f849c; stroke-width: 1.3; }',
    '.clbit-tick.dimmed { opacity: 0.22; }',
    '.node .node-shape { fill: #45475a; stroke: #11111b; stroke-width: 1.5; }',
    '.node .node-text { fill: #11111b; font-size: 11px; font-family: monospace; text-anchor: middle; }',
    '.node .node-index { fill: #7f849c; font-size: 9px; font-family: monospace; text-anchor: middle; }',
    '.node .node-line { fill: #7f849c; font-size: 9px; font-family: monospace; text-anchor: middle; }',
    '.lod-mid .node-index, .lod-mid .node-line, .lod-far .node-index, .lod-far .node-line,',
    '.lod-far .node-text, .lod-far .lint-badge text { display: none; }',
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
    '.lint-badge text { fill: #11111b; font-size: 9px; font-weight: 700; font-family: sans-serif; text-anchor: middle; }',
    '.node.sl-many .node-shape { fill: #cdd6f4; }',
    '.node.striped .node-text { transform: translateY(-4px); }',
    '.slice-more { fill: #cdd6f4; font-size: 8px; font-family: monospace; text-anchor: end; }',
    '.slice-num circle { stroke: #ffffff; stroke-width: 1.5; }',
    '.slice-num text { fill: #11111b; font-size: 9px; font-weight: 700; font-family: sans-serif; text-anchor: middle; }',
    '.node.chop-from .node-shape { fill: #ff6b35; stroke: #ffffff; stroke-width: 2.5; }',
    '.node.chop-to .node-shape { stroke: #ffffff; stroke-width: 2.5; stroke-dasharray: 4 2; }',
    '.chop-tag rect { fill: #11111b; stroke-width: 1.3; }',
    '.chop-tag.chop-from rect { stroke: #ff6b35; }',
    '.chop-tag.chop-to rect { stroke: #b4befe; }',
    '.chop-tag text { fill: #cdd6f4; font-size: 8px; font-weight: 700; font-family: sans-serif; text-anchor: middle; }',
    '.insight-mark rect { fill: #11111b; stroke-width: 1.2; }',
    '.insight-mark text { font-size: 8.5px; font-weight: 700; font-family: sans-serif; text-anchor: middle; }',
    '.insight-mark.mark-phase rect { stroke: #cba6f7; } .insight-mark.mark-phase text { fill: #cba6f7; }',
    '.insight-mark.mark-kickback rect { stroke: #f5c2e7; } .insight-mark.mark-kickback text { fill: #f5c2e7; }',
    '.insight-mark.mark-always rect { stroke: #a6e3a1; } .insight-mark.mark-always text { fill: #a6e3a1; }',
    '.lod-far .insight-mark, .lod-far .slice-more, .lod-far .chop-tag text, .lod-far .slice-num text { display: none; }',
    '.mirror-arc { fill: none; stroke: #94e2d5; stroke-width: 1.3; stroke-dasharray: 2 3; opacity: 0.7; }',
    '.mirror-arc.focus, .mirror-arc.hot { stroke-width: 2; opacity: 1; }',
    '.lane-slice rect { fill: #313244; stroke: #45475a; }',
    '.lane-slice text { fill: #a6adc8; font-size: 8px; font-family: sans-serif; text-anchor: middle; }',
    '.lane-slice.unmeasured { opacity: 0.45; }',
    '.lane-label.corr-measured { fill: #b4befe; font-weight: 700; }',
    '.lane-label.corr-unmeasured { fill: #f5c2e7; font-style: italic; }'
  ].concat(SLICE_COLORS.map(function (color, k) {
    return '.node.sl-' + k + ' .node-shape, .node.criterion.sl-' + k + ' .node-shape { fill: ' + color + '; } ' +
      '.slice-stripe.sl-' + k + ', .slice-num.sl-' + k + ' circle { fill: ' + color + '; }';
  })).join('\n');

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
  QGraph.MIN_SCALE = MIN_SCALE;
  QGraph.routing = { classicalRoute: classicalRoute, roundedPath: roundedPath,
                     clipRoute: clipRoute, clipCubic: clipCubic,
                     sharedClbits: sharedClbits, classicalOnly: classicalOnly,
                     prepareEdges: prepareEdges };
  QGraph.roles = { of: qubitRoles };
  QGraph.culling = { drawWindow: drawWindow, contains: contains, overlaps: overlaps,
                     OVERSCAN: OVERSCAN };
  QGraph.slices = { COLORS: SLICE_COLORS, MAX_STRIPES: MAX_STRIPES, membership: sliceMembership,
                    stripes: stripesFor, sliceClass: sliceClass, sliceColor: sliceColor };
  QGraph.mirrors = { index: mirrorIndex, arc: mirrorArc, hangs: mirrorHangs, CAP: MIRROR_CAP };
  QGraph.insightText = { phase: phaseSentence, kickback: kickbackSentence,
                         predetermined: predeterminedSentence, noEffect: noEffectSentence,
                         correlation: correlationSentences, normalize: normalizeInsights };
  window.QGraph = QGraph;
})();
