import json
from pathlib import Path

from ahd.api.reports import Column
from ahd.catalog import SOLUTION_GROUPS, build_catalog, load_solution

FIX = Path(__file__).parent / "fixtures"
METRICS = json.loads((FIX / "metrics.json").read_text())
DIMS = json.loads((FIX / "dimensions.json").read_text())


def catalog():
    return build_catalog(METRICS, DIMS, load_solution())


def by_id():
    return {c.id: c for c in catalog()}


def test_evar_instances_become_custom_evars():
    c = by_id()["metrics/evar5instances"]
    assert c.group == "custom_evars"
    assert c.number == 5
    assert c.name == "Internal Search Term"
    assert c.kind == "metric"
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
    assert c.kind == "metric"
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
