# Opening Range Breakout (ORB) Backtester

Backtests an opening-range-breakout strategy on ES/MES (S&P 500 E-mini /
Micro E-mini futures) using 1-minute historical bars from
[Financial Modeling Prep](https://financialmodelingprep.com/).

## Strategy rules

1. **Opening range**: high/low of the first 15 minutes of the session
   (09:30–09:45 AM ET by default).
2. **Entry**: a stop order sits 1 tick above the opening-range high (long)
   and 1 tick below the opening-range low (short) at the same time.
   Whichever triggers first becomes the day's trade; the other side is
   cancelled. Only one trade is taken per day.
3. **Stop loss**: 1 tick beyond the opposite side of the opening range
   (i.e. a long's stop is 1 tick below the OR low; a short's stop is 1 tick
   above the OR high).
4. **Target**: 1:1.5 risk/reward by default.
5. **Breakeven**: once price reaches 1R in your favor, the stop moves to
   entry (breakeven).
6. **Entry cutoff**: if neither side has triggered by 11:30 AM ET, no trade
   is taken for the day.
7. **Forced flat**: any open position is closed at 15:55 PM ET regardless
   of P&L, so nothing carries past the session close.

All of the above (OR length, session times, R:R, breakeven trigger) are
configurable via CLI flags — see below.

## Setup

```bash
cd orb_backtest
pip install -r requirements.txt
```

You need an [FMP API key](https://financialmodelingprep.com/developer/docs/pricing)
with intraday-history access for commodity/futures symbols. Set it as an
environment variable:

```bash
export FMP_API_KEY=your_key_here
```

or pass `--api-key your_key_here` on each run.

## Usage

```bash
python main.py --symbol ESUSD --start 2025-01-01 --end 2025-12-31 \
    --account 10000 --risk-pct 1.0
```

This will:
1. Download (and locally cache under `data/`) 1-minute bars for the date
   range, so re-running the same range later doesn't re-download.
2. Run the ORB simulation across every trading day in range.
3. Write `results/trades.csv` (one row per trade) and `results/summary.txt`
   (aggregate stats), and print the summary to the console.

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--symbol` | `MESUSD` | FMP symbol for price data. **Use `ESUSD`** — FMP only carries the full-size E-mini feed; MES and ES trade at identical prices, so ES data is used for both, and `--tick-value` is what actually distinguishes them. |
| `--or-minutes` | `15` | Opening range length in minutes |
| `--cutoff-entry` | `11:30` | No new entries after this ET time |
| `--flat-time` | `15:55` | Force-close any open trade at this ET time |
| `--tick-size` | `0.25` | Instrument tick size |
| `--tick-value` | `1.25` | $ per tick per contract — `1.25` for MES, `12.50` for ES |
| `--reward-r` | `1.5` | Target as a multiple of initial risk |
| `--breakeven-r` | `1.0` | R multiple at which the stop moves to breakeven |
| `--account` | `10000` | Starting account size ($) |
| `--risk-pct` | `1.0` | % of account risked per trade |
| `--fixed-contracts` | *(none)* | Override risk-based sizing with a fixed contract count |
| `--no-compound` | off | Size every trade off the fixed starting balance instead of running equity |
| `--commission` | `0` | $ commission per contract per fill (entry and exit each count) |
| `--slippage-ticks` | `0` | Ticks of slippage applied against every fill |
| `--refresh` | off | Ignore the local cache and re-download everything |

Run `python main.py --help` for the full list.

### Example: full-size ES, 2 contracts fixed, no risk-based sizing

```bash
python main.py --symbol ESUSD --start 2025-01-01 --end 2025-12-31 \
    --tick-value 12.50 --fixed-contracts 2
```

## Important assumptions and limitations

- **Timezone**: FMP's intraday commodity data comes back as naive
  timestamps that are already in US/Eastern "market time." The code treats
  them as ET directly with no conversion. If FMP changes this behavior,
  session-time comparisons will be wrong — spot-check a day's timestamps
  against known market hours before trusting results.
- **Intrabar fill ordering (the main approximation)**: this backtest only
  has 1-minute OHLC bars, not tick data. When a bar's range spans multiple
  relevant price levels (e.g. both the breakeven trigger and the target),
  the code assumes they're reached in order of distance from the bar's
  *open* price (closest first), and assumes a single directional move per
  bar (no intrabar reversals) once that ordering is set. This is a
  standard, documented simplification for OHLC-bar backtesting — it's
  usually reasonable but can occasionally diverge from the true intrabar
  path, especially in fast/volatile bars. For higher fidelity, source
  tick or 1-second data and adapt `engine.py`.
- **Position sizing**: with risk-% sizing, if 1 contract's risk already
  exceeds your risk budget for the day (small account, wide opening
  range), the backtest still takes 1 contract minimum — it will not skip
  the trade, but the *actual* dollar risk that day exceeds your configured
  `--risk-pct`. Check `trades.csv`'s `pnl`/`r_multiple` columns if this
  matters for your account size.
- **Data gaps**: days with no data in the 09:30–09:45 window (holidays,
  data provider gaps) are silently skipped — no trade is recorded for
  that day.
- **FMP request quirk**: FMP's intraday endpoint silently truncates
  multi-day requests to roughly the most recent ~1,400 one-minute bars,
  regardless of the requested date range. `data.py` works around this by
  always fetching one calendar day per HTTP request (skipping Saturdays,
  which are never trading days for CME equity-index futures) — this is
  slower for a long backtest window but avoids silently dropping days.

## Files

- `data.py` — FMP fetch + local CSV cache (`data/<SYMBOL>_1min.csv`)
- `engine.py` — the ORB simulation (opening range, entries, stop/target/
  breakeven/time-exit logic, position sizing)
- `main.py` — CLI: wires data + engine together, computes summary stats,
  writes `results/trades.csv` and `results/summary.txt`
