from datetime import UTC, date, datetime

from ahd.dates import RunWindow, today_in_zone


def test_window_matches_workbook():
    w = RunWindow.for_today(date(2026, 11, 7))
    assert w.trend_range() == "2026-08-08T00:00:00.000/2026-11-08T00:00:00.000"
    assert w.yesterday_range() == "2026-11-06T00:00:00.000/2026-11-07T00:00:00.000"
    assert w.historical_range() == "2026-08-08T00:00:00.000/2026-11-06T00:00:00.000"
    assert (w.week_start, w.week_end) == (date(2026, 10, 31), date(2026, 11, 6))
    assert (w.baseline_start, w.baseline_end) == (date(2026, 8, 8), date(2026, 10, 30))
    assert (w.baseline_end - w.baseline_start).days + 1 == 84
    assert w.selectable_days[0] == date(2026, 11, 6) and len(w.selectable_days) == 7
    assert w.selectable_days[-1] == date(2026, 10, 31)
    assert (w.hist_start, w.hist_end) == (date(2026, 8, 8), date(2026, 11, 5))


def test_today_uses_suite_zone():
    assert today_in_zone("US/Pacific", datetime(2026, 11, 7, 23, 30, tzinfo=UTC)) == date(2026, 11, 7)
    assert today_in_zone("US/Pacific", datetime(2026, 11, 8, 2, 0, tzinfo=UTC)) == date(2026, 11, 7)
