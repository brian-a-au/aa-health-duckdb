import json
from datetime import date
from pathlib import Path

from ahd.api.reports import (
    Column,
    breakdown_body,
    parse_breakdown,
    parse_ranked,
    parse_trend,
    top_items_body,
    trend_body,
)

FIX = Path(__file__).parent / "fixtures"
RANGE = "2026-08-08T00:00:00.000/2026-11-08T00:00:00.000"


def load(name):
    return json.loads((FIX / name).read_text())


def test_trend_body_exists_column_shape():
    cols = [Column("metrics/event1", "metrics/event1"),
            Column("variables/prop1", "metrics/occurrences", exists_dimension="variables/prop1")]
    b = trend_body("rs", RANGE, cols, None, True)
    assert b["rsid"] == "rs"
    assert b["dimension"] == "variables/daterangeday"
    assert b["settings"] == {"limit": 400, "dimensionSort": "asc", "includeAnomalyDetection": True}
    mc = b["metricContainer"]
    assert [m["columnId"] for m in mc["metrics"]] == ["0", "1"]
    assert "filters" not in mc["metrics"][0]
    assert mc["metrics"][1]["id"] == "metrics/occurrences"
    assert mc["metrics"][1]["filters"] == ["f1"]
    assert mc["metricFilters"] == [{
        "id": "f1", "type": "segment",
        "segmentDefinition": {"func": "segment", "version": [1, 0, 0], "container": {
            "func": "container", "context": "hits",
            "pred": {"func": "exists", "val": {"func": "attr", "name": "variables/prop1"}}}}}]
    assert len(b["globalFilters"]) == 1 and b["globalFilters"][0]["type"] == "dateRange"
    assert b["globalFilters"][0]["dateRange"] == RANGE


def test_trend_body_global_segment_appended():
    b = trend_body("rs", RANGE, [Column("metrics/a", "metrics/a")], "s123", False)
    assert b["globalFilters"][-1] == {"type": "segment", "segmentId": "s123"}
    assert b["settings"]["includeAnomalyDetection"] is False


def test_top_items_body():
    b = top_items_body("rs", RANGE, "variables/page", "metrics/occurrences", "s1")
    assert b["dimension"] == "variables/page"
    assert b["settings"] == {"countRepeatInstances": True, "limit": 100, "page": 0}
    m = b["metricContainer"]["metrics"][0]
    assert m["id"] == "metrics/occurrences" and m["sort"] == "desc"
    assert b["globalFilters"][-1] == {"type": "segment", "segmentId": "s1"}


def test_breakdown_body_browser_ids():
    b = breakdown_body("rs", "r", "variables/page", "metrics/occurrences",
                       "variables/browsertype", ["8", "2", "6", "7", "1"], None)
    mc = b["metricContainer"]
    assert [f["itemId"] for f in mc["metricFilters"]] == ["8", "2", "6", "7", "1"]
    assert all(f["type"] == "breakdown" and f["dimension"] == "variables/browsertype"
               for f in mc["metricFilters"])
    assert [m["sort"] for m in mc["metrics"] if "sort" in m] == ["desc"]
    assert "sort" in mc["metrics"][0]
    assert [m["filters"] for m in mc["metrics"]] == [[f["id"]] for f in mc["metricFilters"]]
    assert b["settings"]["limit"] == 10


def test_parse_trend_reads_bounds():
    cols = [Column(f"metrics/m{i}", f"metrics/m{i}") for i in range(3)]
    vals, errs = parse_trend(load("trend_anomaly.json"), cols)
    assert errs == []
    assert len(vals) == 92 * 3
    c0 = [v for v in vals if v.component_id == "metrics/m0"]
    assert len(c0) == 92
    assert c0[0].day == date(2026, 8, 8) and c0[-1].day == date(2026, 11, 7)
    assert c0[0].value == 100.0 and c0[0].expected == 98.5
    assert c0[0].upper == 110.0 and c0[0].lower == 90.0
    assert all(isinstance(v.anomaly, bool) for v in vals)
    assert c0[0].anomaly is True and c0[1].anomaly is False


def test_parse_trend_column_error_gives_no_zeros():
    cols = [Column("metrics/ok", "metrics/ok"), Column("metrics/bad", "metrics/bad")]
    vals, errs = parse_trend(load("trend_206.json"), cols)
    assert not [v for v in vals if v.component_id == cols[1].component_id]
    assert len([v for v in vals if v.component_id == "metrics/ok"]) == 92
    assert len(errs) == 1
    assert errs[0].component_id == "metrics/bad"
    assert errs[0].error_code == "unauthorized_metric"
    assert "access" in errs[0].message


def test_parse_trend_without_anomaly_fields():
    cols = [Column("metrics/ok", "metrics/ok"), Column("metrics/bad", "metrics/bad")]
    vals, _ = parse_trend(load("trend_206.json"), cols)
    assert all(v.expected is None and v.upper is None and v.lower is None and v.anomaly is None
               for v in vals)


def test_parse_trend_date_falls_back_to_item_id():
    resp = {"columns": {"columnIds": ["0"]},
            "rows": [{"itemId": "1251028", "value": "garbage", "data": [3.0]}]}
    vals, _ = parse_trend(resp, [Column("metrics/a", "metrics/a")])
    assert vals[0].day == date(2025, 10, 28)


def test_parse_ranked():
    rows, total = parse_ranked(load("top_items.json"))
    assert total == 250
    assert rows[0] == (1, "Home Page", 5000.0)
    assert rows[2] == (3, "<img src=x onerror=alert(1)>", 900.0)


def test_parse_breakdown():
    rows, totals = parse_breakdown(load("breakdown_browser.json"))
    assert rows[0] == ("Home Page", [10.0, 20.0, 0.0, 5.0, 1.0])
    assert totals == [14.0, 23.0, 2.0, 6.0, 1.0]
