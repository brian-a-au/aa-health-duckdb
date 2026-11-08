"""ahd run with several report suites: --rsid more than once, or --all-suites."""

import json

import pytest
from fakes import ApiError, FakeClient, is_anomaly_trend, suite

from ahd.cli import main
from ahd.store import Store

CONFIG = {"org_id": "o@AdobeOrg", "client_id": "cid-12345", "secret": "sec-67890", "scopes": "x"}
METRICS = [{"id": f"metrics/event{i}", "name": f"Event {i}"} for i in (1, 2, 3)] + [
    {"id": "metrics/occurrences", "name": "Occurrences", "type": "int"}
]
DIMS = [
    {"id": "variables/prop1", "name": "Prop 1", "allowedForReporting": True},
    {"id": "variables/page", "name": "Page", "allowedForReporting": True},
]
SUITES = [("rs1", "Alpha Suite"), ("rs2", "Beta Suite"), ("rs3", "Gamma Suite")]


def fake(suites=SUITES, posts=None, broken=()):
    """A client that sees these suites. A suite in broken fails when its details are read."""
    gets = {
        "/reportsuites/collections/suites": {
            "content": [{"rsid": r, "name": n} for r, n in suites],
            "lastPage": True,
        },
        "/metrics": METRICS,
        "/dimensions": DIMS,
    }
    for rsid, name in suites:
        detail = suite(rsid, name)
        if rsid in broken:
            detail = ApiError(500, "Adobe had a problem", "server_error")
        gets[f"/reportsuites/collections/suites/{rsid}"] = detail
    return FakeClient(gets=gets, posts=posts)


@pytest.fixture
def run(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(CONFIG), encoding="utf-8")

    def go(client, *options):
        argv = ["run", "--config", str(cfg), "--db", str(tmp_path / "t.duckdb")]
        argv += ["--date", "2026-11-07", "--out", str(tmp_path / "out"), *options]
        return main(argv, client_factory=lambda creds, company_id=None: client)

    return go


def stored(tmp_path):
    store = Store.open(tmp_path / "t.duckdb", read_only=True)
    try:
        return [(r["rsid"], r["status"]) for r in reversed(store.list_runs())]
    finally:
        store.con.close()


def test_rsid_more_than_once_checks_each_suite(run, tmp_path, capsys):
    client = fake()
    assert run(client, "--rsid", "rs1", "--rsid", "Beta Suite") == 0
    out = capsys.readouterr().out
    assert stored(tmp_path) == [("rs1", "ok"), ("rs2", "ok")]
    assert len(list((tmp_path / "out").glob("*.html"))) == 2
    assert "Report suite 1 of 2: rs1" in out and "Report suite 2 of 2: rs2" in out
    assert "Run 1 for rs1 finished with status: ok" in out
    assert "Run 2 for rs2 finished with status: ok" in out
    assert out.count("Alerts for 2026-11-06: ") == 2
    assert out.rstrip().endswith("Checked 2 report suites: 2 ok, 0 partial, 0 failed.")
    # The suite list is read once, not once for each suite.
    lists = [c for c in client.calls if c[1] == "/reportsuites/collections/suites"]
    assert len(lists) == 1


def test_all_suites_checks_every_suite_the_credential_sees(run, tmp_path):
    assert run(fake(), "--all-suites") == 0
    assert [r for r, _ in stored(tmp_path)] == ["rs1", "rs2", "rs3"]


def test_the_same_suite_twice_is_checked_once(run, tmp_path, capsys):
    assert run(fake(), "--rsid", "rs2", "--rsid", "beta suite") == 0
    assert stored(tmp_path) == [("rs2", "ok")]
    out = capsys.readouterr().out
    assert "Report suite" not in out and "Checked" not in out


def test_a_failed_suite_does_not_stop_the_others(run, tmp_path, capsys):
    assert run(fake(broken={"rs2"}), "--all-suites") == 1
    captured = capsys.readouterr()
    assert [r for r, _ in stored(tmp_path)] == ["rs1", "rs3"]
    assert "Error for rs2: Adobe API error 500 [server_error]" in captured.err
    assert "Checked 3 report suites: 2 ok, 0 partial, 1 failed." in captured.out


def test_a_partial_suite_gives_exit_code_3(run, tmp_path, capsys):
    def rs2_anomaly(body):
        return is_anomaly_trend(body) and body.get("rsid") == "rs2"

    client = fake(posts=[(rs2_anomaly, ApiError(403, "no", "invalid_access"))])
    assert run(client, "--rsid", "rs1", "--rsid", "rs2") == 3
    assert stored(tmp_path) == [("rs1", "ok"), ("rs2", "partial")]
    assert "1 ok, 1 partial, 0 failed" in capsys.readouterr().out


def test_a_wrong_name_stops_before_any_run(run, tmp_path, capsys):
    client = fake()
    assert run(client, "--rsid", "rs1", "--rsid", "No Such Suite") == 1
    assert "No report suite has the ID or name 'No Such Suite'" in capsys.readouterr().err
    assert stored(tmp_path) == [] and client.posted() == []


def test_all_suites_without_suites_is_an_error(run, capsys):
    assert run(fake(suites=[]), "--all-suites") == 1
    assert "sees no report suites" in capsys.readouterr().err


@pytest.mark.parametrize("options", [[], ["--rsid", "rs1", "--all-suites"]])
def test_rsid_or_all_suites_is_needed_but_not_both(run, capsys, options):
    with pytest.raises(SystemExit) as e:
        run(fake(), *options)
    assert e.value.code == 2
    assert "--rsid" in capsys.readouterr().err


def test_a_network_error_in_one_suite_shows_only_its_type(run, tmp_path, capsys):
    import requests

    client = fake()
    client.gets["/reportsuites/collections/suites/rs1"] = lambda params: (_ for _ in ()).throw(
        requests.ConnectionError("https://analytics.adobe.io/secret-path")
    )
    assert run(client, "--rsid", "rs1", "--rsid", "rs2") == 1
    err = capsys.readouterr().err
    assert "Error for rs1: the request failed with a network error (ConnectionError)." in err
    assert "secret-path" not in err
    assert stored(tmp_path) == [("rs2", "ok")]


def test_alert_line_counts_each_kind(tmp_path, capsys):
    from datetime import date, timedelta

    from ahd.api.reports import Column, DailyValue
    from ahd.catalog import Component
    from ahd.cli import _alert_line

    store = Store.open(tmp_path / "a.duckdb")
    rid = store.create_run("rs1", "S", None, date(2026, 11, 7), "UTC", "0")
    comps = [
        Component(
            f"metrics/event{i}", f"E{i}", "metric", "custom_events", i, Column("c", "c"), True
        )
        for i in (1, 2, 3)
    ]
    store.add_components(rid, comps)
    NONE = (None, None, None, None)
    values = []
    day = date(2026, 8, 8)
    while day <= date(2026, 11, 7):
        friday = day.weekday() == 4
        y = day == date(2026, 11, 6)
        values += [
            DailyValue("metrics/event1", day, 0.0 if y else (100.0 if friday else 0.0), *NONE),
            DailyValue("metrics/event2", day, 50.0 if y else (100.0 if friday else 0.0), *NONE),
            DailyValue("metrics/event3", day, 40.0 if y else (100.0 if friday else 0.0), *NONE),
        ]
        day += timedelta(days=1)
    store.add_daily_values(rid, values)
    store.finish_run(rid, "ok")
    line = _alert_line(store, rid, 0.30)
    assert line == "Alerts for 2026-11-06: 0 unusual values, 2 large drops, 1 drop to zero"
    assert _alert_line(store, rid, 0.55) == (
        "Alerts for 2026-11-06: 0 unusual values, 1 large drop, 1 drop to zero"
    )
