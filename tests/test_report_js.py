"""Tests that run report.js in headless Chrome. See tests/chrome.py for how a probe works."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from chrome import chrome_or_skip, find_chrome, run_probe, without_csp

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.render.html import render_html
from ahd.store import Store
from ahd.summary import ANOMALY_LABELS, ANOMALY_METHODS, build_view

TODAY = date(2026, 11, 7)
YESTERDAY = date(2026, 11, 6)  # a Friday
START = TODAY - timedelta(days=91)
NAN = float("nan")
# The page starts at the default of 30 percent. The probe then moves the slider to each of these.
SLIDER = (10, 15, 20, 60)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def chrome():
    return chrome_or_skip()


def _days():
    d = START
    while d <= TODAY:
        yield d
        d += timedelta(days=1)


def _series(cid, friday, yesterday, others=0.0):
    """friday on every Friday, others on the other days, and yesterday on yesterday."""
    return [
        _dv(cid, d, yesterday if d == YESTERDAY else friday if d.weekday() == 4 else others)
        for d in _days()
    ]


def _dv(cid, day, value, expected=None, upper=None, lower=None, anomaly=None):
    return DailyValue(cid, day, value, expected, upper, lower, anomaly)


def _flagged(cid, usual, yesterday, expected, upper, lower):
    """usual on every day, and yesterday on yesterday with an Adobe flag and band."""
    out = [v for v in _series(cid, usual, usual, usual) if v.day != YESTERDAY]
    return out + [_dv(cid, YESTERDAY, yesterday, expected, upper, lower, True)]


# Values that vary around 100 on the 12 Fridays before yesterday.
SPREAD = [100.0, 110.0, 90.0, 100.0, 120.0, 80.0, 100.0, 105.0, 95.0, 100.0, 130.0, 70.0]


def _spread(cid, yesterday):
    """SPREAD on the 12 Fridays before yesterday, 100 on the other days, and yesterday."""
    fridays = iter(SPREAD)
    return [
        _dv(cid, d, yesterday if d == YESTERDAY else next(fridays) if d.weekday() == 4 else 100.0)
        for d in _days()
    ]


# Each case is (name, daily values). The cases are the edge cases of the alert rules.
CASES = {
    "event1": ("Weekday average exactly 10, yesterday 7", lambda c: _series(c, 10.0, 7.0)),
    "event2": ("Weekday average exactly 10, yesterday 8", lambda c: _series(c, 10.0, 8.0)),
    "event3": ("Weekday average 8, yesterday 1", lambda c: _series(c, 8.0, 1.0)),
    "event4": ("Drop of 15 percent", lambda c: _series(c, 100.0, 85.0)),
    "event5": ("Drop of 50 percent", lambda c: _series(c, 100.0, 50.0)),
    "event6": ("Dropped to zero", lambda c: _series(c, 100.0, 0.0)),
    "event7": ("Never collected data", lambda c: _series(c, 0.0, 0.0)),
    "event8": (
        "NaN on some days, zero on the others",
        lambda c: [_dv(c, d, 0.0 if (d == YESTERDAY or d.day % 2) else NAN) for d in _days()],
    ),
    "event9": ("Anomaly and zero on the same day", lambda c: _flagged(c, 100.0, 0.0, 100, 130, 70)),
    "event10": (
        "Never collected data, with an Adobe flag",
        lambda c: (
            [v for v in _series(c, 0.0, 0.0) if v.day != YESTERDAY]
            + [_dv(c, YESTERDAY, 0.0, 50.0, 80.0, 20.0, True)]
        ),
    ),
    "event11": ("Above the expected range", lambda c: _flagged(c, 100.0, 140.0, 100, 120, 80)),
    "event12": ("NaN yesterday, with an Adobe flag", lambda c: _flagged(c, 100, NAN, 100, 130, 70)),
    "event13": (
        'Name with </script> & "quotes" and   a line separator',
        lambda c: [v for v in _series(c, 50.0, 50.0) if v.day != YESTERDAY - timedelta(days=3)],
    ),
    "event14": ("Spike of 60 percent", lambda c: _series(c, 100.0, 160.0)),
    "event15": ("Spike of 20 percent", lambda c: _series(c, 100.0, 120.0)),
    "event16": ("Weekday average 8, yesterday 30", lambda c: _series(c, 8.0, 30.0)),
    # Inside the range of normal and outside the narrower range of high, for every local method.
    "event17": ("Values that vary, yesterday 25 percent up", lambda c: _spread(c, 125.0)),
}


@pytest.fixture(scope="module")
def edges(tmp_path_factory):
    path = tmp_path_factory.mktemp("edges") / "edges.duckdb"
    store = Store.open(path)
    rid = store.create_run("rs1", "Suite One", None, TODAY, "US/Pacific", "0.1.0")
    store.add_pair(rid, "variables/page", "metrics/occurrences")
    comps, values = [], []
    for i, (key, (name, make)) in enumerate(CASES.items(), start=1):
        cid = f"metrics/{key}"
        comps.append(Component(cid, name, "metric", "custom_events", i, Column(cid, cid), True))
        values += make(cid)
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    yield store, rid
    store.con.close()


@pytest.fixture(scope="module")
def showcase(tmp_path_factory):
    """The synthetic run that scripts/make_showcase.py builds for the README."""
    spec = importlib.util.spec_from_file_location(
        "make_showcase", ROOT / "scripts/make_showcase.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store, rid = mod.build_store(tmp_path_factory.mktemp("showcase") / "showcase.duckdb")
    yield store, rid
    store.con.close()


# Reads the four alert lists, the alert text and the tiles. Then moves the slider and reads them
# again for each percent in SLIDER.
ALERTS_PROBE = """
function ids(selector) {
  return [].map.call(document.querySelectorAll(selector + " tbody code"), function (c) {
    return c.textContent;
  }).sort();
}
function snapshot() {
  var tiles = {};
  [].forEach.call(document.querySelectorAll("#tiles .tile"), function (t) {
    var note = t.querySelector(".tile-note");
    tiles[t.querySelector(".tile-label").lastChild.textContent] = {
      value: t.querySelector(".tile-value").textContent, note: note ? note.textContent : null
    };
  });
  return {
    shown: document.getElementById("change-alert-value").textContent,
    valuetext: document.getElementById("change-alert").getAttribute("aria-valuetext"),
    pos: document.getElementById("change-alert").style.getPropertyValue("--pos"),
    strength: document.getElementById("change-alert").style.getPropertyValue("--strength"),
    unusual: ids("#alerts section[aria-labelledby=unusual-title]"),
    drops: ids("#alerts section[aria-labelledby=drops-title]"),
    spikes: ids("#alerts section[aria-labelledby=spikes-title]"),
    spikeShown: document.getElementById("spike-alert-value").textContent,
    zero: ids("#alerts section[aria-labelledby=zero-title]"),
    unused: ids("#alerts details.alert-group"),
    text: document.getElementById("alert-text").value,
    tiles: tiles
  };
}
var out = { "30": snapshot() };
var slider = document.getElementById("change-alert");
var spike = document.getElementById("spike-alert");
// Both sliders move to the same percent. The spike slider moves in steps of 10, so 15 becomes 20.
SLIDER.forEach(function (p) {
  slider.value = String(-p);
  slider.dispatchEvent(new Event("input"));
  spike.value = String(p);
  spike.dispatchEvent(new Event("input"));
  out[String(p)] = snapshot();
});
return out;
"""


def _macro(store, rid, threshold, spike=0.5, method="adobe", sensitivity="normal"):
    rows = store.con.execute(
        "select component_id, alert, anomaly from alerts(?, threshold := ?, spike_threshold := ?,"
        " method := ?, sensitivity := ?)",
        [rid, threshold, spike, method, sensitivity],
    ).fetchall()
    return {cid: (alert, anomaly) for cid, alert, anomaly in rows}


def _never_collected(store, rid):
    rows = store.con.execute(
        "select component_id from component_days"
        " where run_id = ? and is_yesterday and zero and unused order by 1",
        [rid],
    ).fetchall()
    return [r[0] for r in rows]


# Each anomaly method with each sensitivity it takes. Adobe's method takes only normal.
METHOD_LEVELS = [("adobe", "normal")] + [
    (m, s) for m in ANOMALY_METHODS if m != "adobe" for s in ("low", "normal", "high")
]


@pytest.fixture(scope="module")
def pages_by_method(chrome, edges, tmp_path_factory):
    """The alert lists and tiles of the edge cases, read once for each anomaly method and
    sensitivity."""
    store, rid = edges
    probe = ALERTS_PROBE.replace("SLIDER", json.dumps(list(SLIDER)))
    pages = {}

    def read(method, sensitivity="normal"):
        if (method, sensitivity) not in pages:
            folder = tmp_path_factory.mktemp("alerts")
            page = render_html(build_view(store, rid, method=method, sensitivity=sensitivity))
            pages[method, sensitivity] = run_probe(chrome, page, probe, folder)
        return pages[method, sensitivity]

    return read


@pytest.fixture(scope="module")
def alert_pages(pages_by_method):
    return pages_by_method("adobe")


@pytest.mark.parametrize(("method", "sensitivity"), METHOD_LEVELS)
@pytest.mark.parametrize("pct", (30, *SLIDER))
def test_alert_lists_match_the_alerts_macro(pages_by_method, edges, pct, method, sensitivity):
    store, rid = edges
    page = pages_by_method(method, sensitivity)[str(pct)]
    spike = int(page["spikeShown"].strip("+%")) / 100
    macro = _macro(store, rid, pct / 100, spike, method, sensitivity)

    def where(kind):
        return sorted(cid for cid, (alert, _) in macro.items() if alert == kind)

    assert page["shown"] == f"-{pct}%"
    assert page["valuetext"] == f"-{pct}%"
    # The handle is pct from the right end of -100 to -10, and gets more orange further left:
    # 20 percent at -10 and 100 percent at -100.
    far = (pct - 10) / 90
    assert page["pos"] == f"{round((1 - far) * 1000) / 10:g}%"
    assert page["strength"] == f"{round(20 + far * 80)}%"
    assert page["unusual"] == where("unusual")
    assert page["drops"] == where("large drop")
    assert page["spikes"] == where("large spike")
    assert page["zero"] == where("dropped to zero")
    assert page["unused"] == _never_collected(store, rid)
    listed = re.findall(r"^- .*\((metrics/[^)]+)\): ", page["text"], re.MULTILINE)
    assert sorted(listed) == sorted(macro)
    if where("large drop"):
        assert (
            f"Drops of more than {pct}% below the weekday average ({len(where('large drop'))}):"
            in page["text"]
        )
    if where("large spike"):
        assert (
            f"Spikes of more than {round(spike * 100)}% above the weekday average "
            f"({len(where('large spike'))}):" in page["text"]
        )


TILE_PROBE = """
function drops() {
  return [].map.call(document.querySelectorAll(
    "#alerts section[aria-labelledby=drops-title] tbody code"), function (c) {
    return c.textContent;
  }).sort();
}
var sel = document.getElementById("alert-day");
sel.value = sel.options[sel.options.length - 1].value;
sel.dispatchEvent(new Event("change"));
var before = { day: sel.value, drops: drops() };
document.getElementById("tab-components").click();
var tile = [].filter.call(document.querySelectorAll("#tiles .tile"), function (t) {
  return t.querySelector(".tile-label").lastChild.textContent === "Large drops";
})[0];
tile.click();
return { before: before, value: tile.querySelector(".tile-value").textContent,
  day: sel.value, drops: drops(),
  tab: document.getElementById("tab-alerts").getAttribute("aria-selected") };
"""


def test_a_tile_opens_the_alerts_of_its_own_day(chrome, edges, tmp_path):
    # The tiles count the day before the run date. The Alerts tab may show another day, chosen
    # earlier, so a tile shows that day again, and its count matches the list.
    store, rid = edges
    got = run_probe(chrome, render_html(build_view(store, rid)), TILE_PROBE, tmp_path)
    assert got["before"]["day"] == "2026-10-31" and got["before"]["drops"] == []
    assert got["tab"] == "true" and got["day"] == YESTERDAY.isoformat()
    expected = sorted(
        c for c, (alert, _) in _macro(store, rid, 0.3).items() if alert == "large drop"
    )
    assert got["drops"] == expected and got["value"] == str(len(expected)) and expected


def test_alert_edge_cases(alert_pages):
    p30, p20, p10 = alert_pages["30"], alert_pages["20"], alert_pages["10"]
    # A drop of exactly the slider percent is not flagged. A weekday average of exactly 10 counts.
    assert "metrics/event1" not in p30["drops"] and "metrics/event1" in p20["drops"]
    assert "metrics/event2" not in p20["drops"] and "metrics/event2" in p10["drops"]
    # A weekday average under 10 is never a drop.
    assert all("metrics/event3" not in alert_pages[str(p)]["drops"] for p in (30, *SLIDER))
    # NaN and zero on every day: never collected data, and not an alert.
    assert {"metrics/event7", "metrics/event8"} <= set(p30["unused"])
    # An Adobe flag on a zero is an unusual value, not a drop to zero.
    assert "metrics/event9" in p30["unusual"] and "metrics/event9" not in p30["zero"]
    # A component that never collected data stays out of the alerts, even with an Adobe flag.
    assert "metrics/event10" in p30["unused"] and "metrics/event10" not in p30["unusual"]
    assert p30["zero"] == ["metrics/event6"]


@pytest.mark.parametrize(("method", "sensitivity"), METHOD_LEVELS)
@pytest.mark.parametrize("pct", (30, *SLIDER))
def test_tiles_match_the_alerts_macro(pages_by_method, edges, pct, method, sensitivity):
    store, rid = edges
    page = pages_by_method(method, sensitivity)[str(pct)]
    tiles = page["tiles"]
    spike = int(page["spikeShown"].strip("+%"))
    macro = _macro(store, rid, pct / 100, spike / 100, method, sensitivity).values()

    def count(alert, anomaly=None):
        return str(sum(1 for a, f in macro if a == alert and (anomaly is None or f == anomaly)))

    assert tiles["Components checked"]["value"] == str(len(CASES))
    assert tiles["Above expected range"]["value"] == count("unusual", "high")
    assert tiles["Below expected range"]["value"] == count("unusual", "low")
    assert tiles["Large drops"] == {
        "value": count("large drop"),
        "note": f"More than {pct}% below the weekday average",
    }
    assert tiles["Large spikes"] == {
        "value": count("large spike"),
        "note": f"More than {spike}% above the weekday average",
    }
    unused = len(_never_collected(edges[0], edges[1]))
    assert tiles["Dropped to zero"] == {
        "value": count("dropped to zero"),
        "note": f"{unused} others never collected data",
    }
    assert tiles["Problems in this run"]["value"] == "0"


def test_the_slider_changes_the_lists_and_tiles(alert_pages):
    drops = {p: alert_pages[str(p)]["drops"] for p in (30, *SLIDER)}
    assert drops[10] == [f"metrics/event{i}" for i in (1, 2, 4, 5)]
    assert drops[15] == [f"metrics/event{i}" for i in (1, 2, 5)]
    assert drops[30] == ["metrics/event5"] and drops[60] == []
    tiles = {p: alert_pages[str(p)]["tiles"]["Large drops"]["value"] for p in drops}
    assert tiles == {30: "1", 10: "4", 15: "3", 20: "2", 60: "0"}
    # The spike slider starts at +50%, and moves to +10%, +20%, +20% and +60%. A spike of exactly
    # the setting is not flagged, and a weekday average under 10 never is. event17 is 25 percent
    # above its weekday average.
    spikes = {p: alert_pages[str(p)]["spikes"] for p in (30, *SLIDER)}
    assert spikes == {
        30: ["metrics/event14"],
        10: ["metrics/event14", "metrics/event15", "metrics/event17"],
        15: ["metrics/event14", "metrics/event17"],
        20: ["metrics/event14", "metrics/event17"],
        60: [],
    }


# Runs the page's own unpackView on the page data, and returns the result.
UNPACK_PROBE = """
var src = [].map.call(document.scripts, function (s) { return s.textContent; })
  .filter(function (t) { return t.indexOf("function unpackView(") >= 0; })[0];
var start = src.indexOf("  function unpackView(");
var end = src.indexOf("\\n  }\\n", start) + 4;
var unpackView = new Function(src.slice(start, end) + "\\nreturn unpackView;")();
var data = unpackView(JSON.parse(document.getElementById("data").textContent));
delete data.components;
delete data.packed;
return data;
"""


@pytest.mark.parametrize("which", ["edges", "showcase"])
def test_unpack_view_gives_back_the_view(chrome, request, tmp_path, which):
    store, rid = request.getfixturevalue(which)
    view = build_view(store, rid)
    page = without_csp(render_html(view))
    got = run_probe(chrome, page, UNPACK_PROBE, tmp_path)
    want = json.loads(json.dumps(view))
    assert got.keys() == want.keys()
    for key in want:
        assert got[key] == want[key], key
    assert want["views"] and want["alerts"] and want["series"]


# Opens each tab and view, and returns how wide the page is compared with its window.
LAYOUT_PROBE = """
var root = document.documentElement, out = [];
function check(label) {
  out.push({ label: label, width: root.clientWidth, scroll: root.scrollWidth });
}
["tab-components", "tab-individual", "tab-alerts"].forEach(function (id) {
  document.getElementById(id).click();
  check(id);
});
document.getElementById("tab-components").click();
var g = document.getElementById("granularity");
g.value = "week";
g.dispatchEvent(new Event("change"));
check("week");
return out;
"""


@pytest.mark.parametrize("width", [375, 960, 1280])
def test_no_horizontal_scroll(chrome, showcase, edges, tmp_path, width):
    for name, (store, rid) in (("showcase", showcase), ("edges", edges)):
        page = render_html(build_view(store, rid))
        checks = run_probe(chrome, page, LAYOUT_PROBE, tmp_path / name, width=width)
        assert len(checks) == 4
        for c in checks:
            assert c["width"] <= width
            assert c["scroll"] <= c["width"], (name, c)


def test_a_probe_error_fails_the_test(chrome, edges, tmp_path):
    page = render_html(build_view(*edges))
    with pytest.raises(AssertionError, match="probe failed on purpose"):
        run_probe(chrome, page, 'throw new Error("probe failed on purpose");', tmp_path / "a")
    broken = page.replace("var SVG_NS", "nosuchfunction(); var SVG_NS", 1)
    with pytest.raises(AssertionError, match="nosuchfunction"):
        run_probe(chrome, broken, "return 1;", tmp_path / "b")


def test_missing_chrome_skips_on_a_laptop_and_fails_in_ci(monkeypatch):
    monkeypatch.setattr("chrome.find_chrome", lambda: None)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception, match="AHD_CHROME"):
        chrome_or_skip()
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="AHD_CHROME"):
        chrome_or_skip()


def test_ahd_chrome_chooses_the_program(monkeypatch, tmp_path):
    program = tmp_path / "my-chrome"
    program.write_text("", encoding="utf-8")
    monkeypatch.setenv("AHD_CHROME", str(program))
    assert find_chrome() == str(program)
    monkeypatch.setenv("AHD_CHROME", str(tmp_path / "missing"))
    with pytest.raises(pytest.fail.Exception, match="no file there"):
        find_chrome()


@pytest.fixture(scope="module")
def with_problem(tmp_path_factory):
    """A run with one problem in its status section, for the Copy problems button."""
    store = Store.open(tmp_path_factory.mktemp("problem") / "p.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "US/Pacific", "0.1.0")
    store.add_pair(rid, "variables/page", "metrics/occurrences")
    cid = "metrics/event1"
    store.add_components(
        rid, [Component(cid, "Orders <b>", "metric", "custom_events", 1, Column(cid, cid), True)]
    )
    store.add_daily_values(rid, _series(cid, 10.0, 10.0))
    store.add_issue(rid, "component", "metrics/event2", "unauthorized_metric", "Event 2 is hidden")
    store.finish_run(rid, "partial")
    yield store, rid
    store.con.close()


def _number(text):
    """A number from a table cell, e.g., "2,716" or "▼ -63.1%". None for an empty cell."""
    found = re.search(r"-?[\d,]+(\.\d+)?", text.replace("−", "-"))
    return float(found.group(0).replace(",", "")) if found else None


def _natural(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text.casefold())]


# Clicks a heading of the first components table three times, then clicks "Original order".
SORT_PROBE = """
var table = document.querySelector("#tables table");
function snap() {
  var note = table.closest(".table-wrap").nextElementSibling;
  return {
    cells: [].map.call(table.tBodies[0].rows, function (r) {
      return [].map.call(r.cells, function (c) { return c.textContent; });
    }),
    sort: [].map.call(table.tHead.rows[0].cells, function (th) { return th.getAttribute("aria-sort"); }),
    note: note.hidden ? null : note.firstChild.textContent,
    button: (note.querySelector("button") || {}).textContent || null
  };
}
var heads = [].map.call(table.tHead.rows[0].cells, function (th) { return th.textContent.slice(0, -1); });
var out = { heads: heads, label: table.getAttribute("aria-label"), runs: {} };
COLUMNS.forEach(function (col) {
  var button = table.tHead.rows[0].cells[col].querySelector("button");
  var steps = [snap()];
  for (var i = 0; i < 3; i++) { button.click(); steps.push(snap()); }
  button.click();
  table.closest(".table-wrap").nextElementSibling.querySelector("button").click();
  steps.push(snap());
  out.runs[col] = steps;
});
return out;
"""


def _sort_run(chrome, store_rid, folder, columns):
    page = render_html(build_view(*store_rid))
    probe = SORT_PROBE.replace("COLUMNS", json.dumps(columns))
    return run_probe(chrome, page, probe, folder)


def test_a_number_column_sorts_largest_first_then_smallest_then_back(chrome, showcase, tmp_path):
    got = _sort_run(chrome, showcase, tmp_path, [3])
    assert got["heads"][3] == "Total for the day" and got["label"]
    original, first, second, third, reset = got["runs"]["3"]
    values = [[_number(r[3]) for r in step["cells"]] for step in (first, second)]
    assert values[0] == sorted(values[0], reverse=True)
    assert values[1] == sorted(values[1])
    assert first["sort"][3] == "descending" and second["sort"][3] == "ascending"
    assert first["note"] == "Sorted by Total for the day, largest first."
    assert second["note"] == "Sorted by Total for the day, smallest first."
    assert third["cells"] == original["cells"] and third["sort"][3] == "none"
    assert third["note"] is None
    assert reset["cells"] == original["cells"] and reset["note"] is None
    assert first["button"] == "Original order"


def test_a_text_column_sorts_a_to_z_then_z_to_a(chrome, showcase, tmp_path):
    got = _sort_run(chrome, showcase, tmp_path, [1])
    _original, first, second, _third, _reset = got["runs"]["1"]
    names = [[r[1] for r in step["cells"]] for step in (first, second)]
    assert names[0] == sorted(names[0], key=_natural)
    assert names[1] == list(reversed(names[0]))
    assert first["sort"][1] == "ascending" and first["note"] == "Sorted by Name, A to Z."


def test_empty_cells_stay_last_in_both_directions(chrome, edges, tmp_path):
    got = _sort_run(chrome, edges, tmp_path, [5])
    assert got["heads"][2] == "Last 13 week"
    assert got["heads"][4] == "Weekday average (12 weeks)"
    assert got["heads"][5] == "Change from weekday average"
    for step in got["runs"]["5"][1:3]:
        values = [_number(r[5]) for r in step["cells"]]
        filled = [v for v in values if v is not None]
        assert None in values and filled
        assert values == filled + [None] * (len(values) - len(filled))


# Reads the metric chart, moves its readout with the keyboard, and picks another metric.
CHART_PROBE = """
function chart(id) {
  var host = document.getElementById(id);
  var svg = host.querySelector("svg");
  if (!svg) return { empty: host.textContent };
  return {
    paths: [].map.call(host.querySelectorAll("path"), function (p) { return p.getAttribute("class"); }),
    readout: host.querySelector(".chart-readout").textContent,
    legend: [].map.call(host.querySelectorAll(".legend li"), function (li) { return li.textContent; }),
    cross: svg.querySelector(".cross").getAttribute("visibility"),
    focusable: svg.getAttribute("tabindex")
  };
}
var svg = document.querySelector("#metric-chart svg");
var out = { first: chart("metric-chart"), pick: document.getElementById("metric-pick").value };
function key(name) { svg.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true })); }
key("ArrowLeft");
out.left = chart("metric-chart");
key("ArrowRight");
key("ArrowRight");
out.right = chart("metric-chart");
var pick = document.getElementById("metric-pick");
pick.value = PICK;
pick.dispatchEvent(new Event("change"));
out.picked = chart("metric-chart");
out.dimension = chart("dimension-chart");
return out;
"""


def _chart_run(chrome, store_rid, folder, pick):
    page = render_html(build_view(*store_rid))
    return run_probe(chrome, page, CHART_PROBE.replace("PICK", json.dumps(pick)), folder)


def test_the_chart_readout_follows_the_keyboard(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    got = _chart_run(chrome, showcase, tmp_path, "metrics/event2")
    first = got["first"]
    assert first["focusable"] == "0" and "actual" in first["paths"] and first["cross"] == "hidden"
    last = view["series"][got["pick"]][-1]
    assert first["readout"].startswith(f"Fri, Nov 6, 2026: {last[1]:,.0f}")
    assert got["left"]["readout"].startswith("Thu, Nov 5, 2026: ")
    assert got["left"]["cross"] == "visible"
    # The readout stops at the last day.
    assert got["right"]["readout"] == first["readout"]
    picked = view["series"]["metrics/event2"][-1]
    assert got["picked"]["readout"].startswith(f"Fri, Nov 6, 2026: {picked[1]:,.0f}")
    assert "actual" in got["dimension"]["paths"]
    assert got["dimension"]["readout"].startswith("Fri, Nov 6, 2026: ")


def test_the_chart_marks_a_flagged_day(chrome, edges, tmp_path):
    got = _chart_run(chrome, edges, tmp_path, "metrics/event11")
    picked = got["picked"]
    assert "mark-high" in picked["paths"] and "Flagged above range" in picked["legend"]
    assert picked["readout"].endswith("Above the expected range.")
    assert got["dimension"] == {"empty": "No dimension data was read."}


# Clicks a copy button with a stand-in clipboard and returns what was copied and the message.
COPY_PROBE = """
var copied = {};
function clipboard(write, writeText) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    write: function (items) {
      if (!write) return Promise.reject(new Error("no rich copy"));
      return Promise.all([items[0].getType("text/html"), items[0].getType("text/plain")])
        .then(function (b) { return Promise.all([b[0].text(), b[1].text()]); })
        .then(function (t) { copied.html = t[0]; copied.text = t[1]; });
    },
    writeText: function (text) {
      if (!writeText) return Promise.reject(new Error("no copy"));
      copied.plain = text;
      return Promise.resolve();
    }
  }});
}
// Waits until the page shows its message. It checks after each turn of the page's task
// queue, not after a set time, and gives up after 200 checks.
function shown(result, tries) {
  if (result.textContent || tries > 200) return Promise.resolve();
  return new Promise(function (r) { setTimeout(r, 0); }).then(function () {
    return shown(result, tries + 1);
  });
}
function press(write, writeText) {
  clipboard(write, writeText);
  copied = {};
  var result = document.getElementById(RESULT);
  result.textContent = "";
  document.getElementById(BUTTON).click();
  return shown(result, 0).then(function () {
    var fallback = document.querySelector(".copy-fallback");
    return { copied: copied, result: document.getElementById(RESULT).textContent,
      fallbackOpen: !!(fallback && fallback.open) };
  });
}
var out = {};
return press(true, true).then(function (r) { out.rich = r; return press(false, true); })
  .then(function (r) { out.plain = r; return press(false, false); })
  .then(function (r) {
    out.none = r;
    out.alertText = document.getElementById("alert-text").value;
    return out;
  });
"""


def _copy_run(chrome, store_rid, folder, button, result):
    page = render_html(build_view(*store_rid))
    probe = COPY_PROBE.replace("BUTTON", json.dumps(button)).replace("RESULT", json.dumps(result))
    return run_probe(chrome, page, probe, folder)


def test_copy_alerts_puts_html_and_text_on_the_clipboard(chrome, edges, tmp_path):
    got = _copy_run(chrome, edges, tmp_path, "copy-alerts", "copy-result")
    rich, plain, none = got["rich"], got["plain"], got["none"]
    assert rich["result"].startswith("Copied. An email program pastes it formatted")
    assert rich["copied"]["text"] == got["alertText"]
    assert "<table" in rich["copied"]["html"] and "metrics/event6" in rich["copied"]["html"]
    assert plain["result"].startswith("Copied as plain text.")
    assert plain["copied"]["plain"] == got["alertText"]
    assert none["result"].startswith("Could not copy.") and none["fallbackOpen"]


def test_copy_problems_lists_each_problem(chrome, with_problem, tmp_path):
    got = _copy_run(chrome, with_problem, tmp_path, "copy-problems", "copy-problems-result")
    text = got["rich"]["copied"]["text"]
    assert "Some metrics or dimensions are not visible to this credential." in text
    assert "metrics/event2" in text and "metrics/event2" in got["rich"]["copied"]["html"]
    assert got["none"]["result"].startswith("Could not copy.")


# For each theme button: the theme the page uses, the pressed button, the saved choice, and the
# text and background colors of the main kinds of text.
THEME_PROBE = """
var KINDS = ["body", ".muted", ".hint", "th", "td", ".tile-label", ".tile-note", ".tile-value",
  "button", ".group-count", ".chart-readout", ".empty", "summary", "code", "select", "label",
  ".badge-good", ".badge-flag", ".badge-zero", ".jump a", ".jump-count", ".jump-label",
  ".calendar-readout", ".calendar-list-title", "button.link", ".calendar-legend", "#find"];
var SVG_KINDS = ["#metric-chart text.axis", "#calendar text.axis"];
function background(n) {
  while (n) {
    var c = getComputedStyle(n).backgroundColor;
    if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") return c;
    n = n.parentElement;
  }
  return getComputedStyle(document.body).backgroundColor;
}
var out = {};
["light", "dark", "auto"].forEach(function (choice) {
  document.querySelector("[data-theme-choice=" + choice + "]").click();
  var colors = {};
  KINDS.forEach(function (k) {
    var n = document.querySelector(k);
    if (n) colors[k] = [getComputedStyle(n).color, background(n), background(document.body)];
  });
  // SVG text takes its color from fill, not color.
  SVG_KINDS.forEach(function (k) {
    var n = document.querySelector(k);
    if (n) colors[k] = [getComputedStyle(n).fill, background(n), background(document.body)];
  });
  out[choice] = {
    theme: document.documentElement.getAttribute("data-theme"),
    pressed: [].filter.call(document.querySelectorAll("[data-theme-choice]"), function (b) {
      return b.getAttribute("aria-pressed") === "true";
    }).map(function (b) { return b.getAttribute("data-theme-choice"); }),
    saved: localStorage.getItem("ahd-theme"),
    colors: colors
  };
});
return out;
"""


def _rgba(text):
    v = [float(x) for x in re.findall(r"[\d.]+", text)]
    return v[:3], v[3] if len(v) > 3 else 1.0


def _contrast(fg, bg, page):
    """The WCAG contrast ratio. A see-through background is mixed with the page color first."""
    (f, _), (b, alpha), (p, _) = _rgba(fg), _rgba(bg), _rgba(page)
    b = [b[i] * alpha + p[i] * (1 - alpha) for i in range(3)]

    def lum(c):
        ch = [x / 255 for x in c]
        ch = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

    hi, lo = sorted([lum(f), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_theme_buttons_and_text_contrast(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), THEME_PROBE, tmp_path)
    for choice in ("light", "dark"):
        t = got[choice]
        assert (t["theme"], t["pressed"], t["saved"]) == (choice, [choice], choice)
        assert len(t["colors"]) >= 15
        assert "#metric-chart text.axis" in t["colors"] and "#calendar text.axis" in t["colors"]
        low = {k: round(_contrast(*c), 2) for k, c in t["colors"].items() if _contrast(*c) < 4.5}
        assert low == {}, f"text below a contrast of 4.5 in the {choice} theme: {low}"
    assert got["auto"]["theme"] is None and got["auto"]["pressed"] == ["auto"]
    light = _rgba(got["light"]["colors"]["body"][1])[0]
    dark = _rgba(got["dark"]["colors"]["body"][1])[0]
    assert sum(dark) < sum(light) / 4


# Every control and table must have a name that a screen reader can read.
NAMES_PROBE = """
["tab-components", "tab-individual", "tab-alerts"].forEach(function (id) {
  document.getElementById(id).click();
});
function name(n) {
  var by = n.getAttribute("aria-labelledby");
  var text = (n.getAttribute("aria-label") || "") + (n.getAttribute("title") || "") +
    (by ? by.split(" ").map(function (id) {
      var t = document.getElementById(id);
      return t ? t.textContent : "";
    }).join(" ") : "") +
    (n.labels && n.labels.length ? n.labels[0].textContent : "") +
    (n.tagName === "BUTTON" ? n.textContent : "") +
    (n.tagName === "TABLE" && n.caption ? n.caption.textContent : "");
  return text.trim();
}
var all = document.querySelectorAll("input, select, textarea, button, table");
return { count: all.length, unnamed: [].filter.call(all, function (n) { return !name(n); })
  .map(function (n) { return n.tagName.toLowerCase() + (n.id ? "#" + n.id : ""); }) };
"""


@pytest.mark.parametrize("which", ["showcase", "edges", "with_problem"])
def test_every_control_and_table_has_a_name(chrome, request, tmp_path, which):
    page = render_html(build_view(*request.getfixturevalue(which)))
    got = run_probe(chrome, page, NAMES_PROBE, tmp_path)
    assert got["count"] > 10 and got["unnamed"] == []


DAY_LABEL_PROBE = """
return {
  day: document.getElementById("day").options[0].textContent,
  alertDay: document.getElementById("alert-day").options[0].textContent,
  slider: document.getElementById("change-alert-result").textContent
};
"""


@pytest.mark.parametrize(
    ("given", "label"), [(False, "(yesterday)"), (True, "(day before the run date)")]
)
def test_day_labels_follow_the_date_option(chrome, edges, tmp_path, given, label):
    view = build_view(*edges)
    view["run"]["date_given"] = given
    got = run_probe(chrome, render_html(view), DAY_LABEL_PROBE, tmp_path)
    assert got["day"] == "Fri, Nov 6, 2026 " + label
    assert got["alertDay"] == "Fri, Nov 6, 2026 " + label
    assert got["slider"].startswith("Fri, Nov 6 " + label + ":")
    other = "(day before the run date)" if not given else "(yesterday)"
    assert other not in got["day"] + got["alertDay"] + got["slider"]


# Opens every tab, the closed sections and the weekly view, and returns all the text a reader
# can see or hear: page text, the alert text, and the titles and labels of elements.
WORDS_PROBE = """
var parts = [];
function read() {
  parts.push(document.body.innerText);
  [].forEach.call(document.querySelectorAll("[title], [aria-label]"), function (n) {
    parts.push(n.getAttribute("title") || "", n.getAttribute("aria-label") || "");
  });
}
[].forEach.call(document.querySelectorAll("details"), function (d) { d.open = true; });
["tab-components", "tab-individual", "tab-alerts"].forEach(function (id) {
  document.getElementById(id).click();
  read();
});
document.getElementById("tab-components").click();
document.querySelector("#tables th button").click();
read();
var g = document.getElementById("granularity");
g.value = "week";
g.dispatchEvent(new Event("change"));
read();
parts.push(document.getElementById("alert-text").value);
return parts.join("\\n");
"""


@pytest.mark.parametrize("which", ["showcase", "edges"])
def test_the_page_says_weekday_average_not_usual(chrome, request, tmp_path, which):
    page = render_html(build_view(*request.getfixturevalue(which)))
    text = run_probe(chrome, page, WORDS_PROBE, tmp_path)
    assert "Weekday average (12 weeks)" in text and "Average week (12 weeks)" in text
    assert "Original order" in text and "How large drops and spikes are found" in text
    assert re.findall(r"\busual\b", text, re.IGNORECASE) == []


# ---- chart marks, days in a row, change bars, sticky headings and jump links ----------------

# Reads the chosen day's marks on a chart.
MARKS_JS = """
function marks(id) {
  var host = document.getElementById(id);
  var svg = host.querySelector("svg");
  function attr(sel, name) {
    var n = svg.querySelector(sel);
    return n ? Number(n.getAttribute(name)) : null;
  }
  return {
    line: attr("line.chosen", "x1"),
    dot: attr("circle.chosen-dot", "cx"),
    average: attr("line.average", "x1") === null ? null :
      (attr("line.average", "x1") + attr("line.average", "x2")) / 2,
    readout: host.querySelector(".chart-readout").textContent,
    legend: [].map.call(host.querySelectorAll(".legend li"), function (li) { return li.textContent; }),
    rows: [].map.call(host.querySelectorAll(".legend-row"), function (row) {
      return [].map.call(row.children, function (li) { return li.textContent; });
    }),
    markBoxes: [].map.call(host.querySelectorAll(".legend-row:nth-child(2) svg"), function (s) {
      return s.getAttribute("viewBox");
    }),
    label: svg.getAttribute("aria-label")
  };
}
function pickDay(i) {
  var d = document.getElementById("day");
  d.selectedIndex = i;
  d.dispatchEvent(new Event("change"));
}
"""

CHART_MARKS_PROBE = (
    MARKS_JS
    + """
var pick = document.getElementById("metric-pick");
pick.value = "metrics/event5";
pick.dispatchEvent(new Event("change"));
var out = { yesterday: marks("metric-chart") };
pickDay(2);
out.picked = marks("metric-chart");
out.dimension = marks("dimension-chart");
document.getElementById("tab-individual").click();
out.individual = marks("individual-chart");
document.getElementById("tab-components").click();
var g = document.getElementById("granularity");
g.value = "week";
g.dispatchEvent(new Event("change"));
out.week = marks("metric-chart");
return out;
"""
)


def test_the_charts_mark_the_chosen_day_and_its_weekday_average(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    got = run_probe(chrome, render_html(view), CHART_MARKS_PROBE, tmp_path)
    y, p = got["yesterday"], got["picked"]
    # The line, the dot and the average bar share the chosen day's x.
    for m in (y, p, got["dimension"], got["individual"]):
        assert m["line"] is not None and m["line"] == m["dot"] == pytest.approx(m["average"])
        assert {"Chosen day", "Weekday average"} <= set(m["legend"])
        # The key's top row ends with the chosen day and its weekday average.
        assert m["rows"][0][0] == "Actual"
        assert m["rows"][0][-2:] == ["Chosen day", "Weekday average"]
    # The top row holds the lines, the band and the chosen day. Adobe's flags get the second row,
    # each in a 12 by 12 box like the triangles on the chart.
    assert got["yesterday"]["rows"][0] == [
        "Actual",
        "Expected",
        "Expected range",
        "Chosen day",
        "Weekday average",
    ]
    assert len(got["yesterday"]["rows"]) == 2
    assert set(got["yesterday"]["rows"][1]) <= {"Flagged above range", "Flagged below range"}
    assert set(got["yesterday"]["markBoxes"]) == {"0 0 12 12"}
    # The Day picker moves the marks: Nov 4 is 2 days left of Nov 6.
    assert p["line"] < y["line"]
    assert y["label"].endswith(
        "A line marks the chosen day, Fri, Nov 6. Use the left and right "
        "arrow keys to read each day."
    )

    def average(cid, day):
        row = next(r for r in view["views"][f"day:{day}"] if r["component_id"] == cid)
        return f"{row['baseline']:,.0f}"

    assert p["readout"].startswith("Wed, Nov 4, 2026: ")
    assert f"weekday average {average('metrics/event5', '2026-11-04')}, expected " in p["readout"]
    assert f"weekday average {average('metrics/event5', '2026-11-06')}," in y["readout"]
    page = f"{view['pairs'][0]['weekday_average']['2026-11-04']:,.0f}"
    assert got["individual"]["readout"].startswith("Wed, Nov 4, 2026: ")
    assert f"weekday average {page}," in got["individual"]["readout"]
    # The weekly view marks no day. A band marks the week, and the readout gives the week's total
    # and the average week.
    week = got["week"]
    assert week["line"] is None and week["dot"] is None and week["average"] is None
    assert "Chosen day" not in week["legend"] and "Chosen week" in week["legend"]
    assert week["rows"][0] == ["Actual", "Expected", "Expected range", "Chosen week"]
    row = next(r for r in view["views"]["week"] if r["component_id"] == "metrics/event5")
    assert week["readout"] == (
        f"Oct 31 to Nov 6, 2026: {row['value']:,.0f} in total, average week {row['baseline']:,.0f}."
    )
    assert "A band marks the chosen week, Oct 31 to Nov 6, 2026." in week["label"]


@pytest.fixture(scope="module")
def streaks(tmp_path_factory):
    """Alerts that held for several days, each one ending yesterday."""
    store = Store.open(tmp_path_factory.mktemp("streaks") / "s.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "US/Pacific", "0.1.0")
    store.add_pair(rid, "variables/page", "metrics/occurrences")
    last = {
        # Drops of 50, 20 and 40 percent, oldest first.
        "metrics/event1": [50.0, 80.0, 60.0],
        # 0 on all 7 days that the page covers.
        "metrics/event2": [0.0] * 7,
    }
    comps, values = [], []
    for i, (cid, tail) in enumerate(last.items(), start=1):
        comps.append(Component(cid, f"E{i}", "metric", "custom_events", i, Column(cid, cid), True))
        first_low = YESTERDAY - timedelta(days=len(tail) - 1)
        values += [_dv(cid, d, 100.0) for d in _days() if d < first_low]
        values += [_dv(cid, first_low + timedelta(days=n), v) for n, v in enumerate(tail)]
    # Above Adobe's range on the last 2 days, after a normal day.
    cid = "metrics/event3"
    comps.append(Component(cid, "E3", "metric", "custom_events", 3, Column(cid, cid), True))
    values += [_dv(cid, d, 100.0) for d in _days() if d < YESTERDAY - timedelta(days=1)]
    values += [
        _dv(cid, YESTERDAY - timedelta(days=n), 300.0, 100.0, 130.0, 70.0, True) for n in (0, 1)
    ]
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    yield store, rid
    store.con.close()


# Reads the Days in a row column of each alert list and the alert text, for each slider percent.
STREAK_PROBE = """
function days(section) {
  var out = {};
  var table = document.querySelector("#alerts section[aria-labelledby=" + section + "] table");
  if (!table) return out;
  var heads = [].map.call(table.tHead.rows[0].cells, function (th) {
    return th.textContent.replace("↕", "");
  });
  var col = heads.indexOf("Days in a row");
  [].forEach.call(table.tBodies[0].rows, function (r) {
    out[r.querySelector("code").textContent] = r.cells[col].textContent;
  });
  return out;
}
var slider = document.getElementById("change-alert");
var out = {};
[30, 15, 10].forEach(function (p) {
  slider.value = String(-p);
  slider.dispatchEvent(new Event("input"));
  out[p] = { unusual: days("unusual-title"), drops: days("drops-title"),
    zero: days("zero-title"), text: document.getElementById("alert-text").value };
});
var day = document.getElementById("alert-day");
day.selectedIndex = 1;
day.dispatchEvent(new Event("change"));
out.before = { unusual: days("unusual-title"), drops: days("drops-title"),
  zero: days("zero-title") };
return out;
"""


def test_alert_lists_show_days_in_a_row(chrome, streaks, tmp_path):
    got = run_probe(chrome, render_html(build_view(*streaks)), STREAK_PROBE, tmp_path)
    # The drop of 40 percent passes 30 percent alone. At 15 percent all 3 days pass.
    assert got["30"]["drops"] == {"metrics/event1": "1"}
    assert got["15"]["drops"] == {"metrics/event1": "3"}
    assert got["10"]["drops"] == {"metrics/event1": "3"}
    # The zero reaches the first day of the report, so it may be longer.
    assert got["30"]["zero"] == {"metrics/event2": "7 or more"}
    assert got["30"]["unusual"] == {"metrics/event3": "2"}
    text = got["30"]["text"]
    assert re.search(
        r"^- E1 \(metrics/event1\): .*\. First day with this alert\.$", text, re.MULTILINE
    )
    assert re.search(
        r"^- E2 \(metrics/event2\): .*\. 7 or more days in a row\.$", text, re.MULTILINE
    )
    assert re.search(r"^- E3 \(metrics/event3\): .*\. 2 days in a row\.$", text, re.MULTILINE)
    assert "- E1 (metrics/event1): " in got["15"]["text"]
    assert re.search(r"^- E1 .*\. 3 days in a row\.$", got["15"]["text"], re.MULTILINE)
    # The day before counts back from its own day. The slider is still at 10 percent, so its
    # drop of 20 percent and the drop of 50 percent the day before it both pass.
    assert got["before"] == {
        "unusual": {"metrics/event3": "1"},
        "drops": {"metrics/event1": "2"},
        "zero": {"metrics/event2": "6 or more"},
    }


def test_copied_alerts_have_days_in_a_row(chrome, streaks, tmp_path):
    got = _copy_run(chrome, streaks, tmp_path, "copy-alerts", "copy-result")
    html = got["rich"]["copied"]["html"]
    assert html.count(">Days in a row</th>") == 3
    assert ">7 or more</td>" in html and ">2</td>" in html


# Reads the change bar of each row in the first components table, then moves the slider.
BAR_PROBE = """
function bars() {
  var out = {};
  [].forEach.call(document.querySelectorAll("#tables table"), function (table) {
    var heads = [].map.call(table.tHead.rows[0].cells, function (th) { return th.textContent; });
    var col = heads.indexOf("Change from weekday average↕");
    [].forEach.call(table.tBodies[0].rows, function (r) {
      var cell = r.cells[col], fill = cell.querySelector(".fill");
      if (!fill) return;
      out[r.cells[1].firstChild.textContent] = {
        text: cell.textContent,
        side: fill.classList.contains("neg") ? "left" : "right",
        over: fill.classList.contains("over"),
        width: fill.style.width,
        ticks: [].map.call(cell.querySelectorAll(".tick"), function (t) { return t.style.left; }),
        hidden: cell.querySelector(".devbar").getAttribute("aria-hidden")
      };
    });
  });
  return out;
}
var out = { "30": bars() };
var slider = document.getElementById("change-alert");
slider.value = "-60";
slider.dispatchEvent(new Event("input"));
out["60"] = bars();
var spike = document.getElementById("spike-alert");
spike.value = "30";
spike.dispatchEvent(new Event("input"));
out.spike30 = bars();
return out;
"""


def test_the_change_column_has_a_bar_with_ticks_at_the_slider(chrome, edges, tmp_path):
    got = run_probe(chrome, render_html(build_view(*edges)), BAR_PROBE, tmp_path)
    p30, p60 = got["30"], got["60"]
    drop = p30["Drop of 50 percent"]
    assert drop == {
        "text": "▼ -50%",
        "side": "left",
        "over": True,
        "width": "25%",
        # The drop slider at -30% and the spike slider at +50%.
        "ticks": ["35%", "75%"],
        "hidden": "true",
    }
    assert p60["Drop of 50 percent"]["over"] is False
    assert p60["Drop of 50 percent"]["ticks"] == ["20%", "75%"]
    assert p30["Drop of 15 percent"]["over"] is False
    # A rise of 40 percent: right of the center, short of the spike tick at +50%. With the spike
    # slider at +30% it is past the tick, and the tick moves to 65%.
    up = p30["Above the expected range"]
    assert up["side"] == "right" and up["width"] == "20%" and up["over"] is False
    up30 = got["spike30"]["Above the expected range"]
    assert up30["over"] is True and up30["ticks"] == ["20%", "65%"]
    assert up30["text"] == "▲ +40%"


# Scrolls the page, or the table's own box on a narrow screen, to the middle of the longest
# table, and reads where its headings are.
STICKY_PROBE = """
var tables = [].slice.call(document.querySelectorAll("#tables table"));
var table = tables.sort(function (a, b) { return b.rows.length - a.rows.length; })[0];
var wrap = table.closest(".table-wrap");
var th = table.tHead.rows[0].cells[0];
var first = table.tBodies[0].rows[0];
var row = table.tBodies[0].rows[Math.floor(table.tBodies[0].rows.length / 2)];
var box = getComputedStyle(wrap).overflowY !== "visible";
if (box) {
  wrap.scrollIntoView();
  wrap.scrollTop = row.offsetTop;
} else {
  window.scrollTo(0, row.getBoundingClientRect().top + window.scrollY);
}
return {
  box: box,
  heading: th.getBoundingClientRect().top,
  top: box ? wrap.getBoundingClientRect().top : 0,
  firstTop: first.getBoundingClientRect().top,
  scrolled: box ? wrap.scrollTop : window.scrollY
};
"""


@pytest.mark.parametrize("width", [375, 1280])
def test_table_headings_stay_in_view(chrome, showcase, tmp_path, width):
    page = render_html(build_view(*showcase))
    got = run_probe(chrome, page, STICKY_PROBE, tmp_path, width=width)
    assert got["box"] is (width < 960)
    assert got["scrolled"] > 0
    # The heading sits at the top of the window or of the box, over the rows that scrolled.
    assert got["heading"] == pytest.approx(got["top"] + (1 if got["box"] else 0), abs=1.5)
    # The first row scrolled up under the heading.
    assert got["firstTop"] < got["heading"]


JUMP_PROBE = """
var links = [].map.call(document.querySelectorAll("#tables nav.jump a"), function (a) {
  var target = document.querySelector(a.getAttribute("href"));
  return { text: a.textContent, href: a.getAttribute("href"),
    target: target ? target.tagName + ":" + target.firstChild.textContent : null };
});
var nav = document.querySelector("#tables nav.jump");
// The second group has enough page below it to scroll to the top of the window.
var second = document.querySelectorAll("#tables nav.jump a")[1];
second.click();
var target = document.querySelector(second.getAttribute("href"));
var out = { label: nav.getAttribute("aria-label"), links: links, hash: location.hash,
  top: target.getBoundingClientRect().top };
document.getElementById("flagged-only").click();
out.flagged = [].map.call(document.querySelectorAll("#tables nav.jump a"), function (a) {
  return a.textContent;
});
return out;
"""


def test_jump_links_go_to_each_group(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), JUMP_PROBE, tmp_path)
    assert got["label"] == "Jump to a group"
    assert [link["text"] for link in got["links"]] == [
        "Custom events 24",
        "Custom eVars 16",
        "Custom props 8",
        "Reserved Props 1",
    ]
    for link in got["links"]:
        assert link["target"] == "H3:" + link["text"].rsplit(" ", 1)[0]
    assert got["hash"] == got["links"][1]["href"] and 0 <= got["top"] < 20
    # The counts follow the "Show only flagged" filter.
    assert got["flagged"][0] == "Custom events 3"


# ---- alerts by day -----------------------------------------------------------------------

CALENDAR_JS = """
function cal() {
  var host = document.getElementById("calendar");
  var cells = {};
  [].forEach.call(host.querySelectorAll("rect.cal-cell"), function (r) {
    cells[r.getAttribute("data-day")] = r.getAttribute("class").replace("cal-cell ", "");
  });
  var ring = host.querySelector("rect.cal-ring");
  var picked = null;
  [].forEach.call(host.querySelectorAll("rect.cal-cell"), function (r) {
    if (r.getAttribute("x") - 2 == ring.getAttribute("x") &&
        r.getAttribute("y") - 2 == ring.getAttribute("y")) picked = r.getAttribute("data-day");
  });
  return {
    cells: cells, picked: picked,
    weekdays: [].map.call(host.querySelectorAll('text.axis[text-anchor="end"]'), function (t) {
      return t.textContent;
    }),
    readout: host.querySelector(".calendar-readout").textContent,
    lists: [].map.call(host.querySelectorAll(".calendar-list"), function (b) {
      return [b.querySelector(".calendar-list-title").textContent].concat(
        [].map.call(b.querySelectorAll("button"), function (x) { return x.textContent; }));
    }),
    more: [].map.call(host.querySelectorAll(".calendar-list summary"), function (s) {
      return s.textContent;
    }),
    top: [].map.call(host.querySelectorAll(".calendar-top tbody tr"), function (tr) {
      return [].map.call(tr.cells, function (c) { return c.textContent; });
    })
  };
}
function key(name) {
  document.querySelector("#calendar svg").dispatchEvent(
    new KeyboardEvent("keydown", { key: name, bubbles: true }));
}
function click(day) {
  document.querySelector('#calendar rect[data-day="' + day + '"]')
    .dispatchEvent(new MouseEvent("click", { bubbles: true }));
}
"""

CALENDAR_PROBE = (
    CALENDAR_JS
    + """
var out = { first: cal(), label: document.querySelector("#calendar svg").getAttribute("aria-label") };
key("ArrowLeft");
out.left = cal().picked;
key("ArrowUp");
out.up = cal().picked;
click("2026-10-15");
out.release = cal();
document.querySelector("#calendar .calendar-list button").click();
out.pick = document.getElementById("dimension-pick").value;
out.chart = document.querySelector("#dimension-chart .chart-readout").textContent;
out.focused = document.activeElement === document.querySelector("#dimension-chart svg");
out.cross = document.querySelector("#dimension-chart .cross").getAttribute("visibility");
var d = document.getElementById("day");
d.selectedIndex = 2;
d.dispatchEvent(new Event("change"));
out.daypicker = cal();
// The new day ends the calendar's day on the chart.
out.chartAfter = document.querySelector("#dimension-chart .chart-readout").textContent;
click("2026-10-15");
document.querySelector("#calendar .calendar-list button").click();
out.chartLook = document.querySelector("#dimension-chart .chart-readout").textContent;
var pick = document.getElementById("dimension-pick");
pick.dispatchEvent(new Event("change"));
out.chartPicked = document.querySelector("#dimension-chart .chart-readout").textContent;
d.selectedIndex = 0;
d.dispatchEvent(new Event("change"));
document.querySelector("#calendar .calendar-top tbody tr button").click();
out.topclick = cal().picked;
return out;
"""
)


def test_the_calendar_shows_alerts_by_day(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), CALENDAR_PROBE, tmp_path)
    first = got["first"]
    assert len(first["cells"]) == 91
    # The most alerts in a day is 6, so 6 and 5 are the darkest, 2 is the second, 1 the first.
    assert first["cells"]["2026-10-15"] == "l4" and first["cells"]["2026-11-06"] == "l4"
    assert first["cells"]["2026-09-21"] == "l2" and first["cells"]["2026-11-04"] == "l1"
    assert first["cells"]["2026-10-01"] == "l0"
    assert first["picked"] == "2026-11-06"
    assert first["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert first["readout"] == "Fri, Nov 6, 2026: 5 components with an alert."
    assert [lst[0] for lst in first["lists"]] == [
        "Below the expected range (3)",
        "Above the expected range (2)",
    ]
    assert got["label"].startswith("Alerts by day for the 13 weeks ending Fri, Nov 6.")
    # Left and right move a week. Up and down move a day.
    assert got["left"] == "2026-10-30" and got["up"] == "2026-10-29"
    release = got["release"]
    assert release["picked"] == "2026-10-15"
    assert len(release["lists"]) == 1 and release["lists"][0][0] == "Below the expected range (6)"
    assert [b.split(":")[0] for b in release["lists"][0][1:]] == [f"eVar{n}" for n in range(5, 11)]
    # A component in the list opens on its chart at that day.
    assert got["pick"] == "metrics/evar5instances" and got["focused"] and got["cross"] == "visible"
    assert got["chart"].startswith("Thu, Oct 15, 2026: ")
    assert got["chart"].endswith("Below the expected range.")
    # The Day picker moves the calendar too.
    assert got["daypicker"]["picked"] == "2026-11-04"
    assert got["daypicker"]["readout"] == "Wed, Nov 4, 2026: 1 component with an alert."
    assert got["chartAfter"].startswith("Wed, Nov 4, 2026: ")
    # Picking a component again also ends it.
    assert got["chartLook"].startswith("Thu, Oct 15, 2026: ")
    assert got["chartPicked"].startswith("Wed, Nov 4, 2026: ")
    # The days with the most alerts: most first, then the newest.
    assert [row[0] for row in first["top"]] == [
        "Fri, Oct 16",
        "Thu, Oct 15",
        "Fri, Nov 6",
        "Tue, Sep 22",
        "Mon, Sep 21",
    ]
    assert first["top"][2] == ["Fri, Nov 6", "3", "2", "0", "5"]
    assert got["topclick"] == "2026-10-16"


def test_the_calendar_marks_missing_days_and_folds_long_lists(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    view["calendar"] = [c for c in view["calendar"] if c["day"] != "2026-10-01"]
    last = view["calendar"][-1]
    ids = [r["component_id"] for r in view["views"]["week"]][:15]
    last["zero"], last["alerts"] = ids, last["alerts"] + len(ids)
    got = run_probe(chrome, render_html(view), CALENDAR_JS + "return cal();", tmp_path)
    assert got["cells"]["2026-10-01"] == "missing"
    zero = next(lst for lst in got["lists"] if lst[0].startswith("Dropped to zero"))
    assert zero[0] == "Dropped to zero (15)" and len(zero) == 16
    assert got["more"] == ["Show 3 more"]


def test_a_calendar_without_alerts_says_so(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    for c in view["calendar"]:
        c["high"], c["low"], c["zero"], c["alerts"] = [], [], [], 0
    got = run_probe(chrome, render_html(view), CALENDAR_JS + "return cal();", tmp_path)
    assert set(got["cells"].values()) == {"l0"} and got["top"] == []
    assert got["lists"] == []
    assert got["readout"] == "Fri, Nov 6, 2026: 0 components with an alert."


# ---- 13-week trend lines and the find box ------------------------------------------------

SPARK_PROBE = """
function sparks() {
  var out = {};
  [].forEach.call(document.querySelectorAll("#tables tbody tr"), function (tr) {
    var b = tr.querySelector("button.spark");
    if (!b) return;
    var path = b.querySelector("path").getAttribute("d");
    var dot = b.querySelector("circle");
    var points = path.match(/[ML][^ML]+/g);
    out[b.getAttribute("aria-label")] = {
      moves: (path.match(/M/g) || []).length,
      first: points[0], last: points[points.length - 1],
      dot: dot ? [Number(dot.getAttribute("cx")), Number(dot.getAttribute("cy"))] : null
    };
  });
  return out;
}
var out = { day: sparks() };
var d = document.getElementById("day");
d.selectedIndex = 2;
d.dispatchEvent(new Event("change"));
out.picked = sparks();
[].filter.call(document.querySelectorAll("#tables button.spark"), function (b) {
  return b.getAttribute("aria-label") === "Show eVar6: Page Type on the chart";
})[0].click();
out.pick = document.getElementById("dimension-pick").value;
out.readout = document.querySelector("#dimension-chart .chart-readout").textContent;
out.focused = document.activeElement === document.querySelector("#dimension-chart svg");
var g = document.getElementById("granularity");
g.value = "week";
g.dispatchEvent(new Event("change"));
out.week = sparks();
return out;
"""


def test_each_row_has_a_trend_line_that_opens_the_chart(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), SPARK_PROBE, tmp_path)
    day = got["day"]
    assert len(day) == 49
    orders = day["Show event5: Orders on the chart"]
    # 91 days from left to right, with no gaps, and a dot on the chosen day, the last one.
    assert orders["moves"] == 1 and orders["first"].startswith("M2 ")
    assert orders["last"].startswith("L86 ") and orders["dot"][0] == 86
    # A value of 0 sits on the bottom of the line's box.
    crash = day["Show event18: App Crash Reports on the chart"]
    assert crash["last"] == "L86 19" and crash["dot"] == [86, 19]
    # The dot follows the Day picker: Nov 4 is 2 of 90 steps left of Nov 6.
    assert got["picked"]["Show event5: Orders on the chart"]["dot"][0] == pytest.approx(
        86 - 2 * 84 / 90, abs=0.1
    )
    assert got["pick"] == "metrics/evar6instances" and got["focused"]
    assert got["readout"].startswith("Wed, Nov 4, 2026: ")
    # The weekly view marks no day.
    assert all(s["dot"] is None for s in got["week"].values())


FIND_PROBE = """
function find(text, flagged) {
  var f = document.getElementById("find");
  f.value = text;
  f.dispatchEvent(new Event("input"));
  var check = document.getElementById("flagged-only");
  if (check.checked !== flagged) check.click();
  return {
    rows: [].map.call(document.querySelectorAll("#tables tbody tr"), function (tr) {
      return tr.querySelector("td.name").firstChild.textContent;
    }),
    empty: [].map.call(document.querySelectorAll("#tables .empty"), function (p) {
      return p.textContent;
    }),
    jump: [].map.call(document.querySelectorAll("#tables nav.jump a"), function (a) {
      return a.textContent;
    })
  };
}
return {
  code: find("code", false), number: find("EVAR5", false), id: find("variables/page", false),
  flagged: find("code", true), none: find("zzz", false), cleared: find("", false)
};
"""


def test_the_find_box_filters_every_table(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), FIND_PROBE, tmp_path)
    assert got["code"]["rows"] == ["Campaign Code", "Promo Code"]
    assert got["code"]["jump"] == ["Custom events 0", "Custom eVars 2", "Custom props 0"]
    assert got["code"]["empty"] == [
        "No component in this group matches “code”.",
        "No component in this group matches “code”.",
        "No solution component matches “code”.",
    ]
    # The number and the id match too, in any case.
    assert got["number"]["rows"] == ["Customer Type"]
    assert got["id"]["rows"] == ["Page"]
    assert got["flagged"]["rows"] == ["Campaign Code"]
    assert got["flagged"]["empty"][0] == (
        "No component in this group matches “code” among the flagged ones."
    )
    assert got["none"]["rows"] == []
    assert len(got["cleared"]["rows"]) == 49


# ---- earlier runs ------------------------------------------------------------------------

HISTORY_PROBE = """
function read() {
  var host = document.getElementById("history");
  return {
    rows: [].map.call(host.querySelectorAll("tbody tr"), function (tr) {
      return [].map.call(tr.cells, function (c) { return c.textContent; });
    }),
    bars: [].map.call(host.querySelectorAll(".runbar span"), function (s) { return s.style.width; }),
    empty: (host.querySelector(".empty") || {}).textContent || null
  };
}
document.getElementById("tab-alerts").click();
var out = { "30": read() };
var slider = document.getElementById("change-alert");
slider.value = "-10";
slider.dispatchEvent(new Event("input"));
out["10"] = read();
var wrap = document.querySelector("#history .table-wrap");
out.boxed = wrap ? wrap.classList.contains("scrolls") : null;
return out;
"""


def test_earlier_runs_follow_the_slider(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    got = run_probe(chrome, render_html(view), HISTORY_PROBE, tmp_path)
    rows = got["30"]["rows"]
    assert [r[0] for r in rows] == ["Run 7 (this run)"] + [f"Run {n}" for n in range(6, 0, -1)]
    assert [r[1] for r in rows[:2]] == ["Fri, Nov 6, 2026", "Thu, Nov 5, 2026"]
    # This run's row is the same as the tiles: 49 checked, 2 above, 3 below, no large drop or
    # spike, 5 in all.
    assert rows[0][2:] == ["49", "2", "3", "0", "0", "0", "5", "0"]
    assert got["30"]["bars"][:3] == ["100%", "20%", "20%"]
    # At 10 percent, the run for Nov 5 has 3 large drops. One is Video Completes, which falls
    # a little each week.
    nov5 = view["history"][1]
    assert len(nov5["drops"]) == 3 and all(-0.3 < p < -0.1 for p in nov5["drops"])
    assert got["10"]["rows"][1][5] == "3" and got["10"]["rows"][1][8] == "4"
    assert got["30"]["empty"] is None
    assert got["boxed"] is False


def test_a_wide_earlier_runs_table_gets_its_own_box(chrome, showcase, tmp_path):
    # At 700 pixels the table of 9 columns is too wide, so it scrolls in its own box.
    page = render_html(build_view(*showcase))
    got = run_probe(chrome, page, HISTORY_PROBE, tmp_path, width=700)
    assert got["30"]["rows"] and got["boxed"] is True


def test_a_single_run_says_it_is_the_only_one(chrome, edges, tmp_path):
    got = run_probe(chrome, render_html(build_view(*edges)), HISTORY_PROBE, tmp_path)
    assert got["30"]["rows"] == []
    assert got["30"]["empty"] == (
        "This is the only run of this report suite in the database. Later runs will show here."
    )


# ---- slow declines ---------------------------------------------------------------------

DECLINE_PROBE = """
document.getElementById("tab-alerts").click();
var host = document.getElementById("declines");
return {
  hints: [].map.call(document.querySelectorAll(".declines .hint"), function (h) {
    return h.textContent;
  }),
  rows: [].map.call(host.querySelectorAll("tbody tr"), function (tr) {
    return [].map.call(tr.cells, function (c) { return c.textContent; });
  }),
  bars: [].map.call(host.querySelectorAll("rect.week-bar"), function (r) {
    return Number(r.getAttribute("height"));
  }),
  label: (host.querySelector("svg") || { getAttribute: function () { return null; } })
    .getAttribute("aria-label"),
  empty: (host.querySelector(".empty") || {}).textContent || null
};
"""


def test_slow_declines_list_the_falling_metric(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    got = run_probe(chrome, render_html(view), DECLINE_PROBE, tmp_path)
    assert got["hints"][0].startswith(
        "Components whose weekly totals fell by 3 percent or more a week for the last 12 weeks."
    )
    assert "below 0.7." in got["hints"][1]
    # Video Completes falls about 4 percent a week in the showcase.
    assert got["rows"] == [["event12: Video Completes", "", "-3.4%", "-29.4%", "0.95"]]
    # One bar per week, oldest first. The tallest is 20 pixels high.
    totals = view["declines"][0]["totals"]
    assert len(got["bars"]) == 12 and max(got["bars"]) == 20
    assert got["bars"][0] > got["bars"][-1]
    assert got["label"] == "Weekly totals, oldest first: " + ", ".join(f"{t:,.0f}" for t in totals)
    assert got["empty"] is None


def test_no_slow_declines_says_so(chrome, edges, tmp_path):
    got = run_probe(chrome, render_html(build_view(*edges)), DECLINE_PROBE, tmp_path)
    assert got["rows"] == [] and got["bars"] == []
    assert got["empty"] == "No component fell steadily in the last 12 weeks."


# ---- the weekly view -----------------------------------------------------------------------

WEEK_PROBE = """
function flags() {
  var out = {};
  [].forEach.call(document.querySelectorAll("#tables table"), function (table) {
    var heads = [].map.call(table.tHead.rows[0].cells, function (th) {
      return th.textContent.replace(/[↕▲▼]/g, "").trim();
    });
    var col = heads.indexOf("Flag"), name = heads.indexOf("Name");
    [].forEach.call(table.tBodies[0].rows, function (r) {
      var t = r.cells[col].textContent;
      if (t) out[r.cells[name].firstChild.textContent] = t;
    });
  });
  return out;
}
function cal() {
  var host = document.getElementById("calendar");
  return {
    outlined: [].map.call(host.querySelectorAll("rect.cal-week"), function (r) { return 1; }).length,
    ring: host.querySelector("rect.cal-ring").getAttribute("visibility"),
    readout: host.querySelector(".calendar-readout").textContent,
    lists: [].map.call(host.querySelectorAll(".calendar-list-title"), function (t) { return t.textContent; })
  };
}
var gran = document.getElementById("granularity");
gran.value = "week";
gran.dispatchEvent(new Event("change"));
var week = document.getElementById("week");
var out = {
  dayHidden: document.getElementById("day-label").hidden,
  weekHidden: document.getElementById("week-label").hidden,
  weekText: week.options[0].textContent, weekDisabled: week.disabled,
  flags: flags(),
  sparkWeek: (document.querySelector("#tables path.spark-week") || { getAttribute: function () { return ""; } })
    .getAttribute("d"),
  cal: cal()
};
var only = document.getElementById("flagged-only");
only.checked = true;
only.dispatchEvent(new Event("change"));
out.flaggedNames = Object.keys(flags()).sort();
out.flaggedRows = document.querySelectorAll("#tables tbody tr").length;
only.checked = false;
only.dispatchEvent(new Event("change"));
var cell = document.querySelector('#calendar rect[data-day="2026-11-04"]');
cell.dispatchEvent(new MouseEvent("click", { bubbles: true }));
out.picked = cal();
gran.value = "day";
gran.dispatchEvent(new Event("change"));
out.daily = cal();
out.dailyWeekHidden = document.getElementById("week-label").hidden;
return out;
"""


def test_the_weekly_view_matches_the_daily_one(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    got = run_probe(chrome, render_html(view), WEEK_PROBE, tmp_path)
    # The Week box takes the Day picker's place, and names the one week.
    assert got["dayHidden"] is True and got["weekHidden"] is False
    assert got["weekText"] == "Oct 31 to Nov 6, 2026" and got["weekDisabled"] is True
    # Adobe's flags count by day over the week, as in the daily view's Flag column.
    assert got["flags"]["Cart Adds"] == "▲Above range on 1 day"
    assert got["flags"]["Orders"] == "▼Below range on 1 day"
    # "Show only flagged" keeps them, and only flagged rows.
    assert {"Cart Adds", "Orders"} <= set(got["flaggedNames"])
    assert got["flaggedRows"] == len(got["flaggedNames"])
    # The 13-week line draws the week's 7 days again.
    assert got["sparkWeek"].count("M") == 1 and got["sparkWeek"].count("L") == 6
    # The calendar outlines the 7 days and sums up the week.
    days = [d for d in view["calendar"] if "2026-10-31" <= d["day"] <= "2026-11-06"]
    distinct = {c for d in days for k in ("high", "low", "zero") for c in d[k]}
    assert got["cal"]["outlined"] == 7 and got["cal"]["ring"] == "hidden"
    assert got["cal"]["readout"] == (
        f"Oct 31 to Nov 6, 2026: {len(distinct)} components had an alert on at least one day."
    )
    assert got["cal"]["lists"][0].startswith("Below the expected range")
    # Choosing a day shows that day. The outline stays.
    assert got["picked"]["ring"] == "visible" and got["picked"]["outlined"] == 7
    assert got["picked"]["readout"].startswith("Wed, Nov 4, 2026: ")
    # Back in the daily view there is no outline, and the Week box is hidden.
    assert got["daily"]["outlined"] == 0 and got["daily"]["ring"] == "visible"
    assert got["dailyWeekHidden"] is True


# ---- the Custom components filter ----------------------------------------------------------

RANGE_PROBE = """
var sel = document.getElementById("range");
function read() {
  var host = document.getElementById("tables");
  return {
    note: (host.querySelector(".filter-note") || {}).textContent || null,
    groups: [].map.call(host.querySelectorAll("h3.group-title"), function (h) {
      return h.firstChild.textContent;
    }),
    headings: [].map.call(host.querySelectorAll("h2"), function (h) { return h.textContent; }),
    numbers: [].map.call(host.querySelectorAll("tbody tr"), function (tr) {
      return tr.cells[0].textContent;
    }),
    jump: [].map.call(host.querySelectorAll(".jump a"), function (a) { return a.firstChild.textContent; })
  };
}
function choose(v) {
  sel.value = v;
  sel.dispatchEvent(new Event("change"));
  return read();
}
var out = {
  label: sel.parentNode.firstChild.textContent.trim(),
  options: [].map.call(sel.options, function (o) {
    return [o.parentNode.tagName === "OPTGROUP" ? o.parentNode.label : "", o.value, o.textContent];
  }),
  all: read()
};
out.evars = choose("custom_evars");
out.middle = choose("custom_evars:101-200");
out.back = choose("");
return out;
"""


def test_the_custom_components_filter_lists_types_and_ranges(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    # Spread the 16 eVars over 15 to 240, so they fall in three ranges of 100.
    for rows in view["views"].values():
        for r in rows:
            if r["group"] == "custom_evars":
                r["number"] = r["number"] * 15
    got = run_probe(chrome, render_html(view), RANGE_PROBE, tmp_path)
    assert got["label"] == "Custom components"
    assert got["options"] == [
        ["", "", "All"],
        ["Custom events", "custom_events", "All custom events (24)"],
        ["Custom eVars", "custom_evars", "All custom eVars (16)"],
        ["Custom eVars", "custom_evars:1-100", "eVar1 to eVar100 (6)"],
        ["Custom eVars", "custom_evars:101-200", "eVar101 to eVar200 (7)"],
        ["Custom eVars", "custom_evars:201-240", "eVar201 to eVar240 (3)"],
        ["Custom props", "custom_props", "All custom props (8)"],
    ]
    assert got["all"]["note"] is None and "Solution components" in got["all"]["headings"]
    evars = got["evars"]
    assert evars["groups"] == ["Custom eVars"] and evars["jump"] == ["Custom eVars"]
    assert "Solution components" not in evars["headings"]
    assert evars["note"] == (
        "Showing all custom eVars only. Choose All under Custom components to see every component."
    )
    middle = got["middle"]
    assert middle["numbers"] == [f"eVar{n * 15}" for n in range(7, 14)]
    assert middle["note"].startswith("Showing eVar101 to eVar200 only.")
    assert got["back"] == got["all"]


# ---- sections the reader can fold ---------------------------------------------------------

FOLD_PROBE = """
var folds = [].slice.call(document.querySelectorAll("details.fold"));
var charts = document.querySelector(".charts-fold");
var out = {
  titles: folds.map(function (d) { return d.querySelector("summary").textContent.trim(); }),
  open: folds.map(function (d) { return d.open; })
};
function width() {
  return Number(document.querySelector("#metric-chart svg").getAttribute("viewBox").split(" ")[2]);
}
// Waits for the page's own toggle handler, which runs first because it listens in the capture
// phase.
function toggled(d) {
  return new Promise(function (done) { d.addEventListener("toggle", done, { once: true }); });
}
out.before = width();
charts.open = false;
return toggled(charts).then(function () {
  // The Day picker draws the charts again while they are folded.
  var day = document.getElementById("day");
  day.selectedIndex = 2;
  day.dispatchEvent(new Event("change"));
  out.folded = width();
  var drawn = document.querySelector("#metric-chart svg");
  charts.open = true;
  return toggled(charts).then(function () { return drawn; });
}).then(function (drawn) {
  out.after = width();
  out.redrawn = document.querySelector("#metric-chart svg") !== drawn;
  charts.open = false;
  return toggled(charts);
}).then(function () {
  // A component chosen in the calendar opens the charts again.
  var cell = document.querySelector('#calendar rect[data-day="2026-10-15"]');
  cell.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  var b = document.querySelector("#calendar .calendar-list button");
  out.picked = b.textContent;
  b.click();
  out.reopened = charts.open;
  var dim = document.getElementById("dimension-pick");
  out.shown = dim.options[dim.selectedIndex].textContent;
  // Every section opens to print, and the folded ones fold again after.
  folds[0].open = false;
  folds[5].open = false;
  window.dispatchEvent(new Event("beforeprint"));
  out.printing = folds.map(function (d) { return d.open; });
  window.dispatchEvent(new Event("afterprint"));
  out.printed = folds.map(function (d) { return d.open; });
  out.back = [].map.call(document.querySelectorAll("#tables .back-top a"), function (a) {
    return a.getAttribute("href");
  });
  out.tables = document.querySelectorAll("#tables table").length;
  out.jump = document.getElementById("jump") ? document.getElementById("jump").tagName : null;
  return out;
});
"""


FOCUS_PROBE = """
var charts = document.querySelector(".charts-fold");
function toggled(d) {
  return new Promise(function (done) { d.addEventListener("toggle", done, { once: true }); });
}
function where() {
  var a = document.activeElement;
  var host = a && a.closest ? a.closest(".chart-host, [id$=-chart]") : null;
  return { tag: a ? a.tagName.toLowerCase() : null, chart: host ? host.id : null };
}
var out = {};
charts.open = false;
return toggled(charts).then(function () {
  var wait = toggled(charts);
  document.querySelector("#tables button.spark").click();
  out.clicked = where();
  return wait;
}).then(function () {
  // The toggle event comes after the click. The chart must keep the focus after it.
  out.after = where();
  out.width = Number(document.querySelector("#metric-chart svg").getAttribute("viewBox")
    .split(" ")[2]);
  return out;
});
"""


def test_a_chart_opened_from_a_folded_section_keeps_the_focus(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), FOCUS_PROBE, tmp_path)
    assert got["clicked"]["tag"] == "svg" and got["clicked"]["chart"]
    assert got["after"] == got["clicked"]
    assert got["width"] > 300


def test_sections_fold_and_open_again(chrome, showcase, tmp_path):
    got = run_probe(chrome, render_html(build_view(*showcase)), FOLD_PROBE, tmp_path)
    assert got["titles"] == [
        "Alerts by day, last 13 weeks",
        "Charts",
        "Top 100 values yesterday",
        "Browser analysis",
        "Device analysis",
        "Slow declines",
        "Earlier runs",
    ]
    # Every section starts open.
    assert got["open"] == [True] * 7
    # Chrome lays out a folded section, so a chart drawn there keeps its width. A browser that
    # does not gets 0 and falls back to 640 pixels, so the page draws the charts again when
    # their section opens.
    assert got["folded"] == got["after"] == got["before"] == 550
    assert got["redrawn"] is True
    assert got["reopened"] is True and got["shown"] == got["picked"]
    assert got["printing"] == [True] * 7
    assert got["printed"] == [False, True, True, True, True, False, True]
    # One link back to the jump links after each table.
    assert got["back"] == ["#jump"] * got["tables"] and got["tables"] == 4
    assert got["jump"] == "NAV"


# Reads which tables scroll in their own box, and whether the page scrolls sideways.
FIT_PROBE = """
document.getElementById("tab-alerts").click();
function boxes(sel) {
  return [].map.call(document.querySelectorAll(sel + " .table-wrap"), function (w) {
    return w.classList.contains("scrolls");
  });
}
var root = document.documentElement;
var out = { alerts: boxes("#alerts"), scroll: root.scrollWidth, width: root.clientWidth };
document.getElementById("tab-components").click();
out.components = boxes("#tables");
return out;
"""


def test_only_a_table_that_does_not_fit_gets_its_own_box(chrome, edges, tmp_path):
    view = build_view(*edges)
    # An id far wider than the window, in the first alert list. An id never wraps.
    long_id = "metrics/" + "x" * 300
    first = next(a for a in view["alerts"] if a["anomaly"])
    first["component_id"] = long_id
    got = run_probe(chrome, render_html(view), FIT_PROBE, tmp_path, width=1280)
    assert got["alerts"][0] is True and not any(got["alerts"][1:])
    assert not any(got["components"])
    assert got["scroll"] <= got["width"]


# ---- chart lines and labels ---------------------------------------------------------------


def _one_metric_page(tmp_path, values):
    """The report of a run with one metric, metrics/event1, and these daily values."""
    store = Store.open(tmp_path / "one.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "US/Pacific", "0.1.0")
    cid = "metrics/event1"
    store.add_components(
        rid, [Component(cid, "Event 1", "metric", "custom_events", 1, Column(cid, cid), True)]
    )
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    html = render_html(build_view(store, rid))
    store.con.close()
    return html


LINES_PROBE = """
var svg = document.querySelector("#metric-chart svg");
function moves(cls) {
  return [].map.call(svg.querySelectorAll("path." + cls), function (p) {
    return (p.getAttribute("d").match(/M/g) || []).length;
  });
}
return {
  actual: moves("actual"), expected: moves("expected"), band: moves("band"),
  spark: [].map.call(document.querySelectorAll("#tables path.spark-line"), function (p) {
    return (p.getAttribute("d").match(/M/g) || []).length;
  }),
  axis: [].map.call(svg.querySelectorAll("text.axis"), function (t) { return t.textContent; })
    .filter(function (t) { return /^[A-Z][a-z]{2} \\d/.test(t); })
};
"""


def test_the_chart_breaks_its_lines_at_missing_days(chrome, tmp_path):
    # Days with no stored value at all are left out of the chart data. The lines must not join
    # across them, as the 13-week line in the table does not.
    cid = "metrics/event1"
    missing = {YESTERDAY - timedelta(days=d) for d in (30, 31, 32)}
    values = [
        _dv(cid, d, 100.0, 100.0, 120.0, 80.0, False)
        for d in _days()
        if d not in missing and d != TODAY
    ]
    got = run_probe(chrome, _one_metric_page(tmp_path, values), LINES_PROBE, tmp_path)
    assert got["spark"] == [2]
    assert sum(got["actual"]) == 2 and sum(got["expected"]) == 2 and sum(got["band"]) == 2


@pytest.mark.parametrize(
    ("days", "labels"),
    [((1,), ["Nov 6"]), ((2, 1), ["Nov 5", "Nov 6"]), ((3, 2, 1), ["Nov 4", "Nov 5", "Nov 6"])],
)
def test_the_chart_names_each_day_on_its_bottom_edge_once(chrome, tmp_path, days, labels):
    # A short series has fewer days than the chart has date labels.
    cid = "metrics/event1"
    values = [_dv(cid, YESTERDAY - timedelta(days=d - 1), 5.0) for d in days]
    got = run_probe(chrome, _one_metric_page(tmp_path, values), LINES_PROBE, tmp_path)
    assert got["axis"] == labels


def _page_of(tmp_path, series):
    """The report of a run with one custom event for each list of daily values."""
    store = Store.open(tmp_path / "some.duckdb")
    rid = store.create_run("rs1", "Suite One", None, TODAY, "US/Pacific", "0.1.0")
    comps, values = [], []
    for i, make in enumerate(series, start=1):
        cid = f"metrics/event{i}"
        comps.append(
            Component(cid, f"Event {i}", "metric", "custom_events", i, Column(cid, cid), True)
        )
        values += make(cid)
    store.add_components(rid, comps)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    html = render_html(build_view(store, rid))
    store.con.close()
    return html


def _halved_last_week(usual):
    """usual on every day, and half of it on each of the last 7 days."""
    week = {YESTERDAY - timedelta(days=n) for n in range(7)}
    return lambda cid: [
        _dv(cid, d, usual / 2 if d in week else usual) for d in _days() if d != TODAY
    ]


SMALL_WEEK_PROBE = """
var g = document.getElementById("granularity");
g.value = "week";
g.dispatchEvent(new Event("change"));
var out = { rows: {}, text: document.getElementById("change-alert-result").textContent };
[].forEach.call(document.querySelectorAll("#tables tbody tr"), function (tr) {
  out.rows[tr.querySelector("td.name").firstChild.textContent] = tr.textContent;
});
var only = document.getElementById("flagged-only");
only.click();
out.flagged = [].map.call(document.querySelectorAll("#tables tbody tr"), function (tr) {
  return tr.querySelector("td.name").firstChild.textContent;
});
return out;
"""


def test_the_weekly_view_skips_a_component_with_a_small_average_week(chrome, tmp_path):
    # Event 1 has 2 a day, so its average week is 14. Every daily view skips it, because its
    # weekday average is under 10, and the weekly view must skip it too. Event 2 has 20 a day.
    page = _page_of(tmp_path, [_halved_last_week(2.0), _halved_last_week(20.0)])
    got = run_probe(chrome, page, SMALL_WEEK_PROBE, tmp_path)
    assert "Large drop" not in got["rows"]["Event 1"]
    assert "Large drop" in got["rows"]["Event 2"]
    assert got["flagged"] == ["Event 2"]
    assert ": 1 component is more than 30% below the average week." in got["text"]
    assert "under 70" in page


SLOW_PROBE = """
var slider = document.getElementById("change-alert");
var result = document.getElementById("change-alert-result");
var find = document.getElementById("find");
var out = { before: result.textContent, rows: document.querySelectorAll("#tables tbody tr").length };
[-40, -50, -60].forEach(function (v) {
  slider.value = String(v);
  slider.dispatchEvent(new Event("input"));
});
out.shown = document.getElementById("change-alert-value").textContent;
out.atOnce = result.textContent;
find.value = "Event 7";
find.dispatchEvent(new Event("input"));
out.rowsAtOnce = document.querySelectorAll("#tables tbody tr").length;
return new Promise(function (r) { setTimeout(r, 1000); }).then(function () {
  out.later = result.textContent;
  out.rowsLater = document.querySelectorAll("#tables tbody tr").length;
  return out;
});
"""


def test_a_large_page_draws_again_once_the_reader_pauses(chrome, tmp_path):
    # With more than 400 components, the tables are drawn again after a pause, not on every step
    # of the slider. The slider's own value still changes at once.
    page = _page_of(tmp_path, [lambda cid: [_dv(cid, YESTERDAY, 5.0)]] * 401)
    got = run_probe(chrome, page, SLOW_PROBE, tmp_path)
    assert got["rows"] == 401 and got["shown"] == "-60%"
    assert "more than 30%" in got["before"] and got["atOnce"] == got["before"]
    assert got["rowsAtOnce"] == 401
    assert "more than 60%" in got["later"]
    assert 0 < got["rowsLater"] < 401


PAGE_EDGE_PROBE = """
var svg = document.querySelector("#calendar svg");
function key(name, times) {
  for (var i = 0; i < (times || 1); i++) {
    svg.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true }));
  }
  return document.querySelector("#calendar .calendar-readout").textContent;
}
var out = { up: key("ArrowUp"), back: key("ArrowDown") };
out.right = key("ArrowRight");
out.down = key("ArrowDown");
out.left = key("ArrowLeft", 20);
out.wide = document.documentElement.scrollWidth - document.documentElement.clientWidth;
var status = document.getElementById("change-alert-result");
var node = status.firstChild;
var find = document.getElementById("find");
find.value = "Event";
find.dispatchEvent(new Event("input"));
out.sameText = status.firstChild === node;
out.axis = [].map.call(document.querySelectorAll("#metric-chart svg text.axis"), function (t) {
  return t.textContent;
}).filter(function (t) { return /^[0-9.]+$/.test(t); });
return out;
"""


def test_calendar_keys_stop_at_the_edges_and_small_values_get_their_own_labels(chrome, tmp_path):
    # Values up to 0.2 give axis steps of 0.05. One decimal named two lines "0.1".
    values = [_dv("metrics/event1", d, 0.2 if d.day % 2 else 0.1) for d in _days() if d != TODAY]
    page = _one_metric_page(tmp_path, values)
    # 650 px is wider than a phone and narrower than the four columns of the sliders need.
    got = run_probe(chrome, page, PAGE_EDGE_PROBE, tmp_path, width=650)
    assert got["up"].startswith("Thu, Nov 5") and got["back"].startswith("Fri, Nov 6")
    # Yesterday is the last day. A key that would leave the grid keeps the choice.
    assert got["right"] == got["back"] and got["down"] == got["back"]
    # 20 weeks back is before the first day, so the choice stops on the first Friday.
    assert got["left"].startswith("Fri, Aug 14")
    assert got["wide"] <= 0
    assert got["sameText"] is True
    assert got["axis"] == ["0", "0.05", "0.1", "0.15", "0.2"]


FLAG_SORT_PROBE = """
var table = document.querySelector("#tables table");
var note = table.closest(".table-wrap").nextElementSibling;
var button = [].filter.call(table.tHead.querySelectorAll("button"), function (b) {
  return b.firstChild.textContent === "Flag";
})[0];
button.click();
var out = { first: note.firstChild.textContent };
button.click();
out.second = note.firstChild.textContent;
var reset = note.querySelector("button");
reset.focus();
reset.click();
out.hidden = note.hidden;
out.focus = document.activeElement === button;
return out;
"""


def test_a_flag_sort_has_no_a_to_z_and_original_order_keeps_the_focus(chrome, edges, tmp_path):
    got = run_probe(chrome, render_html(build_view(*edges)), FLAG_SORT_PROBE, tmp_path)
    assert got["first"] == "Sorted by Flag."
    assert got["second"] == "Sorted by Flag, in reverse order."
    assert got["hidden"] is True and got["focus"] is True


# ---- fixes from the review before 0.1.7 --------------------------------------------------

NO_ANOMALY_PROBE = """
var tiles = [].map.call(document.querySelectorAll(".tile"), function (t) {
  var note = t.querySelector(".tile-note");
  return [t.querySelector(".tile-value").textContent, note ? note.textContent : ""];
});
var flags = [].map.call(document.querySelectorAll("#tables .muted"), function (n) {
  return n.textContent;
});
document.getElementById("tab-alerts").click();
return { tiles: tiles.slice(1, 3), flags: flags,
  alerts: document.getElementById("panel-alerts").innerText };
"""


@pytest.mark.parametrize("code", ["anomaly_not_allowed", "anomaly_probe_failed"])
def test_a_run_without_anomaly_detection_does_not_say_that_nothing_was_flagged(
    chrome, with_problem, tmp_path, code
):
    view = build_view(*with_problem)
    view["issues"].append(
        {"scope": "permissions", "component_id": None, "code": code, "message": "m"}
    )
    got = run_probe(chrome, render_html(view), NO_ANOMALY_PROBE, tmp_path)
    assert got["tiles"] == [["–", "Anomaly detection not available"]] * 2
    assert "Not available" in got["flags"]
    assert "Anomaly detection is not available for this run" in got["alerts"]
    assert "Adobe flagged no unusual values" not in got["alerts"]


MISSING_DAY_PROBE = """
var r = document.querySelector("#calendar rect.cal-cell.missing");
r.scrollIntoView();
var b = r.getBoundingClientRect();
var hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
hit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
return { day: r.getAttribute("data-day"), hit: hit === r,
  readout: document.querySelector("#calendar .calendar-readout").textContent };
"""


def test_a_calendar_day_without_data_can_be_clicked(chrome, showcase, tmp_path):
    view = build_view(*showcase)
    view["calendar"] = [c for c in view["calendar"] if c["day"] != "2026-10-01"]
    got = run_probe(chrome, render_html(view), MISSING_DAY_PROBE, tmp_path)
    assert got["day"] == "2026-10-01" and got["hit"]
    assert got["readout"].startswith("Thu, Oct 1, 2026: ")


LONE_POINT_PROBE = """
return document.querySelector("#tables button.spark path").getAttribute("d");
"""


def test_a_trend_line_shows_a_day_that_has_no_day_next_to_it(chrome, with_problem, tmp_path):
    view = build_view(*with_problem)
    points = view["series"]["metrics/event1"]
    assert len(points) == 91
    # Values on one day alone, on two days in a row, and on the last day alone.
    for at, p in enumerate(points):
        if at not in (10, 50, 51, 90):
            p[1] = None
    path = run_probe(chrome, render_html(view), LONE_POINT_PROBE, tmp_path)
    parts = re.findall(r"M[^M]+", path)
    assert len(parts) == 3
    # A lone day gets a very short line, which draws a dot. Two days in a row get a line.
    assert parts[0].endswith("l0.01 0") and parts[2].endswith("l0.01 0")
    assert "L" in parts[1] and "l0.01" not in parts[1]


# ---- the anomaly method ------------------------------------------------------------------

# Every text of the page a reader can see or hear, in the daily and the weekly view, and the
# copied alerts.
METHOD_PROBE = """
var copied = null;
Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
  write: function (items) {
    return items[0].getType("text/html").then(function (b) { return b.text(); })
      .then(function (t) { copied = t; });
  },
  writeText: function () { return Promise.resolve(); }
}});
function texts() {
  var out = [document.body.innerText];
  [].forEach.call(document.querySelectorAll("[title], [aria-label]"), function (n) {
    out.push(n.getAttribute("title") || "", n.getAttribute("aria-label") || "");
  });
  return out.join("\\n");
}
function titles(selector) {
  return [].map.call(document.querySelectorAll(selector), function (n) {
    return n.getAttribute("title");
  });
}
// The flag badges, without the badges of large drops and spikes.
function flags(selector) {
  return titles(selector).filter(function (t) { return t.indexOf("weekday average") < 0; });
}
var out = { badges: flags("#tables .badge-flag[title]") };
var tiles = {};
[].forEach.call(document.querySelectorAll("#tiles .tile"), function (t) {
  var note = t.querySelector(".tile-note");
  tiles[t.querySelector(".tile-label").lastChild.textContent] = [
    t.querySelector(".tile-value").textContent, note ? note.textContent : ""];
});
out.tiles = tiles;
out.muted = [].map.call(document.querySelectorAll("#tables .muted"), function (n) {
  return n.textContent;
});
document.getElementById("tab-alerts").click();
out.unusualHint = (document.querySelector(
  "#alerts section[aria-labelledby=unusual-title] .hint") || {}).textContent || null;
out.unusualEmpty = (document.querySelector(
  "#alerts section[aria-labelledby=unusual-title] .empty") || {}).textContent || null;
out.dropNote = (document.querySelector(
  "#alerts section[aria-labelledby=drops-title] .empty") || {}).textContent || null;
out.daily = texts();
var gran = document.getElementById("granularity");
gran.value = "week";
gran.dispatchEvent(new Event("change"));
document.getElementById("tab-components").click();
out.weekBadges = flags("#tables .week-flags .badge[title]");
out.weekly = texts();
document.getElementById("copy-alerts").click();
function wait(tries) {
  if (copied !== null || tries > 200) return Promise.resolve();
  return new Promise(function (r) { setTimeout(r, 0); }).then(function () { return wait(tries + 1); });
}
return wait(0).then(function () { out.copied = copied; return out; });
"""

LOCAL_METHODS = [m for m in ANOMALY_METHODS if m != "adobe"]


def _blocked(view, code="anomaly_not_allowed"):
    view["issues"].append(
        {"scope": "permissions", "component_id": None, "code": code, "message": "m"}
    )
    return view


@pytest.mark.parametrize("method", LOCAL_METHODS)
def test_a_local_method_names_itself_and_never_adobe(chrome, edges, tmp_path, method):
    label = ANOMALY_LABELS[method]
    got = run_probe(chrome, render_html(build_view(*edges, method=method)), METHOD_PROBE, tmp_path)
    for text in (got["daily"], got["weekly"], got["copied"]):
        # Two component names of the edge cases name Adobe. The page shows them as they are.
        for name in sorted((n for n, _ in CASES.values()), key=len, reverse=True):
            text = text.replace(name, "")
        assert "Adobe" not in text
    assert got["badges"] and set(got["badges"]) <= {
        f"The {label} method flagged this value as above the expected range.",
        f"The {label} method flagged this value as below the expected range.",
    }
    assert got["weekBadges"] and all(
        t.startswith(f"The {label} method flagged this value ") for t in got["weekBadges"]
    )
    assert got["unusualHint"].startswith(
        f"The {label} method flagged these values as outside the expected range."
    )
    assert got["tiles"]["Above expected range"][1] == f"Flagged by the {label} method"
    assert got["tiles"]["Below expected range"][1] == f"Flagged by the {label} method"
    # The 50 percent drop of event5 is also below the local range.
    assert f"and the {label} method also flagged it." in got["dropNote"]
    assert f"Unusual values, flagged by the {label} method (" in got["copied"]


def test_adobe_keeps_its_words(chrome, edges, tmp_path):
    got = run_probe(chrome, render_html(build_view(*edges)), METHOD_PROBE, tmp_path)
    assert set(got["badges"]) == {
        "Adobe flagged this value as above the expected range.",
        "Adobe flagged this value as below the expected range.",
    }
    assert got["unusualHint"].startswith(
        "Adobe flagged these values as outside the expected range."
    )
    assert got["tiles"]["Above expected range"][1] == "Flagged by Adobe"
    assert "Unusual values, flagged by Adobe (" in got["copied"]
    assert "ahd render" not in got["daily"]


@pytest.mark.parametrize("code", ["anomaly_not_allowed", "anomaly_probe_failed"])
@pytest.mark.parametrize("method", LOCAL_METHODS)
def test_a_blocked_run_with_a_local_method_shows_flags(chrome, edges, tmp_path, method, code):
    view = _blocked(build_view(*edges, method=method), code)
    got = run_probe(chrome, render_html(view), METHOD_PROBE, tmp_path)
    macro = _macro(*edges, 0.3, method=method).values()
    high = sum(1 for a, f in macro if a == "unusual" and f == "high")
    low = sum(1 for a, f in macro if a == "unusual" and f == "low")
    assert low and got["tiles"]["Below expected range"][0] == str(low)
    assert got["tiles"]["Above expected range"][0] == str(high)
    assert "Not available" not in got["muted"]
    assert "Anomaly detection is not available" not in got["daily"]
    assert "Anomaly detection not available" not in got["daily"]
    assert got["badges"]


@pytest.mark.parametrize("code", ["anomaly_not_allowed", "anomaly_probe_failed"])
def test_a_blocked_run_with_adobe_suggests_a_local_method(chrome, with_problem, tmp_path, code):
    view = _blocked(build_view(*with_problem), code)
    got = run_probe(chrome, render_html(view), METHOD_PROBE, tmp_path)
    run = view["run"]["id"]
    assert got["unusualEmpty"] == (
        "Anomaly detection is not available for this run, so no values could be flagged. "
        "See the status section. To flag unusual values without it, write this report again "
        f"with ahd render --run {run} --anomaly-method weekday-median."
    )


def test_the_edge_cases_differ_by_sensitivity(edges):
    # So the two tests above compare different alert lists for each sensitivity. event17 is
    # inside the range of normal and low, and above the range of high.
    for method in LOCAL_METHODS:
        got = {s: _macro(*edges, 0.3, 0.5, method, s) for s in ("low", "normal", "high")}
        assert got["low"] == got["normal"] != got["high"], method
        assert "metrics/event17" not in got["normal"]
        assert got["high"]["metrics/event17"] == ("unusual", "high")


# The header facts, the rule list and the texts that name who flagged a value.
SENSITIVITY_PROBE = """
var facts = {};
[].forEach.call(document.querySelectorAll(".facts > div"), function (d) {
  facts[d.querySelector("dt").textContent] = d.querySelector("dd").textContent;
});
var tiles = {};
[].forEach.call(document.querySelectorAll("#tiles .tile"), function (t) {
  var note = t.querySelector(".tile-note");
  tiles[t.querySelector(".tile-label").lastChild.textContent] = note ? note.textContent : "";
});
document.getElementById("tab-alerts").click();
return {
  facts: facts,
  rules: [].map.call(document.querySelectorAll("#change-alert-rule li"), function (li) {
    return li.textContent;
  }),
  tiles: tiles,
  hint: document.querySelector("#alerts section[aria-labelledby=unusual-title] .hint").textContent,
  text: document.body.innerText
};
"""


@pytest.mark.parametrize(
    ("method", "sensitivity", "title", "far"),
    [
        (
            "weekday-median",
            "high",
            "Weekday median, high sensitivity",
            "Far means more than 2 times the usual spread of those values.",
        ),
        (
            "weekday-iqr",
            "low",
            "Weekday quartile range, low sensitivity",
            "Far means more than 2 times the width of the middle half past its edge.",
        ),
        (
            "weekday-trend",
            "normal",
            "Weekday trend",
            "far means more than 3 times the usual spread of how",
        ),
    ],
)
def test_the_page_shows_the_sensitivity(chrome, edges, tmp_path, method, sensitivity, title, far):
    view = build_view(*edges, method=method, sensitivity=sensitivity)
    got = run_probe(chrome, render_html(view), SENSITIVITY_PROBE, tmp_path)
    label = ANOMALY_LABELS[method]
    assert got["facts"]["Anomaly detection"] == (
        f"{title} (computed by ahd from the daily values of this run)"
    )
    [rule] = [r for r in got["rules"] if r.startswith("A value is unusual when")]
    assert far in rule
    assert "A day is flagged only when this run has at least 8 " in rule
    # The texts that name who flagged a value keep the plain name of the method.
    assert got["tiles"]["Above expected range"] == f"Flagged by the {label} method"
    assert got["hint"].startswith(f"The {label} method flagged these values")
    assert ("sensitivity" in got["text"]) is (sensitivity != "normal")
    assert "sensitivity method" not in got["text"]
