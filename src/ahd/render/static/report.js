/* Analytics Health Dashboard page script.
 * Safety: every piece of data is set with textContent or setAttribute on elements built
 * with DOM calls. Nothing here parses data as HTML.
 */
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("data").textContent);
  var SVG_NS = "http://www.w3.org/2000/svg";

  var CUSTOM = ["custom_events", "custom_evars", "custom_props"];
  var CUSTOM_TITLES = {
    custom_events: "Custom events",
    custom_evars: "Custom eVars",
    custom_props: "Custom props"
  };
  var NUMBER_PREFIX = { custom_events: "event", custom_evars: "eVar", custom_props: "prop" };
  var SOLUTION_ORDER = [
    "Reserved Traffic Events", "Reserved Purchase Events", "App Lifecycle Events",
    "Heartbeat Events", "Reserved eVars", "List Variables", "App Lifecycle Dimensions",
    "Heartbeat Dimensions", "Reserved Props", "Activity Map Instances",
    "Target Integration Instances", "AMO Integration Instances"
  ];

  // ---- small DOM helpers ---------------------------------------------------
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) {
          node.setAttribute(k, attrs[k] === true ? "" : String(attrs[k]));
        }
      });
    }
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function sv(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, String(attrs[k])); });
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function append(parent, children) {
    children.forEach(function (c) { if (c) parent.appendChild(c); });
    return parent;
  }
  function byId(id) { return document.getElementById(id); }

  // ---- formatting ----------------------------------------------------------
  var numFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  function fmtNum(v) {
    return v === null || v === undefined ? "No data" : numFmt.format(v);
  }
  // Averages and model values: whole numbers from 100 up, one decimal below that.
  var roundBig = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var roundSmall = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  function fmtRound(v) {
    if (v === null || v === undefined) return "No data";
    return Math.abs(v) >= 100 ? roundBig.format(v) : roundSmall.format(v);
  }
  function fmtShort(v) {
    var a = Math.abs(v);
    if (a >= 1e9) return trim(v / 1e9) + "B";
    if (a >= 1e6) return trim(v / 1e6) + "M";
    if (a >= 1e3) return trim(v / 1e3) + "K";
    return trim(v);
  }
  function trim(v) { return String(Math.round(v * 10) / 10); }
  function fmtPct(p) {
    if (p === null || p === undefined) return "";
    var n = Math.round(p * 1000) / 10;
    return (n > 0 ? "+" : "") + n + "%";
  }
  function parseDay(iso) {
    var p = iso.split("-");
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
  }
  function fmtDay(iso, withYear) {
    var opts = { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };
    if (withYear) opts.year = "numeric";
    return parseDay(iso).toLocaleDateString(undefined, opts);
  }
  function fmtShortDay(iso) {
    return parseDay(iso).toLocaleDateString(undefined,
      { month: "short", day: "numeric", timeZone: "UTC" });
  }
  function numberLabel(row) {
    var prefix = NUMBER_PREFIX[row.group];
    return prefix && row.number !== null && row.number !== undefined ? prefix + row.number : "";
  }
  function componentLabel(row) {
    var n = numberLabel(row);
    return n ? n + ": " + row.name : row.name;
  }

  // ---- light / dark toggle ---------------------------------------------------
  // "auto" follows the computer's setting. The choice is kept in this browser only.
  var THEME_KEY = "ahd-theme";
  function applyTheme(choice) {
    if (choice === "light" || choice === "dark") {
      document.documentElement.setAttribute("data-theme", choice);
    } else {
      choice = "auto";
      document.documentElement.removeAttribute("data-theme");
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-choice]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-theme-choice") === choice ? "true" : "false");
    });
    return choice;
  }
  var savedTheme = null;
  try { savedTheme = window.localStorage.getItem(THEME_KEY); } catch (e) { savedTheme = null; }
  applyTheme(savedTheme);
  Array.prototype.forEach.call(document.querySelectorAll("[data-theme-choice]"), function (b) {
    b.addEventListener("click", function () {
      var choice = applyTheme(b.getAttribute("data-theme-choice"));
      try { window.localStorage.setItem(THEME_KEY, choice); } catch (e) { /* storage blocked */ }
    });
  });

  // ---- state ---------------------------------------------------------------
  var dayKeys = Object.keys(data.views).filter(function (k) { return k.indexOf("day:") === 0; })
    .map(function (k) { return k.slice(4); }).sort().reverse();
  var yesterday = dayKeys[0] || "";
  var state = { gran: "day", day: yesterday, range: "", flagged: false };
  var sortState = {};
  var anomalyBlocked = (data.issues || []).some(function (i) {
    return i.code === "anomaly_not_allowed";
  });

  // ---- tabs ----------------------------------------------------------------
  var redrawers = [];
  function redrawAll() { redrawers.forEach(function (f) { f(); }); }
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawAll, 150);
  });

  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function selectTab(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      byId(t.getAttribute("aria-controls")).hidden = !on;
    });
    redrawAll();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { selectTab(t); });
    t.addEventListener("keydown", function (e) {
      var next = null;
      if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
      if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
      if (next) { e.preventDefault(); selectTab(next); next.focus(); }
    });
  });

  // ---- tables --------------------------------------------------------------
  // A cell is text, a DOM node, or {c: content, k: sort key, cls: class, a: shade 0..1}.
  function isSpec(c) { return c !== null && typeof c === "object" && !(c instanceof Node); }
  function numSort(h) { return !!(h.num || h.numSort); }
  function cellContent(c) { return isSpec(c) ? c.c : c; }
  function cellKey(c) {
    if (isSpec(c) && "k" in c) return c.k;
    var v = cellContent(c);
    if (v instanceof Node) return v.textContent;
    return v;
  }
  function compareKeys(a, b) {
    var an = a === null || a === undefined || a === "", bn = b === null || b === undefined || b === "";
    if (an || bn) return an === bn ? 0 : an ? 1 : -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  }

  // Tables start in their usual order. A heading click sorts (numbers largest first, text A to Z),
  // a second click reverses, a third goes back to the usual order.
  function table(headers, rows, foot, id) {
    var wrap = el("div", { "class": "table-wrap" });
    var outer = document.createDocumentFragment();
    var t = el("table");
    var thead = el("thead");
    var tr = el("tr");
    var tbody = el("tbody");
    var note = el("p", { "class": "sort-note", hidden: true });
    var st = (id && sortState[id]) || { col: -1, dir: 0 };
    function cls(h) { return h ? (h.num ? "num" : h.cls || null) : null; }

    function buildRow(cells) {
      var r = el("tr");
      cells.forEach(function (c, i) {
        var spec = isSpec(c) ? c : null;
        var classes = [cls(headers[i]), spec && spec.cls].filter(Boolean).join(" ") || null;
        var td = el("td", { "class": classes });
        var content = cellContent(c);
        if (content instanceof Node) td.appendChild(content);
        else td.textContent = content === null || content === undefined ? "" : content;
        if (spec && typeof spec.a === "number") td.style.setProperty("--a", spec.a.toFixed(3));
        r.appendChild(td);
      });
      return r;
    }
    var built = rows.map(buildRow);

    function paint() {
      var order = rows.map(function (_, i) { return i; });
      if (st.dir) {
        var asc = numSort(headers[st.col]) ? st.dir === 2 : st.dir === 1;
        order.sort(function (a, b) {
          var ka = cellKey(rows[a][st.col]), kb = cellKey(rows[b][st.col]);
          var aNull = ka === null || ka === undefined || ka === "";
          var bNull = kb === null || kb === undefined || kb === "";
          if (aNull || bNull) return aNull === bNull ? a - b : aNull ? 1 : -1;  // blanks stay last
          var d = compareKeys(ka, kb);
          return (asc ? d : -d) || a - b;
        });
      }
      clear(tbody);
      order.forEach(function (i) { tbody.appendChild(built[i]); });
      ths.forEach(function (th, i) {
        var on = st.dir && st.col === i;
        var up = on && (numSort(headers[i]) ? st.dir === 2 : st.dir === 1);
        th.setAttribute("aria-sort", on ? (up ? "ascending" : "descending") : "none");
        var mark = th.querySelector(".sort-mark");
        if (mark) mark.textContent = on ? (up ? "▲" : "▼") : "↕";
      });
      clear(note);
      if (st.dir) {
        var h = headers[st.col];
        var how = numSort(h) ? (st.dir === 1 ? "largest first" : "smallest first") :
          (st.dir === 1 ? "A to Z" : "Z to A");
        note.appendChild(document.createTextNode("Sorted by " + h.label + ", " + how + "."));
        var reset = el("button", { type: "button" }, "Usual order");
        reset.addEventListener("click", function () { setSort(-1, 0); });
        note.appendChild(reset);
        note.hidden = false;
      } else {
        note.hidden = true;
      }
    }
    function setSort(col, dir) {
      st = { col: col, dir: dir };
      if (id) sortState[id] = st;
      paint();
    }

    var ths = headers.map(function (h, i) {
      var th = el("th", { scope: "col", "class": cls(h) });
      if (h.sort === false || rows.length < 2) {
        th.textContent = h.label;
      } else {
        var b = el("button", { type: "button", "class": "sort", title: "Sort by " + h.label });
        b.appendChild(el("span", null, h.label));
        b.appendChild(el("span", { "class": "sort-mark", "aria-hidden": "true" }, "↕"));
        b.addEventListener("click", function () {
          setSort(i, st.col === i ? (st.dir + 1) % 3 : 1);
        });
        th.appendChild(b);
      }
      tr.appendChild(th);
      return th;
    });
    if (st.col >= headers.length) st = { col: -1, dir: 0 };
    thead.appendChild(tr);
    append(t, [thead, tbody]);
    if (foot) {
      var tfoot = el("tfoot");
      var fr = el("tr");
      foot.forEach(function (c, i) {
        fr.appendChild(el("td", { "class": cls(headers[i]) }, c));
      });
      tfoot.appendChild(fr);
      t.appendChild(tfoot);
    }
    paint();
    wrap.appendChild(t);
    outer.appendChild(wrap);
    outer.appendChild(note);
    return outer;
  }

  function badge(kind, icon, label, title) {
    var b = el("span", { "class": "badge badge-" + kind, title: title || null });
    b.appendChild(el("span", { "class": "icon", "aria-hidden": "true" }, icon));
    b.appendChild(document.createTextNode(label));
    return b;
  }
  function flagBadge(a) {
    if (a === "high") return badge("flag", "▲", "Above range", "Adobe flagged this value as above the expected range.");
    if (a === "low") return badge("flag", "▼", "Below range", "Adobe flagged this value as below the expected range.");
    return null;
  }
  function zeroBadge() { return badge("zero", "●", "Zero data", "This component had a value of 0."); }

  function changeCell(arrow, pct) {
    var span = el("span");
    if (arrow === "up" || arrow === "down") {
      span.appendChild(el("span", { "class": "arrow", "aria-hidden": "true" },
        arrow === "up" ? "▲ " : "▼ "));
    }
    span.appendChild(document.createTextNode(fmtPct(pct)));
    return { c: span, k: pct === null || pct === undefined ? null : pct };
  }

  function devCell(arrow, pct) {
    if (pct === null || pct === undefined) return { c: "", k: null };
    var wrap = el("span", { "class": "dev" });
    var bar = el("span", { "class": "devbar", "aria-hidden": "true" });
    var fill = el("span", { "class": pct < 0 ? "neg" : "pos" });
    var w = Math.min(Math.abs(pct), 1) * 50;
    fill.style.width = Math.max(w, 1.5) + "%";
    if (pct < 0) fill.style.right = "50%"; else fill.style.left = "50%";
    bar.appendChild(fill);
    wrap.appendChild(bar);
    wrap.appendChild(changeCell(arrow, pct).c);
    return { c: wrap, k: pct };
  }

  function nameCell(row) {
    if (!row.zero) return { c: row.name, k: row.name };
    var span = el("span");
    span.appendChild(document.createTextNode(row.name));
    span.appendChild(zeroBadge());
    return { c: span, k: row.name };
  }

  function anomalyCell(a) {
    var key = a === "high" ? 2 : a === "low" ? 1 : null;
    var b = flagBadge(a);
    if (b) return { c: b, k: key };
    return { c: anomalyBlocked ? el("span", { "class": "muted" }, "Not available") : "", k: null };
  }

  function isFlagged(row) { return !!(row.anomaly || row.zero); }

  function inRange(row) {
    if (!state.range || CUSTOM.indexOf(row.group) < 0) return true;
    var p = state.range.split("-");
    return row.number !== null && row.number >= +p[0] && row.number <= +p[1];
  }

  function componentTable(rows, custom, id) {
    var daily = state.gran === "day";
    var headers = [];
    if (custom) headers.push({ label: "Number" });
    headers.push({ label: "Name", cls: "name" });
    headers.push({ label: daily ? "Total for the day" : "Total for the week", num: true });
    headers.push({
      label: daily ? "Usual for this weekday (12-week average)" : "Usual week (12-week average)",
      num: true
    });
    headers.push({ label: "Change from usual", num: true });
    if (daily) headers.push({ label: "Flag" });
    var body = rows.map(function (r) {
      var cells = [];
      if (custom) cells.push({ c: numberLabel(r), k: r.number });
      cells.push(nameCell(r));
      cells.push({ c: fmtNum(r.value), k: r.value });
      cells.push({ c: fmtRound(r.baseline), k: r.baseline });
      cells.push(changeCell(r.arrow, r.pct));
      if (daily) cells.push(anomalyCell(r.anomaly));
      return cells;
    });
    return table(headers, body, null, id);
  }

  function groupBlock(title, rows, custom, emptyText) {
    var frag = document.createDocumentFragment();
    var h = el("h3", { "class": "group-title" }, title);
    if (rows.length) h.appendChild(el("span", { "class": "group-count" },
      rows.length === 1 ? "1 component" : rows.length + " components"));
    frag.appendChild(h);
    frag.appendChild(rows.length ? componentTable(rows, custom, "group:" + title) :
      el("p", { "class": "empty" }, emptyText));
    return frag;
  }

  function currentRows() {
    return (state.gran === "week" ? data.views.week : data.views["day:" + state.day]) || [];
  }

  function renderTables() {
    var host = byId("tables");
    clear(host);
    var rows = currentRows();
    if (state.flagged) rows = rows.filter(isFlagged);
    var byGroup = {};
    rows.forEach(function (r) { (byGroup[r.group] = byGroup[r.group] || []).push(r); });
    var allRows = currentRows();
    var hasGroup = {};
    allRows.forEach(function (r) { hasGroup[r.group] = true; });

    host.appendChild(el("h2", null, "Custom components"));
    CUSTOM.forEach(function (g) {
      var all = byGroup[g] || [];
      var shown = all.filter(inRange);
      var empty;
      if (!hasGroup[g]) empty = "None were found. See the status section if you expected some.";
      else if (state.flagged && !all.length) empty = "Nothing flagged in this group.";
      else empty = "None in this number range.";
      host.appendChild(groupBlock(CUSTOM_TITLES[g], shown, true, empty));
    });

    host.appendChild(el("h2", null, "Solution components"));
    var known = SOLUTION_ORDER.slice();
    Object.keys(byGroup).forEach(function (g) {
      if (CUSTOM.indexOf(g) < 0 && known.indexOf(g) < 0) known.push(g);
    });
    var any = false;
    known.forEach(function (g) {
      if (!byGroup[g] || !byGroup[g].length) return;
      any = true;
      host.appendChild(groupBlock(g, byGroup[g], false, ""));
    });
    if (!any) host.appendChild(el("p", { "class": "empty" },
      state.flagged ? "Nothing flagged in the solution components." : "None were found."));
  }

  // ---- charts --------------------------------------------------------------
  function niceStep(max, count) {
    var raw = max / count;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    var step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
    return step * mag;
  }

  function segments(points, pick) {
    var out = [], cur = [];
    points.forEach(function (p) {
      var v = pick(p);
      if (v === null || v === undefined) { if (cur.length) out.push(cur); cur = []; }
      else cur.push(p);
    });
    if (cur.length) out.push(cur);
    return out;
  }

  function legendItem(kind, label) {
    var li = el("li");
    var s = sv("svg", { viewBox: "0 0 18 10", "aria-hidden": "true" });
    if (kind === "actual") s.appendChild(sv("line", { x1: 1, y1: 5, x2: 17, y2: 5, "class": "actual" }));
    if (kind === "expected") s.appendChild(sv("line", { x1: 1, y1: 5, x2: 17, y2: 5, "class": "expected" }));
    if (kind === "band") s.appendChild(sv("rect", { x: 1, y: 1, width: 16, height: 8, "class": "band" }));
    if (kind === "high") s.appendChild(sv("path", { d: "M9 0.5 L14.5 9.5 L3.5 9.5 Z", "class": "mark-high" }));
    if (kind === "low") s.appendChild(sv("path", { d: "M9 9.5 L14.5 0.5 L3.5 0.5 Z", "class": "mark-low" }));
    li.appendChild(s);
    li.appendChild(el("span", null, label));
    return li;
  }

  function describe(p) {
    var parts = [fmtDay(p[0], true) + ": " + fmtNum(p[1])];
    if (p[2] !== null) parts.push("expected " + fmtRound(p[2]));
    if (p[3] !== null && p[4] !== null) {
      parts.push("expected range " + fmtRound(p[4]) + " to " + fmtRound(p[3]));
    }
    var text = parts.join(", ");
    if (p[5] === "high") text += ". Above the expected range.";
    else if (p[5] === "low") text += ". Below the expected range.";
    return text;
  }

  function drawChart(host, points, title) {
    clear(host);
    points = (points || []).filter(function (p) { return p && p[0]; });
    if (!points.length) {
      host.appendChild(el("p", { "class": "chart-empty" }, "No trend data was read for this item."));
      return;
    }
    // Draw at the real pixel width so the axis text stays readable on a phone.
    var W = Math.max(280, Math.round(host.clientWidth || 640)), H = W < 480 ? 200 : 240;
    var L = 40, R = 10, T = 12, B = 26;
    var pw = W - L - R, ph = H - T - B;
    var t0 = parseDay(points[0][0]).getTime();
    var span = Math.max(1, (parseDay(points[points.length - 1][0]).getTime() - t0) / 864e5);
    function x(p) { return L + ((parseDay(p[0]).getTime() - t0) / 864e5) / span * pw; }
    var hasBands = points.some(function (p) { return p[3] !== null && p[4] !== null; });
    var hasExpected = points.some(function (p) { return p[2] !== null; });
    var max = 0;
    points.forEach(function (p) {
      [p[1], p[2], p[3]].forEach(function (v) { if (v !== null && v > max) max = v; });
    });
    var min = 0;
    points.forEach(function (p) {
      [p[1], p[4]].forEach(function (v) { if (v !== null && v < min) min = v; });
    });
    if (max === min) max = min + 1;
    var step = niceStep(max - min, 4);
    var top = Math.ceil(max / step) * step;
    var bottom = Math.floor(min / step) * step;
    function y(v) { return T + (top - v) / (top - bottom) * ph; }

    var svg = sv("svg", {
      viewBox: "0 0 " + W + " " + H, role: "img", tabindex: 0,
      "aria-label": "Trend chart for " + title + ", " + points.length + " days. " +
        "Use the left and right arrow keys to read each day."
    });

    for (var v = bottom; v <= top + step / 2; v += step) {
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v),
        "class": Math.abs(v) < step / 2 ? "base" : "grid" }));
      var lab = sv("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end", "class": "axis" });
      lab.textContent = fmtShort(v);
      svg.appendChild(lab);
    }
    var ticks = W < 480 ? 3 : 5;
    for (var i = 0; i < ticks; i++) {
      var idx = Math.round(i * (points.length - 1) / (ticks - 1));
      var p = points[idx];
      var tx = sv("text", {
        x: x(p), y: H - 8, "class": "axis",
        "text-anchor": i === 0 ? "start" : i === ticks - 1 ? "end" : "middle"
      });
      tx.textContent = fmtShortDay(p[0]);
      svg.appendChild(tx);
    }

    if (hasBands) {
      segments(points, function (p) { return p[3] !== null && p[4] !== null ? 1 : null; })
        .forEach(function (seg) {
          var d = seg.map(function (p, j) { return (j ? "L" : "M") + x(p) + " " + y(p[3]); }).join(" ");
          d += " " + seg.slice().reverse().map(function (p) { return "L" + x(p) + " " + y(p[4]); }).join(" ");
          svg.appendChild(sv("path", { d: d + " Z", "class": "band" }));
        });
    }
    if (hasExpected) {
      segments(points, function (p) { return p[2]; }).forEach(function (seg) {
        var d = seg.map(function (p, j) { return (j ? "L" : "M") + x(p) + " " + y(p[2]); }).join(" ");
        svg.appendChild(sv("path", { d: d, "class": "expected" }));
      });
    }
    segments(points, function (p) { return p[1]; }).forEach(function (seg) {
      var d = seg.map(function (p, j) { return (j ? "L" : "M") + x(p) + " " + y(p[1]); }).join(" ");
      if (seg.length === 1) d += " l0.01 0";
      svg.appendChild(sv("path", { d: d, "class": "actual" }));
    });
    var anyHigh = false, anyLow = false;
    points.forEach(function (p) {
      if (p[1] === null) return;
      var cx = x(p), cy = y(p[1]);
      if (p[5] === "high") {
        anyHigh = true;
        svg.appendChild(sv("path", {
          d: "M" + cx + " " + (cy - 8) + " L" + (cx + 6) + " " + (cy + 4) + " L" + (cx - 6) + " " + (cy + 4) + " Z",
          "class": "mark-high"
        }));
      } else if (p[5] === "low") {
        anyLow = true;
        svg.appendChild(sv("path", {
          d: "M" + cx + " " + (cy + 8) + " L" + (cx + 6) + " " + (cy - 4) + " L" + (cx - 6) + " " + (cy - 4) + " Z",
          "class": "mark-low"
        }));
      }
    });

    // hover and keyboard readout
    var cross = sv("line", { y1: T, y2: T + ph, "class": "cross", visibility: "hidden" });
    var dot = sv("circle", { r: 4, "class": "dot", visibility: "hidden" });
    svg.appendChild(cross);
    svg.appendChild(dot);
    var hit = sv("rect", { x: L, y: 0, width: pw, height: H, fill: "transparent" });
    svg.appendChild(hit);
    var readout = el("p", { "class": "chart-readout", "aria-live": "polite" });
    var last = points.length - 1;
    var active = last;
    function show(k, visible) {
      active = Math.max(0, Math.min(last, k));
      var p = points[active];
      readout.textContent = describe(p);
      var cx = x(p);
      cross.setAttribute("x1", cx);
      cross.setAttribute("x2", cx);
      cross.setAttribute("visibility", visible ? "visible" : "hidden");
      if (p[1] !== null) {
        dot.setAttribute("cx", cx);
        dot.setAttribute("cy", y(p[1]));
        dot.setAttribute("visibility", visible ? "visible" : "hidden");
      } else {
        dot.setAttribute("visibility", "hidden");
      }
    }
    function nearest(evt) {
      var box = svg.getBoundingClientRect();
      var px = (evt.clientX - box.left) / box.width * W;
      var best = 0, bestD = Infinity;
      points.forEach(function (p, k) {
        var d = Math.abs(x(p) - px);
        if (d < bestD) { bestD = d; best = k; }
      });
      return best;
    }
    svg.addEventListener("pointermove", function (e) { show(nearest(e), true); });
    svg.addEventListener("pointerleave", function () { show(last, false); });
    svg.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { e.preventDefault(); show(active - 1, true); }
      if (e.key === "ArrowRight") { e.preventDefault(); show(active + 1, true); }
    });
    svg.addEventListener("blur", function () { show(last, false); });
    show(last, false);

    host.appendChild(svg);
    host.appendChild(readout);
    var legend = el("ul", { "class": "legend" });
    legend.appendChild(legendItem("actual", "Actual"));
    if (hasExpected) legend.appendChild(legendItem("expected", "Expected"));
    if (hasBands) legend.appendChild(legendItem("band", "Expected range"));
    if (anyHigh) legend.appendChild(legendItem("high", "Flagged above range"));
    if (anyLow) legend.appendChild(legendItem("low", "Flagged below range"));
    host.appendChild(legend);
    if (!hasBands) {
      host.appendChild(byId("no-bands-template").content.cloneNode(true));
    }
  }

  // The type filter in the Dimension card sorts dimensions by their group.
  var EVAR_GROUPS = ["custom_evars", "Reserved eVars"];
  var PROP_GROUPS = ["custom_props", "Reserved Props"];
  function typeOf(r) {
    if (EVAR_GROUPS.indexOf(r.group) >= 0) return "evars";
    if (PROP_GROUPS.indexOf(r.group) >= 0) return "props";
    return "other";
  }
  var TYPE_EMPTY = {
    evars: "No eVar data was read.", props: "No prop data was read.",
    other: "No other dimension data was read."
  };

  function fillPicker(select, kind, chartId, typeSelect) {
    var all = (data.views.week || []).filter(function (r) {
      return r.kind === kind && data.series[r.component_id];
    });
    var rows = all;
    var host = byId(chartId);

    function fill() {
      var keep = rows[select.selectedIndex];
      var type = typeSelect ? typeSelect.value : "";
      rows = all.filter(function (r) { return !type || typeOf(r) === type; });
      clear(select);
      var group = null, parent = select;
      rows.forEach(function (r) {
        if (r.group !== group) {
          group = r.group;
          parent = el("optgroup", { label: CUSTOM_TITLES[group] || group });
          select.appendChild(parent);
        }
        parent.appendChild(el("option", { value: r.component_id }, componentLabel(r)));
      });
      var at = keep ? rows.indexOf(keep) : -1;
      select.selectedIndex = at >= 0 ? at : (rows.length ? 0 : -1);
      select.disabled = !rows.length;
    }
    function draw() {
      var r = rows[select.selectedIndex];
      if (!r) {
        clear(host);
        var type = typeSelect ? typeSelect.value : "";
        host.appendChild(el("p", { "class": "chart-empty" }, TYPE_EMPTY[type] ||
          (kind === "metric" ? "No metric data was read." : "No dimension data was read.")));
        return;
      }
      drawChart(host, data.series[r.component_id], componentLabel(r));
    }
    fill();
    select.addEventListener("change", draw);
    if (typeSelect) {
      typeSelect.disabled = !all.length;
      typeSelect.addEventListener("change", function () { fill(); draw(); });
    }
    redrawers.push(draw);
    draw();
  }

  // ---- individual component tab ---------------------------------------------
  function movementCell(m) {
    if (m === "*") return { c: el("span", { title: "New to the top 100" }, "* new"), k: 1e9 };
    if (m === "-" || m === 0) return { c: el("span", { title: "No change" }, "-"), k: 0 };
    if (typeof m === "number") {
      var span = el("span", { title: m > 0 ? "Moved up" : "Moved down" });
      span.appendChild(el("span", { "class": "arrow", "aria-hidden": "true" }, m > 0 ? "▲ " : "▼ "));
      span.appendChild(document.createTextNode((m > 0 ? "up " : "down ") + Math.abs(m)));
      return { c: span, k: m };
    }
    return { c: String(m), k: null };
  }

  function drawIndividual() {
    var run = data.run || {};
    var dim = run.dimension_name || run.dimension;
    var metric = run.metric_name || run.metric;
    drawChart(byId("individual-chart"), data.series[data.individual || "individual"],
      dim && metric ? dim + " by " + metric : "the chosen dimension and metric");
  }

  function renderIndividual() {
    redrawers.push(drawIndividual);
    drawIndividual();
    var top = byId("top100");
    clear(top);
    var rows = data.top100 || [];
    if (!rows.length) {
      top.appendChild(el("p", { "class": "empty" }, "No top values were returned."));
    } else {
      top.appendChild(table(
        [{ label: "Movement", numSort: true }, { label: "Rank", num: true }, { label: "Value", cls: "name" },
         { label: "Count", num: true }],
        rows.map(function (r) {
          return [movementCell(r.movement), { c: String(r.rank), k: r.rank }, r.item,
            { c: fmtNum(r.value), k: r.value }];
        }), null, "top100"));
    }
    ["browser", "device"].forEach(function (kind) {
      var host = byId(kind);
      clear(host);
      var b = data[kind] || { labels: [], rows: [], totals: [] };
      if (!b.rows.length) {
        host.appendChild(el("p", { "class": "empty" }, "No data was returned."));
        return;
      }
      var headers = [{ label: "Value", cls: "name" }].concat(b.labels.map(function (l) {
        return { label: l, num: true };
      }));
      // One blue shade per column: the largest value in the column is the deepest.
      var colMax = b.labels.map(function (_, j) {
        return b.rows.reduce(function (m, r) { return Math.max(m, r.values[j] || 0); }, 0);
      });
      var body = b.rows.map(function (r) {
        return [r.item].concat(r.values.map(function (v, j) {
          var a = colMax[j] > 0 && v ? 0.04 + 0.30 * (v / colMax[j]) : 0;
          return { c: fmtNum(v), k: v, cls: "shade", a: a };
        }));
      });
      var foot = b.totals && b.totals.length ? ["Total"].concat(b.totals.map(fmtNum)) : null;
      host.appendChild(table(headers, body, foot, kind));
    });
  }

  // ---- alerts tab ------------------------------------------------------------
  function alertReason(a) {
    if (a.anomaly === "low") return a.actual === 0 ? "below the expected range, value is 0" :
      "below the expected range";
    if (a.anomaly === "high") return "above the expected range";
    return "value is 0";
  }

  function splitAlerts() {
    var alerts = data.alerts || [];
    return {
      unusual: alerts.filter(function (a) { return !!a.anomaly; }),
      zero: alerts.filter(function (a) { return !a.anomaly; })
    };
  }

  function alertLine(a) {
    var s = "- " + a.name + " (" + a.component_id + "): " + alertReason(a) +
      ". Actual " + fmtNum(a.actual);
    if (a.expected !== null) s += ", expected " + fmtRound(a.expected);
    if (a.pct_vs_expected !== null) s += ", change " + fmtPct(a.pct_vs_expected);
    return s + ".";
  }

  function alertText() {
    var run = data.run || {};
    var parts = splitAlerts();
    var lines = [];
    lines.push("Analytics Health alerts for " + (run.rsid_name || run.rsid) + " (" + run.rsid +
      "), " + yesterday);
    lines.push("Segment: " + (run.segment_id || "none (all visits)"));
    if (!parts.unusual.length && !parts.zero.length) {
      lines.push("No alerts for this day.");
    } else {
      if (parts.unusual.length) {
        lines.push("Unusual values (" + parts.unusual.length + "):");
        parts.unusual.forEach(function (a) { lines.push(alertLine(a)); });
      }
      if (parts.zero.length) {
        lines.push("Zero data (" + parts.zero.length + "):");
        parts.zero.forEach(function (a) { lines.push(alertLine(a)); });
      }
    }
    if ((data.issues || []).length) {
      lines.push("Some data could not be read in this run. See the status section of the report.");
    }
    return lines.join("\n");
  }

  function renderAlerts() {
    var host = byId("alerts");
    clear(host);
    var parts = splitAlerts();

    var g1 = el("section", { "class": "alert-group", "aria-labelledby": "unusual-title" });
    var h1 = el("h3", { "class": "group-title", id: "unusual-title" }, "Unusual values");
    h1.appendChild(el("span", { "class": "group-count" },
      parts.unusual.length === 1 ? "1 component" : parts.unusual.length + " components"));
    g1.appendChild(h1);
    if (parts.unusual.length) {
      g1.appendChild(el("p", { "class": "hint" },
        "Adobe flagged these values as outside the expected range. The bar shows the size of " +
        "the change from the expected value, up to 100 percent."));
      g1.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" }, { label: "Flag" },
         { label: "Expected", num: true }, { label: "Actual", num: true },
         { label: "Change from expected", num: true }],
        parts.unusual.map(function (a) {
          return [{ c: a.name, k: a.name }, { c: el("code", null, a.component_id), k: a.component_id },
            anomalyCell(a.anomaly),
            { c: a.expected === null ? "Not available" : fmtRound(a.expected), k: a.expected },
            { c: fmtNum(a.actual), k: a.actual }, devCell(a.arrow, a.pct_vs_expected)];
        }), null, "alerts-unusual"));
    } else {
      g1.appendChild(el("p", { "class": "empty" }, anomalyBlocked ?
        "Anomaly detection is not available for this run, so no values could be flagged. " +
        "See the status section." : "Adobe flagged no unusual values yesterday."));
    }
    host.appendChild(g1);

    var g2 = el("details", { "class": "alert-group" });
    if (parts.zero.length && parts.zero.length <= 10) g2.open = true;
    var expectedZero = parts.zero.filter(function (a) { return a.expected === 0; }).length;
    g2.appendChild(el("summary", null, "Zero data (" + parts.zero.length + ")"));
    if (parts.zero.length) {
      var note = expectedZero === parts.zero.length ?
        "These components had a value of 0 yesterday. Adobe expected 0 for all of them, so " +
        "they may be unused." :
        "These components had a value of 0 yesterday. " + (parts.zero.length - expectedZero) +
        (parts.zero.length - expectedZero === 1 ? " of them was" : " of them were") +
        " expected to have a value, so check those first.";
      g2.appendChild(el("p", { "class": "hint" }, note));
      g2.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Expected", num: true }, { label: "Actual", num: true }],
        parts.zero.map(function (a) {
          var name = el("span");
          name.appendChild(document.createTextNode(a.name));
          name.appendChild(zeroBadge());
          return [{ c: name, k: a.name }, { c: el("code", null, a.component_id), k: a.component_id },
            { c: a.expected === null ? "Not available" : fmtRound(a.expected), k: a.expected },
            { c: fmtNum(a.actual), k: a.actual }];
        }), null, "alerts-zero"));
    } else {
      g2.appendChild(el("p", { "class": "empty" }, "No component had a value of 0 yesterday."));
    }
    host.appendChild(g2);

    var text = alertText();
    byId("alert-text").value = text;
    var result = byId("copy-result");
    byId("copy-alerts").addEventListener("click", function () {
      function failed() {
        result.textContent = "Could not copy. Open \"Show the alert summary as text\" below " +
          "and copy it from there.";
        document.querySelector(".copy-fallback").open = true;
      }
      if (!navigator.clipboard || !navigator.clipboard.writeText) { failed(); return; }
      navigator.clipboard.writeText(text).then(function () {
        result.textContent = "Copied. You can paste it into an email or chat.";
      }, failed);
    });
  }

  // ---- summary tiles ------------------------------------------------------------
  function renderTiles() {
    var host = byId("tiles");
    clear(host);
    var rows = data.views["day:" + yesterday] || [];
    var high = rows.filter(function (r) { return r.anomaly === "high"; }).length;
    var low = rows.filter(function (r) { return r.anomaly === "low"; }).length;
    var zero = rows.filter(function (r) { return r.zero; }).length;
    var problems = (data.issues || []).length;
    var dayText = yesterday ? fmtDay(yesterday, false) : "";

    function tile(kind, icon, label, value, note, onClick) {
      var t = el(onClick ? "button" : "div", { "class": "tile tile-" + kind,
        type: onClick ? "button" : null });
      var l = el("span", { "class": "tile-label" });
      l.appendChild(el("span", { "class": "icon", "aria-hidden": "true" }, icon));
      l.appendChild(document.createTextNode(label));
      append(t, [l, el("span", { "class": "tile-value" }, value),
        note ? el("span", { "class": "tile-note" }, note) : null]);
      if (onClick) t.addEventListener("click", onClick);
      host.appendChild(t);
    }
    function toAlerts() { var t = byId("tab-alerts"); selectTab(t); t.focus(); }
    function toStatus() { byId("status").scrollIntoView(); }

    tile("plain", "●", "Components checked", numFmt.format(rows.length), dayText);
    if (anomalyBlocked) {
      tile("flag", "▲", "Above expected range", "–", "Anomaly detection not available");
      tile("flag", "▼", "Below expected range", "–", "Anomaly detection not available");
    } else {
      tile("flag", "▲", "Above expected range", numFmt.format(high), "Flagged by Adobe", toAlerts);
      tile("flag", "▼", "Below expected range", numFmt.format(low), "Flagged by Adobe", toAlerts);
    }
    tile("zero", "●", "Zero data", numFmt.format(zero), "Value of 0 " + (dayText ? "on " + dayText : ""),
      toAlerts);
    tile(problems ? "flag" : "good", problems ? "!" : "✓", "Problems in this run",
      numFmt.format(problems), problems ? "See the status section" : "Everything was read",
      problems ? toStatus : null);
  }

  // ---- controls --------------------------------------------------------------
  var daySel = byId("day");
  dayKeys.forEach(function (d, i) {
    daySel.appendChild(el("option", { value: d },
      fmtDay(d, true) + (i === 0 ? " (yesterday)" : "")));
  });
  daySel.addEventListener("change", function () { state.day = daySel.value; renderTables(); });
  byId("granularity").addEventListener("change", function (e) {
    state.gran = e.target.value;
    byId("week-note").hidden = state.gran !== "week";
    byId("day-label").hidden = state.gran === "week";
    renderTables();
  });
  byId("range").addEventListener("change", function (e) {
    state.range = e.target.value;
    renderTables();
  });
  byId("flagged-only").addEventListener("change", function (e) {
    state.flagged = e.target.checked;
    renderTables();
  });

  renderTiles();
  renderTables();
  fillPicker(byId("metric-pick"), "metric", "metric-chart");
  fillPicker(byId("dimension-pick"), "dimension", "dimension-chart", byId("dimension-type"));
  renderIndividual();
  renderAlerts();
})();
