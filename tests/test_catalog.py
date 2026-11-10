import json
from pathlib import Path

from ahd.api.reports import Column
from ahd.catalog import SOLUTION_GROUPS, build_catalog, load_solution

FIX = Path(__file__).parent / "fixtures"
METRICS = json.loads((FIX / "metrics.json").read_text(encoding="utf-8"))
DIMS = json.loads((FIX / "dimensions.json").read_text(encoding="utf-8"))


def catalog():
    return build_catalog(METRICS, DIMS, load_solution())


def by_id():
    return {c.id: c for c in catalog()}


def test_evar_instances_become_custom_evars():
    c = by_id()["metrics/evar5instances"]
    assert c.group == "custom_evars"
    assert c.number == 5
    assert c.name == "Internal Search Term"
    # An eVar is a dimension, even though it is counted with its instances metric.
    assert c.kind == "dimension"
    assert c.column == Column("metrics/evar5instances", "metrics/evar5instances")


def test_custom_events_numbered():
    c = by_id()["metrics/event10"]
    assert (c.group, c.number) == ("custom_events", 10)


def test_props_use_exists_occurrences():
    c = by_id()["variables/prop1"]
    assert c.group == "custom_props"
    assert c.kind == "dimension"
    assert c.column.exists_dimension == "variables/prop1"
    assert c.column.metric_id == "metrics/occurrences"
    assert c.allowed_for_reporting is True
    assert by_id()["variables/prop2"].allowed_for_reporting is False


def test_dotted_prop_ids_skipped():
    assert "variables/prop1.1" not in by_id()


def test_page_always_present():
    c = by_id()["variables/page"]
    assert c.group == "Reserved Props"
    assert c.column.exists_dimension == "variables/page"
    ids = {x.id for x in build_catalog([], [], load_solution())}
    assert "variables/page" in ids


def test_solution_member_grouped():
    c = by_id()["metrics/pageviews"]
    assert c.group == "Reserved Traffic Events"
    assert c.column == Column("metrics/pageviews", "metrics/pageviews")


def test_unknown_standard_dropped():
    ids = by_id()
    assert "metrics/bouncerate" not in ids
    assert "variables/browser" not in ids


def test_solution_json_has_12_groups():
    sol = load_solution()
    assert list(sol) == SOLUTION_GROUPS
    assert len(sol) == 12
    assert "metrics/pageviews" in sol["Reserved Traffic Events"]


def test_solution_ids_belong_to_one_group():
    sol = load_solution()
    ids = [cid for members in sol.values() for cid in members]
    assert len(ids) == len(set(ids))
    # The workbook's "Video Ad" row points at videoadstart by mistake; its own range is videoad.
    assert "metrics/videoadstart" in sol["Heartbeat Events"]
    assert sol["Heartbeat Dimensions"][0] == "metrics/videoad"
    assert "metrics/videoadstart" not in sol["Heartbeat Dimensions"]


def test_order_custom_first_then_solution():
    groups = []
    for c in catalog():
        if c.group not in groups:
            groups.append(c.group)
    assert groups == [
        "custom_events",
        "custom_evars",
        "custom_props",
        "Reserved Traffic Events",
        "Reserved eVars",
        "List Variables",
        "Reserved Props",
        "Activity Map Instances",
        "AMO Integration Instances",
    ]
    nums = [c.number for c in catalog() if c.group == "custom_events"]
    assert nums == [2, 10]


def test_instances_fallback_and_group():
    c = by_id()["metrics/campaigninstances"]
    assert c.group == "Reserved eVars"
    assert c.kind == "dimension"
    assert c.column == Column("metrics/campaigninstances", "metrics/campaigninstances")
    assert "variables/campaign" not in by_id()
    assert by_id()["metrics/clickmaplinkinstances"].group == "Activity Map Instances"


def test_exact_id_wins_over_instances():
    metrics = METRICS + [{"id": "metrics/campaign", "name": "Campaign"}]
    ids = {c.id for c in build_catalog(metrics, DIMS, load_solution())}
    assert "metrics/campaign" in ids
    assert "metrics/campaigninstances" not in ids


def test_amo_member():
    c = by_id()["metrics/amo_clicks"]
    assert c.group == "AMO Integration Instances"
    assert c.column == Column("metrics/amo_clicks", "metrics/amo_clicks")


def test_list_variable_stays_exists_column():
    c = by_id()["variables/listvariable1"]
    assert c.group == "List Variables"
    assert c.column == Column(
        "variables/listvariable1", "metrics/occurrences", "variables/listvariable1"
    )


def test_metric_type_is_kept():
    from ahd.catalog import build_catalog

    metrics = [
        {"id": "metrics/event1", "name": "Revenue X", "type": "currency"},
        {"id": "metrics/event2", "name": "Units", "type": "decimal"},
        {"id": "metrics/evar1instances", "name": "Campaign Instances", "type": "int"},
        {"id": "metrics/event3", "name": "Odd", "type": 5},
    ]
    dims = [{"id": "variables/prop1", "name": "P1"}]
    types = {c.id: c.metric_type for c in build_catalog(metrics, dims, {})}
    assert types["metrics/event1"] == "currency"
    assert types["metrics/event2"] == "decimal"
    assert types["metrics/evar1instances"] == "int"
    assert types["metrics/event3"] is None
    assert types["variables/prop1"] is None


def test_entries_that_cannot_be_read_are_left_out():
    """An entry without a text ID, or one that is not an object, does not stop the catalog."""
    bad = [{"name": "no id"}, "text", None, 5, {"id": 5}, {"id": None}, {"id": ""}]
    cat = build_catalog(METRICS + bad, DIMS + bad, load_solution())
    assert [c.id for c in cat] == [c.id for c in catalog()]


def test_a_name_or_flag_of_another_type_is_not_used():
    metrics = [
        {"id": "metrics/event1", "name": 5},
        {"id": "metrics/evar1instances", "name": {"a": 1}},
        {"id": "metrics/evar2instances", "name": "Campaign Instances", "type": 7},
    ]
    dims = [
        {"id": "variables/prop1", "name": ["x"], "allowedForReporting": "yes"},
        {"id": "variables/page", "name": 3, "allowedForReporting": True},
    ]
    cat = {c.id: c for c in build_catalog(metrics, dims, load_solution())}
    assert cat["metrics/event1"].name == "metrics/event1"
    assert cat["metrics/evar1instances"].name == "metrics/evar1instances"
    assert cat["metrics/evar2instances"].name == "Campaign"
    assert cat["metrics/evar2instances"].metric_type is None
    assert cat["variables/prop1"].name == "variables/prop1"
    assert cat["variables/prop1"].allowed_for_reporting is None
    assert cat["variables/page"].name == "Page"
    assert cat["variables/page"].allowed_for_reporting is True
