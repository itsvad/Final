"""
Opening Range Breakout (ORB) backtest engine.

Strategy rules implemented:
  - Opening range = high/low of the first N minutes of the session (default
    09:30-09:45 ET).
  - A stop order sits 1 tick above the OR high (long) and 1 tick below the
    OR low (short) simultaneously. Whichever triggers first becomes the
    day's trade; the other side is cancelled. Only one trade per day.
  - Stop loss = opposite side of the opening range, 1 tick beyond it.
  - Target = entry +/- (risk * reward_r), default reward_r = 1.5 (i.e. a
    1:1.5 R:R).
  - Once price reaches 1R in favor, the stop is moved to breakeven (entry
    price).
  - Entries are only accepted if the breakout occurs before `cutoff_entry`
    (default 11:30 ET). If neither side has triggered by then, no trade is
    taken for the day.
  - Any open position is force-closed at `flat_time` (default 15:55 ET)
    regardless of P&L.

Intrabar fill assumption (1-minute OHLC data, no tick data):
  Within a single bar, when multiple price levels (entry trigger, stop,
  breakeven trigger, target) all fall inside [bar.low, bar.high], we assume
  they are reached in order of their distance from the bar's OPEN price
  (closest first). This is a standard, documented simplification for
  OHLC-bar backtesting and is generally conservative but can occasionally
  differ from the true intrabar path. Using finer-granularity data (e.g.
  1-second bars) would remove this ambiguity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time as dtime

import pandas as pd


@dataclass
class ORBConfig:
    or_start: dtime = dtime(9, 30)
    or_end: dtime = dtime(9, 45)
    cutoff_entry: dtime = dtime(11, 30)
    flat_time: dtime = dtime(15, 55)
    tick_size: float = 0.25
    tick_value: float = 1.25          # $ per tick per contract (MES default)
    reward_r: float = 1.5             # target = risk * reward_r
    breakeven_r: float = 1.0          # move stop to entry once this R is reached
    account_size: float = 10_000.0
    risk_pct: float = 1.0             # % of account risked per trade
    fixed_contracts: int | None = None  # override risk-based sizing if set
    commission_per_side: float = 0.0  # $ per contract per fill (entry or exit)
    slippage_ticks: float = 0.0       # applied against you on entry and exit


@dataclass
class Trade:
    date: object
    direction: str
    entry_time: object
    entry_price: float
    initial_stop: float
    target: float
    or_high: float
    or_low: float
    contracts: int
    risk_per_contract_dollars: float
    exit_time: object = None
    exit_price: float = None
    exit_reason: str = None
    moved_to_breakeven: bool = False
    pnl: float = None
    r_multiple: float = None


def _size_position(risk_per_contract_ticks: float, cfg: ORBConfig, account_equity: float) -> tuple[int, float]:
    """Return (contracts, risk_per_contract_dollars)."""
    risk_per_contract_dollars = risk_per_contract_ticks * cfg.tick_value
    if cfg.fixed_contracts is not None:
        return cfg.fixed_contracts, risk_per_contract_dollars
    risk_budget = account_equity * (cfg.risk_pct / 100.0)
    contracts = math.floor(risk_budget / risk_per_contract_dollars) if risk_per_contract_dollars > 0 else 0
    contracts = max(contracts, 1)  # always take at least 1 contract if a signal fires
    return contracts, risk_per_contract_dollars


def _round_to_tick(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 10)


class _TradeState:
    def __init__(self):
        self.entered = False
        self.direction = None
        self.entry_price = None
        self.entry_time = None
        self.current_stop = None
        self.target = None
        self.be_trigger = None
        self.moved_be = False


def _simulate_day(day_bars: pd.DataFrame, day_date, cfg: ORBConfig, account_equity: float) -> Trade | None:
    or_bars = day_bars[(day_bars["time"] >= cfg.or_start) & (day_bars["time"] < cfg.or_end)]
    if or_bars.empty:
        return None
    or_high = or_bars["high"].max()
    or_low = or_bars["low"].min()
    if or_high <= or_low:
        return None

    long_trigger = _round_to_tick(or_high + cfg.tick_size, cfg.tick_size)
    short_trigger = _round_to_tick(or_low - cfg.tick_size, cfg.tick_size)

    post_or_bars = day_bars[day_bars["time"] >= cfg.or_end]
    if post_or_bars.empty:
        return None

    state = _TradeState()
    trade: Trade | None = None

    for _, bar in post_or_bars.iterrows():
        bar_time = bar["time"]

        # Force-flat: close any open position at/after flat_time, no new entries after cutoff.
        if state.entered and bar_time >= cfg.flat_time:
            trade.exit_time = bar["date"]
            trade.exit_price = _apply_slippage(bar["open"], trade.direction, exiting=True, cfg=cfg)
            trade.exit_reason = "time_exit"
            break

        if not state.entered and bar_time >= cfg.cutoff_entry:
            # No entry yet and past the "must trigger by" cutoff -> no trade today.
            return None

        # Cascade through whatever price levels are relevant right now, resolving
        # the closest-to-open one first, until nothing more happens in this bar.
        be_moved_this_bar = False
        while True:
            candidates = []
            if not state.entered:
                candidates.append(("enter_long", long_trigger))
                candidates.append(("enter_short", short_trigger))
            else:
                # If breakeven just moved the stop earlier in this same bar, don't
                # re-check it: under the monotonic-from-open path assumption, price
                # already passed through that level on its way out and hasn't
                # reversed back within this bar. It becomes checkable again next bar.
                if not be_moved_this_bar:
                    candidates.append(("stop", state.current_stop))
                if not state.moved_be:
                    candidates.append(("breakeven", state.be_trigger))
                candidates.append(("target", state.target))

            reachable = [
                (abs(price - bar["open"]), name, price)
                for name, price in candidates
                if bar["low"] <= price <= bar["high"]
            ]
            if not reachable:
                break
            reachable.sort(key=lambda x: x[0])
            _, name, price = reachable[0]

            if name in ("enter_long", "enter_short"):
                direction = "long" if name == "enter_long" else "short"
                entry_price = _apply_slippage(price, direction, exiting=False, cfg=cfg)
                if direction == "long":
                    initial_stop = or_low - cfg.tick_size
                    risk = entry_price - initial_stop
                    target = entry_price + risk * cfg.reward_r
                    be_trigger = entry_price + risk * cfg.breakeven_r
                else:
                    initial_stop = or_high + cfg.tick_size
                    risk = initial_stop - entry_price
                    target = entry_price - risk * cfg.reward_r
                    be_trigger = entry_price - risk * cfg.breakeven_r

                risk_ticks = risk / cfg.tick_size
                contracts, risk_per_contract_dollars = _size_position(risk_ticks, cfg, account_equity)

                state.entered = True
                state.direction = direction
                state.entry_price = entry_price
                state.entry_time = bar["date"]
                state.current_stop = _round_to_tick(initial_stop, cfg.tick_size)
                state.target = _round_to_tick(target, cfg.tick_size)
                state.be_trigger = _round_to_tick(be_trigger, cfg.tick_size)
                state.moved_be = False

                trade = Trade(
                    date=day_date,
                    direction=direction,
                    entry_time=state.entry_time,
                    entry_price=entry_price,
                    initial_stop=state.current_stop,
                    target=state.target,
                    or_high=or_high,
                    or_low=or_low,
                    contracts=contracts,
                    risk_per_contract_dollars=risk_per_contract_dollars,
                )

            elif name == "stop":
                exit_price = _apply_slippage(price, state.direction, exiting=True, cfg=cfg)
                trade.exit_time = bar["date"]
                trade.exit_price = exit_price
                trade.exit_reason = "breakeven_stop" if state.moved_be else "stop"
                trade.moved_to_breakeven = state.moved_be
                return trade

            elif name == "breakeven":
                state.current_stop = _round_to_tick(state.entry_price, cfg.tick_size)
                state.moved_be = True
                be_moved_this_bar = True
                trade.moved_to_breakeven = True

            elif name == "target":
                exit_price = _apply_slippage(price, state.direction, exiting=True, cfg=cfg)
                trade.exit_time = bar["date"]
                trade.exit_price = exit_price
                trade.exit_reason = "target"
                return trade

    if trade is not None and trade.exit_price is None:
        # Data ran out (or hit the flat_time branch above) without an explicit stop/target hit.
        last_bar = post_or_bars.iloc[-1]
        trade.exit_time = trade.exit_time or last_bar["date"]
        trade.exit_price = trade.exit_price if trade.exit_price is not None else _apply_slippage(
            last_bar["close"], trade.direction, exiting=True, cfg=cfg
        )
        trade.exit_reason = trade.exit_reason or "data_end"

    return trade


def _apply_slippage(price: float, direction: str, exiting: bool, cfg: ORBConfig) -> float:
    if cfg.slippage_ticks == 0:
        return price
    adj = cfg.slippage_ticks * cfg.tick_size
    # Slippage always works against the trader.
    if direction == "long":
        return price - adj if exiting else price + adj
    else:
        return price + adj if exiting else price - adj


def _finalize_pnl(trade: Trade, cfg: ORBConfig) -> None:
    ticks = (trade.exit_price - trade.entry_price) / cfg.tick_size
    if trade.direction == "short":
        ticks = -ticks
    gross = ticks * cfg.tick_value * trade.contracts
    commission = cfg.commission_per_side * 2 * trade.contracts
    trade.pnl = gross - commission
    risk_dollars_total = trade.risk_per_contract_dollars * trade.contracts
    trade.r_multiple = trade.pnl / risk_dollars_total if risk_dollars_total else 0.0


def run_backtest(bars: pd.DataFrame, cfg: ORBConfig, compound: bool = True) -> list[Trade]:
    """Run the ORB backtest over all trading days present in `bars`.

    If `compound` is True (default), each day's risk-% position sizing is
    computed off the running account equity (starting balance +/- realized
    P&L so far). If False, every day sizes off the fixed starting
    `cfg.account_size`.
    """
    bars = bars.copy()
    bars["time"] = bars["date"].dt.time
    bars["day"] = bars["date"].dt.date

    trades: list[Trade] = []
    equity = cfg.account_size
    for day, day_bars in bars.groupby("day", sort=True):
        day_bars = day_bars.sort_values("date")
        trade = _simulate_day(day_bars, day, cfg, account_equity=equity)
        if trade is not None:
            _finalize_pnl(trade, cfg)
            trades.append(trade)
            if compound:
                equity += trade.pnl
    return trades
