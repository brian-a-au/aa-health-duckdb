import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fakes import fake_for, restricted_dimensions, restricted_metrics

from ahd.api.reports import Column, DailyValue
from ahd.catalog import Component
from ahd.config import Credentials
from ahd.fetch import RunOptions, run_fetch
from ahd.render.html import (
    BREAKDOWN_NOTES,
    CSP,
    ISSUE_HEADINGS,
    NO_BANDS_NOTE,
    WEEK_NOTE,
    issue_heading,
    render_html,
    write_report,
)
from ahd.store import Store
from ahd.summary import build_view

TODAY = date(2026, 11, 7)
YESTERDAY = date(2026, 11, 6)
START = TODAY - timedelta(days=91)
HOSTILE = '</script><img src=x onerror=alert(1)>'
DATA_RE = re.compile(r'<script type="application/json" id="data">(.*?)</script>', re.DOTALL)


def _days():
    d = START
    while d <= YESTERDAY:
        yield d
        d += timedelta(days=1)


def _metric(cid, name, group, number=None):
    return Component(cid, name, "metric", group, number, Column(cid, cid), None)


def _dim(cid, name, group, number=None):
    return Component(cid, name, "dimension", group, number,
                     Column(cid, "metrics/occurrences", cid), True)


def _series(cid, base, bands=True, spike=None, dip=None, zero_yesterday=False):
    out = []
    for i, d in enumerate(_days()):
        v = base * (1.3 if d.weekday() < 5 else 0.7) + (i % 5)
        exp = upper = lower = None
        anomaly = None
        if bands:
            exp = base * (1.3 if d.weekday() < 5 else 0.7) + 2
            upper, lower, anomaly = exp * 1.25, exp * 0.75, False
        if spike and d == spike:
            v, anomaly = v * 2, bands or None
        if dip and d == dip:
            v, anomaly = v * 0.3, bands or None
        if zero_yesterday and d == YESTERDAY:
            v = 0.0
        out.append(DailyValue(cid, d, float(v), exp, upper, lower, anomaly))
    return out


def sample_store(path: Path, bands: bool = True) -> tuple[Store, int]:
    comps = [
        _metric("metrics/event1", "Cart opens", "custom_events", 1),
        _metric("metrics/event2", "Newsletter sign ups", "custom_events", 2),
        _metric("metrics/event150", "Video starts", "custom_events", 150),
        _metric("metrics/evar1instances", "Internal campaign", "custom_evars", 1),
        _metric("metrics/evar3instances", HOSTILE, "custom_evars", 3),
        _dim("variables/prop1", "Page type", "custom_props", 1),
        _dim("variables/prop2", "Search term", "custom_props", 2),
        _metric("metrics/pageviews", "Page Views", "Reserved Traffic Events"),
        _metric("metrics/orders", "Orders", "Reserved Purchase Events"),
        _dim("variables/page", "Page", "Reserved Props"),
    ]
    vals = []
    vals += _series("metrics/event1", 500, bands, spike=YESTERDAY)
    vals += _series("metrics/event2", 40, bands, dip=YESTERDAY - timedelta(days=3))
    vals += _series("metrics/event150", 90, bands, zero_yesterday=True)
    vals += _series("metrics/evar1instances", 300, bands)
    vals += _series("metrics/evar3instances", 12, bands)
    vals += _series("variables/prop1", 2000, bands, dip=YESTERDAY)
    vals += _series("variables/prop2", 150, bands)
    vals += _series("metrics/pageviews", 9000, bands)
    vals += _series("metrics/orders", 25, bands, spike=YESTERDAY - timedelta(days=10))
    vals += _series("variables/page", 9000, bands)
    vals += _series("individual", 1200, bands, spike=YESTERDAY - timedelta(days=1))

    store = Store.open(path)
    rid = store.create_run("luma.prod", "Luma Production", None, TODAY, "US/Pacific", "0.1.0",
                           dimension="variables/page", metric="metrics/orders")
    store.add_components(rid, comps)
    store.add_daily_values(rid, vals)
    store.add_top_items(rid, "current", [
        (1, "Home", 1200.0), (2, HOSTILE, 800.0), (3, "Men > Shirts & Tops", 400.0),
        (4, "Brand new page", 100.0)])
    store.add_top_items(rid, "historical", [
        (1, "Men > Shirts & Tops", 9.0), (2, "Home", 8.0), (3, HOSTILE, 7.0)])
    store.add_breakdown(rid, "browser", ["Google", "Microsoft", "Apple", "Mozilla", "Other"],
                        [("Home", [500.0, 100.0, 400.0, 50.0, 10.0]),
                         ("Search", [90.0, 10.0, 60.0, 5.0, 1.0])],
                        [590.0, 110.0, 460.0, 55.0, 11.0])
    store.add_breakdown(rid, "device", ["Other", "Mobile Phone", "Tablet", "Media Player",
                                        "Television"],
                        [("Home", [700.0, 400.0, 90.0, 0.0, 10.0])],
                        [700.0, 400.0, 90.0, 0.0, 10.0])
    store.add_issue(rid, "column", "metrics/event9", "unauthorized_metric",
                    "You do not have access to this metric.")
    if not bands:
        store.add_issue(rid, "permissions", None, "anomaly_not_allowed",
                        "Anomaly detection is not allowed for this credential.")
    store.finish_run(rid, "partial")
    return store, rid


def sample_view(bands: bool = True) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store, rid = sample_store(Path(tmp) / "s.duckdb", bands)
        try:
            return build_view(store, rid)
        finally:
            store.con.close()


def _data_json(html: str) -> dict:
    m = DATA_RE.search(html)
    assert m
    return json.loads(m.group(1))


def test_single_file_no_external_urls():
    html = render_html(sample_view())
    assert not re.search(r'(src|href)=["\']https?://|@import|url\(\s*["\']?https?://', html)
    assert "<link" not in html and "<script src" not in html
    assert html.count("<style>") == 1


def test_hostile_item_is_inert():  # Review Focus 2
    v = sample_view()
    v["top100"][0]["item"] = HOSTILE
    html = render_html(v)
    assert "</script><img" not in html and "innerHTML" not in html
    assert "<img" not in html
    # the value survives the round trip as text data
    data = _data_json(html)
    assert data["top100"][0]["item"] == HOSTILE
    assert any(r["name"] == HOSTILE for r in data["views"]["week"])


def test_hostile_issue_message_is_escaped():
    v = sample_view()
    v["issues"].append({"scope": "column", "component_id": HOSTILE, "code": "weird_code",
                        "message": HOSTILE})
    html = render_html(v)
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_json_escapes_angle_brackets_and_ampersand():
    html = render_html(sample_view())
    m = DATA_RE.search(html)
    body = m.group(1)
    assert "<" not in body and ">" not in body and "&" not in body
    assert "\\u003c" in body and "\\u0026" in body


def test_no_secrets_in_html(tmp_path):
    secret = "SUPER-SECRET-VALUE-8731"
    creds = Credentials(org_id="org@AdobeOrg", client_id="client-" + secret,
                        secret=secret, scopes="openid")
    client = fake_for("rs1", restricted_metrics(), restricted_dimensions())
    client.credentials = creds  # what a real client would hold
    store = Store.open(tmp_path / "run.duckdb")
    rid = run_fetch(client, store, RunOptions(rsid="rs1", today=TODAY))
    html = render_html(build_view(store, rid))
    assert secret not in html
    assert "client-" not in html


def test_three_tabs_and_status():
    html = render_html(sample_view(bands=False))
    for tab in ("Components Summary", "Individual Component Summary", "Alerts Summary"):
        assert f">{tab}<" in html
    assert "Luma Production" in html and "luma.prod" in html
    assert "2026-11-07" in html
    assert "Complete, with some gaps" in html
    assert ("Anomaly bands are not available. The API credential&#39;s product profile does "
            "not include the Anomaly Detection permission (Report Suite Tools).") in html
    # a code with a heading still lists its items with the message
    assert "unauthorized_metric" in html and "You do not have access to this metric." in html
    assert "metrics/event9" in html
    assert "Copy alert summary" in html


def test_no_custom_components_heading():
    v = sample_view()
    v["issues"] = [{"scope": "permissions", "component_id": None,
                    "code": "no_custom_components", "message": "x"}]
    html = render_html(v)
    assert ("No custom eVars, props, or events were returned. The API credential&#39;s "
            "product profile probably hides them.") in html


def test_no_issues_says_so():
    v = sample_view()
    v["issues"] = []
    v["run"]["status"] = "ok"
    html = render_html(v)
    assert "No problems were found in this run." in html


def test_missing_bands_note():
    html = render_html(sample_view(bands=False))
    assert NO_BANDS_NOTE == "Anomaly bands are not available. See the status section."
    assert NO_BANDS_NOTE in html
    data = _data_json(html)
    rows = data["series"]["metrics/event1"]
    assert all(r[3] is None and r[4] is None for r in rows)


def test_week_note_and_controls():
    html = render_html(sample_view())
    assert WEEK_NOTE == ("This week is the 7 days ending yesterday. "
                         "The Excel version used 8 to 2 days ago.")
    assert WEEK_NOTE in html
    for label in ("Daily", "Weekly", "1 to 100", "901 to 1000", "Metric chart",
                  "Dimension chart"):
        assert label in html
    assert re.search(r"<label>Show\s*<select id=\"granularity\">", html)
    assert "Granularity" not in html


def test_static_assets_inlined():
    html = render_html(sample_view())
    assert "prefers-color-scheme: dark" in html
    assert "textContent" in html
    assert "navigator.clipboard.writeText" in html


def test_write_report_file_name(tmp_path):
    v = sample_view()
    path = write_report(v, tmp_path / "out")
    assert path.name == f"ahd_luma.prod_2026-11-07_run{v['run']['id']}.html"
    assert path.read_text(encoding="utf-8") == render_html(v)


def test_write_report_sanitizes_rsid(tmp_path):
    v = sample_view()
    v["run"]["rsid"] = "../evil/rs"
    path = write_report(v, tmp_path)
    assert path.parent == tmp_path
    assert "/" not in path.name and ".." not in path.name


def test_individual_names_in_heading_trend_and_header():
    html = render_html(sample_view())
    assert "Trend for the chosen dimension and metric" not in html
    assert '<h2 id="individual-title">Page by Orders</h2>' in html
    assert "<figcaption>Trend for Page by Orders</figcaption>" in html
    assert re.search(r"<dt>Individual component</dt><dd>Page by Orders", html)
    data = _data_json(html)
    assert data["run"]["dimension_name"] == "Page" and data["run"]["metric_name"] == "Orders"


def test_individual_names_fall_back_for_old_runs():
    v = sample_view()
    for k in ("dimension", "metric", "dimension_name", "metric_name"):
        v["run"][k] = None
    html = render_html(v)
    assert "<figcaption>Trend for the chosen dimension and metric</figcaption>" in html


def test_issue_headings_for_known_codes():
    for code in ("unauthorized_metric", "network_error", "anomaly_probe_failed", "no_rows",
                 "anomaly_not_allowed", "no_custom_components"):
        assert ISSUE_HEADINGS[code] == issue_heading(code)
    assert issue_heading("unauthorized_metric") == (
        "Some metrics or dimensions are not visible to this credential.")
    assert issue_heading("network_error") == (
        "Some requests failed because of network problems. Running again may fix this.")
    for code in ("http_400", "http_404", "http_500", "http_503"):
        assert issue_heading(code) == "Adobe returned an error for some requests."
    assert issue_heading("http_") is None and issue_heading("weird_code") is None
    html = render_html(sample_view())  # the sample has one unauthorized_metric issue
    assert "Some metrics or dimensions are not visible to this credential." in html
    assert "metrics/event9" in html  # entries with a component id are still listed


def test_csp_meta_is_first_in_head():
    html = render_html(sample_view())
    assert CSP == ("default-src 'none'; style-src 'unsafe-inline'; "
                   "script-src 'unsafe-inline'; img-src data:")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    assert meta.replace("'", "&#39;") in html or meta in html
    assert html.index("Content-Security-Policy") < html.index("<style>")


def test_breakdown_notes_under_tables():
    html = render_html(sample_view())
    assert BREAKDOWN_NOTES["browser"] == (
        "Rows are the top 10 values in the first column (Google), as in the Excel version.")
    assert BREAKDOWN_NOTES["device"] == (
        "Rows are the top 10 values in the first column (Other), as in the Excel version.")
    for kind in ("browser", "device"):
        note = BREAKDOWN_NOTES[kind]
        assert html.index(f'<div id="{kind}"></div>') < html.index(note)


def test_status_badge_has_icon_and_label():
    html = render_html(sample_view())  # sample run is partial
    assert re.search(r'<span class="badge badge-flag"><span class="icon" aria-hidden="true">!</span>'
                     r'Complete, with some gaps', html)
    v = sample_view()
    v["run"]["status"] = "ok"
    html = render_html(v)
    assert '<span class="badge badge-good"><span class="icon" aria-hidden="true">✓</span>Complete' in html


def test_page_has_tiles_flag_filter_and_sortable_tables():
    html = render_html(sample_view())
    assert '<section class="tiles" id="tiles"' in html
    assert html.index('id="tiles"') < html.index('id="status"')
    assert '<input type="checkbox" id="flagged-only">' in html
    assert "aria-sort" in html and "Usual order" in html


def test_alerts_carry_direction_for_the_page():
    data = _data_json(render_html(sample_view()))
    for a in data["alerts"]:
        assert "anomaly" in a and "zero" in a


def test_flag_colors_are_not_good_and_bad():
    # Both directions share one warning color; red is kept for zero data.
    css = render_html(sample_view())
    assert ".mark-low, .mark-high { fill: var(--flag)" in css
    assert "--high:" not in css and "--low:" not in css


def test_theme_toggle_has_three_choices_and_css_scopes():
    html = render_html(sample_view())
    for choice in ("auto", "light", "dark"):
        assert f'data-theme-choice="{choice}"' in html
    assert ':root:not([data-theme="light"])' in html  # a light choice beats a dark computer
    assert ':root[data-theme="dark"]' in html          # a dark choice beats a light computer
    assert "localStorage" in html and "try {" in html
