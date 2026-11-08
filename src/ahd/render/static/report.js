/* Analytics Health Dashboard page script.
 * Safety: every piece of data is set with textContent or setAttribute on elements built
 * with DOM calls. Nothing here parses data as HTML.
 */
(function () {
  "use strict";

  // ---- unpack the page data ------------------------------------------------
  // html.py packs the data to keep the file small: each component's name and group once in
  // data.components, table rows and alerts as lists, and each chart as one list per field.
  // This turns it back into the shape build_view returns. data.packed names the fields.
  function unpackView(d) {
    var f = d.packed;
    if (!f) return d;
    function obj(names, list, into) {
      var o = into || {};
      names.forEach(function (name, i) { o[name] = list[i]; });
      return o;
    }
    function withComponent(o) {
      var c = d.components[o.component_id];
      return c ? obj(f.component, c, o) : o;
    }
    Object.keys(d.views).forEach(function (k) {
      d.views[k] = d.views[k].map(function (r) { return withComponent(obj(f.row, r)); });
    });
    d.alerts = (d.alerts || []).map(function (a) { return withComponent(obj(f.alert, a)); });
    Object.keys(d.series).forEach(function (cid) {
      var s = d.series[cid], start = Date.parse(s.start + "T00:00:00Z"), points = [];
      s[f.series[0]].forEach(function (_, i) {
        var day = new Date(start + i * 864e5).toISOString().slice(0, 10);
        points.push([day].concat(f.series.map(function (name) { return s[name][i]; })));
      });
      d.series[cid] = points;
    });
    return d;
  }

  var data = unpackView(JSON.parse(document.getElementById("data").textContent));
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
  // Currency metrics use the report suite's currency, e.g. "$1,234.56" for USD and "¥1,235"
  // for JPY. Without opts.digits, a currency keeps its own number of decimals. Without a valid
  // currency code the values show as plain numbers with two decimals.
  function currencyFmt(opts) {
    var code = (data.run || {}).currency;
    function options(withCode) {
      var digits = opts.digits === undefined && !withCode ? 2 : opts.digits;
      var o = opts.compact ? { notation: "compact", maximumFractionDigits: 1 } :
        digits === undefined ? {} :
        { minimumFractionDigits: digits, maximumFractionDigits: digits };
      if (withCode) { o.style = "currency"; o.currency = code; }
      return o;
    }
    try {
      if (code) return new Intl.NumberFormat(undefined, options(true));
    } catch (e) { /* not a currency code the browser knows */ }
    return opts.compact ? null : new Intl.NumberFormat(undefined, options(false));
  }
  var money = currencyFmt({});
  var moneyRound = currencyFmt({ digits: 0 });
  var moneyShort = currencyFmt({ compact: true });
  function fmtNum(v, type) {
    if (v === null || v === undefined) return "No data";
    return (type === "currency" ? money : numFmt).format(v);
  }
  // Averages and model values: whole numbers from 100 up, one decimal below that.
  var roundBig = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var roundSmall = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  function fmtRound(v, type) {
    if (v === null || v === undefined) return "No data";
    if (type === "currency") return (Math.abs(v) >= 100 ? moneyRound : money).format(v);
    return Math.abs(v) >= 100 ? roundBig.format(v) : roundSmall.format(v);
  }
  function fmtShort(v, type) {
    if (type === "currency" && moneyShort) return moneyShort.format(v);
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
  // t: the slider's share. A drop below usual by more than t is flagged, and a change by more
  // than t either way gets an arrow.
  var state = { gran: "day", day: yesterday, alertDay: yesterday, range: "", flagged: false,
    t: typeof data.change_alert === "number" ? data.change_alert : 0.3 };
  var MIN_USUAL = typeof data.min_usual === "number" ? data.min_usual : 10;
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
  function zeroBadge() {
    return badge("zero", "●", "Zero data",
      "This component had a value of 0, but it had data on other days in this run.");
  }
  function unusedMark() {
    return el("span", { "class": "muted unused-badge",
      title: "This component had a value of 0 on every day in this run." }, "never collected data");
  }

  function dropBadge() {
    return badge("flag", "▼", "Large drop", "More than " + Math.round(state.t * 100) +
      "% below the usual value. Set the percent with the slider at the top of the page.");
  }

  function arrowFor(pct) {
    if (pct === null || pct === undefined || Math.abs(pct) <= state.t) return null;
    return pct > 0 ? "up" : "down";
  }
  // A value that fell further below the usual value than the slider allows. Zero is its own alert.
  function isLargeDrop(value, usual, pct) {
    return value !== 0 && pct !== null && pct !== undefined && pct < -state.t &&
      usual !== null && usual >= MIN_USUAL;
  }

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
    span.appendChild(row.unused ? unusedMark() : zeroBadge());
    return { c: span, k: row.name };
  }

  function anomalyCell(a) {
    var key = a === "high" ? 2 : a === "low" ? 1 : null;
    var b = flagBadge(a);
    if (b) return { c: b, k: key };
    return { c: anomalyBlocked ? el("span", { "class": "muted" }, "Not available") : "", k: null };
  }

  // The Flag column: Adobe's anomaly flag (daily only), else a large drop from the slider.
  function flagCell(row, daily) {
    var a = daily ? anomalyCell(row.anomaly) : { c: "", k: null };
    if (row.anomaly) return a;
    if (isLargeDrop(row.value, row.baseline, row.pct)) return { c: dropBadge(), k: 0.5 };
    return a;
  }

  function isFlagged(row) {
    return !!(row.anomaly || (row.zero && !row.unused) ||
      isLargeDrop(row.value, row.baseline, row.pct));
  }

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
    headers.push({ label: "Flag" });
    var body = rows.map(function (r) {
      var cells = [];
      if (custom) cells.push({ c: numberLabel(r), k: r.number });
      cells.push(nameCell(r));
      cells.push({ c: fmtNum(r.value, r.metric_type), k: r.value });
      cells.push({ c: fmtRound(r.baseline, r.metric_type), k: r.baseline });
      cells.push(changeCell(arrowFor(r.pct), r.pct));
      cells.push(flagCell(r, daily));
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

  function renderSliderResult() {
    var when = state.gran === "week" ? "The week ending " + fmtDay(yesterday, false) :
      fmtDay(state.day, false) + (state.day === yesterday ? " (yesterday)" : "");
    byId("change-alert-result").textContent = dropSummary(currentRows(), when);
  }

  function renderTables() {
    renderSliderResult();
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

  function describe(p, type) {
    var parts = [fmtDay(p[0], true) + ": " + fmtNum(p[1], type)];
    if (p[2] !== null) parts.push("expected " + fmtRound(p[2], type));
    if (p[3] !== null && p[4] !== null) {
      parts.push("expected range " + fmtRound(p[4], type) + " to " + fmtRound(p[3], type));
    }
    var text = parts.join(", ");
    if (p[5] === "high") text += ". Above the expected range.";
    else if (p[5] === "low") text += ". Below the expected range.";
    return text;
  }

  function drawChart(host, points, title, type) {
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
      lab.textContent = fmtShort(v, type);
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
      readout.textContent = describe(p, type);
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

  // The type filter in the Dimension card sorts dimensions into eVars, props and other.
  // The type filter in the Metric card lists the metric groups found in this run.
  var EVAR_GROUPS = ["custom_evars", "Reserved eVars"];
  var PROP_GROUPS = ["custom_props", "Reserved Props"];
  function typeOf(r) {
    if (r.kind === "metric") return r.group;
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
    if (typeSelect && kind === "metric") {
      // One choice per metric group that has data, in report order.
      var seen = {};
      all.forEach(function (r) {
        if (seen[r.group]) return;
        seen[r.group] = true;
        typeSelect.appendChild(el("option", { value: r.group }, CUSTOM_TITLES[r.group] || r.group));
      });
    }

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
      if (!keep) {
        // As in the workbook after a refresh: Occurrences and Page first, when they have data.
        var first = kind === "metric" ? "metrics/occurrences" : "variables/page";
        rows.forEach(function (r, i) { if (r.component_id === first) at = i; });
      }
      select.selectedIndex = at >= 0 ? at : (rows.length ? 0 : -1);
      select.disabled = !rows.length;
    }
    function draw() {
      var r = rows[select.selectedIndex];
      if (!r) {
        clear(host);
        var type = typeSelect ? typeSelect.value : "";
        host.appendChild(el("p", { "class": "chart-empty" }, (kind === "dimension" && TYPE_EMPTY[type]) ||
          (kind === "metric" ? "No metric data was read." : "No dimension data was read.")));
        return;
      }
      drawChart(host, data.series[r.component_id], componentLabel(r), r.metric_type);
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

  // The Individual Component Summary shows one stored pair at a time.
  var pairs = data.pairs || [];
  var pairIndex = 0;
  function pairLabel(p) {
    return p ? (p.dimension_name || p.dimension) + " by " + (p.metric_name || p.metric) :
      "the chosen dimension and metric";
  }

  function drawIndividual() {
    var p = pairs[pairIndex];
    drawChart(byId("individual-chart"), p ? data.series[p.series_id] : [], pairLabel(p),
      p ? p.metric_type : null);
  }

  function fillPairPickers() {
    var dimSel = byId("pair-dimension"), metSel = byId("pair-metric");
    var dims = [];
    pairs.forEach(function (p) { if (dims.indexOf(p.dimension) < 0) dims.push(p.dimension); });
    clear(dimSel);
    dims.forEach(function (d) {
      var p = pairs.filter(function (x) { return x.dimension === d; })[0];
      dimSel.appendChild(el("option", { value: d }, p.dimension_name || d));
    });
    function fillMetrics() {
      clear(metSel);
      pairs.forEach(function (p, i) {
        if (p.dimension === dimSel.value) {
          metSel.appendChild(el("option", { value: String(i) }, p.metric_name || p.metric));
        }
      });
    }
    var current = pairs[pairIndex];
    if (current) dimSel.value = current.dimension;
    fillMetrics();
    if (current) metSel.value = String(pairIndex);
    dimSel.disabled = dims.length < 2;
    metSel.disabled = metSel.options.length < 2;
    dimSel.addEventListener("change", function () {
      fillMetrics();
      metSel.disabled = metSel.options.length < 2;
      pairIndex = Number(metSel.value);
      renderPair();
    });
    metSel.addEventListener("change", function () {
      pairIndex = Number(metSel.value);
      renderPair();
    });
  }

  function renderPair() {
    var p = pairs[pairIndex];
    var label = pairLabel(p);
    byId("individual-title").textContent = label;
    byId("individual-caption").textContent = "Trend for " + label;
    drawIndividual();
    var type = p ? p.metric_type : null;
    var top = byId("top100");
    clear(top);
    var rows = p ? p.top100 : [];
    if (!rows.length) {
      top.appendChild(el("p", { "class": "empty" }, "No top values were returned."));
    } else {
      top.appendChild(table(
        [{ label: "Movement", numSort: true }, { label: "Rank", num: true }, { label: "Value", cls: "name" },
         { label: "Count", num: true }],
        rows.map(function (r) {
          return [movementCell(r.movement), { c: String(r.rank), k: r.rank }, r.item,
            { c: fmtNum(r.value, type), k: r.value }];
        }), null, "top100"));
    }
    ["browser", "device"].forEach(function (kind) {
      var host = byId(kind);
      clear(host);
      var b = (p && p[kind]) || { labels: [], rows: [], totals: [] };
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
          return { c: fmtNum(v, type), k: v, cls: "shade", a: a };
        }));
      });
      var foot = b.totals && b.totals.length ?
        ["Total"].concat(b.totals.map(function (v) { return fmtNum(v, type); })) : null;
      host.appendChild(table(headers, body, foot, kind));
    });
  }

  function renderIndividual() {
    redrawers.push(drawIndividual);
    fillPairPickers();
    renderPair();
  }

  // ---- alerts tab ------------------------------------------------------------
  function alertReason(a) {
    if (a.anomaly === "low") return a.actual === 0 ? "below the expected range, value is 0" :
      "below the expected range";
    if (a.anomaly === "high") return "above the expected range";
    if (!a.zero) return fmtPct(a.pct_vs_usual).replace("-", "") + " below the usual value";
    return "value is 0";
  }

  function splitAlerts() {
    var alerts = (data.alerts || []).filter(function (a) {
      return !a.day || a.day === state.alertDay;
    });
    function plain(a) { return !a.anomaly; }
    return {
      unusual: alerts.filter(function (a) { return !!a.anomaly; }),
      drops: alerts.filter(function (a) {
        return plain(a) && !a.zero && isLargeDrop(a.actual, a.usual, a.pct_vs_usual);
      }),
      zero: alerts.filter(function (a) { return plain(a) && a.zero && !a.unused; }),
      unused: alerts.filter(function (a) { return plain(a) && a.zero && a.unused; })
    };
  }

  function alertLine(a) {
    var s = "- " + a.name + " (" + a.component_id + "): " + alertReason(a) +
      ". Actual " + fmtNum(a.actual, a.metric_type);
    if (a.anomaly) {
      if (a.expected !== null) s += ", expected " + fmtRound(a.expected, a.metric_type);
      if (a.pct_vs_expected !== null) s += ", change " + fmtPct(a.pct_vs_expected);
    } else if (a.usual !== null) {
      s += ", usual " + fmtRound(a.usual, a.metric_type);
    }
    return s + ".";
  }

  function alertText() {
    var run = data.run || {};
    var parts = splitAlerts();
    var lines = [];
    lines.push("Analytics Health alerts for " + (run.rsid_name || run.rsid) + " (" + run.rsid +
      "), " + state.alertDay);
    lines.push("Segment: " + (run.segment_id || "none (all visits)"));
    function section(title, list) {
      if (!list.length) return;
      lines.push(title + " (" + list.length + "):");
      list.forEach(function (a) { lines.push(alertLine(a)); });
    }
    if (!parts.unusual.length && !parts.drops.length && !parts.zero.length) {
      lines.push("No alerts for this day.");
    } else {
      section("Unusual values", parts.unusual);
      section("Drops of more than " + Math.round(state.t * 100) + "% below usual", parts.drops);
      section("Dropped to zero", parts.zero);
    }
    if (parts.unused.length) {
      lines.push(parts.unused.length + (parts.unused.length === 1 ? " component" : " components") +
        " never collected data in this run. They are not listed.");
    }
    if ((data.issues || []).length) {
      lines.push("Some data could not be read in this run. See the status section of the report.");
    }
    return lines.join("\n");
  }

  function groupHeading(id, title, count) {
    var h = el("h3", { "class": "group-title", id: id }, title);
    h.appendChild(el("span", { "class": "group-count" },
      count === 1 ? "1 component" : count + " components"));
    return h;
  }
  function nameIdCells(a) {
    return [{ c: a.name, k: a.name }, { c: el("code", null, a.component_id), k: a.component_id }];
  }
  function expectedCell(a) {
    return { c: a.expected === null ? "Not available" : fmtRound(a.expected, a.metric_type),
      k: a.expected };
  }

  function alertDayText() {
    return fmtDay(state.alertDay, false) + (state.alertDay === yesterday ? " (yesterday)" : "");
  }

  // One plain sentence about large drops in these rows, naming the largest drop even when it is
  // under the slider, so a reader can see why nothing was flagged.
  function dropSummary(rows, when) {
    var pct = Math.round(state.t * 100) + "%";
    var drops = rows.filter(function (r) { return isLargeDrop(r.value, r.baseline, r.pct); });
    var flagged = drops.length;
    var byAdobe = drops.filter(function (r) { return !!r.anomaly; }).length;
    var worst = null;
    rows.forEach(function (r) {
      if (r.zero || r.pct === null || r.pct === undefined || r.pct >= 0) return;
      if (r.baseline === null || r.baseline < MIN_USUAL) return;
      if (!worst || r.pct < worst.pct) worst = r;
    });
    var head = flagged ?
      when + ": " + flagged + (flagged === 1 ? " component is" : " components are") +
        " more than " + pct + " below usual" + (byAdobe ? ", and Adobe also flagged " +
        (byAdobe === flagged ? (flagged === 1 ? "it" : "all of them") : byAdobe + " of them") :
        "") + "." :
      when + ": no component is more than " + pct + " below usual.";
    if (!worst) return head + " No component is below its usual value.";
    return head + " The largest drop is " + fmtPct(worst.pct).replace("-", "") + " (" +
      componentLabel(worst) + ").";
  }

  function renderAlerts() {
    var host = byId("alerts");
    clear(host);
    var parts = splitAlerts();
    var pct = Math.round(state.t * 100) + "%";

    var g1 = el("section", { "class": "alert-group", "aria-labelledby": "unusual-title" });
    g1.appendChild(groupHeading("unusual-title", "Unusual values", parts.unusual.length));
    if (parts.unusual.length) {
      g1.appendChild(el("p", { "class": "hint" },
        "Adobe flagged these values as outside the expected range. The bar shows the size of " +
        "the change from the expected value, up to 100 percent."));
      g1.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" }, { label: "Flag" },
         { label: "Expected", num: true }, { label: "Actual", num: true },
         { label: "Change from expected", num: true }],
        parts.unusual.map(function (a) {
          return nameIdCells(a).concat([anomalyCell(a.anomaly), expectedCell(a),
            { c: fmtNum(a.actual, a.metric_type), k: a.actual },
            devCell(arrowFor(a.pct_vs_expected), a.pct_vs_expected)]);
        }), null, "alerts-unusual"));
    } else {
      g1.appendChild(el("p", { "class": "empty" }, anomalyBlocked ?
        "Anomaly detection is not available for this run, so no values could be flagged. " +
        "See the status section." : "Adobe flagged no unusual values on " + alertDayText() + "."));
    }
    host.appendChild(g1);

    var g2 = el("section", { "class": "alert-group", "aria-labelledby": "drops-title" });
    g2.appendChild(groupHeading("drops-title", "Drops of more than " + pct + " below usual",
      parts.drops.length));
    if (parts.drops.length) {
      g2.appendChild(el("p", { "class": "hint" },
        "These values were more than " + pct + " below the usual value for the weekday, but " +
        "Adobe did not flag them. Move the slider at the top of the page to change the percent."));
      g2.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Usual", num: true }, { label: "Actual", num: true },
         { label: "Change from usual", num: true }],
        parts.drops.map(function (a) {
          return nameIdCells(a).concat([{ c: fmtRound(a.usual, a.metric_type), k: a.usual },
            { c: fmtNum(a.actual, a.metric_type), k: a.actual },
            devCell(arrowFor(a.pct_vs_usual), a.pct_vs_usual)]);
        }), null, "alerts-drops"));
    } else {
      g2.appendChild(el("p", { "class": "empty" },
        dropSummary(data.views["day:" + state.alertDay] || [], alertDayText())));
    }
    host.appendChild(g2);

    var g3 = el("section", { "class": "alert-group", "aria-labelledby": "zero-title" });
    g3.appendChild(groupHeading("zero-title", "Dropped to zero", parts.zero.length));
    if (parts.zero.length) {
      g3.appendChild(el("p", { "class": "hint" },
        "These components had data on other days in this run, but a value of 0 on " +
        alertDayText() + "."));
      g3.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Usual", num: true }, { label: "Expected", num: true },
         { label: "Actual", num: true }],
        parts.zero.map(function (a) {
          var name = el("span");
          name.appendChild(document.createTextNode(a.name));
          name.appendChild(zeroBadge());
          return [{ c: name, k: a.name }, { c: el("code", null, a.component_id), k: a.component_id },
            { c: fmtRound(a.usual, a.metric_type), k: a.usual }, expectedCell(a),
            { c: fmtNum(a.actual, a.metric_type), k: a.actual }];
        }), null, "alerts-zero"));
    } else {
      g3.appendChild(el("p", { "class": "empty" },
        "No component that collects data had a value of 0 on " + alertDayText() + "."));
    }
    host.appendChild(g3);

    if (parts.unused.length) {
      var g4 = el("details", { "class": "alert-group" });
      g4.appendChild(el("summary", null, "Never collected data (" + parts.unused.length + ")"));
      g4.appendChild(el("p", { "class": "hint" },
        "These components had a value of 0 on every day in this run, so they are not alerts. " +
        "They are often unused slots. If you expected data from one, check its tracking."));
      g4.appendChild(table([{ label: "Name", cls: "name" }, { label: "ID", cls: "id" }],
        parts.unused.map(nameIdCells), null, "alerts-unused"));
      host.appendChild(g4);
    }

    byId("alert-text").value = alertText();
  }

  // ---- copying ------------------------------------------------------------------
  // Puts HTML and plain text on the clipboard together. An email program pastes the HTML as a
  // formatted table; a chat or text editor pastes the text. Without the rich clipboard (older
  // browsers), it copies the text only.
  function copyBoth(html, text, done, failed) {
    function plain() {
      if (!navigator.clipboard || !navigator.clipboard.writeText) { failed(); return; }
      navigator.clipboard.writeText(text).then(function () { done(false); }, failed);
    }
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      var item = new window.ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text], { type: "text/plain" })
      });
      navigator.clipboard.write([item]).then(function () { done(true); }, plain);
    } else {
      plain();
    }
  }

  // Inline styles only: email programs drop style blocks in the head.
  var MAIL = {
    wrap: "font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2328;",
    h: "font-size:16px;margin:16px 0 6px;",
    p: "margin:0 0 6px;",
    table: "border-collapse:collapse;margin:0 0 12px;",
    th: "text-align:left;padding:4px 10px;border-bottom:2px solid #d0d7de;background:#f6f8fa;",
    thNum: "text-align:right;padding:4px 10px;border-bottom:2px solid #d0d7de;background:#f6f8fa;",
    td: "padding:4px 10px;border-bottom:1px solid #eaeef2;",
    tdNum: "padding:4px 10px;border-bottom:1px solid #eaeef2;text-align:right;" +
      "font-variant-numeric:tabular-nums;",
    muted: "color:#59636e;font-size:12px;margin:8px 0 0;"
  };
  function mailTable(headers, rows) {
    var t = el("table", { style: MAIL.table, cellpadding: 0, cellspacing: 0 });
    var tr = el("tr");
    headers.forEach(function (h) {
      tr.appendChild(el("th", { style: h.num ? MAIL.thNum : MAIL.th }, h.label));
    });
    t.appendChild(tr);
    rows.forEach(function (row) {
      var r = el("tr");
      row.forEach(function (cell, i) {
        r.appendChild(el("td", { style: headers[i].num ? MAIL.tdNum : MAIL.td }, cell));
      });
      t.appendChild(r);
    });
    return t;
  }

  function alertHtml() {
    var run = data.run || {};
    var parts = splitAlerts();
    var pct = Math.round(state.t * 100) + "%";
    var box = el("div", { style: MAIL.wrap });
    box.appendChild(el("p", { style: MAIL.h + "margin-top:0;" },
      "Analytics Health alerts for " + (run.rsid_name || run.rsid) + ", " + alertDayText()));
    box.appendChild(el("p", { style: MAIL.p }, "Report suite " + run.rsid + ". Segment: " +
      (run.segment_id || "none (all visits)") + "."));
    function name(a) { return a.name + " (" + a.component_id + ")"; }
    function section(title, list, headers, cells) {
      if (!list.length) return;
      box.appendChild(el("p", { style: MAIL.h }, title + " (" + list.length + ")"));
      box.appendChild(mailTable(headers, list.map(cells)));
    }
    if (!parts.unusual.length && !parts.drops.length && !parts.zero.length) {
      box.appendChild(el("p", { style: MAIL.p }, "No alerts for this day."));
    }
    section("Unusual values, flagged by Adobe", parts.unusual,
      [{ label: "Component" }, { label: "Flag" }, { label: "Expected", num: true },
       { label: "Actual", num: true }, { label: "Change", num: true }],
      function (a) {
        return [name(a), a.anomaly === "high" ? "Above range" : "Below range",
          a.expected === null ? "Not available" : fmtRound(a.expected, a.metric_type),
          fmtNum(a.actual, a.metric_type), fmtPct(a.pct_vs_expected)];
      });
    section("Drops of more than " + pct + " below usual", parts.drops,
      [{ label: "Component" }, { label: "Usual", num: true }, { label: "Actual", num: true },
       { label: "Change", num: true }],
      function (a) {
        return [name(a), fmtRound(a.usual, a.metric_type), fmtNum(a.actual, a.metric_type),
          fmtPct(a.pct_vs_usual)];
      });
    section("Dropped to zero", parts.zero,
      [{ label: "Component" }, { label: "Usual", num: true }, { label: "Actual", num: true }],
      function (a) {
        return [name(a), fmtRound(a.usual, a.metric_type), fmtNum(a.actual, a.metric_type)];
      });
    var notes = [];
    if (parts.unused.length) {
      notes.push(parts.unused.length + (parts.unused.length === 1 ? " component" : " components") +
        " never collected data in this run, so they are not listed.");
    }
    if ((data.issues || []).length) {
      notes.push("Some data could not be read in this run. See the status section of the report.");
    }
    notes.push("Run " + run.id + ", made with ahd " + (run.tool_version || "") + ".");
    box.appendChild(el("p", { style: MAIL.muted }, notes.join(" ")));
    return box.outerHTML;
  }

  function copyButton(buttonId, resultId, getHtml, getText, fallback) {
    var result = byId(resultId);
    var button = byId(buttonId);
    if (!button) return;
    button.addEventListener("click", function () {
      copyBoth(getHtml(), getText(), function (rich) {
        result.textContent = rich ?
          "Copied. An email program pastes it formatted, and a chat pastes plain text." :
          "Copied as plain text. This browser could not copy the formatted version.";
      }, function () {
        result.textContent = "Could not copy. " + fallback;
        var f = document.querySelector(".copy-fallback");
        if (f && buttonId === "copy-alerts") f.open = true;
      });
    });
  }
  copyButton("copy-alerts", "copy-result", alertHtml, function () { return byId("alert-text").value; },
    "Open \"Show the alert summary as text\" below and copy it from there.");

  // The problems as the status section shows them: one heading per problem code, then items.
  function problemGroups() {
    return [].map.call(document.querySelectorAll("#status .issue"), function (li) {
      var heading = li.querySelector(".issue-heading");
      return {
        heading: heading ? heading.textContent.replace(/\s+/g, " ").trim() : "",
        items: [].map.call(li.querySelectorAll(".issue-items li"), function (x) {
          return x.textContent.replace(/\s+/g, " ").trim();
        })
      };
    });
  }
  function problemsTitle() {
    var run = data.run || {};
    return "Problems in run " + run.id + " for " + (run.rsid_name || run.rsid) + " (" + run.rsid +
      "), " + run.today;
  }
  function problemsText() {
    var lines = [problemsTitle()];
    problemGroups().forEach(function (g) {
      lines.push(g.heading);
      g.items.forEach(function (i) { lines.push("- " + i); });
    });
    return lines.join("\n");
  }
  function problemsHtml() {
    var box = el("div", { style: MAIL.wrap });
    box.appendChild(el("p", { style: MAIL.h + "margin-top:0;" }, problemsTitle()));
    problemGroups().forEach(function (g) {
      box.appendChild(el("p", { style: MAIL.p + "font-weight:bold;" }, g.heading));
      if (g.items.length) {
        var ul = el("ul", { style: "margin:0 0 10px;padding-left:20px;" });
        g.items.forEach(function (i) { ul.appendChild(el("li", null, i)); });
        box.appendChild(ul);
      }
    });
    return box.outerHTML;
  }
  copyButton("copy-problems", "copy-problems-result", problemsHtml, problemsText,
    "Select the problems above and copy them by hand.");

  // ---- summary tiles ------------------------------------------------------------
  function renderTiles() {
    var host = byId("tiles");
    clear(host);
    var rows = data.views["day:" + yesterday] || [];
    var high = rows.filter(function (r) { return r.anomaly === "high"; }).length;
    var low = rows.filter(function (r) { return r.anomaly === "low"; }).length;
    // Each alert counts once: a zero that Adobe flagged is in the "Below expected range" tile.
    var zero = rows.filter(function (r) { return r.zero && !r.unused && !r.anomaly; }).length;
    var unused = rows.filter(function (r) { return r.zero && r.unused; }).length;
    var drops = rows.filter(function (r) {
      return !r.anomaly && isLargeDrop(r.value, r.baseline, r.pct);
    }).length;
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
    tile("flag", "▼", "Large drops", numFmt.format(drops),
      "More than " + Math.round(state.t * 100) + "% below usual", toAlerts);
    tile("zero", "●", "Dropped to zero", numFmt.format(zero),
      unused ? numFmt.format(unused) + " others never collected data" : "Had data on other days",
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

  var alertDaySel = byId("alert-day");
  dayKeys.forEach(function (d, i) {
    alertDaySel.appendChild(el("option", { value: d },
      fmtDay(d, true) + (i === 0 ? " (yesterday)" : "")));
  });
  alertDaySel.addEventListener("change", function () {
    state.alertDay = alertDaySel.value;
    renderAlerts();
  });

  var slider = byId("change-alert");
  slider.value = String(Math.round(state.t * 100));
  slider.addEventListener("input", function () {
    state.t = Number(slider.value) / 100;
    byId("change-alert-value").textContent = slider.value + "%";
    renderTiles();
    renderTables();
    renderAlerts();
  });

  renderTiles();
  renderTables();
  fillPicker(byId("metric-pick"), "metric", "metric-chart", byId("metric-type"));
  fillPicker(byId("dimension-pick"), "dimension", "dimension-chart", byId("dimension-type"));
  renderIndividual();
  renderAlerts();
})();
