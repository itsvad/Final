# ORB Futures Trading Bot

A 15-minute Opening Range Breakout (ORB) bot for futures, trading through
[Tradovate](https://tradovate.com)'s API. Every parameter of the strategy
is adjustable via `config.yaml` - nothing is hardcoded.

> **This places real orders on a real brokerage account when
> `dry_run: false`. Trading futures involves substantial risk of loss.
> Test thoroughly in `account.environment: demo` with `dry_run: true`
> before ever going live. Nothing here is financial advice.**

## Strategy

1. Marks the high and low of price during the **opening range window**
   (default 9:30-9:45 AM ET).
2. During the **trading window** (default 9:45-11:30 AM ET), watches for
   price to break one tick above the opening-range high (long) or one
   tick below the opening-range low (short).
3. On breakout: enters, places a stop loss one tick beyond the opposite
   side of the range, and a take-profit at your configured reward:risk
   ratio (default 1:1).
4. Position size is calculated automatically from your dollar risk
   amount and the stop distance - it never risks more than your budget
   (rounding down, e.g. risking $600 with $400 risk/contract takes 1
   contract, not 2, since 2 would risk $800).
5. Takes at most **one trade per New York session** (configurable), then
   stands down for the rest of the day.
6. If no breakout happens before the trading window closes, no trade is
   taken for that day.
7. Any open trade is force-closed at **3:55 PM ET** (configurable) regardless
   of profit or loss - trades are never held past market close.

## Setup

```bash
cd orb_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml   # edit your settings
cp .env.example .env                 # fill in your Tradovate credentials
```

Get Tradovate API app credentials (`TRADOVATE_APP_ID`, `TRADOVATE_CID`,
`TRADOVATE_SEC`) from your Tradovate account's API access page. Your
`TRADOVATE_USERNAME`/`TRADOVATE_PASSWORD` are your normal Tradovate login.

## Configuration (`config.yaml`)

Every field is documented inline in `config.example.yaml`. The main knobs:

| Section | Field | What it controls |
|---|---|---|
| `account` | `environment` | `demo` or `live` Tradovate environment |
| `account` | `dry_run` | `true` = log intended orders, never send them |
| `contract` | `symbol` | Root future to trade, e.g. `MES`, `NQ`, `ES` |
| `session` | `opening_range_start/end` | Window used to mark the OR high/low |
| `session` | `trading_window_start/end` | Window during which breakouts are traded |
| `session` | `max_trades_per_session` | Trades allowed per NY session (default 1) |
| `session` | `force_close_time` | Any open trade is flattened here regardless of P&L (default 3:55 PM ET) |
| `strategy` | `entry_trigger_ticks` | Ticks beyond OR high/low to trigger entry |
| `strategy` | `stop_ticks` | Ticks beyond the opposite side for the stop |
| `strategy` | `reward_risk_ratio` | Target distance = risk distance * this |
| `risk` | `risk_amount_usd` | Dollar risk budget for the day's trade |
| `risk` | `max_contracts` / `min_contracts` | Safety caps on position size |

Tick size/value for common futures (ES, MES, NQ, MNQ, YM, MYM, RTY, M2K,
CL, MCL, NG, GC, MGC, SI, SIL, ZB, ZN, 6E, 6B) are built in
(`orb_bot/contracts.py`). For anything else, set `contract.tick_size` and
`contract.tick_value` explicitly in `config.yaml`.

## Running live / paper

```bash
python scripts/run_live.py --config config.yaml --env .env
```

Leave `account.dry_run: true` until you've watched it correctly mark the
opening range and log signals for several sessions on the demo
environment. The bot re-authenticates hourly and reconnects its
market-data stream automatically; stop it with Ctrl+C.

## Backtesting

Feed it a CSV of historical 1-minute bars (`timestamp,open,high,low,close`,
Eastern time by default) to see how the strategy would have performed
with your current config:

```bash
python scripts/run_backtest.py --data data/MES_1min.csv --config config.yaml --out-csv logs/backtest_trades.csv
```

This prints a summary (win rate, total P&L, total/average R, max
drawdown) and, with `--out-csv`, a trade-by-trade CSV. Backtest
simplifications: entries fill exactly at the trigger price (no
slippage), and if a single bar's range spans both the stop and the
target, the stop is assumed hit first (conservative).

## Trade log

Every signal (taken or skipped) and every no-trade day is appended to
`logging.trade_log_csv` (default `./logs/trades.csv`) so you have a full
audit trail.

## Running tests

```bash
pip install -r requirements.txt
pytest
```

Unit tests cover position sizing, the full ORB state machine (opening
range marking, long/short breakouts, one-trade-per-session enforcement,
no-trade days, configurable windows/RR), and the backtest engine - all
without needing Tradovate credentials or network access.

## Project layout

```
orb_bot/
  config.py           # loads/validates config.yaml + .env
  contracts.py         # tick size/value lookup table
  position_sizer.py    # risk $ -> contract count
  session.py            # NY timezone / window helpers
  orb_strategy.py       # core ORB state machine (broker-agnostic)
  trade_log.py          # CSV trade journal
  tradovate_client.py   # Tradovate REST + WebSocket client, live executor
  live_runner.py         # wires Tradovate client to the strategy
  backtest/
    data_loader.py       # CSV -> normalized bars
    engine.py             # replays bars through the strategy, simulates fills
    report.py             # win rate / P&L / R / drawdown summary
scripts/
  run_live.py
  run_backtest.py
tests/
```
