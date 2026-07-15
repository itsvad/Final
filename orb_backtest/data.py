"""
Fetches 1-minute historical bars from the Financial Modeling Prep (FMP) API
and caches them locally as CSV so repeated backtest runs don't re-download.

FMP requires an API key with intraday-history access for commodities/futures
symbols (e.g. ESUSD, MESUSD). Set it via the FMP_API_KEY environment variable
or pass --api-key on the command line.

Timestamps returned by FMP's intraday commodity endpoint are already in
US/Eastern "market time" (naive, no UTC offset). This code treats them as
such and does not perform any timezone conversion.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import requests

# FMP has migrated most endpoints to a "stable" namespace; some accounts/plans
# may still be on the legacy v3 path. We try stable first and fall back to v3.
FMP_STABLE_URL = "https://financialmodelingprep.com/stable/historical-chart/1min"
FMP_LEGACY_URL = "https://financialmodelingprep.com/api/v3/historical-chart/1min/{symbol}"
REQUEST_PAUSE_SEC = 0.25  # be polite to the API between requests

# IMPORTANT: FMP's intraday endpoint silently caps how much it returns per
# request (empirically, roughly the most recent ~1,400 one-minute bars
# counted back from `to`), regardless of how wide a `from`/`to` window you
# ask for. A multi-day request can silently drop the older days in the
# range with no error. A single calendar day reliably returns that whole
# day, so we always fetch one day per HTTP call.


def _cache_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / f"{symbol}_1min.csv"


def _request_json(url: str, params: dict):
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _fetch_chunk(symbol: str, from_date: str, to_date: str, api_key: str) -> pd.DataFrame:
    params = {"symbol": symbol, "from": from_date, "to": to_date, "apikey": api_key}
    try:
        payload = _request_json(FMP_STABLE_URL, params)
    except Exception as stable_err:
        try:
            payload = _request_json(FMP_LEGACY_URL.format(symbol=symbol), params)
        except Exception:
            raise stable_err  # surface the original (stable-endpoint) error

    if isinstance(payload, dict):
        # FMP returns an error object like {"Error Message": "..."} on failure
        msg = payload.get("Error Message") or payload.get("error") or payload
        raise RuntimeError(f"FMP API error for {symbol} {from_date}..{to_date}: {msg}")
    if not payload:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(payload)
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "open", "high", "low", "close", "volume"]]


def load_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    data_dir: Path,
    api_key: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return 1-min OHLCV bars for `symbol` covering [start_date, end_date] (inclusive).

    Uses a local CSV cache under `data_dir`; only fetches date ranges that are
    missing from the cache (or everything, if refresh=True).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(data_dir, symbol)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    cached = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if cache_file.exists() and not refresh:
        cached = pd.read_csv(cache_file, parse_dates=["date"])

    have_dates = set(cached["date"].dt.date) if not cached.empty else set()
    all_days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    # CME equity-index futures are closed all day Saturday; skip to save requests.
    all_days = [d for d in all_days if d.dayofweek != 5]
    missing_days = [d for d in all_days if d.date() not in have_dates]

    new_frames = []
    if missing_days:
        api_key = api_key or os.environ.get("FMP_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No FMP API key provided. Set FMP_API_KEY env var or pass --api-key. "
                f"Missing {len(missing_days)} day(s) of data for {symbol}."
            )
        for day in missing_days:
            day_str = day.strftime("%Y-%m-%d")
            df = _fetch_chunk(symbol, day_str, day_str, api_key)
            if not df.empty:
                new_frames.append(df)
            time.sleep(REQUEST_PAUSE_SEC)

    if new_frames:
        combined = pd.concat([cached] + new_frames, ignore_index=True)
    else:
        combined = cached

    if combined.empty:
        raise RuntimeError(f"No data returned for {symbol} between {start_date} and {end_date}.")

    combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    combined.to_csv(cache_file, index=False)

    mask = (combined["date"] >= start) & (combined["date"] < end + pd.Timedelta(days=1))
    result = combined.loc[mask].reset_index(drop=True)
    if result.empty:
        raise RuntimeError(f"No bars found for {symbol} in requested range {start_date}..{end_date}.")
    return result
