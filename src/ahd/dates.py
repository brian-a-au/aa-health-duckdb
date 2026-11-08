"""Run window dates. Single source of every date the tool uses."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ahd.config import ConfigError


def _range(start: date, end_exclusive: date) -> str:
    return f"{start.isoformat()}T00:00:00.000/{end_exclusive.isoformat()}T00:00:00.000"


def today_in_zone(zoneinfo_name: str, now_utc: datetime) -> date:
    """Today's date in the report suite's time zone."""
    try:
        zone = ZoneInfo(zoneinfo_name)
    except (ZoneInfoNotFoundError, ValueError):
        # ValueError: a name that is not a valid key, e.g. an absolute path.
        raise ConfigError(
            f"The report suite uses the time zone {zoneinfo_name!r}, which this computer does "
            "not know. Run uv sync to install the time zone data, then try again. For ahd run, "
            "you can also give the date with --date YYYY-MM-DD."
        ) from None
    return now_utc.astimezone(zone).date()


@dataclass(frozen=True)
class RunWindow:
    today: date
    yesterday: date
    start: date
    end_exclusive: date
    week_start: date
    week_end: date
    baseline_start: date
    baseline_end: date
    hist_start: date
    hist_end: date
    selectable_days: list[date]

    @classmethod
    def for_today(cls, today: date, offset_days: int = 91) -> "RunWindow":
        d = timedelta
        yesterday = today - d(days=1)
        return cls(
            today=today,
            yesterday=yesterday,
            start=today - d(days=offset_days),
            end_exclusive=today + d(days=1),
            week_start=today - d(days=7),
            week_end=yesterday,
            baseline_start=today - d(days=offset_days),
            baseline_end=today - d(days=8),
            hist_start=today - d(days=offset_days),
            hist_end=today - d(days=2),
            selectable_days=[today - d(days=n) for n in range(1, 8)],
        )

    def trend_range(self) -> str:
        return _range(self.start, self.end_exclusive)

    def yesterday_range(self) -> str:
        return _range(self.yesterday, self.today)

    def historical_range(self) -> str:
        return _range(self.hist_start, self.hist_end + timedelta(days=1))
