import json
from datetime import date
from pathlib import Path

import pytest

from ahd.api.reports import (
    Column,
    MalformedResponse,
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
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_trend_body_exists_column_shape():
    cols = [
        Column("metrics/event1", "metrics/event1"),
        Column("variables/prop1", "metrics/occurrences", exists_dimension="variables/prop1"),
    ]
    b = trend_body("rs", RANGE, cols, None, True)
    assert b["rsid"] == "rs"
    assert b["dimension"] == "variables/daterangeday"
    assert b["settings"] == {"limit": 400, "dimensionSort": "asc", "includeAnomalyDetection": True}
    mc = b["metricContainer"]
    assert [m["columnId"] for m in mc["metrics"]] == ["0", "1"]
    assert "filters" not in mc["metrics"][0]
    assert mc["metrics"][1]["id"] == "metrics/occurrences"
    assert mc["metrics"][1]["filters"] == ["f1"]
    assert mc["metricFilters"] == [
        {
            "id": "f1",
            "type": "segment",
            "segmentDefinition": {
                "func": "segment",
                "version": [1, 0, 0],
                "container": {
                    "func": "container",
                    "context": "hits",
                    "pred": {"func": "exists", "val": {"func": "attr", "name": "variables/prop1"}},
                },
            },
        }
    ]
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
    b = breakdown_body(
        "rs",
        "r",
        "variables/page",
        "metrics/occurrences",
        "variables/browsertype",
        ["8", "2", "6", "7", "1"],
        None,
    )
    mc = b["metricContainer"]
    assert [f["itemId"] for f in mc["metricFilters"]] == ["8", "2", "6", "7", "1"]
    assert all(
        f["type"] == "breakdown" and f["dimension"] == "variables/browsertype"
        for f in mc["metricFilters"]
    )
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
    assert all(
        v.expected is None and v.upper is None and v.lower is None and v.anomaly is None
        for v in vals
    )


def test_parse_trend_date_falls_back_to_item_id():
    resp = {
        "columns": {"columnIds": ["0"]},
        "rows": [
            # Adobe counts the month in an itemId from 0: 09 is October and 00 is January.
            {"itemId": "1250928", "value": "garbage", "data": [3.0]},
            {"itemId": "1260008", "value": "garbage", "data": [4.0]},
        ],
    }
    vals, _ = parse_trend(resp, [Column("metrics/a", "metrics/a")])
    assert [v.day for v in vals] == [date(2025, 10, 28), date(2026, 1, 8)]


def test_item_id_fallback_agrees_with_the_dates_adobe_sent():
    """Every row of a saved live answer gives the same day by its itemId as by its text."""
    rows = load("live_trend_anomaly.json")["rows"]
    cols = [Column("metrics/a", "metrics/a")]
    by_text, _ = parse_trend({"rows": rows}, cols)
    by_id, _ = parse_trend({"rows": [{**r, "value": "garbage"} for r in rows]}, cols)
    assert rows and [v.day for v in by_id] == [v.day for v in by_text]


@pytest.mark.parametrize(
    "resp",
    [
        [],
        {"rows": [{"itemId": "1260708", "value": "Aug 8, 2026", "data": [None]}]},
        {"rows": [{"data": [1]}]},
        {"rows": [{"itemId": "x", "value": "garbage", "data": [1]}]},
        {"rows": "text"},
        # A value that is not a number would else reach the database and end the run there.
        {"rows": [{"value": "Aug 8, 2026", "data": [1], "dataExpected": [{"a": 1}]}]},
        {"rows": [{"value": "Aug 8, 2026", "data": [1], "dataUpperBound": ["text"]}]},
        {"columns": {"columnErrors": [{"columnId": "0", "errorCode": {"a": 1}}]}},
    ],
)
def test_parse_trend_names_an_answer_of_the_wrong_shape(resp):
    with pytest.raises(MalformedResponse):
        parse_trend(resp, [Column("metrics/a", "metrics/a")])


def test_parse_ranked_and_breakdown_name_an_answer_of_the_wrong_shape():
    with pytest.raises(MalformedResponse):
        parse_ranked({"rows": [{"value": "x", "data": []}]})
    with pytest.raises(MalformedResponse):
        parse_breakdown({"rows": [{"value": "x", "data": ["text"]}]})
    with pytest.raises(MalformedResponse):
        parse_ranked({"rows": [{"value": {"a": 1}, "data": [1]}]})
    with pytest.raises(MalformedResponse):
        parse_breakdown({"rows": [{"value": ["x"], "data": [1]}]})


def test_a_number_where_text_belongs_is_read_as_text():
    assert parse_ranked({"rows": [{"value": 404, "data": [2]}]}) == ([(1, "404", 2.0)], 1)
    vals, errs = parse_trend(
        {
            "columns": {"columnErrors": [{"columnId": "0", "errorCode": None}]},
            "rows": [{"value": "Aug 8, 2026", "data": [1, 2], "dataExpected": ["1.5", None]}],
        },
        [Column("metrics/a", "metrics/a"), Column("metrics/b", "metrics/b")],
    )
    assert (errs[0].error_code, errs[0].message) == ("", "")
    assert (vals[0].component_id, vals[0].value, vals[0].expected) == ("metrics/b", 2.0, None)


def test_parse_ranked():
    rows, total = parse_ranked(load("top_items.json"))
    assert total == 250
    assert rows[0] == (1, "Home Page", 5000.0)
    assert rows[2] == (3, "<img src=x onerror=alert(1)>", 900.0)


def test_parse_breakdown():
    rows, totals = parse_breakdown(load("breakdown_browser.json"))
    assert rows[0] == ("Home Page", [10.0, 20.0, 0.0, 5.0, 1.0])
    assert totals == [14.0, 23.0, 2.0, 6.0, 1.0]


# Responses saved from a live run on Demo Data (dgeo1xxpnwcidluma) on 2026-11-07.
# They hold the extra column metadata the live API returns and the parsers ignore.


def test_live_trend_parses_with_anomaly_fields():
    cols = [
        Column("metrics/event25", "metrics/event25"),
        Column("variables/prop7", "metrics/occurrences", exists_dimension="variables/prop7"),
    ]
    values, errors = parse_trend(load("live_trend_anomaly.json"), cols)
    assert errors == []
    assert len(values) == 2 * 92
    by = {(v.component_id, v.day): v for v in values}
    assert by[("metrics/event25", date(2026, 11, 6))].value == 5136.0
    assert by[("variables/prop7", date(2026, 11, 6))].value == 28177.0
    assert min(v.day for v in values) == date(2026, 8, 8)
    assert max(v.day for v in values) == date(2026, 11, 7)
    assert all(
        v.expected is not None and v.upper is not None and v.lower is not None for v in values
    )


def test_live_top_items_parse():
    rows, total = parse_ranked(load("live_top_items.json"))
    assert len(rows) == 100
    assert rows[0][1] == "home"
    assert total == 1387


def test_live_browser_breakdown_parse():
    rows, totals = parse_breakdown(load("live_breakdown_browser.json"))
    assert len(rows) == 10
    assert all(len(vals) == 5 for _, vals in rows)
    assert totals == [213974.0, 65324.0, 83695.0, 12170.0, 56776.0]
