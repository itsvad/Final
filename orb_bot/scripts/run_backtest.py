#!/usr/bin/env python3
"""Backtest the ORB strategy against historical 1-min CSV bars.

Usage:
    python scripts/run_backtest.py --data data/MES_1min.csv [--config config.yaml]

CSV must have columns: timestamp, open, high, low, close (case-insensitive).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb_bot.backtest.data_loader import load_bars_csv
from orb_bot.backtest.engine import run_backtest
from orb_bot.config import load_config
from orb_bot.contracts import resolve_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the ORB strategy")
    parser.add_argument("--data", required=True, help="Path to CSV of 1-min OHLC bars")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env (not required for backtests)")
    parser.add_argument(
        "--data-timezone",
        default="America/New_York",
        help="Timezone the CSV timestamps are already in if they carry no UTC offset",
    )
    parser.add_argument("--out-csv", default=None, help="Optional path to write trade-by-trade CSV")
    args = parser.parse_args()

    config = load_config(args.config, args.env)
    spec = resolve_spec(config.contract.symbol, config.contract.tick_size, config.contract.tick_value)

    bars = load_bars_csv(args.data, source_timezone=args.data_timezone)
    report = run_backtest(config, spec, bars)

    print(report.summary())
    if args.out_csv:
        report.to_csv(args.out_csv)
        print(f"\nTrade-by-trade detail written to {args.out_csv}")


if __name__ == "__main__":
    main()
