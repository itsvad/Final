#!/usr/bin/env python3
"""
CLI entry point for the Opening Range Breakout (ORB) backtester.

Example:
    python main.py --symbol ESUSD --start 2026-01-01 --end 2026-07-01 \\
        --account 10000 --risk-pct 1.0

Requires an FMP API key (env var FMP_API_KEY or --api-key) with intraday
history access for futures/commodity symbols.
"""
from __future__ import annotations

import argparse
import sys
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from data import load_bars
from engine import ORBConfig, run_backtest


def parse_time(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backtest an opening-range-breakout strategy.")
    p.add_argument("--symbol", default="ESUSD", help="FMP symbol for price data (default: ESUSD). "
                   "FMP only carries the full-size E-mini feed — MES and ES trade at identical prices, "
                   "so ES data is used for both; --tick-value is what distinguishes MES vs ES sizing.")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")

    p.add_argument("--or-start", default="09:30", help="Opening range start time, HH:MM ET (default 09:30)")
    p.add_argument("--or-minutes", type=int, default=15, help="Opening range length in minutes (default 15)")
    p.add_argument("--cutoff-entry", default="11:30", help="No new entries after this time ET (default 11:30)")
    p.add_argument("--flat-time", default="15:55", help="Force-close any open position at this time ET (default 15:55)")

    p.add_argument("--tick-size", type=float, default=0.25, help="Instrument tick size (default 0.25 for ES/MES)")
    p.add_argument("--tick-value", type=float, default=1.25, help="$ value per tick per contract (default 1.25 for MES; use 12.50 for ES)")
    p.add_argument("--reward-r", type=float, default=1.5, help="Target as multiple of risk (default 1.5 = 1:1.5 R:R)")
    p.add_argument("--breakeven-r", type=float, default=1.0, help="Move stop to breakeven once this R multiple is reached (default 1.0)")

    p.add_argument("--account", type=float, default=10_000.0, help="Starting account size in $ (default 10000)")
    p.add_argument("--risk-pct", type=float, default=1.0, help="Percent of account risked per trade (default 1.0)")
    p.add_argument("--fixed-contracts", type=int, default=None, help="Use a fixed contract count instead of risk-%% sizing")
    p.add_argument("--no-compound", action="store_true", help="Size every trade off the fixed starting account instead of running equity")

    p.add_argument("--commission", type=float, default=0.0, help="Commission $ per contract per fill (default 0)")
    p.add_argument("--slippage-ticks", type=float, default=0.0, help="Slippage in ticks applied against fills (default 0)")

    p.add_argument("--data-dir", default="data", help="Local cache directory for downloaded bars (default ./data)")
    p.add_argument("--api-key", default=None, help="FMP API key (defaults to FMP_API_KEY env var)")
    p.add_argument("--refresh", action="store_true", help="Ignore local cache and re-download all data")

    p.add_argument("--out-dir", default="results", help="Directory to write trades.csv / summary.txt (default ./results)")
    return p


def summarize(trades, cfg: ORBConfig) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}, pd.DataFrame()

    df = pd.DataFrame([{
        "date": t.date,
        "direction": t.direction,
        "entry_time": t.entry_time,
        "entry_price": t.entry_price,
        "exit_time": t.exit_time,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "moved_to_breakeven": t.moved_to_breakeven,
        "contracts": t.contracts,
        "pnl": t.pnl,
        "r_multiple": t.r_multiple,
    } for t in trades])

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] < 0]
    flat = df[df["pnl"] == 0]

    gross_profit = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    equity_curve = pd.concat([pd.Series([cfg.account_size]), cfg.account_size + df["pnl"].cumsum()], ignore_index=True)
    running_max = equity_curve.cummax()
    drawdown = equity_curve - running_max
    max_drawdown = drawdown.min()
    max_drawdown_pct = (drawdown / running_max).min() * 100

    total_pnl = df["pnl"].sum()

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven_or_flat": len(flat),
        "win_rate_pct": 100 * len(wins) / n,
        "avg_r_multiple": df["r_multiple"].mean(),
        "expectancy_r": df["r_multiple"].mean(),
        "total_pnl": total_pnl,
        "avg_pnl_per_trade": total_pnl / n,
        "profit_factor": profit_factor,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "ending_equity": cfg.account_size + total_pnl,
        "return_pct": 100 * total_pnl / cfg.account_size,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "long_trades": int((df["direction"] == "long").sum()),
        "short_trades": int((df["direction"] == "short").sum()),
        "target_hits": int((df["exit_reason"] == "target").sum()),
        "stop_hits": int((df["exit_reason"] == "stop").sum()),
        "breakeven_stop_hits": int((df["exit_reason"] == "breakeven_stop").sum()),
        "time_exits": int((df["exit_reason"] == "time_exit").sum()),
    }, df


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    or_start = parse_time(args.or_start)
    or_start_dt = pd.Timestamp("2000-01-01") + pd.Timedelta(hours=or_start.hour, minutes=or_start.minute)
    or_end_dt = or_start_dt + pd.Timedelta(minutes=args.or_minutes)
    or_end = or_end_dt.time()

    cfg = ORBConfig(
        or_start=or_start,
        or_end=or_end,
        cutoff_entry=parse_time(args.cutoff_entry),
        flat_time=parse_time(args.flat_time),
        tick_size=args.tick_size,
        tick_value=args.tick_value,
        reward_r=args.reward_r,
        breakeven_r=args.breakeven_r,
        account_size=args.account,
        risk_pct=args.risk_pct,
        fixed_contracts=args.fixed_contracts,
        commission_per_side=args.commission,
        slippage_ticks=args.slippage_ticks,
    )

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.symbol} 1-min bars {args.start}..{args.end} (cache: {data_dir})...", file=sys.stderr)
    bars = load_bars(args.symbol, args.start, args.end, data_dir, api_key=args.api_key, refresh=args.refresh)
    print(f"Loaded {len(bars):,} bars.", file=sys.stderr)

    trades = run_backtest(bars, cfg, compound=not args.no_compound)
    print(f"Simulated {bars['date'].dt.date.nunique():,} trading days -> {len(trades)} trades taken.", file=sys.stderr)

    if not trades:
        print("No trades were generated for this period.")
        return

    stats, trades_df = summarize(trades, cfg)

    trades_csv = out_dir / "trades.csv"
    trades_df.to_csv(trades_csv, index=False)

    lines = [
        f"ORB Backtest Summary — {args.symbol}  {args.start} to {args.end}",
        "=" * 60,
        f"Trades taken:          {stats['trades']}  (long {stats['long_trades']} / short {stats['short_trades']})",
        f"Wins / Losses / Flat:  {stats['wins']} / {stats['losses']} / {stats['breakeven_or_flat']}",
        f"Win rate:              {stats['win_rate_pct']:.1f}%",
        f"Exit breakdown:        target {stats['target_hits']}, stop {stats['stop_hits']}, "
        f"breakeven-stop {stats['breakeven_stop_hits']}, time exit {stats['time_exits']}",
        "-" * 60,
        f"Avg R multiple:        {stats['avg_r_multiple']:.3f}R",
        f"Profit factor:         {stats['profit_factor']:.2f}",
        f"Total P&L:             ${stats['total_pnl']:,.2f}",
        f"Avg P&L per trade:     ${stats['avg_pnl_per_trade']:,.2f}",
        f"Return on starting acct: {stats['return_pct']:.2f}%",
        f"Starting equity:       ${cfg.account_size:,.2f}",
        f"Ending equity:         ${stats['ending_equity']:,.2f}",
        f"Max drawdown:          ${stats['max_drawdown']:,.2f}  ({stats['max_drawdown_pct']:.2f}%)",
        "=" * 60,
        f"Trade log written to:  {trades_csv}",
    ]
    summary_text = "\n".join(lines)
    print(summary_text)
    (out_dir / "summary.txt").write_text(summary_text + "\n")


if __name__ == "__main__":
    main()
