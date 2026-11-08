"""Tests that run report.js in headless Chrome. See tests/chrome.py for how a probe works."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from chrome import chrome_or_skip, run_probe, without_csp

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.render.html import render_html
from ahd.store import Store
from ahd.summary import build_view

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


# Each case is (name, daily values). The cases are the edge cases of the alert rules.
CASES = {
    "event1": ("Usual exactly 10, yesterday 7", lambda c: _series(c, 10.0, 7.0)),
    "event2": ("Usual exactly 10, yesterday 8", lambda c: _series(c, 10.0, 8.0)),
    "event3": ("Usual 8, yesterday 1", lambda c: _series(c, 8.0, 1.0)),
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
    unusual: ids("#alerts section[aria-labelledby=unusual-title]"),
    drops: ids("#alerts section[aria-labelledby=drops-title]"),
    zero: ids("#alerts section[aria-labelledby=zero-title]"),
    unused: ids("#alerts details.alert-group"),
    text: document.getElementById("alert-text").value,
    tiles: tiles
  };
}
var out = { "30": snapshot() };
var slider = document.getElementById("change-alert");
SLIDER.forEach(function (p) {
  slider.value = String(p);
  slider.dispatchEvent(new Event("input"));
  out[String(p)] = snapshot();
});
return out;
"""


def _macro(store, rid, threshold):
    rows = store.con.execute(
        "select component_id, alert, anomaly from alerts(?, threshold := ?)", [rid, threshold]
    ).fetchall()
    return {cid: (alert, anomaly) for cid, alert, anomaly in rows}


def _never_collected(store, rid):
    rows = store.con.execute(
        "select component_id from component_days"
        " where run_id = ? and is_yesterday and zero and unused order by 1",
        [rid],
    ).fetchall()
    return [r[0] for r in rows]


@pytest.fixture(scope="module")
def alert_pages(chrome, edges, tmp_path_factory):
    store, rid = edges
    probe = ALERTS_PROBE.replace("SLIDER", json.dumps(list(SLIDER)))
    folder = tmp_path_factory.mktemp("alerts")
    return run_probe(chrome, render_html(build_view(store, rid)), probe, folder)


@pytest.mark.parametrize("pct", (30, *SLIDER))
def test_alert_lists_match_the_alerts_macro(alert_pages, edges, pct):
    store, rid = edges
    page = alert_pages[str(pct)]
    macro = _macro(store, rid, pct / 100)

    def where(kind):
        return sorted(cid for cid, (alert, _) in macro.items() if alert == kind)

    assert page["shown"] == f"{pct}%"
    assert page["unusual"] == where("unusual")
    assert page["drops"] == where("large drop")
    assert page["zero"] == where("dropped to zero")
    assert page["unused"] == _never_collected(store, rid)
    listed = re.findall(r"^- .*\((metrics/[^)]+)\): ", page["text"], re.MULTILINE)
    assert sorted(listed) == sorted(macro)
    if where("large drop"):
        assert (
            f"Drops of more than {pct}% below usual ({len(where('large drop'))}):" in page["text"]
        )


def test_alert_edge_cases(alert_pages):
    p30, p20, p10 = alert_pages["30"], alert_pages["20"], alert_pages["10"]
    # A drop of exactly the slider percent is not flagged. A usual value of exactly 10 counts.
    assert "metrics/event1" not in p30["drops"] and "metrics/event1" in p20["drops"]
    assert "metrics/event2" not in p20["drops"] and "metrics/event2" in p10["drops"]
    # A usual value under 10 is never a drop.
    assert all("metrics/event3" not in alert_pages[str(p)]["drops"] for p in (30, *SLIDER))
    # NaN and zero on every day: never collected data, and not an alert.
    assert {"metrics/event7", "metrics/event8"} <= set(p30["unused"])
    # An Adobe flag on a zero is an unusual value, not a drop to zero.
    assert "metrics/event9" in p30["unusual"] and "metrics/event9" not in p30["zero"]
    # A component that never collected data stays out of the alerts, even with an Adobe flag.
    assert "metrics/event10" in p30["unused"] and "metrics/event10" not in p30["unusual"]
    assert p30["zero"] == ["metrics/event6"]


@pytest.mark.parametrize("pct", (30, *SLIDER))
def test_tiles_match_the_alerts_macro(alert_pages, edges, pct):
    store, rid = edges
    tiles = alert_pages[str(pct)]["tiles"]
    macro = _macro(store, rid, pct / 100).values()

    def count(alert, anomaly=None):
        return str(sum(1 for a, f in macro if a == alert and (anomaly is None or f == anomaly)))

    assert tiles["Components checked"]["value"] == str(len(CASES))
    assert tiles["Above expected range"]["value"] == count("unusual", "high")
    assert tiles["Below expected range"]["value"] == count("unusual", "low")
    assert tiles["Large drops"] == {
        "value": count("large drop"),
        "note": f"More than {pct}% below usual",
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


@pytest.mark.parametrize("width", [390, 1280])
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
    import chrome as module

    monkeypatch.setattr(module, "find_chrome", lambda: None)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception, match="AHD_CHROME"):
        module.chrome_or_skip()
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="AHD_CHROME"):
        module.chrome_or_skip()


def test_ahd_chrome_chooses_the_program(monkeypatch, tmp_path):
    import chrome as module

    program = tmp_path / "my-chrome"
    program.write_text("", encoding="utf-8")
    monkeypatch.setenv("AHD_CHROME", str(program))
    assert module.find_chrome() == str(program)
    monkeypatch.setenv("AHD_CHROME", str(tmp_path / "missing"))
    with pytest.raises(pytest.fail.Exception, match="no file there"):
        module.find_chrome()
