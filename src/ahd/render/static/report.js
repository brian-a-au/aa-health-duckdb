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
    // Adds the named fields of the row's component to the row.
    function withComponent(o, names) {
      var c = d.components[o.component_id];
      if (c) {
        f.component.forEach(function (name, i) { if (names.indexOf(name) >= 0) o[name] = c[i]; });
      }
      return o;
    }
    Object.keys(d.views).forEach(function (k) {
      d.views[k] = d.views[k].map(function (r) { return withComponent(obj(f.row, r), f.component); });
    });
    d.alerts = (d.alerts || []).map(function (a) {
      return withComponent(obj(f.alert, a), f.alert_component);
    });
    // Calendar lists name components by their place in d.components.
    var ids = Object.keys(d.components || {});
    d.calendar = (d.calendar || []).map(function (c) {
      var o = obj(f.calendar || [], c);
      ["high", "low", "zero"].forEach(function (k) {
        o[k] = (o[k] || []).map(function (x) { return typeof x === "number" ? ids[x] : x; });
      });
      return o;
    });
    Object.keys(d.series).forEach(function (cid) {
      var s = d.series[cid], start = Date.parse(s.start + "T00:00:00Z"), points = [];
      var gaps = s.gaps || [];
      s[f.series[0]].forEach(function (_, i) {
        if (gaps.indexOf(i) >= 0) return;
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
  // A run made with ahd run --date is about the days before that date, not before the run time.
  var yesterdayNote = (data.run || {}).date_given ? " (day before the run date)" : " (yesterday)";
  function dayNote(d) { return d === yesterday ? yesterdayNote : ""; }
  // t: the slider's share. A drop below the weekday average by more than t is flagged, and a
  // change by more than t either way gets an arrow.
  // The weekday average is the average of the same weekday over 12 weeks. The data and the SQL
  // call it "usual"; the page always calls it the weekday average.
  var state = { gran: "day", day: yesterday, alertDay: yesterday, range: "", flagged: false,
    find: "",
    t: typeof data.change_alert === "number" ? data.change_alert : 0.3,
    s: typeof data.spike_alert === "number" ? data.spike_alert : 0.5 };
  var MIN_USUAL = typeof data.min_usual === "number" ? data.min_usual : 10;
  var sortState = {};
  var anomalyBlocked = (data.issues || []).some(function (i) {
    return i.code === "anomaly_not_allowed";
  });

  // ---- tabs ----------------------------------------------------------------
  var redrawers = [];
  function redrawAll() { redrawers.forEach(function (f) { f(); }); }

  // A table that is wider than its place gets its own scroll box (the class "scrolls"). Every
  // other table stays part of the page, so its headings stick to the top of the window. The page
  // checks after it draws tables, and when a tab opens or the window changes size. A table on a
  // hidden tab has no width yet, so it is checked when its tab opens.
  function fitTables() {
    Array.prototype.forEach.call(document.querySelectorAll(".table-wrap"), function (w) {
      w.classList.remove("scrolls");
      var t = w.firstChild;
      if (t && w.clientWidth && t.offsetWidth > w.clientWidth + 1) w.classList.add("scrolls");
    });
  }
  redrawers.push(fitTables);
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawAll, 150);
  });

  // ---- folding sections ----------------------------------------------------------------
  // Some sections are <details class="fold"> elements, open at the start. The reader can fold
  // them. Some browsers give a folded chart or table no width, so the page draws everything
  // again when a folded section opens. The browser also sends a toggle event for each open
  // section when the page loads, so the page only redraws for a section that was folded first.
  // The toggle event does not bubble, so the page listens in the capture phase.
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.classList || !d.classList.contains("fold")) return;
    if (!d.open) d.setAttribute("data-folded", "");
    else if (d.hasAttribute("data-folded")) {
      d.removeAttribute("data-folded");
      redrawAll();
    }
  }, true);
  // Opens every folded section that holds the node, e.g., before the page shows a chart.
  function openFolds(node) {
    for (var n = node; n; n = n.parentNode) {
      if (n.tagName === "DETAILS" && n.classList.contains("fold")) n.open = true;
    }
  }
  // A folded section does not print, so the page opens them all to print and folds them again
  // after.
  var foldedForPrint = [];
  window.addEventListener("beforeprint", function () {
    foldedForPrint = Array.prototype.filter.call(document.querySelectorAll("details.fold"),
      function (d) { return !d.open; });
    foldedForPrint.forEach(function (d) { d.open = true; });
  });
  window.addEventListener("afterprint", function () {
    foldedForPrint.forEach(function (d) { d.open = false; });
    foldedForPrint = [];
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

  // Tables start in their original order. A heading click sorts (numbers largest first, text A
  // to Z), a second click reverses, a third goes back to the original order.
  // name is what a screen reader calls the table: {label: text} or {labelledby: a heading's id}.
  function table(headers, rows, foot, id, name) {
    var wrap = el("div", { "class": "table-wrap" });
    var outer = document.createDocumentFragment();
    var t = el("table");
    if (name && name.label) t.setAttribute("aria-label", name.label);
    if (name && name.labelledby) t.setAttribute("aria-labelledby", name.labelledby);
    var thead = el("thead");
    var tr = el("tr");
    var tbody = el("tbody");
    var note = el("p", { "class": "sort-note", hidden: true });
    var st = (id && sortState[id]) || { col: -1, dir: 0 };
    function cls(h) {
      if (!h) return null;
      return [h.num ? "num" : null, h.cls].filter(Boolean).join(" ") || null;
    }

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
        var reset = el("button", { type: "button" }, "Original order");
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
      "% below the weekday average. Set the percent with the slider at the top of the page.");
  }

  function spikeBadge() {
    return badge("flag", "▲", "Large spike", "More than " + Math.round(state.s * 100) +
      "% above the weekday average. Set the percent with the slider at the top of the page.");
  }

  // An arrow for a change past a slider: the drop slider for down, the spike slider for up.
  function arrowFor(pct) {
    if (pct === null || pct === undefined) return null;
    if (pct < -state.t) return "down";
    if (pct > state.s) return "up";
    return null;
  }
  // A value that rose further above its weekday average than the spike slider allows.
  function isLargeSpike(value, usual, pct) {
    return value !== 0 && pct !== null && pct !== undefined && pct > state.s &&
      usual !== null && usual >= MIN_USUAL;
  }
  // A value that fell further below its weekday average than the slider allows. Zero is its own
  // alert.
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

  // A percent change with a small bar: left of the gray center line for down, right for up.
  // Half the bar is a change of 100 percent. A tick on the left marks the drop slider and a tick
  // on the right the spike slider, with a light band between them. A bar past a tick is in the
  // flag color. The text and the arrow carry
  // the same facts as the bar.
  function devCell(arrow, pct) {
    if (pct === null || pct === undefined) return { c: "", k: null };
    var wrap = el("span", { "class": "dev" });
    var bar = el("span", { "class": "devbar", "aria-hidden": "true" });
    var fill = el("span", { "class": "fill " + (pct < 0 ? "neg" : "pos") +
      (arrow ? " over" : "") });
    var w = Math.min(Math.abs(pct), 1) * 50;
    fill.style.width = Math.max(w, 1.5) + "%";
    if (pct < 0) fill.style.right = "50%"; else fill.style.left = "50%";
    var down = Math.min(state.t, 1) * 50, up = Math.min(state.s, 1) * 50;
    var zone = el("span", { "class": "zone" });
    zone.style.left = (50 - down) + "%";
    zone.style.width = (down + up) + "%";
    bar.appendChild(zone);
    bar.appendChild(fill);
    [50 - down, 50 + up].forEach(function (at) {
      var tick = el("span", { "class": "tick" });
      tick.style.left = at + "%";
      bar.appendChild(tick);
    });
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
    if (isLargeSpike(row.value, row.baseline, row.pct)) return { c: spikeBadge(), k: 0.6 };
    return a;
  }

  function isFlagged(row) {
    return !!(row.anomaly || (row.zero && !row.unused) ||
      isLargeDrop(row.value, row.baseline, row.pct) ||
      isLargeSpike(row.value, row.baseline, row.pct));
  }

  // The find box matches a component's name, number, e.g., "eVar5", or id, in any case.
  function matchesFind(row) {
    if (!state.find) return true;
    var q = state.find.toLowerCase();
    return [row.name, numberLabel(row), row.component_id].some(function (t) {
      return t && String(t).toLowerCase().indexOf(q) >= 0;
    });
  }

  // ---- 13-week trend lines in the tables -------------------------------------
  // One small line per row, from the same daily values as the charts, on a scale from 0 to the
  // row's largest value. A dot marks the chosen day. The line is a button that shows the
  // component on its chart.
  var SPARK_W = 88, SPARK_H = 22;
  var sparkCache = {};
  function sparkShape(cid) {
    if (cid in sparkCache) return sparkCache[cid];
    var pts = (data.series[cid] || []).filter(function (p) { return p[1] !== null; });
    var shape = null;
    if (pts.length && yesterday) {
      var end = parseDay(yesterday).getTime(), span = 90;
      var max = 0;
      pts.forEach(function (p) { if (p[1] > max) max = p[1]; });
      var at = {};
      var d = "", prev = null;
      pts.forEach(function (p) {
        var back = (end - parseDay(p[0]).getTime()) / 864e5;
        if (back < 0 || back > span) return;
        var x = 2 + (span - back) / span * (SPARK_W - 4);
        var y = SPARK_H - 3 - (max > 0 ? Math.max(0, p[1]) / max : 0) * (SPARK_H - 6);
        x = Math.round(x * 10) / 10;
        y = Math.round(y * 10) / 10;
        // A day without a value breaks the line.
        d += (prev !== null && back === prev - 1 ? "L" : "M") + x + " " + y;
        prev = back;
        at[p[0]] = [x, y];
      });
      shape = d ? { d: d, at: at } : null;
    }
    sparkCache[cid] = shape;
    return shape;
  }
  function sparkCell(r) {
    var shape = sparkShape(r.component_id);
    if (!shape) return { c: "", k: null };
    var label = "Show " + componentLabel(r) + " on the chart";
    var b = el("button", { type: "button", "class": "spark", title: label, "aria-label": label });
    var svg = sv("svg", { viewBox: "0 0 " + SPARK_W + " " + SPARK_H, width: SPARK_W,
      height: SPARK_H, "aria-hidden": "true" });
    svg.appendChild(sv("path", { d: shape.d, "class": "spark-line" }));
    var day = chosenDay();
    if (day && shape.at[day]) {
      svg.appendChild(sv("circle", { cx: shape.at[day][0], cy: shape.at[day][1], r: 2.5,
        "class": "spark-dot" }));
    }
    b.appendChild(svg);
    b.addEventListener("click", function () { showOnChart(r.component_id, chosenDay()); });
    return { c: b, k: null };
  }

  function inRange(row) {
    if (!state.range || CUSTOM.indexOf(row.group) < 0) return true;
    var p = state.range.split("-");
    return row.number !== null && row.number >= +p[0] && row.number <= +p[1];
  }

  function componentTable(rows, custom, id, title) {
    var daily = state.gran === "day";
    var headers = [];
    if (custom) headers.push({ label: "Number" });
    headers.push({ label: "Name", cls: "name" });
    headers.push({ label: "Last 13 weeks", sort: false, cls: "spark-col" });
    headers.push({ label: daily ? "Total for the day" : "Total for the week", num: true });
    headers.push({
      label: daily ? "Weekday average (12 weeks)" : "Average week (12 weeks)",
      num: true
    });
    headers.push({ label: daily ? "Change from weekday average" : "Change from average week",
      num: true });
    headers.push({ label: "Flag" });
    var body = rows.map(function (r) {
      var cells = [];
      if (custom) cells.push({ c: numberLabel(r), k: r.number });
      cells.push(nameCell(r));
      cells.push(sparkCell(r));
      cells.push({ c: fmtNum(r.value, r.metric_type), k: r.value });
      cells.push({ c: fmtRound(r.baseline, r.metric_type), k: r.baseline });
      cells.push(devCell(arrowFor(r.pct), r.pct));
      cells.push(flagCell(r, daily));
      return cells;
    });
    return table(headers, body, null, id, { label: title });
  }

  function groupBlock(title, rows, custom, emptyText, id) {
    var frag = document.createDocumentFragment();
    var h = el("h3", { "class": "group-title", id: id }, title);
    if (rows.length) h.appendChild(el("span", { "class": "group-count" },
      rows.length === 1 ? "1 component" : rows.length + " components"));
    frag.appendChild(h);
    frag.appendChild(rows.length ? componentTable(rows, custom, "group:" + title, title) :
      el("p", { "class": "empty" }, emptyText));
    return frag;
  }

  function currentRows() {
    return (state.gran === "week" ? data.views.week : data.views["day:" + state.day]) || [];
  }

  function renderSliderResult() {
    var when = state.gran === "week" ? "The week ending " + fmtDay(yesterday, false) :
      fmtDay(state.day, false) + dayNote(state.day);
    byId("change-alert-result").textContent = dropSummary(currentRows(), when,
      state.gran === "week");
    byId("spike-alert-result").textContent = spikeSummary(currentRows(), when,
      state.gran === "week");
  }

  function renderTables() {
    renderSliderResult();
    var host = byId("tables");
    clear(host);
    var rows = currentRows();
    if (state.flagged) rows = rows.filter(isFlagged);
    rows = rows.filter(matchesFind);
    var byGroup = {};
    rows.forEach(function (r) { (byGroup[r.group] = byGroup[r.group] || []).push(r); });
    var allRows = currentRows();
    var hasGroup = {};
    allRows.forEach(function (r) { hasGroup[r.group] = true; });

    // Links to each group's heading. The ids use the group's place, not its name.
    var jump = el("nav", { "class": "jump", id: "jump", "aria-label": "Jump to a group" });
    jump.appendChild(el("span", { "class": "jump-label" }, "Jump to"));
    var jumpList = el("ul");
    jump.appendChild(jumpList);
    var groupNumber = 0;
    function block(title, rows, custom, empty) {
      var id = "group-" + groupNumber++;
      var li = el("li");
      var a = el("a", { href: "#" + id }, title);
      a.appendChild(el("span", { "class": "jump-count" }, " " + rows.length));
      li.appendChild(a);
      jumpList.appendChild(li);
      var frag = groupBlock(title, rows, custom, empty, id);
      // A link back to the jump links after each table, to choose another group.
      if (rows.length) {
        var back = el("p", { "class": "back-top" });
        back.appendChild(el("a", { href: "#jump" }, "Back to the top of the tables"));
        frag.appendChild(back);
      }
      return frag;
    }
    host.appendChild(jump);

    host.appendChild(el("h2", null, "Custom components"));
    CUSTOM.forEach(function (g) {
      var all = byGroup[g] || [];
      var shown = all.filter(inRange);
      var empty;
      if (!hasGroup[g]) empty = "None were found. See the status section if you expected some.";
      else if (state.find && !all.length) {
        empty = "No component in this group matches \u201c" + state.find + "\u201d" +
          (state.flagged ? " among the flagged ones." : ".");
      }
      else if (state.flagged && !all.length) empty = "Nothing flagged in this group.";
      else empty = "None in this number range.";
      host.appendChild(block(CUSTOM_TITLES[g], shown, true, empty));
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
      host.appendChild(block(g, byGroup[g], false, ""));
    });
    if (!any) host.appendChild(el("p", { "class": "empty" },
      state.find ? "No solution component matches \u201c" + state.find + "\u201d." :
      state.flagged ? "Nothing flagged in the solution components." : "None were found."));
    fitTables();
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

  // One entry of the chart's key. A line or band gets an 18 by 12 box. A mark gets a 12 by 12
  // box, with the same triangle as on the chart, so it sits next to its label like the lines.
  var MARK_KINDS = ["high", "low", "chosen", "average"];
  function legendItem(kind, label) {
    var li = el("li");
    var mark = MARK_KINDS.indexOf(kind) >= 0, w = mark ? 12 : 18;
    var s = sv("svg", { viewBox: "0 0 " + w + " 12", width: w, height: 12, "aria-hidden": "true",
      "class": mark ? "key-mark" : "key-line" });
    if (kind === "actual") s.appendChild(sv("line", { x1: 1, y1: 6, x2: 17, y2: 6, "class": "actual" }));
    if (kind === "expected") s.appendChild(sv("line", { x1: 1, y1: 6, x2: 17, y2: 6, "class": "expected" }));
    if (kind === "band") s.appendChild(sv("rect", { x: 1, y: 2, width: 16, height: 8, "class": "band" }));
    if (kind === "high") s.appendChild(sv("path", { d: "M6 0 L12 12 L0 12 Z", "class": "mark-high" }));
    if (kind === "low") s.appendChild(sv("path", { d: "M6 12 L12 0 L0 0 Z", "class": "mark-low" }));
    if (kind === "chosen") {
      s.appendChild(sv("line", { x1: 6, y1: 0, x2: 6, y2: 12, "class": "chosen" }));
      s.appendChild(sv("circle", { cx: 6, cy: 6, r: 3.5, "class": "chosen-dot" }));
    }
    if (kind === "average") s.appendChild(sv("line", { x1: 0, y1: 6, x2: 12, y2: 6, "class": "average" }));
    li.appendChild(s);
    li.appendChild(el("span", null, label));
    return li;
  }

  // average: the weekday average, given only for the chosen day.
  function describe(p, type, average) {
    var parts = [fmtDay(p[0], true) + ": " + fmtNum(p[1], type)];
    if (average !== null && average !== undefined) {
      parts.push("weekday average " + fmtRound(average, type));
    }
    if (p[2] !== null) parts.push("expected " + fmtRound(p[2], type));
    if (p[3] !== null && p[4] !== null) {
      parts.push("expected range " + fmtRound(p[4], type) + " to " + fmtRound(p[3], type));
    }
    var text = parts.join(", ");
    if (p[5] === "high") text += ". Above the expected range.";
    else if (p[5] === "low") text += ". Below the expected range.";
    return text;
  }

  // mark: {day, average} marks the chosen day with a line and a dot, and its weekday average
  // with a short bar. Without it, or when the day is not in the chart, nothing is marked.
  // mark.look: a day to read and to show with the crosshair, e.g., a day chosen in the
  // calendar. The readout starts on it instead of the chosen day.
  function drawChart(host, points, title, type, mark) {
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
    var chosen = -1;
    if (mark && mark.day) {
      points.forEach(function (p, k) { if (p[0] === mark.day) chosen = k; });
    }
    var average = chosen >= 0 && typeof mark.average === "number" ? mark.average : null;
    var max = average !== null && average > 0 ? average : 0;
    points.forEach(function (p) {
      [p[1], p[2], p[3]].forEach(function (v) { if (v !== null && v > max) max = v; });
    });
    var min = average !== null && average < 0 ? average : 0;
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
        (chosen >= 0 ? "A line marks the chosen day, " + fmtDay(points[chosen][0], false) + ". " : "") +
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

    // The chosen day's line sits behind the data.
    if (chosen >= 0) {
      svg.appendChild(sv("line", { x1: x(points[chosen]), x2: x(points[chosen]), y1: T, y2: T + ph,
        "class": "chosen" }));
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

    // The weekday average as a short bar on the chosen day, and the chosen value as a dot. Each
    // has a ring in the surface color, so it stays clear where it crosses a line.
    if (average !== null) {
      var ax = x(points[chosen]), ay = y(average);
      svg.appendChild(sv("line", { x1: ax - 8, x2: ax + 8, y1: ay, y2: ay, "class": "average-ring" }));
      svg.appendChild(sv("line", { x1: ax - 8, x2: ax + 8, y1: ay, y2: ay, "class": "average" }));
    }
    if (chosen >= 0 && points[chosen][1] !== null) {
      svg.appendChild(sv("circle", { cx: x(points[chosen]), cy: y(points[chosen][1]), r: 4,
        "class": "chosen-dot" }));
    }

    // hover and keyboard readout
    var cross = sv("line", { y1: T, y2: T + ph, "class": "cross", visibility: "hidden" });
    var dot = sv("circle", { r: 4, "class": "dot", visibility: "hidden" });
    svg.appendChild(cross);
    svg.appendChild(dot);
    var hit = sv("rect", { x: L, y: 0, width: pw, height: H, fill: "transparent" });
    svg.appendChild(hit);
    var readout = el("p", { "class": "chart-readout", "aria-live": "polite" });
    var last = points.length - 1;
    // At rest, the readout describes the look day, else the chosen day, else the last day.
    var look = -1;
    if (mark && mark.look) {
      points.forEach(function (p, k) { if (p[0] === mark.look) look = k; });
    }
    var rest = look >= 0 ? look : chosen >= 0 ? chosen : last;
    var active = rest;
    function show(k, visible) {
      active = Math.max(0, Math.min(last, k));
      var p = points[active];
      readout.textContent = describe(p, type, active === chosen ? average : null);
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
    svg.addEventListener("pointerleave", function () { show(rest, look >= 0); });
    svg.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { e.preventDefault(); show(active - 1, true); }
      if (e.key === "ArrowRight") { e.preventDefault(); show(active + 1, true); }
    });
    svg.addEventListener("blur", function () { show(rest, look >= 0); });
    show(rest, look >= 0);

    host.appendChild(svg);
    host.appendChild(readout);
    // The key has up to three rows: the lines and the band, then Adobe's flags, then the chosen
    // day and its weekday average. A row with nothing in it is left out.
    var legend = el("div", { "class": "legend" });
    function row(items) {
      var ul = el("ul", { "class": "legend-row" });
      items.forEach(function (it) { if (it[0]) ul.appendChild(legendItem(it[1], it[2])); });
      if (ul.firstChild) legend.appendChild(ul);
    }
    row([[true, "actual", "Actual"], [hasExpected, "expected", "Expected"],
      [hasBands, "band", "Expected range"]]);
    row([[anyHigh, "high", "Flagged above range"], [anyLow, "low", "Flagged below range"]]);
    row([[chosen >= 0, "chosen", "Chosen day"],
      [average !== null, "average", "Weekday average (12 weeks)"]]);
    host.appendChild(legend);
    if (!hasBands) {
      host.appendChild(byId("no-bands-template").content.cloneNode(true));
    }
  }

  // The day the Day picker chose, or null in the weekly view, which marks no day.
  function chosenDay() { return state.gran === "day" ? state.day : null; }
  var averageByDay = {};
  function weekdayAverage(cid, day) {
    if (!averageByDay[day]) {
      averageByDay[day] = {};
      (data.views["day:" + day] || []).forEach(function (r) {
        averageByDay[day][r.component_id] = r.baseline;
      });
    }
    var v = averageByDay[day][cid];
    return v === undefined ? null : v;
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
      var day = chosenDay();
      var mark = day ? { day: day, average: weekdayAverage(r.component_id, day) } : {};
      if (look && look.cid === r.component_id) mark.look = look.day;
      drawChart(host, data.series[r.component_id], componentLabel(r), r.metric_type, mark);
    }
    // A component and day picked elsewhere on the page, e.g., in the calendar. It holds until
    // the reader picks another component or day.
    var look = null;
    fill();
    select.addEventListener("change", function () { look = null; draw(); });
    if (typeSelect) {
      typeSelect.disabled = !all.length;
      typeSelect.addEventListener("change", function () { look = null; fill(); draw(); });
    }
    redrawers.push(draw);
    draw();
    return {
      forget: function () { look = null; },
      has: function (cid) {
        return all.some(function (r) { return r.component_id === cid; });
      },
      // Shows the component on this chart and reads the given day, then moves to the chart.
      show: function (cid, day) {
        if (typeSelect && typeSelect.value) {
          typeSelect.value = "";
          fill();
        }
        rows.forEach(function (r, i) { if (r.component_id === cid) select.selectedIndex = i; });
        look = day ? { cid: cid, day: day } : null;
        openFolds(host);
        draw();
        var svg = host.querySelector("svg");
        if (svg) svg.focus(); else host.scrollIntoView();
      }
    };
  }

  // ---- alerts by day: a calendar of the last 13 weeks ---------------------------
  // Each square is a day, darker for more components with an alert that day. The SQL lists the
  // components for each day. The page only places the squares and shows the lists.
  var pickers = {};
  var calendar = { select: function () {} };
  var LEVELS = 4;
  var CAL_KINDS = [
    { key: "low", label: "Below the expected range" },
    { key: "high", label: "Above the expected range" },
    { key: "zero", label: "Dropped to zero" }
  ];
  var LIST_SHOWN = 12;

  function showOnChart(cid, day) {
    var picker = pickers.metric && pickers.metric.has(cid) ? pickers.metric :
      pickers.dimension && pickers.dimension.has(cid) ? pickers.dimension : null;
    if (picker) picker.show(cid, day);
  }

  function renderCalendar() {
    var host = byId("calendar");
    if (!host || !yesterday) return;
    var byDay = {};
    (data.calendar || []).forEach(function (c) { byDay[c.day] = c; });
    var rowsById = {};
    (data.views.week || []).forEach(function (r) { rowsById[r.component_id] = r; });
    var end = parseDay(yesterday).getTime();
    var days = [];
    for (var n = 90; n >= 0; n--) days.push(new Date(end - n * 864e5).toISOString().slice(0, 10));
    var max = 0;
    days.forEach(function (d) { if (byDay[d] && byDay[d].alerts > max) max = byDay[d].alerts; });
    // Monday is row 0. The first column may start part way through a week.
    var offset = (parseDay(days[0]).getUTCDay() + 6) % 7;
    var cols = Math.ceil((days.length + offset) / 7);
    var selected = days.length - 1;

    function level(d) {
      var c = byDay[d];
      if (!c) return -1;
      return c.alerts ? Math.max(1, Math.ceil(c.alerts / max * LEVELS)) : 0;
    }
    function countText(c) {
      return c.alerts === 1 ? "1 component with an alert" : c.alerts + " components with an alert";
    }
    function dayText(d) {
      var c = byDay[d];
      return fmtDay(d, true) + ": " + (c ? countText(c) : "no data was read") + ".";
    }

    var svgHost = el("div", { "class": "calendar-grid" });
    var readout = el("p", { "class": "chart-readout calendar-readout", "aria-live": "polite" });
    var lists = el("div", { "class": "calendar-lists" });

    function drawGrid() {
      clear(svgHost);
      var width = svgHost.clientWidth || host.clientWidth || 640;
      var L = 40, R = 18, T = 16, gap = 3;
      var size = Math.max(10, Math.min(22, Math.floor((width - L - R) / cols) - gap));
      var W = L + cols * (size + gap) + R, H = T + 7 * (size + gap);
      var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
        tabindex: 0, "class": "calendar-svg",
        "aria-label": "Alerts by day for the 13 weeks ending " + fmtDay(yesterday, false) +
          ". Use the arrow keys to move between days." });
      ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(function (name, row) {
        var t = sv("text", { x: L - 6, y: T + row * (size + gap) + size * 0.75,
          "text-anchor": "end", "class": "axis" });
        t.textContent = name;
        svg.appendChild(t);
      });
      var lastMonth = "";
      var cells = days.map(function (d, i) {
        var at = i + offset, col = Math.floor(at / 7), row = at % 7;
        var x = L + col * (size + gap), y = T + row * (size + gap);
        var month = parseDay(d).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
        if (month !== lastMonth && row === 0) {
          var m = sv("text", { x: x, y: T - 5, "class": "axis" });
          m.textContent = month;
          svg.appendChild(m);
          lastMonth = month;
        }
        var lv = level(d);
        var rect = sv("rect", { x: x, y: y, width: size, height: size, rx: 3,
          "class": "cal-cell " + (lv < 0 ? "missing" : "l" + lv), "data-day": d });
        var title = sv("title", {});
        title.textContent = dayText(d);
        rect.appendChild(title);
        svg.appendChild(rect);
        return { x: x, y: y };
      });
      var ring = sv("rect", { width: size + 4, height: size + 4, rx: 5, "class": "cal-ring" });
      svg.appendChild(ring);
      function place() {
        ring.setAttribute("x", cells[selected].x - 2);
        ring.setAttribute("y", cells[selected].y - 2);
      }
      place();
      svg.addEventListener("click", function (e) {
        var d = e.target.getAttribute && e.target.getAttribute("data-day");
        if (d) select(days.indexOf(d));
      });
      svg.addEventListener("keydown", function (e) {
        var step = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 }[e.key];
        if (!step) return;
        e.preventDefault();
        select(Math.max(0, Math.min(days.length - 1, selected + step)));
      });
      svgHost.appendChild(svg);
      drawGrid.place = place;
    }

    function listBlock(kind, ids, d) {
      var box = el("div", { "class": "calendar-list" });
      box.appendChild(el("p", { "class": "calendar-list-title" }, kind.label + " (" + ids.length + ")"));
      function item(cid) {
        var row = rowsById[cid];
        var b = el("button", { type: "button", "class": "link",
          title: "Show this component on the chart for " + fmtDay(d, false) },
          row ? componentLabel(row) : cid);
        b.addEventListener("click", function () { showOnChart(cid, d); });
        return el("li", null).appendChild(b).parentNode;
      }
      var ul = el("ul");
      ids.slice(0, LIST_SHOWN).forEach(function (cid) { ul.appendChild(item(cid)); });
      box.appendChild(ul);
      if (ids.length > LIST_SHOWN) {
        var more = el("details");
        more.appendChild(el("summary", null, "Show " + (ids.length - LIST_SHOWN) + " more"));
        var ul2 = el("ul");
        ids.slice(LIST_SHOWN).forEach(function (cid) { ul2.appendChild(item(cid)); });
        more.appendChild(ul2);
        box.appendChild(more);
      }
      return box;
    }

    function select(i) {
      if (i < 0) return;
      selected = i;
      if (drawGrid.place) drawGrid.place();
      var d = days[i], c = byDay[d];
      readout.textContent = dayText(d);
      clear(lists);
      if (!c || !c.alerts) return;
      CAL_KINDS.forEach(function (k) {
        if (c[k.key] && c[k.key].length) lists.appendChild(listBlock(k, c[k.key], d));
      });
    }
    calendar.select = function (d) { select(days.indexOf(d)); };

    var legend = el("div", { "class": "calendar-legend", "aria-hidden": "true" });
    legend.appendChild(el("span", null, "None"));
    for (var lv = 0; lv <= LEVELS; lv++) legend.appendChild(el("span", { "class": "swatch l" + lv }));
    legend.appendChild(el("span", null, max ? "Most: " + numFmt.format(max) : "Most"));

    // The table view: the days with the most alerts, newest first among equal days.
    var top = (data.calendar || []).filter(function (c) { return c.alerts > 0; })
      .sort(function (a, b) { return b.alerts - a.alerts || (a.day < b.day ? 1 : -1); }).slice(0, 5);
    var topBox = el("div", { "class": "calendar-top" });
    topBox.appendChild(el("h3", { id: "calendar-top-title" }, "Days with the most alerts"));
    if (!top.length) {
      topBox.appendChild(el("p", { "class": "empty" },
        "No day in the last 13 weeks had a component that Adobe flagged or that dropped to zero."));
    } else {
      topBox.appendChild(table(
        [{ label: "Day" }, { label: "Below range", num: true, cls: "days" },
         { label: "Above range", num: true, cls: "days" },
         { label: "Dropped to zero", num: true, cls: "days" }, { label: "Total", num: true, cls: "days" }],
        top.map(function (c) {
          var b = el("button", { type: "button", "class": "link nowrap" }, fmtDay(c.day, false));
          b.addEventListener("click", function () {
            select(days.indexOf(c.day));
            var svg = svgHost.querySelector("svg");
            if (svg) svg.focus();
          });
          return [{ c: b, k: c.day }, { c: String(c.low.length), k: c.low.length },
            { c: String(c.high.length), k: c.high.length },
            { c: String(c.zero.length), k: c.zero.length }, { c: String(c.alerts), k: c.alerts }];
        }), null, "calendar-top", { labelledby: "calendar-top-title" }));
    }

    clear(host);
    var main = el("div", { "class": "calendar-main" });
    append(main, [append(el("div", { "class": "calendar-left" }), [svgHost, legend]),
      append(el("div", { "class": "calendar-right" }), [readout, lists])]);
    append(host, [main, topBox]);
    fitTables();
    drawGrid();
    redrawers.push(drawGrid);
    select(days.indexOf(state.day) >= 0 ? days.indexOf(state.day) : days.length - 1);
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
    var day = chosenDay();
    var averages = (p && p.weekday_average) || {};
    drawChart(byId("individual-chart"), p ? data.series[p.series_id] : [], pairLabel(p),
      p ? p.metric_type : null,
      day ? { day: day, average: day in averages ? averages[day] : null } : null);
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
        }), null, "top100", { labelledby: "top100-title" }));
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
      host.appendChild(table(headers, body, foot, kind, { labelledby: kind + "-title" }));
    });
    fitTables();
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
    if (!a.zero && a.pct_vs_usual > 0) {
      return fmtPct(a.pct_vs_usual).replace("+", "") + " above the weekday average";
    }
    if (!a.zero) return fmtPct(a.pct_vs_usual).replace("-", "") + " below the weekday average";
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
      spikes: alerts.filter(function (a) {
        return plain(a) && !a.zero && isLargeSpike(a.actual, a.usual, a.pct_vs_usual);
      }),
      zero: alerts.filter(function (a) { return plain(a) && a.zero && !a.unused; }),
      unused: alerts.filter(function (a) { return plain(a) && a.zero && a.unused; })
    };
  }

  // How many days in a row, up to the alert's day, the same alert held. The SQL counts it. For a
  // large drop or spike it lists the change for each day back, and the page counts the days
  // that pass the slider. open: the row reaches the first day of the report, so it may be
  // longer.
  function streak(a) {
    var n = typeof a.days_in_row === "number" ? a.days_in_row : 1;
    var open = !!a.streak_open;
    if (a.drop_run && a.drop_run.length) {
      n = a.drop_run.filter(function (p) { return p < -state.t; }).length;
      open = open && n === a.drop_run.length;
    }
    if (a.spike_run && a.spike_run.length) {
      n = a.spike_run.filter(function (p) { return p > state.s; }).length;
      open = open && n === a.spike_run.length;
    }
    return { n: n, open: open };
  }
  function streakText(a) {
    var s = streak(a);
    return s.open ? s.n + " or more" : String(s.n);
  }
  function streakCell(a) {
    return { c: streakText(a), k: streak(a).n };
  }
  function streakSentence(a) {
    var s = streak(a);
    if (s.open) return s.n + " or more days in a row.";
    return s.n === 1 ? "First day with this alert." : s.n + " days in a row.";
  }

  function alertLine(a) {
    var s = "- " + a.name + " (" + a.component_id + "): " + alertReason(a) +
      ". Actual " + fmtNum(a.actual, a.metric_type);
    if (a.anomaly) {
      if (a.expected !== null) s += ", expected " + fmtRound(a.expected, a.metric_type);
      if (a.pct_vs_expected !== null) s += ", change " + fmtPct(a.pct_vs_expected);
    } else if (a.usual !== null) {
      s += ", weekday average " + fmtRound(a.usual, a.metric_type);
    }
    return s + ". " + streakSentence(a);
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
    if (!anyAlert(parts)) {
      lines.push("No alerts for this day.");
    } else {
      section("Unusual values", parts.unusual);
      section(dropTitle(), parts.drops);
      section(spikeTitle(), parts.spikes);
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

  function anyAlert(parts) {
    return !!(parts.unusual.length || parts.drops.length || parts.spikes.length ||
      parts.zero.length);
  }
  function dropTitle() {
    return "Drops of more than " + Math.round(state.t * 100) + "% below the weekday average";
  }
  function spikeTitle() {
    return "Spikes of more than " + Math.round(state.s * 100) + "% above the weekday average";
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
    return fmtDay(state.alertDay, false) + dayNote(state.alertDay);
  }

  // One plain sentence about large drops in these rows, naming the largest drop even when it is
  // under the slider, so a reader can see why nothing was flagged. A week is compared with the
  // average week, and a day with its weekday average.
  function dropSummary(rows, when, week) {
    var base = week ? "the average week" : "the weekday average";
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
        " more than " + pct + " below " + base + (byAdobe ? ", and Adobe also flagged " +
        (byAdobe === flagged ? (flagged === 1 ? "it" : "all of them") : byAdobe + " of them") :
        "") + "." :
      when + ": no component is more than " + pct + " below " + base + ".";
    if (!worst) return head + " No component is below " + base + ".";
    return head + " The largest drop is " + fmtPct(worst.pct).replace("-", "") + " (" +
      componentLabel(worst) + ").";
  }

  // The same sentence for large spikes, naming the largest rise.
  function spikeSummary(rows, when, week) {
    var base = week ? "the average week" : "the weekday average";
    var pct = Math.round(state.s * 100) + "%";
    var spikes = rows.filter(function (r) { return isLargeSpike(r.value, r.baseline, r.pct); });
    var flagged = spikes.length;
    var byAdobe = spikes.filter(function (r) { return !!r.anomaly; }).length;
    var best = null;
    rows.forEach(function (r) {
      if (r.pct === null || r.pct === undefined || r.pct <= 0) return;
      if (r.baseline === null || r.baseline < MIN_USUAL) return;
      if (!best || r.pct > best.pct) best = r;
    });
    var head = flagged ?
      when + ": " + flagged + (flagged === 1 ? " component is" : " components are") +
        " more than " + pct + " above " + base + (byAdobe ? ", and Adobe also flagged " +
        (byAdobe === flagged ? (flagged === 1 ? "it" : "all of them") : byAdobe + " of them") :
        "") + "." :
      when + ": no component is more than " + pct + " above " + base + ".";
    if (!best) return head + " No component is above " + base + ".";
    return head + " The largest rise is " + fmtPct(best.pct).replace("+", "") + " (" +
      componentLabel(best) + ").";
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
         { label: "Change from expected", num: true }, { label: "Days in a row", num: true, cls: "days" }],
        parts.unusual.map(function (a) {
          return nameIdCells(a).concat([anomalyCell(a.anomaly), expectedCell(a),
            { c: fmtNum(a.actual, a.metric_type), k: a.actual },
            devCell(arrowFor(a.pct_vs_expected), a.pct_vs_expected), streakCell(a)]);
        }), null, "alerts-unusual", { labelledby: "unusual-title" }));
    } else {
      g1.appendChild(el("p", { "class": "empty" }, anomalyBlocked ?
        "Anomaly detection is not available for this run, so no values could be flagged. " +
        "See the status section." : "Adobe flagged no unusual values on " + alertDayText() + "."));
    }
    host.appendChild(g1);

    var g2 = el("section", { "class": "alert-group", "aria-labelledby": "drops-title" });
    g2.appendChild(groupHeading("drops-title", dropTitle(), parts.drops.length));
    if (parts.drops.length) {
      g2.appendChild(el("p", { "class": "hint" },
        "These values were more than " + pct + " below their weekday average, but " +
        "Adobe did not flag them. Move the slider at the top of the page to change the percent."));
      g2.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Weekday average", num: true }, { label: "Actual", num: true },
         { label: "Change from weekday average", num: true },
         { label: "Days in a row", num: true, cls: "days" }],
        parts.drops.map(function (a) {
          return nameIdCells(a).concat([{ c: fmtRound(a.usual, a.metric_type), k: a.usual },
            { c: fmtNum(a.actual, a.metric_type), k: a.actual },
            devCell(arrowFor(a.pct_vs_usual), a.pct_vs_usual), streakCell(a)]);
        }), null, "alerts-drops", { labelledby: "drops-title" }));
    } else {
      g2.appendChild(el("p", { "class": "empty" },
        dropSummary(data.views["day:" + state.alertDay] || [], alertDayText())));
    }
    host.appendChild(g2);

    var spct = Math.round(state.s * 100) + "%";
    var g2b = el("section", { "class": "alert-group", "aria-labelledby": "spikes-title" });
    g2b.appendChild(groupHeading("spikes-title", spikeTitle(), parts.spikes.length));
    if (parts.spikes.length) {
      g2b.appendChild(el("p", { "class": "hint" },
        "These values were more than " + spct + " above their weekday average, but " +
        "Adobe did not flag them. A value that doubled, e.g., from a tag that fires twice, is " +
        "about +100%. Move the spike slider at the top of the page to change the percent."));
      g2b.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Weekday average", num: true }, { label: "Actual", num: true },
         { label: "Change from weekday average", num: true },
         { label: "Days in a row", num: true, cls: "days" }],
        parts.spikes.map(function (a) {
          return nameIdCells(a).concat([{ c: fmtRound(a.usual, a.metric_type), k: a.usual },
            { c: fmtNum(a.actual, a.metric_type), k: a.actual },
            devCell(arrowFor(a.pct_vs_usual), a.pct_vs_usual), streakCell(a)]);
        }), null, "alerts-spikes", { labelledby: "spikes-title" }));
    } else {
      g2b.appendChild(el("p", { "class": "empty" },
        spikeSummary(data.views["day:" + state.alertDay] || [], alertDayText())));
    }
    host.appendChild(g2b);

    var g3 = el("section", { "class": "alert-group", "aria-labelledby": "zero-title" });
    g3.appendChild(groupHeading("zero-title", "Dropped to zero", parts.zero.length));
    if (parts.zero.length) {
      g3.appendChild(el("p", { "class": "hint" },
        "These components had data on other days in this run, but a value of 0 on " +
        alertDayText() + "."));
      g3.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" },
         { label: "Weekday average", num: true }, { label: "Expected", num: true },
         { label: "Actual", num: true }, { label: "Days in a row", num: true, cls: "days" }],
        parts.zero.map(function (a) {
          var name = el("span");
          name.appendChild(document.createTextNode(a.name));
          name.appendChild(zeroBadge());
          return [{ c: name, k: a.name }, { c: el("code", null, a.component_id), k: a.component_id },
            { c: fmtRound(a.usual, a.metric_type), k: a.usual }, expectedCell(a),
            { c: fmtNum(a.actual, a.metric_type), k: a.actual }, streakCell(a)];
        }), null, "alerts-zero", { labelledby: "zero-title" }));
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
        parts.unused.map(nameIdCells), null, "alerts-unused",
        { label: "Never collected data" }));
      host.appendChild(g4);
    }

    byId("alert-text").value = alertText();
    fitTables();
  }

  // ---- earlier runs ---------------------------------------------------------------
  // The SQL counts the alerts on the last day of each run of this suite and segment, by the
  // same rules as the tiles. Large drops follow the slider: the SQL lists each candidate's
  // change, and the page counts the ones past the slider.
  var RUN_NOTES = { partial: " (some gaps)", failed: " (failed)", running: " (not finished)" };
  // ---- slow declines ------------------------------------------------------------------
  // The SQL finds the components and fits the line. The page shows one bar per week, oldest
  // first, from 0 to the largest week.
  var weekRows = {};
  (data.views.week || []).forEach(function (r) { weekRows[r.component_id] = r; });
  function weekBars(d) {
    var n = d.totals.length, gap = 2, w = (SPARK_W - gap * (n - 1)) / n;
    var max = Math.max.apply(null, d.totals);
    var svg = sv("svg", { viewBox: "0 0 " + SPARK_W + " " + SPARK_H, width: SPARK_W,
      height: SPARK_H, role: "img", "aria-label": "Weekly totals, oldest first: " +
        d.totals.map(function (t) { return numFmt.format(t); }).join(", ") });
    d.totals.forEach(function (t, i) {
      var h = max > 0 ? Math.max(1, t / max * (SPARK_H - 2)) : 1;
      svg.appendChild(sv("rect", { x: Math.round(i * (w + gap) * 10) / 10,
        y: Math.round((SPARK_H - h) * 10) / 10, width: Math.round(w * 10) / 10,
        height: Math.round(h * 10) / 10, rx: 1, "class": "week-bar" }));
    });
    return { c: svg, k: null };
  }
  function renderDeclines() {
    var host = byId("declines");
    if (!host) return;
    clear(host);
    var rows = (data.declines || []).filter(function (d) { return weekRows[d.component_id]; });
    if (!rows.length) {
      host.appendChild(el("p", { "class": "empty" }, "No component fell steadily in the last " +
        host.getAttribute("data-weeks") + " weeks."));
      return;
    }
    function pct(v) { return { c: fmtPct(v), k: v }; }
    host.appendChild(table(
      [{ label: "Name", cls: "name" }, { label: "Weekly totals", sort: false, cls: "spark-col" },
       { label: "Change per week", num: true }, { label: "Change over the weeks", num: true },
       { label: "Steady (0 to 1)", num: true }],
      rows.map(function (d) {
        var r = weekRows[d.component_id], name = componentLabel(r);
        return [{ c: name, k: name }, weekBars(d), pct(d.per_week), pct(d.overall),
          { c: d.fit.toFixed(2), k: d.fit }];
      }), null, "declines", { labelledby: "declines-title" }));
    fitTables();
  }

  function renderHistory() {
    var host = byId("history");
    if (!host) return;
    clear(host);
    var run = data.run || {};
    var runs = data.history || [];
    if (runs.length < 2) {
      host.appendChild(el("p", { "class": "empty" }, "This is the only run of this report suite" +
        (run.segment_id ? " and segment" : "") + " in the database. Later runs will show here."));
      return;
    }
    var rows = runs.map(function (h) {
      var drops = h.drops.filter(function (p) { return p < -state.t; }).length;
      var spikes = (h.spikes || []).filter(function (p) { return p > state.s; }).length;
      return { h: h, drops: drops, spikes: spikes,
        total: h.high + h.low + drops + spikes + h.zero };
    });
    var max = rows.reduce(function (m, r) { return Math.max(m, r.total); }, 0);
    function totalCell(r) {
      var wrap = el("span", { "class": "dev" });
      var bar = el("span", { "class": "runbar", "aria-hidden": "true" });
      var fill = el("span");
      fill.style.width = (max ? r.total / max * 100 : 0) + "%";
      bar.appendChild(fill);
      wrap.appendChild(bar);
      wrap.appendChild(el("span", null, numFmt.format(r.total)));
      return { c: wrap, k: r.total };
    }
    host.appendChild(table(
      [{ label: "Run" }, { label: "Day checked" }, { label: "Components", num: true, cls: "days" },
       { label: "Above range", num: true, cls: "days" },
       { label: "Below range", num: true, cls: "days" },
       { label: "Large drops", num: true, cls: "days" },
       { label: "Large spikes", num: true, cls: "days" },
       { label: "Dropped to zero", num: true, cls: "days" },
       { label: "All alerts", num: true }, { label: "Problems", num: true, cls: "days" }],
      rows.map(function (r) {
        var h = r.h, mine = h.run_id === run.id;
        var name = el("span", { "class": mine ? "this-run nowrap" : "nowrap" },
          "Run " + h.run_id + (mine ? " (this run)" : "") + (RUN_NOTES[h.status] || ""));
        function n(v) { return { c: numFmt.format(v), k: v }; }
        return [{ c: name, k: h.run_id },
          { c: el("span", { "class": "nowrap" }, fmtDay(h.day, true)), k: h.day }, n(h.checked),
          n(h.high), n(h.low), n(r.drops), n(r.spikes), n(h.zero), totalCell(r), n(h.problems)];
      }), null, "history", { labelledby: "history-title" }));
    fitTables();
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
    if (!anyAlert(parts)) {
      box.appendChild(el("p", { style: MAIL.p }, "No alerts for this day."));
    }
    section("Unusual values, flagged by Adobe", parts.unusual,
      [{ label: "Component" }, { label: "Flag" }, { label: "Expected", num: true },
       { label: "Actual", num: true }, { label: "Change", num: true },
       { label: "Days in a row", num: true, cls: "days" }],
      function (a) {
        return [name(a), a.anomaly === "high" ? "Above range" : "Below range",
          a.expected === null ? "Not available" : fmtRound(a.expected, a.metric_type),
          fmtNum(a.actual, a.metric_type), fmtPct(a.pct_vs_expected), streakText(a)];
      });
    function changeCells(a) {
      return [name(a), fmtRound(a.usual, a.metric_type), fmtNum(a.actual, a.metric_type),
        fmtPct(a.pct_vs_usual), streakText(a)];
    }
    var changeHeaders = [{ label: "Component" }, { label: "Weekday average", num: true },
      { label: "Actual", num: true }, { label: "Change", num: true },
      { label: "Days in a row", num: true, cls: "days" }];
    section(dropTitle(), parts.drops, changeHeaders, changeCells);
    section(spikeTitle(), parts.spikes, changeHeaders, changeCells);
    section("Dropped to zero", parts.zero,
      [{ label: "Component" }, { label: "Weekday average", num: true },
       { label: "Actual", num: true }, { label: "Days in a row", num: true, cls: "days" }],
      function (a) {
        return [name(a), fmtRound(a.usual, a.metric_type), fmtNum(a.actual, a.metric_type),
          streakText(a)];
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
    var spikes = rows.filter(function (r) {
      return !r.anomaly && isLargeSpike(r.value, r.baseline, r.pct);
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
      "More than " + Math.round(state.t * 100) + "% below the weekday average", toAlerts);
    tile("flag", "▲", "Large spikes", numFmt.format(spikes),
      "More than " + Math.round(state.s * 100) + "% above the weekday average", toAlerts);
    tile("zero", "●", "Dropped to zero", numFmt.format(zero),
      unused ? numFmt.format(unused) + " others never collected data" : "Had data on other days",
      toAlerts);
    tile(problems ? "flag" : "good", problems ? "!" : "✓", "Problems in this run",
      numFmt.format(problems), problems ? "See the status section" : "Everything was read",
      problems ? toStatus : null);
  }

  // ---- controls --------------------------------------------------------------
  var daySel = byId("day");
  dayKeys.forEach(function (d) {
    daySel.appendChild(el("option", { value: d },
      fmtDay(d, true) + dayNote(d)));
  });
  // The Day picker also moves the mark on the charts, so they are drawn again.
  // A new day or view ends a day picked in the calendar, so the charts read the chosen day.
  function forgetLook() {
    Object.keys(pickers).forEach(function (k) { pickers[k].forget(); });
  }
  daySel.addEventListener("change", function () {
    state.day = daySel.value;
    forgetLook();
    renderTables();
    redrawAll();
    calendar.select(state.day);
  });
  byId("granularity").addEventListener("change", function (e) {
    state.gran = e.target.value;
    byId("week-note").hidden = state.gran !== "week";
    byId("day-label").hidden = state.gran === "week";
    forgetLook();
    renderTables();
    redrawAll();
  });
  byId("range").addEventListener("change", function (e) {
    state.range = e.target.value;
    renderTables();
  });
  byId("flagged-only").addEventListener("change", function (e) {
    state.flagged = e.target.checked;
    renderTables();
  });
  byId("find").addEventListener("input", function (e) {
    state.find = e.target.value.trim();
    renderTables();
  });

  var alertDaySel = byId("alert-day");
  dayKeys.forEach(function (d) {
    alertDaySel.appendChild(el("option", { value: d },
      fmtDay(d, true) + dayNote(d)));
  });
  alertDaySel.addEventListener("change", function () {
    state.alertDay = alertDaySel.value;
    renderAlerts();
  });

  // The slider shows the drop as a negative change, from -100% on the left to -10% on the right,
  // so moving it left flags only deeper drops. state.t keeps the size of the drop, e.g., 0.3.
  // Colors the slider: the handle gets as much orange as the line under it, from 100 percent at
  // the left end to 20 percent at the right end. strongEnd says which end is furthest from zero.
  function paintSlider(input, strongEnd) {
    var min = Number(input.min), max = Number(input.max);
    var pos = (Number(input.value) - min) / (max - min);
    var far = strongEnd === "left" ? 1 - pos : pos;
    input.style.setProperty("--pos", Math.round(pos * 1000) / 10 + "%");
    input.style.setProperty("--strength", Math.round(20 + far * 80) + "%");
  }
  function afterSlider() {
    renderTiles();
    renderTables();
    renderAlerts();
    renderHistory();
  }
  var slider = byId("change-alert");
  slider.value = String(-Math.round(state.t * 100));
  paintSlider(slider, "left");
  slider.addEventListener("input", function () {
    state.t = -Number(slider.value) / 100;
    paintSlider(slider, "left");
    byId("change-alert-value").textContent = slider.value + "%";
    slider.setAttribute("aria-valuetext", slider.value + "%");
    afterSlider();
  });
  // The spike slider runs from +10% on the left to +200% on the right, so moving it right flags
  // only bigger spikes. Its orange is strongest at the right end.
  var spikeSlider = byId("spike-alert");
  spikeSlider.value = String(Math.round(state.s * 100));
  paintSlider(spikeSlider, "right");
  spikeSlider.addEventListener("input", function () {
    state.s = Number(spikeSlider.value) / 100;
    paintSlider(spikeSlider, "right");
    byId("spike-alert-value").textContent = "+" + spikeSlider.value + "%";
    spikeSlider.setAttribute("aria-valuetext", "+" + spikeSlider.value + "%");
    afterSlider();
  });

  renderTiles();
  renderTables();
  pickers.metric = fillPicker(byId("metric-pick"), "metric", "metric-chart", byId("metric-type"));
  pickers.dimension = fillPicker(byId("dimension-pick"), "dimension", "dimension-chart",
    byId("dimension-type"));
  renderCalendar();
  renderIndividual();
  renderAlerts();
  renderDeclines();
  renderHistory();
})();
