import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fakes import fake_for, restricted_dimensions, restricted_metrics
from markupsafe import escape

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
from ahd.summary import ANOMALY_LABELS, ANOMALY_METHODS, build_view

TODAY = date(2026, 11, 7)
YESTERDAY = date(2026, 11, 6)
START = TODAY - timedelta(days=91)
HOSTILE = "</script><img src=x onerror=alert(1)>"
DATA_RE = re.compile(r'<script type="application/json" id="data">(.*?)</script>', re.DOTALL)


def _days():
    d = START
    while d <= YESTERDAY:
        yield d
        d += timedelta(days=1)


def _metric(cid, name, group, number=None):
    return Component(cid, name, "metric", group, number, Column(cid, cid), None)


def _dim(cid, name, group, number=None):
    return Component(
        cid, name, "dimension", group, number, Column(cid, "metrics/occurrences", cid), True
    )


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
    vals += _series("individual/1", 1200, bands, spike=YESTERDAY - timedelta(days=1))

    store = Store.open(path)
    rid = store.create_run(
        "luma.prod",
        "Luma Production",
        None,
        TODAY,
        "US/Pacific",
        "0.1.0",
    )
    store.add_pair(rid, "variables/page", "metrics/orders")
    store.add_components(rid, comps)
    store.add_daily_values(rid, vals)
    store.add_top_items(
        rid,
        "current",
        [
            (1, "Home", 1200.0),
            (2, HOSTILE, 800.0),
            (3, "Men > Shirts & Tops", 400.0),
            (4, "Brand new page", 100.0),
        ],
    )
    store.add_top_items(
        rid, "historical", [(1, "Men > Shirts & Tops", 9.0), (2, "Home", 8.0), (3, HOSTILE, 7.0)]
    )
    store.add_breakdown(
        rid,
        "browser",
        ["Google", "Microsoft", "Apple", "Mozilla", "Other"],
        [("Home", [500.0, 100.0, 400.0, 50.0, 10.0]), ("Search", [90.0, 10.0, 60.0, 5.0, 1.0])],
        [590.0, 110.0, 460.0, 55.0, 11.0],
    )
    store.add_breakdown(
        rid,
        "device",
        ["Other", "Mobile Phone", "Tablet", "Media Player", "Television"],
        [("Home", [700.0, 400.0, 90.0, 0.0, 10.0])],
        [700.0, 400.0, 90.0, 0.0, 10.0],
    )
    store.add_issue(
        rid,
        "column",
        "metrics/event9",
        "unauthorized_metric",
        "You do not have access to this metric.",
    )
    if not bands:
        store.add_issue(
            rid,
            "permissions",
            None,
            "anomaly_not_allowed",
            "Anomaly detection is not allowed for this credential.",
        )
    store.finish_run(rid, "partial")
    return store, rid


def sample_view(bands: bool = True, method: str = "adobe") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store, rid = sample_store(Path(tmp) / "s.duckdb", bands)
        try:
            return build_view(store, rid, method=method)
        finally:
            store.con.close()


def _unpack(d: dict) -> dict:
    """The same steps as unpackView in report.js, so tests can read the page data."""
    f = d["packed"]

    def with_component(o, names):
        c = dict(zip(f["component"], d["components"].get(o["component_id"], [])))
        return {**o, **{k: v for k, v in c.items() if k in names}}

    d["views"] = {
        k: [with_component(dict(zip(f["row"], r)), f["component"]) for r in rows]
        for k, rows in d["views"].items()
    }
    d["alerts"] = [
        with_component(dict(zip(f["alert"], a)), f["alert_component"]) for a in d["alerts"]
    ]
    series = {}
    for cid, s in d["series"].items():
        start = date.fromisoformat(s["start"])
        series[cid] = [
            [(start + timedelta(days=i)).isoformat(), *(s[name][i] for name in f["series"])]
            for i in range(len(s[f["series"][0]]))
            if i not in s.get("gaps", [])
        ]
    d["series"] = series
    ids = list(d["components"])
    d["calendar"] = [
        {
            k: [ids[x] if isinstance(x, int) else x for x in v]
            if k in ("high", "low", "zero")
            else v
            for k, v in zip(f["calendar"], c, strict=True)
        }
        for c in d.get("calendar", [])
    ]
    return d


def _data_json(html: str) -> dict:
    m = DATA_RE.search(html)
    assert m
    return _unpack(json.loads(m.group(1)))


def test_single_file_no_external_urls():
    html = render_html(sample_view())
    assert not re.search(r'(src|href)=["\']https?://|@import|url\(\s*["\']?https?://', html)
    assert "<link" not in html and "<script src" not in html
    assert html.count("<style>") == 1


def test_hostile_item_is_inert():  # Review Focus 2
    v = sample_view()
    v["pairs"][0]["top100"][0]["item"] = HOSTILE
    html = render_html(v)
    assert "</script><img" not in html and "innerHTML" not in html
    assert "<img" not in html
    # the value survives the round trip as text data
    data = _data_json(html)
    assert data["pairs"][0]["top100"][0]["item"] == HOSTILE
    assert any(r["name"] == HOSTILE for r in data["views"]["week"])


def test_hostile_issue_message_is_escaped():
    v = sample_view()
    v["issues"].append(
        {"scope": "column", "component_id": HOSTILE, "code": "weird_code", "message": HOSTILE}
    )
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
    creds = Credentials(
        org_id="org@AdobeOrg", client_id="client-" + secret, secret=secret, scopes="openid"
    )
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
    assert (
        "Anomaly bands are not available. The API credential&#39;s product profile does "
        "not include the Anomaly Detection permission (Report Suite Tools)."
    ) in html
    # a code with a heading still lists its items with the message
    assert "unauthorized_metric" in html and "You do not have access to this metric." in html
    assert "metrics/event9" in html
    assert "Copy alerts" in html


def test_no_custom_components_heading():
    v = sample_view()
    v["issues"] = [
        {
            "scope": "permissions",
            "component_id": None,
            "code": "no_custom_components",
            "message": "x",
        }
    ]
    html = render_html(v)
    assert (
        "No custom eVars, props, or events were returned. The API credential&#39;s "
        "product profile probably hides them."
    ) in html


def test_no_issues_says_so():
    v = sample_view()
    v["issues"] = []
    v["run"]["status"] = "ok"
    html = render_html(v)
    assert "No problems were found in this run." in html


def test_status_is_one_line_without_problems():
    v = sample_view()
    v["issues"] = []
    html = render_html(v)
    assert '<section class="status-section status-ok" id="status"' in html
    # The heading, the badge and the sentence share one line.
    assert re.search(
        r'<div class="status-line"><h2 id="status-title">Status</h2>'
        r'<span class="badge badge-good">.*?No problems</span>'
        r'<span class="hide-narrow">No problems were found in this run.</span></div>',
        html,
    )
    assert html.count('id="status-title"') == 1
    # With problems, the box keeps its heading on its own line.
    html = render_html(sample_view())
    assert "status-section status-ok" not in html
    assert '<h2 id="status-title">Status</h2>' in html
    assert html.count('id="status-title"') == 1
    # A warning about fewer components is a problem too.
    v["component_drops"] = [{"message": "This run sees 7 eVars."}]
    html = render_html(v)
    assert "status-section status-ok" not in html and html.count('id="status-title"') == 1
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
    assert WEEK_NOTE == "This week is the 7 days ending yesterday."
    assert WEEK_NOTE in html
    for label in ("Daily", "Weekly", "Custom components", "Metric chart", "Dimension chart"):
        assert label in html
    # The page builds the Custom components options from the run's components.
    assert "901 to 1000" not in html and "Component numbers" not in html
    assert re.search(r"<label>Show\s*<select id=\"granularity\" autocomplete=\"off\">", html)
    assert "Granularity" not in html
    assert 'title="The report compares one week: the 7 days ending yesterday."' in html
    # The script starts these at Daily, All and not ticked. Firefox would else put back what
    # the reader chose before a reload, and the tables would not match the controls.
    for control in ('<select id="range"', '<input type="checkbox" id="flagged-only"'):
        assert control + ' autocomplete="off">' in html


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


def test_write_report_names_the_segment(tmp_path):
    v = sample_view()
    v["run"]["segment_id"] = "s300000_5a1b"
    path = write_report(v, tmp_path)
    assert path.name == f"ahd_luma.prod_s300000_5a1b_2026-11-07_run{v['run']['id']}.html"


def test_write_report_keeps_the_report_of_a_run_from_another_file(tmp_path):
    # Two database files can each hold a run 1 of the same suite and date, e.g., after the
    # user starts a new file. The second report must not replace the first.
    first = sample_view()
    path = write_report(first, tmp_path)
    other = sample_view()
    other["run"]["started_at"] = "2026-11-07T09:00:00.123456"
    second = write_report(other, tmp_path)
    assert second.name == path.name.replace(".html", "_2.html")
    assert path.read_text(encoding="utf-8") == render_html(first)
    assert second.read_text(encoding="utf-8") == render_html(other)
    # Writing a report again replaces the file of the same run.
    assert write_report(other, tmp_path) == second
    assert write_report(first, tmp_path) == path
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([path.name, second.name])


def test_write_report_replaces_a_report_without_a_run_mark(tmp_path):
    # Reports from 0.1.1 and earlier have no mark, so the run they belong to is not known.
    v = sample_view()
    name = f"ahd_luma.prod_2026-11-07_run{v['run']['id']}.html"
    (tmp_path / name).write_text("<!doctype html><title>old</title>", encoding="utf-8")
    assert write_report(v, tmp_path) == tmp_path / name
    assert (tmp_path / name).read_text(encoding="utf-8") == render_html(v)


def test_write_report_passes_a_folder_of_the_same_name(tmp_path):
    v = sample_view()
    stem = f"ahd_luma.prod_2026-11-07_run{v['run']['id']}"
    (tmp_path / f"{stem}.html").mkdir()
    assert write_report(v, tmp_path).name == f"{stem}_2.html"


def test_write_report_sanitizes_rsid(tmp_path):
    v = sample_view()
    v["run"]["rsid"] = "../evil/rs"
    path = write_report(v, tmp_path)
    assert path.parent == tmp_path
    assert "/" not in path.name and ".." not in path.name


def test_individual_pairs_in_header_and_data():
    v = sample_view()
    html = render_html(v)
    assert re.search(r"<dt>Individual component</dt><dd>Page by Orders</dd>", html)
    assert '<select id="pair-dimension"></select>' in html
    assert '<select id="pair-metric"></select>' in html
    assert f"ahd individual --run {v['run']['id']} --dimension" in html
    assert "Add the same <code>--profile</code> or <code>--db</code> option" in html
    [pair] = _data_json(html)["pairs"]
    assert pair["dimension_name"] == "Page" and pair["metric_name"] == "Orders"
    v["pairs"].append({**pair, "pair": 2, "dimension_name": "Search", "metric_name": "Carts"})
    v["pairs"].append({**pair, "pair": 3})
    assert "<dd>Page by Orders, and 2 more pairs</dd>" in render_html(v)
    v["pairs"].pop()
    assert "<dd>Page by Orders, and 1 more pair</dd>" in render_html(v)


def test_individual_label_without_pairs_or_names():
    from ahd.render.html import individual_label, pair_label

    assert individual_label([]) == "the chosen dimension and metric"
    assert pair_label({"dimension": None, "metric": "m"}) == "the chosen dimension and metric"
    assert pair_label({"dimension": "variables/x", "metric": "metrics/y"}) == (
        "variables/x by metrics/y"
    )


def test_issue_headings_for_known_codes():
    for code in (
        "unauthorized_metric",
        "network_error",
        "sign_in_unavailable",
        "anomaly_probe_failed",
        "no_rows",
        "anomaly_not_allowed",
        "no_custom_components",
    ):
        assert ISSUE_HEADINGS[code] == issue_heading(code)
    assert issue_heading("unauthorized_metric") == (
        "Some metrics or dimensions are not visible to this credential."
    )
    assert issue_heading("network_error") == (
        "Some requests failed because of network problems. Running again may fix this."
    )
    for code in ("http_400", "http_404", "http_500", "http_503"):
        assert issue_heading(code) == "Adobe returned an error for some requests."
    assert issue_heading("http_") is None and issue_heading("weird_code") is None
    html = render_html(sample_view())  # the sample has one unauthorized_metric issue
    assert "Some metrics or dimensions are not visible to this credential." in html
    assert "metrics/event9" in html  # entries with a component id are still listed


def test_csp_meta_is_first_in_head():
    html = render_html(sample_view())
    assert CSP == (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'"
    )
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    assert meta.replace("'", "&#39;") in html or meta in html
    assert html.index("Content-Security-Policy") < html.index("<style>")


def test_breakdown_notes_under_tables():
    html = render_html(sample_view())
    assert BREAKDOWN_NOTES["browser"] == (
        "Rows are the top 10 values in the first column (Google), as in the Excel version."
    )
    assert BREAKDOWN_NOTES["device"] == (
        "Rows are the top 10 values in the first column (Other), as in the Excel version."
    )
    for kind in ("browser", "device"):
        note = BREAKDOWN_NOTES[kind]
        assert html.index(f'<div id="{kind}"></div>') < html.index(note)


def test_status_badge_has_icon_and_label():
    html = render_html(sample_view())  # sample run is partial
    assert re.search(
        r'<span class="badge badge-flag"><span class="icon" aria-hidden="true">!</span>'
        r"Complete, with some gaps",
        html,
    )
    v = sample_view()
    v["run"]["status"] = "ok"
    html = render_html(v)
    assert (
        '<span class="badge badge-good"><span class="icon" aria-hidden="true">✓</span>Complete'
        in html
    )


def test_page_has_tiles_flag_filter_and_sortable_tables():
    html = render_html(sample_view())
    assert '<section class="tiles" id="tiles"' in html
    assert html.index('id="tiles"') < html.index('id="status"')
    assert '<input type="checkbox" id="flagged-only" autocomplete="off">' in html
    assert "aria-sort" in html and "Original order" in html


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
    assert ':root[data-theme="dark"]' in html  # a dark choice beats a light computer
    assert "localStorage" in html and "try {" in html


def test_dimension_chart_has_type_filter():
    html = render_html(sample_view())
    for value, label in (("", "All"), ("evars", "eVars"), ("props", "Props"), ("other", "Other")):
        assert f'<option value="{value}">{label}</option>' in html
    # The page uses the stored kind. It does not move eVars by group.
    assert "kindOf" not in html
    assert "r.kind === kind" in html


def test_component_drop_warning_is_shown_and_escaped():
    v = sample_view()
    assert v["component_drops"] == []
    assert 'id="component-drops"' not in render_html(v)
    v["component_drops"] = [{"message": "This run sees 7 eVars. <b>Run 1</b> saw 10."}]
    html = render_html(v)
    assert 'id="component-drops"' in html
    assert "fewer components than the run before it" in html
    assert "&lt;b&gt;Run 1&lt;/b&gt;" in html
    assert "product profile of the credential" in html


def test_metric_type_filter_and_currency_data():
    v = sample_view()
    html = render_html(v)
    # The page adds one choice per metric group, so the template holds only "All".
    start = html.index('<select id="metric-type">')
    select = html[start : html.index("</select>", start)]
    assert select.count("<option") == 1 and '<option value="">All</option>' in select
    data = _data_json(html)
    assert "currency" in data["run"]
    assert all("metric_type" in r for r in data["views"]["week"])
    assert all("metric_type" in a for a in data["alerts"])


def test_change_alert_slider():
    v = sample_view()
    html = render_html(v)
    assert (
        '<input type="range" id="change-alert" min="-100" max="-10" step="5" value="-30" '
        'aria-valuetext="-30%"'
    ) in html
    assert '<output for="change-alert" id="change-alert-value">-30%</output>' in html
    assert "flag changes below</label>" in html and "from the weekday average</span>" in html
    # One line under the slider, and the full rule in a section that opens.
    assert (
        '<p class="hint" id="change-alert-hint">The weekday average is the average of the same '
        "weekday in the 12 weeks before the day.</p>"
    ) in html
    rule = html[html.index('<details class="rule"') : html.index("</details>")]
    assert "<summary>How large drops and spikes are found</summary>" in rule
    assert "weekday average under 10 are skipped" in rule
    v["change_alert"] = 0.45
    html = render_html(v)
    assert 'value="-45"' in html and ">-45%</output>" in html
    data = _data_json(html)
    assert data["change_alert"] == 0.45 and data["min_usual"] == 10


def test_copy_buttons():
    v = sample_view()
    assert v["issues"]
    html = render_html(v)
    assert '<button type="button" id="copy-alerts">Copy alerts</button>' in html
    assert '<button type="button" id="copy-problems">Copy problems</button>' in html
    assert "An email program such as Outlook or Gmail pastes the table" in html
    v["issues"] = []
    assert 'id="copy-problems"' not in render_html(v)


def test_every_form_control_has_a_name():
    html = render_html(sample_view())
    textarea = re.search(r"<textarea[^>]*>", html).group(0)
    assert 'aria-label="Alert summary as text"' in textarea


def test_currency_values_keep_the_currency_decimals():
    # report.js formats money with the currency's own decimals, e.g. none for yen. A fixed
    # digits option would show "¥0.00". Checked in Chrome for USD, JPY, KWD and no code.
    js = (Path(__file__).parent.parent / "src/ahd/render/static/report.js").read_text(
        encoding="utf-8"
    )
    assert "var money = currencyFmt({});" in js


def _plain(v):
    """A build_view value as the page sees it after unpacking: 2.0 and 2 are the same."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return float(v) if isinstance(v, int) and not isinstance(v, bool) else v


ROW_KEYS = (
    "name",
    "kind",
    "group",
    "number",
    "metric_type",
    "unused",
    "value",
    "baseline",
    "pct",
    "zero",
    "anomaly",
)


def test_packed_page_data_unpacks_to_the_view():
    view = sample_view()
    data = _data_json(render_html(view))
    for key, rows in view["views"].items():
        assert [{k: _plain(r[k]) for k in r} for r in data["views"][key]] == _plain(rows), key
    for packed, original in zip(data["alerts"], view["alerts"], strict=True):
        assert _plain(packed) == _plain(original)
    for cid, points in view["series"].items():
        assert _plain(data["series"][cid]) == _plain(points), cid
    assert data["calendar"] == view["calendar"]


def test_packed_calendar_names_components_by_place():
    from ahd.render.html import pack_view

    view = {
        "views": {"week": [{"component_id": c, **dict.fromkeys(ROW_KEYS)} for c in ("a", "b")]},
        "calendar": [
            {"day": "2026-11-06", "high": ["b"], "low": [], "zero": ["a", "x"], "alerts": 3}
        ],
    }
    packed = pack_view(view)
    assert packed["calendar"] == [["2026-11-06", [1], [], [0, "x"], 3]]
    assert packed["packed"]["calendar"] == ("day", "high", "low", "zero", "alerts")


def test_packed_page_data_is_smaller():
    view = sample_view()
    plain = len(json.dumps(view, separators=(",", ":")))
    m = DATA_RE.search(render_html(view))
    assert len(m.group(1)) < plain * 0.6


def test_packed_series_keeps_missing_days_out():
    from ahd.render.html import pack_view

    view = {
        "views": {},
        "alerts": [],
        "series": {
            "metrics/a": [
                ["2026-11-01", 1.0, None, None, None, None],
                ["2026-11-02", None, None, None, None, None],  # a point with no value
                ["2026-11-04", 4.0, 3.0, 5.0, 2.0, "high"],  # 2026-11-03 is missing
            ]
        },
    }
    packed = pack_view(view)
    assert packed["series"]["metrics/a"]["gaps"] == [2]
    assert packed["series"]["metrics/a"]["value"] == [1, None, None, 4]
    assert _unpack(json.loads(json.dumps(packed)))["series"] == view["series"]
    view["series"]["metrics/a"].pop()
    assert "gaps" not in pack_view(view)["series"]["metrics/a"]


def _view_with_date_given(given: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store, rid = sample_store(Path(tmp) / "s.duckdb")
        try:
            store.con.execute("UPDATE runs SET date_given = ? WHERE run_id = ?", [given, rid])
            return build_view(store, rid)
        finally:
            store.con.close()


def test_a_run_made_with_date_names_its_days():
    view = _view_with_date_given(True)
    today = date.fromisoformat(view["run"]["today"])
    last = (today - timedelta(days=1)).isoformat()
    html = render_html(view)
    assert view["run"]["date_given"] is True
    assert (
        '<p class="hint" id="date-note"><strong>Run date set with --date.</strong> Every day in '
        f"this report counts back from the run date, so the last day is {last}.</p>"
    ) in html
    assert f"Top 100 values on {last}" in html
    assert f"Top 10 values on {last} for each major browser group." in html
    assert f"Top 10 values on {last} for each device type." in html
    assert f'aria-label="{last} at a glance"' in html
    assert f"This week is the 7 days ending on {last}." in html
    assert f'title="The report compares one week: the 7 days ending on {last}."' in html
    assert "Top 100 values yesterday" not in html


def test_a_normal_run_says_yesterday():
    view = _view_with_date_given(False)
    html = render_html(view)
    assert view["run"]["date_given"] is False
    assert "Run date set with --date" not in html and 'id="date-note"' not in html
    assert "Top 100 values yesterday" in html
    assert 'aria-label="Yesterday at a glance"' in html
    assert WEEK_NOTE in html


def test_earlier_runs_hint_names_the_segment():
    v = sample_view()
    v["run"]["segment_id"] = "seg1"
    assert "of this report suite and segment in the database" in render_html(v)
    v["run"]["segment_id"] = None
    html = render_html(v)
    assert "of this report suite in the database, newest first, up to 30 runs." in html


# ---- the anomaly method ---------------------------------------------------------


def _fact(html: str) -> str:
    return re.search(r"<div><dt>Anomaly detection</dt><dd>(.*?)</dd></div>", html).group(1)


def test_the_header_names_the_anomaly_method():
    assert _fact(render_html(sample_view())) == "Adobe"
    for method, label in (
        ("weekday-median", "Weekday median"),
        ("weekday-iqr", "Weekday quartile range"),
    ):
        for bands in (True, False):
            assert _fact(render_html(sample_view(bands, method))) == (
                f'{label} <span class="muted">(computed by ahd from the 92 days of this run)</span>'
            )
    # The run has the issue anomaly_not_allowed, so Adobe's detection was not available.
    assert _fact(render_html(sample_view(bands=False))) == (
        'Adobe <span class="muted">(not available for this run)</span>'
    )
    view = sample_view()
    view["issues"].append(
        {
            "scope": "permissions",
            "component_id": None,
            "code": "anomaly_probe_failed",
            "message": "m",
        }
    )
    assert "(not available for this run)" in _fact(render_html(view))


def _rule(html: str) -> str:
    return html[html.index('<details class="rule"') : html.index("</details>")]


def test_the_rule_list_states_the_rule_of_the_method():
    for method in ANOMALY_METHODS:
        view = sample_view(method=method)
        rule = _rule(render_html(view))
        assert f"<li>{escape(view['anomaly_method']['rule'])}" in rule
        needs = (
            "A day is flagged only when this run has at least 4 earlier values of its weekday "
            "and its expected value is 10 or more."
        )
        assert (needs in rule) is (method != "adobe")


def test_a_local_method_does_not_say_adobe_flagged_a_value():
    adobe = render_html(sample_view())
    assert "A value that Adobe flagged is listed as an unusual value" in adobe
    assert "more components that Adobe flagged or that dropped to zero" in adobe
    assert "Components that Adobe flagged as unusual" in adobe
    for method in ("weekday-median", "weekday-iqr"):
        html = render_html(sample_view(bands=False, method=method))
        label = ANOMALY_LABELS[method]
        markup = html[: html.index('<script type="application/json"')]
        assert "Adobe flagged" not in markup
        assert f"A value that the {label} method flagged is listed as an unusual value" in markup
        assert f"more components that the {label} method flagged or that dropped" in markup
        assert f"Components that the {label} method flagged as unusual" in markup


def test_a_local_method_has_its_own_note_for_a_chart_without_a_range():
    assert NO_BANDS_NOTE in render_html(sample_view())
    html = render_html(sample_view(method="weekday-median"))
    assert NO_BANDS_NOTE not in html
    assert (
        "This chart has no expected range, because no day has at least 4 earlier values of its "
        "weekday in this run."
    ) in html


def test_a_local_method_says_it_does_not_need_adobes_anomaly_detection():
    blocked = render_html(sample_view(bands=False))
    assert str(escape(ISSUE_HEADINGS["anomaly_not_allowed"])) in blocked
    html = render_html(sample_view(bands=False, method="weekday-iqr"))
    assert str(escape(ISSUE_HEADINGS["anomaly_not_allowed"])) not in html
    assert (
        "Adobe&#39;s anomaly detection is not available, because the API credential&#39;s "
        "product profile does not include the Anomaly Detection permission (Report Suite Tools). "
        "This report does not need it, because it uses the Weekday quartile range method."
    ) in html
    view = sample_view(method="weekday-median")
    view["issues"].append(
        {
            "scope": "permissions",
            "component_id": None,
            "code": "anomaly_probe_failed",
            "message": "m",
        }
    )
    assert (
        "Adobe&#39;s anomaly detection could not be tested, because the test request failed. "
        "This report does not need it, because it uses the Weekday median method."
    ) in render_html(view)
