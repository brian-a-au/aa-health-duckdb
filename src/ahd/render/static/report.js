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

  // ---- state ---------------------------------------------------------------
  var dayKeys = Object.keys(data.views).filter(function (k) { return k.indexOf("day:") === 0; })
    .map(function (k) { return k.slice(4); }).sort().reverse();
  var yesterday = dayKeys[0] || "";
  var state = { gran: "day", day: yesterday, range: "" };
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
  function table(headers, rows, foot) {
    var wrap = el("div", { "class": "table-wrap" });
    var t = el("table");
    var thead = el("thead");
    var tr = el("tr");
    function cls(h) { return h ? (h.num ? "num" : h.cls || null) : null; }
    headers.forEach(function (h) {
      tr.appendChild(el("th", { scope: "col", "class": cls(h) }, h.label));
    });
    thead.appendChild(tr);
    var tbody = el("tbody");
    rows.forEach(function (cells) {
      var r = el("tr");
      cells.forEach(function (c, i) {
        var td = el("td", { "class": cls(headers[i]) });
        if (c instanceof Node) td.appendChild(c); else td.textContent = c === null ? "" : c;
        r.appendChild(td);
      });
      tbody.appendChild(r);
    });
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
    wrap.appendChild(t);
    return wrap;
  }

  function changeCell(arrow, pct) {
    var span = el("span");
    if (arrow === "up" || arrow === "down") {
      span.appendChild(el("span", {
        "class": "arrow-" + arrow,
        "aria-label": arrow === "up" ? "up" : "down"
      }, arrow === "up" ? "▲ " : "▼ "));
    }
    span.appendChild(document.createTextNode(fmtPct(pct)));
    return span;
  }

  function nameCell(row) {
    if (!row.zero) return document.createTextNode(row.name);
    var span = el("span", { "class": "name-zero", title: "This component had a value of 0." },
      row.name);
    span.appendChild(el("span", { "class": "zero-tag" }, "(zero data)"));
    return span;
  }

  function anomalyCell(a) {
    if (a === "high") return el("span", { "class": "anom-high" }, "▲ High");
    if (a === "low") return el("span", { "class": "anom-low" }, "▼ Low");
    return anomalyBlocked ? el("span", { "class": "muted" }, "Not available") : "";
  }

  function inRange(row) {
    if (!state.range || CUSTOM.indexOf(row.group) < 0) return true;
    var p = state.range.split("-");
    return row.number !== null && row.number >= +p[0] && row.number <= +p[1];
  }

  function componentTable(rows, custom) {
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
    if (daily) headers.push({ label: "Anomaly" });
    var body = rows.map(function (r) {
      var cells = [];
      if (custom) cells.push(numberLabel(r));
      cells.push(nameCell(r));
      cells.push(fmtNum(r.value));
      cells.push(fmtNum(r.baseline));
      cells.push(changeCell(r.arrow, r.pct));
      if (daily) cells.push(anomalyCell(r.anomaly));
      return cells;
    });
    return table(headers, body);
  }

  function groupBlock(title, rows, custom, emptyText) {
    var frag = document.createDocumentFragment();
    var h = el("h3", { "class": "group-title" }, title);
    if (rows.length) h.appendChild(el("span", { "class": "group-count" },
      rows.length === 1 ? "1 component" : rows.length + " components"));
    frag.appendChild(h);
    frag.appendChild(rows.length ? componentTable(rows, custom) : el("p", { "class": "empty" },
      emptyText));
    return frag;
  }

  function currentRows() {
    return (state.gran === "week" ? data.views.week : data.views["day:" + state.day]) || [];
  }

  function renderTables() {
    var host = byId("tables");
    clear(host);
    var rows = currentRows();
    var byGroup = {};
    rows.forEach(function (r) { (byGroup[r.group] = byGroup[r.group] || []).push(r); });

    host.appendChild(el("h2", null, "Custom components"));
    CUSTOM.forEach(function (g) {
      var all = byGroup[g] || [];
      var shown = all.filter(inRange);
      var empty = all.length ? "None in this number range." :
        "None were found. See the status section if you expected some.";
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
    if (!any) host.appendChild(el("p", { "class": "empty" }, "None were found."));
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
    if (kind === "high") s.appendChild(sv("path", { d: "M9 1 L14 9 L4 9 Z", "class": "mark-high" }));
    if (kind === "low") s.appendChild(sv("path", { d: "M9 9 L14 1 L4 1 Z", "class": "mark-low" }));
    li.appendChild(s);
    li.appendChild(el("span", null, label));
    return li;
  }

  function describe(p) {
    var parts = [fmtDay(p[0], true) + ": " + fmtNum(p[1])];
    if (p[2] !== null) parts.push("expected " + fmtNum(p[2]));
    if (p[3] !== null && p[4] !== null) {
      parts.push("expected range " + fmtNum(p[4]) + " to " + fmtNum(p[3]));
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
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), "class": "grid" }));
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
          d: "M" + cx + " " + (cy - 7) + " L" + (cx + 6) + " " + (cy + 4) + " L" + (cx - 6) + " " + (cy + 4) + " Z",
          "class": "mark-high"
        }));
      } else if (p[5] === "low") {
        anyLow = true;
        svg.appendChild(sv("path", {
          d: "M" + cx + " " + (cy + 7) + " L" + (cx + 6) + " " + (cy - 4) + " L" + (cx - 6) + " " + (cy - 4) + " Z",
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
    if (anyHigh) legend.appendChild(legendItem("high", "Above the range"));
    if (anyLow) legend.appendChild(legendItem("low", "Below the range"));
    host.appendChild(legend);
    if (!hasBands) {
      host.appendChild(byId("no-bands-template").content.cloneNode(true));
    }
  }

  function fillPicker(select, kind, chartId) {
    var rows = (data.views.week || []).filter(function (r) {
      return r.kind === kind && data.series[r.component_id];
    });
    clear(select);
    rows.forEach(function (r) {
      select.appendChild(el("option", { value: r.component_id }, componentLabel(r)));
    });
    var host = byId(chartId);
    function draw() {
      var r = rows[select.selectedIndex];
      if (!r) {
        clear(host);
        host.appendChild(el("p", { "class": "chart-empty" },
          kind === "metric" ? "No metric data was read." : "No dimension data was read."));
        return;
      }
      drawChart(host, data.series[r.component_id], componentLabel(r));
    }
    select.disabled = !rows.length;
    select.addEventListener("change", draw);
    redrawers.push(draw);
    draw();
  }

  // ---- individual component tab ---------------------------------------------
  function movementCell(m) {
    if (m === "*") return el("span", { title: "New to the top 100" }, "* new");
    if (m === "-" || m === 0) return el("span", { title: "No change" }, "-");
    if (typeof m === "number") {
      return el("span", { "class": m > 0 ? "arrow-up" : "arrow-down",
        title: m > 0 ? "Moved up" : "Moved down" },
        (m > 0 ? "▲ " : "▼ ") + Math.abs(m));
    }
    return String(m);
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
        [{ label: "Movement" }, { label: "Rank", num: true }, { label: "Value", cls: "name" },
         { label: "Count", num: true }],
        rows.map(function (r) {
          return [movementCell(r.movement), String(r.rank), r.item, fmtNum(r.value)];
        })));
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
      var body = b.rows.map(function (r) {
        return [r.item].concat(r.values.map(fmtNum));
      });
      var foot = b.totals && b.totals.length ? ["Total"].concat(b.totals.map(fmtNum)) : null;
      host.appendChild(table(headers, body, foot));
    });
  }

  // ---- alerts tab ------------------------------------------------------------
  function alertReason(a) {
    if (a.actual === 0) return "value is 0";
    return a.arrow === "down" || (a.expected !== null && a.actual < a.expected) ?
      "below the expected range" : "above the expected range";
  }

  function alertText() {
    var run = data.run || {};
    var alerts = data.alerts || [];
    var lines = [];
    lines.push("Analytics Health alerts for " + (run.rsid_name || run.rsid) + " (" + run.rsid +
      "), " + yesterday);
    lines.push("Segment: " + (run.segment_id || "none (all visits)"));
    if (!alerts.length) {
      lines.push("No alerts for this day.");
    } else {
      lines.push(alerts.length === 1 ? "1 alert:" : alerts.length + " alerts:");
      alerts.forEach(function (a) {
        var s = "- " + a.name + " (" + a.component_id + "): " + alertReason(a) +
          ". Actual " + fmtNum(a.actual);
        if (a.expected !== null) s += ", expected " + fmtNum(a.expected);
        if (a.pct_vs_expected !== null) s += ", change " + fmtPct(a.pct_vs_expected);
        lines.push(s + ".");
      });
    }
    if ((data.issues || []).length) {
      lines.push("Some data could not be read in this run. See the status section of the report.");
    }
    return lines.join("\n");
  }

  function renderAlerts() {
    var host = byId("alerts");
    clear(host);
    var alerts = data.alerts || [];
    if (!alerts.length) {
      host.appendChild(el("p", { "class": "empty" }, "No alerts for yesterday."));
    } else {
      host.appendChild(table(
        [{ label: "Name", cls: "name" }, { label: "ID", cls: "id" }, { label: "Expected", num: true },
         { label: "Actual", num: true }, { label: "Change from expected", num: true }],
        alerts.map(function (a) {
          var name = a.actual === 0 ? el("span", { "class": "name-zero" }, a.name) : a.name;
          return [name, el("code", null, a.component_id),
            a.expected === null ? "Not available" : fmtNum(a.expected),
            fmtNum(a.actual), changeCell(a.arrow, a.pct_vs_expected)];
        })));
    }
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

  renderTables();
  fillPicker(byId("metric-pick"), "metric", "metric-chart");
  fillPicker(byId("dimension-pick"), "dimension", "dimension-chart");
  renderIndividual();
  renderAlerts();
})();
