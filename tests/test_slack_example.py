"""docs/examples/send_slack.py posts the alerts of ahd alerts to Slack. No test posts: each one
writes slack.json or replaces the request with a fake."""

import argparse
import http.client
import io
import json
import subprocess
import sys
import urllib.error
from datetime import date

import pytest
from test_examples import (
    EXAMPLES,
    MISSING,
    NEW_ONLY_TOO_OLD,
    WARNING,
    _db,
    _event_db,
    _load,
    _old_drop_db,
    _suites_db,
)

URL = "https://hooks.slack.com/services/T000/B000/secretpart"


@pytest.fixture
def slack(monkeypatch, tmp_path):
    """The script, run in tmp_path without a webhook URL."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    return _load("send_slack")


def _written(tmp_path):
    return json.loads((tmp_path / "slack.json").read_text(encoding="utf-8"))


def _tables(payload):
    return [b for b in payload["blocks"] if b["type"] == "table"]


def _texts(payload):
    """The text of every block that is not a table."""
    out = []
    for b in payload["blocks"]:
        if b["type"] in ("header", "section"):
            out.append(b["text"]["text"])
        elif b["type"] == "context":
            out += [e["text"] for e in b["elements"]]
    return out


def _alert(n, **kw):
    a = {
        "alert": "large drop",
        "name": f"Event {n}",
        "component_id": f"metrics/event{n}",
        "usual": 100.0,
        "value": 50.0,
        "pct_vs_usual": -0.5,
        "since": "2026-11-06",
        "days": 1,
        "at_least": False,
        "is_new": True,
        "event": None,
    }
    return a | kw


def _run(alerts, events=(), **kw):
    run = {
        "rsid": "rs1",
        "rsid_name": "Suite",
        "day": "2026-11-06",
        "method": "adobe",
        "method_name": "Adobe",
        "sensitivity": "normal",
        "events": list(events),
    }
    return run | {"alerts": alerts} | kw


def test_it_writes_the_messages_without_a_webhook(slack, tmp_path, capsys):
    db = _db(tmp_path, 5_000_000.0)
    assert slack.main([str(db), "rs1"]) == 0
    assert "Wrote slack.json with 1 messages and 1 alerts" in capsys.readouterr().out
    [payload] = _written(tmp_path)
    assert payload["text"] == "1 Analytics Health alerts for Suite (rs1) on 2026-11-06"
    texts = _texts(payload)
    assert texts[0] == "Suite: 1 alerts, 1 new"
    assert texts[1].startswith("Report suite rs1, alerts of 2026-11-06. A large drop is more ")
    assert "Adobe's anomaly detection flagged the unusual values." in texts[1]
    [table] = _tables(payload)
    rows = [[c["text"] for c in row] for row in table["rows"]]
    assert rows == [
        slack.HEADERS,
        [
            "large drop",
            "Big <event>",  # raw text: Slack shows it as it is
            "metrics/event1",
            "25,000,000",
            "5,000,000",
            "-80.0%",
            "new on 2026-11-06",
        ],
    ]
    assert all(c["type"] == "raw_text" for row in table["rows"] for c in row)
    assert [s.get("align") for s in table["column_settings"]][3:6] == ["right"] * 3


def test_the_table_has_the_columns_of_the_email(slack):
    assert slack.HEADERS == _load("send_alerts").HEADERS


def test_plain_text_shows_names_as_they_are(slack):
    """Plain text objects turn off emoji codes, and the notification text escapes markup, so a
    name from Adobe cannot mention a channel or make a link."""
    payload = slack.message(_run([_alert(1)], rsid_name="<!channel> & <https://x|y>"), "Rules.")
    assert payload["text"].startswith("1 Analytics Health alerts for &lt;!channel&gt; &amp; ")
    assert "<" not in payload["text"]
    header = payload["blocks"][0]["text"]
    assert header == {"type": "plain_text", "text": header["text"], "emoji": False}
    assert header["text"].startswith("<!channel> & ")


def test_nothing_to_send(slack, tmp_path, capsys):
    assert slack.main([str(_db(tmp_path, 24_000_000.0)), "rs1"]) == 2
    assert "Nothing to send" in capsys.readouterr().out
    assert not (tmp_path / "slack.json").exists()


def test_an_event_gets_one_line_and_the_table_lists_the_others(slack, tmp_path):
    assert slack.main([_event_db(tmp_path), "rs1"]) == 0
    [payload] = _written(tmp_path)
    texts = _texts(payload)
    assert texts[0] == "Suite: 13 alerts, 13 new"
    assert texts[2] == (
        "Suite-wide event: 12 of the 20 components that collected data had a value of 0 on "
        "2026-11-06. For all of them, this started on 2026-11-06, so it is new."
    )
    assert texts[3] == "Other alerts"
    assert texts[-1] == "ahd alerts --every-alert lists every alert."
    [table] = _tables(payload)
    assert [row[1]["text"] for row in table["rows"]] == ["Component", "Event 13"]


def test_an_event_without_other_alerts_has_no_table(slack):
    zeros = [_alert(n, alert="dropped to zero", event="zero") for n in range(12)]
    payload = slack.message(_run(zeros, [{"event": "zero", "text": "12 had 0."}]), "Rules.")
    assert _tables(payload) == []
    assert "Suite-wide event: 12 had 0." in _texts(payload)
    assert "Other alerts" not in _texts(payload)


def test_without_an_event_every_alert_is_in_the_table(slack, tmp_path):
    assert slack.main([_event_db(tmp_path, zeros=9), "rs1"]) == 0
    [payload] = _written(tmp_path)
    [table] = _tables(payload)
    assert len(table["rows"]) == 1 + 10
    assert not any(t.startswith("Suite-wide event") for t in _texts(payload))
    assert "ahd alerts --every-alert lists every alert." not in _texts(payload)


def test_the_table_lists_at_most_10_alerts_and_counts_the_others(slack):
    payload = slack.message(_run([_alert(n) for n in range(13)]), "Rules.")
    [table] = _tables(payload)
    assert len(table["rows"]) == 1 + slack.SHOWN
    assert "3 more alerts are not listed." in _texts(payload)
    assert "ahd alerts --every-alert lists every alert." in _texts(payload)


def test_the_cells_stay_within_the_limit_of_slack(slack):
    """Slack takes at most 10,000 characters in the cells of a message, and 150 in a header."""
    long = "x" * 500
    alerts = [_alert(n, name=long, component_id=long, alert=long) for n in range(20)]
    payload = slack.message(_run(alerts, rsid_name=long), "Rules.")
    [table] = _tables(payload)
    cells = [c["text"] for row in table["rows"] for c in row]
    assert max(len(c) for c in cells) == slack.CELL_LIMIT
    assert cells[8].endswith("...")
    assert sum(len(c) for c in cells) <= 10_000
    assert len(payload["blocks"][0]["text"]["text"]) == 150


def test_no_cell_is_empty(slack):
    """Slack refuses an empty text."""
    payload = slack.message(_run([_alert(1, name="", pct_vs_usual=None)]), "Rules.")
    cells = [c["text"] for row in _tables(payload)[0]["rows"] for c in row]
    assert "" not in cells
    assert cells[7 + 1] == "(empty)" and cells[7 + 5] == "Not known"


def test_a_url_with_a_space_is_not_printed(slack, tmp_path, monkeypatch, capsys):
    """urllib names the URL in the text of the error. The script names only the type."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T0/B0/sec ret")
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    captured = capsys.readouterr()
    assert "Could not reach Slack (InvalidURL)." in captured.err
    assert "sec ret" not in captured.err + captured.out


def test_since_and_change_cells(slack):
    payload = slack.message(
        _run(
            [
                _alert(1, is_new=False, since="2026-11-04", days=3),
                _alert(2, is_new=False, since="2026-11-05", days=1, at_least=True),
                _alert(3, alert="dropped to zero", value=0.0, pct_vs_usual=None, usual=None),
            ]
        ),
        "Rules.",
    )
    rows = [[c["text"] for c in row] for row in _tables(payload)[0]["rows"]]
    assert rows[1][6] == "since 2026-11-04, 3 days"
    assert rows[2][6] == "since 2026-11-05 or earlier, at least 1 day"
    assert rows[3][3:6] == ["No data", "0", "Not known"]
    assert _texts(payload)[0] == "Suite: 3 alerts, 1 new"


class _Slack:
    """A fake for urllib.request.urlopen. answers gives an error to raise for each post, or
    None for a post that Slack takes."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.posts = []

    def __call__(self, request, timeout):
        self.posts.append((request, timeout))
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            raise answer
        return io.BytesIO(b"ok")


def test_it_posts_one_message_for_each_suite(slack, tmp_path, monkeypatch, capsys):
    fake = _Slack()
    pauses = []
    monkeypatch.setattr(slack.urllib.request, "urlopen", fake)
    monkeypatch.setattr(slack.time, "sleep", pauses.append)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0, "rs2": 1.0, "rs3": 24_000_000.0})
    assert slack.main([db, "--all"]) == 0
    out = capsys.readouterr().out
    assert "Posted 2 messages with 2 alerts to Slack." in out
    assert "No alerts for rs3." in out
    assert URL not in out
    assert [r.full_url for r, _ in fake.posts] == [URL, URL]
    assert all(t == slack.SLACK_TIMEOUT == 30 for _, t in fake.posts)
    request = fake.posts[0][0]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json; charset=utf-8"
    assert json.loads(request.data)["text"].endswith("(rs1) on 2026-11-06")
    # One pause between the two messages, none before the first.
    assert pauses == [slack.PAUSE]
    assert not (tmp_path / "slack.json").exists()


def _http(code, body=b""):
    return urllib.error.HTTPError(URL, code, "error", {}, io.BytesIO(body))


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (_http(404, b"no_service"), "Slack refused the message (404 no_service)."),
        (_http(410, b"channel_is_archived"), "(410 channel_is_archived). Check SLACK_WEBHOOK_URL"),
        (_http(400, b"<html>" + URL.encode() + b"</html>"), "Slack refused the message (400)."),
        (_http(429), "Slack asked the script to send fewer messages (429)."),
        (urllib.error.URLError("no route " + URL), "Could not reach Slack (URLError)."),
        (TimeoutError(), "Slack did not answer within 30 seconds."),
        (http.client.InvalidURL(f"URL can't contain control characters. {URL!r}"), "(InvalidURL)"),
        (http.client.BadStatusLine(URL), "Could not reach Slack (BadStatusLine)."),
    ],
)
def test_it_says_why_slack_did_not_take_a_message(
    slack, tmp_path, monkeypatch, capsys, error, said
):
    fake = _Slack(error)
    monkeypatch.setattr(slack.urllib.request, "urlopen", fake)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0, "rs2": 1.0})
    assert slack.main([db, "rs1", "rs2"]) == 1
    captured = capsys.readouterr()
    assert said in captured.err
    assert "Posted 0 of 2 messages." in captured.err
    # It stops after the first message that Slack did not take, and never prints the URL.
    assert len(fake.posts) == 1
    assert "secretpart" not in captured.err + captured.out


def test_a_body_that_cannot_be_read_is_left_out(slack):
    error = _http(500)
    error.read = lambda n: (_ for _ in ()).throw(OSError())
    assert slack._slack_problem(error) == (
        "Slack refused the message (500). Check SLACK_WEBHOOK_URL."
    )


def test_it_refuses_a_webhook_without_encryption(slack, tmp_path, monkeypatch, capsys):
    fake = _Slack()
    monkeypatch.setattr(slack.urllib.request, "urlopen", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hooks.slack.com/services/x")
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    err = capsys.readouterr().err
    assert "SLACK_WEBHOOK_URL must start with https://." in err
    assert "services/x" not in err
    assert fake.posts == []


def test_it_posts_the_others_when_one_suite_is_missing(slack, tmp_path, capsys):
    db = _suites_db(tmp_path, {"rs1": 5_000_000.0})
    assert slack.main([db, "rs1", "rs_typo"]) == 1
    assert "holds no finished run of rs_typo" in capsys.readouterr().err
    # The alerts of rs1, then one message that names the missing suite.
    payloads = _written(tmp_path)
    assert len(payloads) == 2
    assert payloads[1]["text"] == "Analytics Health warning for rs_typo"


@pytest.mark.parametrize(
    "argv", [["db"], ["db", "rs1", "--all"], ["db", "rs1", "--threshold", "2"]]
)
def test_bad_arguments_print_usage_and_exit_with_1(slack, argv, capsys):
    with pytest.raises(SystemExit) as stop:
        slack.main(argv)
    assert stop.value.code == 1
    assert "usage:" in capsys.readouterr().err


def test_a_wrong_anomaly_method_is_named(slack, tmp_path, capsys):
    db = str(_db(tmp_path, 5_000_000.0))
    with pytest.raises(SystemExit) as stop:
        slack.main([db, "rs1", "--anomaly-method", "nope"])
    assert stop.value.code == 1
    assert "nope" in capsys.readouterr().err


def _answer(monkeypatch, slack, code, stdout, stderr=""):
    done = subprocess.CompletedProcess([], code, stdout, stderr)
    monkeypatch.setattr(slack.subprocess, "run", lambda *a, **kw: done)


def test_an_ahd_without_events_is_too_old(slack, monkeypatch, capsys):
    """ahd 0.1.12 gives no events. The script needs them, so it says to update."""
    old = {"runs": [_run([_alert(1)])]}
    del old["runs"][0]["events"]
    _answer(monkeypatch, slack, 0, json.dumps(old))
    assert slack.main(["db", "rs1"]) == 1
    assert "needs ahd 0.1.13 or newer" in capsys.readouterr().err


def test_an_ahd_without_the_alerts_command_is_named(slack, monkeypatch, capsys):
    _answer(monkeypatch, slack, 2, "", "ahd: error: argument command: invalid choice: 'alerts'")
    assert slack.main(["db", "rs1"]) == 1
    assert "older than 0.1.11" in capsys.readouterr().err


@pytest.mark.parametrize(("code", "stdout"), [(0, "not json"), (3, ""), (1, "")])
def test_other_answers_of_ahd_are_errors(slack, monkeypatch, code, stdout):
    _answer(monkeypatch, slack, code, stdout)
    assert slack.main(["db", "rs1"]) == 1


def test_without_ahd_it_says_how_to_get_it(slack, monkeypatch, capsys):
    monkeypatch.setattr(slack, "_ahd_command", lambda: None)
    assert slack.main(["db", "rs1"]) == 1
    assert "uv tool install aa-health-duckdb" in capsys.readouterr().err


def test_it_shares_the_options_and_texts_of_the_email_script(slack):
    """The two scripts are separate files, so each can be downloaded alone. Their options and
    the texts they share must stay the same."""
    mail = _load("send_alerts")

    def options(mod):
        return sorted(o for a in mod._parser()._actions for o in a.option_strings)

    assert options(slack) == options(mail)
    args = argparse.Namespace(
        database="db",
        threshold="0.275",
        spike_threshold="0.5",
        all=False,
        rsids=["rs1", "-rs2"],
        segment="s1",
        anomaly_method="weekday-trend",
        anomaly_sensitivity="high",
        new_only=True,
    )
    assert slack._ahd_options(args) == mail._ahd_options(args)
    for method in (("adobe", "Adobe", "normal"), ("weekday-iqr", "Weekday IQR", "low")):
        assert slack._flagged_by(*method) == mail._flagged_by(*method)
    for a in (_alert(1), _alert(2, is_new=False, days=4, at_least=True)):
        assert slack._since(a) == mail._since(a)
    assert slack._percent(0.275) == mail._percent(0.275) == "27.5%"
    assert slack._round(2.5) == mail._round(2.5) == 3


def test_the_script_writes_nothing_into_the_examples_folder(slack, tmp_path):
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 0
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".json"] == ["slack.json"]
    assert not (EXAMPLES / "slack.json").exists()


# ---- --new-only, the old-run warning, --always and --test ---------------------------------


def test_new_only_lists_only_the_new_alerts_and_says_so(slack, tmp_path):
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1", "--new-only"]) == 0
    [payload] = _written(tmp_path)
    assert payload["text"] == "1 new Analytics Health alerts for Suite (rs1) on 2026-11-06"
    assert _texts(payload)[0] == "Suite: 1 new alerts"


def test_new_only_with_only_old_alerts_says_no_new_alerts(slack, tmp_path, capsys):
    assert slack.main([str(_old_drop_db(tmp_path)), "rs1", "--new-only"]) == 2
    out = capsys.readouterr().out
    assert "No new alerts for rs1." in out and "Nothing to send." in out


def test_new_only_needs_a_newer_ahd(slack, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(slack, "_ahd_command", lambda: [sys.executable, "-c", NEW_ONLY_TOO_OLD])
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1", "--new-only"]) == 1
    assert "--new-only needs ahd 0.1.16 or newer." in capsys.readouterr().err


EVENT = {"event": "zero", "alerts": 12, "new": 0, "text": "12 of the 21 components ..."}


def test_an_event_without_new_alerts_still_gets_its_message(slack, tmp_path, monkeypatch, capsys):
    event = EVENT
    _answer(monkeypatch, slack, 0, json.dumps({"runs": [_run([], events=[event])]}))
    assert slack.main(["db", "rs1", "--new-only"]) == 0
    assert "Wrote slack.json with the event message." in capsys.readouterr().out
    [payload] = _written(tmp_path)
    assert payload["text"] == "Analytics Health: a suite-wide event goes on in Suite (rs1)"
    texts = _texts(payload)
    assert texts[0] == "Suite: no new alerts"
    assert (
        "Suite-wide event: 12 of the 21 components ... None of its alerts are new on 2026-11-06."
    ) in texts


def test_events_in_two_suites_are_two_event_messages(slack, tmp_path, monkeypatch, capsys):
    runs = [_run([], events=[EVENT]), _run([], events=[EVENT], rsid="rs2", rsid_name="Two")]
    _answer(monkeypatch, slack, 0, json.dumps({"runs": runs}))
    assert slack.main(["db", "--all", "--new-only"]) == 0
    assert "Wrote slack.json with the event messages." in capsys.readouterr().out


def test_an_event_beside_a_missing_suite_is_posted_as_warnings(
    slack, tmp_path, monkeypatch, capsys
):
    _answer(monkeypatch, slack, 0, json.dumps({"runs": [_run([], events=[EVENT])]}))
    assert slack.main(["db", "rs1", "nope", "--new-only"]) == 0
    assert "Wrote slack.json with the warnings." in capsys.readouterr().out
    first, second = _written(tmp_path)
    assert first["text"] == "Analytics Health: a suite-wide event goes on in Suite (rs1)"
    assert second["text"] == "Analytics Health warning for nope"


def test_an_old_run_without_alerts_is_still_posted_and_exits_with_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    slack = _load("send_slack", today=date(2026, 11, 9))
    assert slack.main([str(_db(tmp_path, 24_000_000.0)), "rs1"]) == 1
    [payload] = _written(tmp_path)
    assert payload["text"] == "Analytics Health warning for Suite (rs1)"
    texts = _texts(payload)
    assert texts[0] == "Suite: no alerts"
    assert texts[2] == "Warning: " + WARNING
    assert not _tables(payload)


def test_an_old_run_with_alerts_puts_the_warning_after_the_note(tmp_path, monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    slack = _load("send_slack", today=date(2026, 11, 9))
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1"]) == 1
    [payload] = _written(tmp_path)
    assert _texts(payload)[2] == "Warning: " + WARNING
    assert len(_tables(payload)) == 1


def test_a_missing_suite_does_not_stop_the_warning_of_another(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    slack = _load("send_slack", today=date(2026, 11, 9))
    assert slack.main([str(_db(tmp_path, 24_000_000.0)), "rs1", "nope"]) == 1
    assert "holds no finished run of nope" in capsys.readouterr().err
    assert "Warning: " + WARNING in _texts(_written(tmp_path)[0])


def test_the_warning_cannot_mention_a_channel(slack):
    run = _run([], warning="The latest finished run of <!channel> has the alerts of ...")
    payload = slack.message(run, "Rules.")
    assert "<!channel>" not in payload["text"]
    block = payload["blocks"][2]["text"]
    assert block["type"] == "plain_text" and block["emoji"] is False


def test_an_ahd_without_the_warning_fields_posts_as_before(slack, tmp_path, monkeypatch):
    _answer(monkeypatch, slack, 0, json.dumps({"runs": [_run([_alert(1)])]}))
    assert slack.main(["db", "rs1"]) == 0
    assert not any(t.startswith("Warning") for t in _texts(_written(tmp_path)[0]))


def test_always_posts_an_all_clear_message(slack, tmp_path, capsys):
    db = _suites_db(tmp_path, {"rs1": 24_000_000.0, "rs2": 24_000_000.0, "rs3": 24_000_000.0})
    assert slack.main([db, "--all", "--always"]) == 0
    [payload] = _written(tmp_path)
    assert payload["text"] == "No Analytics Health alerts for 3 report suites"
    texts = _texts(payload)
    assert texts[0] == "No Analytics Health alerts"
    assert "No alerts for Suite rs1 (rs1) on 2026-11-06." in texts[1]
    assert "Wrote slack.json with the all-clear message." in capsys.readouterr().out


def test_always_with_new_only_says_no_new_alerts(slack, tmp_path):
    assert slack.main([str(_old_drop_db(tmp_path)), "rs1", "--always", "--new-only"]) == 0
    [payload] = _written(tmp_path)
    assert payload["text"] == "No new Analytics Health alerts for rs1"
    assert _texts(payload)[0] == "No new Analytics Health alerts"
    assert "No new alerts for Suite (rs1) on 2026-11-06." in _texts(payload)[1]


def test_without_always_a_quiet_day_posts_nothing(slack, tmp_path):
    assert slack.main([str(_db(tmp_path, 24_000_000.0)), "rs1"]) == 2
    assert not (tmp_path / "slack.json").exists()


def test_test_posts_a_test_message(slack, monkeypatch, capsys):
    fake = _Slack()
    monkeypatch.setattr(slack.urllib.request, "urlopen", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    assert slack.main(["--test"]) == 0
    [(request, _timeout)] = fake.posts
    assert json.loads(request.data) == {
        "text": "ahd can post Analytics Health alerts to this channel. This is a test message "
        "from send_slack.py."
    }
    assert capsys.readouterr().out == "Posted a test message to Slack.\n"


def test_test_needs_the_webhook(slack, tmp_path, capsys):
    assert slack.main(["--test"]) == 1
    assert capsys.readouterr().err == (
        "--test needs SLACK_WEBHOOK_URL. Set it to the webhook URL of the channel.\n"
    )
    assert not (tmp_path / "slack.json").exists()


def test_test_says_why_slack_did_not_take_it(slack, monkeypatch, capsys):
    monkeypatch.setattr(slack.urllib.request, "urlopen", _Slack(_http(404, b"no_service")))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    assert slack.main(["--test"]) == 1
    err = capsys.readouterr().err
    assert err == "Slack refused the message (404 no_service). Check SLACK_WEBHOOK_URL.\n"
    assert URL not in err


def test_test_refuses_a_webhook_without_encryption(slack, monkeypatch, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hooks.slack.com/x")
    assert slack.main(["--test"]) == 1
    assert "SLACK_WEBHOOK_URL must start with https://." in capsys.readouterr().err


def test_test_does_not_start_ahd(slack, monkeypatch):
    monkeypatch.setattr(slack.urllib.request, "urlopen", _Slack())
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    monkeypatch.setattr(slack, "_ahd_command", lambda: pytest.fail("ahd was started"))
    assert slack.main(["db.duckdb", "rs1", "--test"]) == 0


def test_a_database_is_needed_without_test(slack, capsys):
    with pytest.raises(SystemExit) as e:
        slack.main([])
    assert e.value.code == 1
    assert "error: give the ahd DuckDB file" in capsys.readouterr().err


def test_always_with_a_missing_suite_is_not_an_all_clear(slack, tmp_path):
    assert slack.main([str(_db(tmp_path, 24_000_000.0)), "rs1", "nope", "--always"]) == 1
    [payload] = _written(tmp_path)
    assert payload["text"] == "Analytics Health warning for nope"
    texts = _texts(payload)
    assert texts[0] == "Analytics Health warning"
    assert MISSING in texts[1] and "No alerts for Suite (rs1) on 2026-11-06." in texts[1]


def _named_run(n):
    return _run([], rsid=f"rs{n}", rsid_name=f"Suite number {n} with a longer name")


def _section_texts(payload):
    return [b["text"]["text"] for b in payload["blocks"] if b["type"] == "section"]


def test_an_all_clear_for_many_suites_names_every_suite(slack):
    payload = slack.all_clear([_named_run(n) for n in range(80)], False)
    sections = _section_texts(payload)
    assert len(sections) > 1
    assert all(len(s) <= slack.SECTION_LIMIT for s in sections)
    joined = "\n".join(sections)
    assert all(f"(rs{n})" in joined for n in range(80))


def test_a_missing_message_for_many_suites_names_every_suite(slack):
    missing = [f"suite_with_a_long_report_suite_id_{n}" for n in range(80)]
    sections = _section_texts(slack.missing_message(missing))
    assert len(sections) > 1
    assert all(len(s) <= slack.SECTION_LIMIT for s in sections)
    joined = "\n".join(sections)
    assert all(f"of {rsid} in" in joined for rsid in missing)


def test_past_the_cap_the_rest_of_the_suites_are_counted(slack):
    payload = slack.all_clear([_named_run(n) for n in range(3000)], False)
    sections = _section_texts(payload)
    assert len(sections) == slack.MAX_SECTIONS
    assert len(payload["blocks"]) < 50
    assert all(len(s) <= slack.SECTION_LIMIT for s in sections)
    named = sum(s.count("No alerts for ") for s in sections)
    assert sections[-1].endswith(f"\n{3000 - named} more suites have no alerts.")


def test_lines_that_fill_the_cap_exactly_get_no_count(slack):
    line = "x" * (slack.SECTION_LIMIT - 1)
    blocks = slack._sections([line] * slack.MAX_SECTIONS, "{} more.")
    assert len(blocks) == slack.MAX_SECTIONS
    assert not any("more." in b["text"]["text"] for b in blocks)


def test_a_line_longer_than_a_section_is_cut(slack):
    [block] = slack._sections(["y" * (slack.SECTION_LIMIT + 500)], "{} more.")
    assert len(block["text"]["text"]) == slack.SECTION_LIMIT


def test_messages_with_alerts_name_a_missing_suite(slack, tmp_path):
    assert slack.main([str(_db(tmp_path, 5_000_000.0)), "rs1", "nope"]) == 1
    payloads = _written(tmp_path)
    assert len(payloads) == 2
    assert MISSING in _texts(payloads[-1])
