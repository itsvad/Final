"""New York session / time-window helpers.

Converts arbitrary (possibly UTC) timestamps into the configured
timezone (default America/New_York) and answers "are we inside the
opening range window / trading window right now" questions. Using
zoneinfo means DST (EST vs EDT) is handled automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from orb_bot.config import SessionConfig


@dataclass
class SessionClock:
    config: SessionConfig

    def __post_init__(self) -> None:
        self.tz = ZoneInfo(self.config.timezone)

    def to_local(self, ts: datetime) -> datetime:
        """Return `ts` converted to the configured timezone.

        Naive datetimes are assumed to already be in the configured
        timezone (useful for backtests fed local-time bars).
        """
        if ts.tzinfo is None:
            return ts.replace(tzinfo=self.tz)
        return ts.astimezone(self.tz)

    def session_date(self, ts: datetime) -> date:
        return self.to_local(ts).date()

    def _time_of(self, ts: datetime) -> time:
        return self.to_local(ts).timetz().replace(tzinfo=None)

    def in_opening_range(self, ts: datetime) -> bool:
        t = self._time_of(ts)
        return self.config.opening_range_start <= t < self.config.opening_range_end

    def opening_range_complete(self, ts: datetime) -> bool:
        return self._time_of(ts) >= self.config.opening_range_end

    def in_trading_window(self, ts: datetime) -> bool:
        t = self._time_of(ts)
        return self.config.trading_window_start <= t < self.config.trading_window_end

    def trading_window_passed(self, ts: datetime) -> bool:
        return self._time_of(ts) >= self.config.trading_window_end

    def before_opening_range(self, ts: datetime) -> bool:
        t = self._time_of(ts)
        return t < self.config.opening_range_start

    def at_or_after_force_close(self, ts: datetime) -> bool:
        return self._time_of(ts) >= self.config.force_close_time
