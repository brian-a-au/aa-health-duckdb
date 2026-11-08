import pytest
from fakes import FakeClient

from ahd.config import ConfigError
from ahd.lookup import list_segments, list_suites, resolve_segment, resolve_suite


def paged(pages):
    """A responder that answers page N with pages[N]."""

    def answer(params):
        n = params["page"]
        return {"content": pages[n], "totalPages": len(pages), "lastPage": n == len(pages) - 1}

    return answer


SUITES = [
    [{"rsid": "rs_b", "name": "beta"}, {"rsid": "rs_a", "name": "Alpha"}],
    [{"rsid": "rs_c", "name": "Alpha"}, {"rsid": "rs_d", "name": "Delta"}, {"name": "no id"}],
]


def client():
    return FakeClient(
        gets={
            "/reportsuites/collections/suites": paged(SUITES),
            "/segments": paged([[{"id": "s1", "name": "Mobile"}, {"id": "s2", "name": "Paid"}]]),
        }
    )


def test_list_suites_reads_every_page_and_sorts_by_name():
    c = client()
    assert [s["id"] for s in list_suites(c)] == ["rs_a", "rs_c", "rs_b", "rs_d"]
    pages = [p["page"] for m, path, p in c.calls]
    assert pages == [0, 1] and all(p["limit"] == 1000 for _, _, p in c.calls)


def test_list_segments_asks_for_the_suite_and_shared_segments():
    c = client()
    assert [s["name"] for s in list_segments(c, "rs_a")] == ["Mobile", "Paid"]
    assert c.calls[0][2]["rsids"] == "rs_a" and c.calls[0][2]["includeType"] == "all"


def test_empty_answer_is_one_page():
    c = FakeClient(gets={"/reportsuites/collections/suites": None})
    assert list_suites(c) == [] and len(c.calls) == 1


def test_resolve_suite_by_id_or_name():
    assert resolve_suite(client(), "rs_d") == "rs_d"
    assert resolve_suite(client(), "  delta ") == "rs_d"


def test_resolve_suite_ambiguous_and_missing():
    with pytest.raises(ConfigError, match="More than one report suite is named 'alpha': rs_a"):
        resolve_suite(client(), "alpha")
    with pytest.raises(ConfigError, match="No report suite has the ID or name 'zzz'.*ahd suites"):
        resolve_suite(client(), "zzz")


def test_resolve_segment():
    assert resolve_segment(client(), "rs_a", "paid") == "s2"
    with pytest.raises(ConfigError, match="ahd segments --rsid rs_a"):
        resolve_segment(client(), "rs_a", "nope")


def comp_client():
    return FakeClient(
        gets={
            "/dimensions": [
                {"id": "variables/evar17", "name": "Internal Search Term"},
                {"id": "variables/page", "name": "Page"},
                {"id": "variables/prop1", "name": "Twin"},
                {"id": "variables/prop2", "name": "twin"},
                {"name": "no id"},
            ],
            "/metrics": [{"id": "metrics/cartadditions", "name": "Cart Additions", "type": "int"}],
        }
    )


@pytest.mark.parametrize(
    ("kind", "value", "found"),
    [
        ("dimension", "variables/evar17", "variables/evar17"),
        ("dimension", "evar17", "variables/evar17"),
        ("dimension", "EVAR17", "variables/evar17"),
        ("dimension", " internal search term ", "variables/evar17"),
        ("metric", "cartadditions", "metrics/cartadditions"),
        ("metric", "Cart Additions", "metrics/cartadditions"),
    ],
)
def test_resolve_component(kind, value, found):
    from ahd.lookup import resolve_component

    c = comp_client()
    r = resolve_component(c, "rs1", kind, value)
    assert r["id"] == found
    assert c.calls[0][2] == {"rsid": "rs1"}


def test_resolve_component_errors():
    from ahd.lookup import resolve_component

    with pytest.raises(ConfigError, match="More than one dimension is named 'TWIN'"):
        resolve_component(comp_client(), "rs1", "dimension", "TWIN")
    with pytest.raises(ConfigError, match="No metric in report suite rs1.*occurrences or event1"):
        resolve_component(comp_client(), "rs1", "metric", "nope")
    with pytest.raises(ConfigError, match="evar1 or page"):
        resolve_component(comp_client(), "rs1", "dimension", "nope")
