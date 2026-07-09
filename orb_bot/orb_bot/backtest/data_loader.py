"""Loads historical 1-minute OHLC bars from CSV for backtesting.

Expected CSV columns (case-insensitive): timestamp, open, high, low, close.
Extra columns (volume, etc.) are ignored.
"""

from __future__ import annotations

from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd

REQUIRED_COLUMNS = {"open", "high", "low", "close"}


def load_bars_csv(
    path: str,
    source_timezone: str = "America/New_York",
    timestamp_column: str = "timestamp",
) -> pd.DataFrame:
    """Load a CSV of 1-min bars and normalize the timestamp column.

    `source_timezone` is the timezone the CSV's timestamps are already
    in when they have no UTC offset info (most retail 1-min futures
    exports are already in US/Eastern local time). If the CSV timestamps
    carry a UTC offset or 'Z', they're converted to the strategy's
    session timezone instead.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    ts_col = timestamp_column.lower()
    if ts_col not in df.columns:
        raise ValueError(f"CSV is missing timestamp column {timestamp_column!r}")
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {sorted(missing)}")

    df[ts_col] = pd.to_datetime(df[ts_col])
    tz = ZoneInfo(source_timezone)
    if df[ts_col].dt.tz is None:
        df[ts_col] = df[ts_col].dt.tz_localize(tz)
    else:
        df[ts_col] = df[ts_col].dt.tz_convert(tz)

    df = df.rename(columns={ts_col: "timestamp"})
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close"]]


def iter_session_days(df: pd.DataFrame, clock) -> Iterator[tuple]:
    """Yield (session_date, [bar_dict, ...]) grouped by NY session date."""
    df = df.copy()
    df["_session_date"] = df["timestamp"].apply(clock.session_date)
    for day, group in df.groupby("_session_date", sort=True):
        bars = group.drop(columns=["_session_date"]).to_dict("records")
        yield day, bars
