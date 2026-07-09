"""Backtest engine: replays historical 1-min bars through OrbStrategy.

Simplifying assumptions (inherent to bar-level backtesting, called out
so results aren't over-trusted):
  - Entries fill exactly at the calculated trigger price (no slippage).
  - When a bar's range covers both the stop and the target, the stop is
    assumed to have been hit first (conservative).
  - Any trade still open at session.force_close_time is flattened at
    that bar's close price, regardless of P&L.
  - If the day's data ends before force_close_time is reached (a
    partial/incomplete feed), the trade is marked-to-market closed at
    the last available bar's close instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from orb_bot.config import BotConfig
from orb_bot.contracts import ContractSpec
from orb_bot.orb_strategy import OrbStrategy, TradeExecutor, TradeSignal
from orb_bot.session import SessionClock


@dataclass
class CompletedTrade:
    session_date: date
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float
    exit_reason: str  # "target" | "stop" | "force_flatten" | "eod_close"
    contracts: int
    risk_per_contract_usd: float
    pnl_usd: float
    r_multiple: float


class SimExecutor(TradeExecutor):
    """TradeExecutor for backtesting: records signals, resolves outcomes bar by bar."""

    def __init__(self, tick_value: float) -> None:
        self.tick_value = tick_value
        self._open: Optional[dict] = None
        self.completed_trades: list[CompletedTrade] = []
        self.no_trade_days: list[tuple[date, str]] = []

    def enter(self, signal: TradeSignal) -> None:
        self._open = {"signal": signal}

    def no_trade_today(self, session_date: date, reason: str) -> None:
        self.no_trade_days.append((session_date, reason))

    def flatten(self, session_date: date, price: float, reason: str) -> None:
        if self._open is None:
            return
        signal: TradeSignal = self._open["signal"]
        self._finish(signal, price, "force_flatten")

    def has_open_trade(self) -> bool:
        return self._open is not None

    def resolve_bar(self, high: float, low: float, close: float) -> None:
        if self._open is None:
            return
        signal: TradeSignal = self._open["signal"]

        if signal.direction == "long":
            hit_stop = low <= signal.stop_price
            hit_target = high >= signal.target_price
        else:
            hit_stop = high >= signal.stop_price
            hit_target = low <= signal.target_price

        if hit_stop and hit_target:
            hit_target = False  # conservative: assume stop hit first

        if not (hit_stop or hit_target):
            return

        exit_price = signal.stop_price if hit_stop else signal.target_price
        exit_reason = "stop" if hit_stop else "target"
        self._finish(signal, exit_price, exit_reason)

    def force_close_eod(self, close_price: float) -> None:
        """Fallback for when the day's data ends before force_close_time is
        reached (e.g. an incomplete/partial data feed) - the strategy itself
        already flattens open trades at session.force_close_time via
        `flatten()` when there's data covering that time."""
        if self._open is None:
            return
        signal: TradeSignal = self._open["signal"]
        self._finish(signal, close_price, "eod_close")

    def _finish(self, signal: TradeSignal, exit_price: float, reason: str) -> None:
        direction_sign = 1 if signal.direction == "long" else -1
        price_pnl = (exit_price - signal.entry_price) * direction_sign
        pnl_usd = price_pnl * (signal.risk_per_contract_usd / abs(signal.entry_price - signal.stop_price)) * signal.contracts
        risk_usd = signal.risk_per_contract_usd * signal.contracts
        r_multiple = pnl_usd / risk_usd if risk_usd else 0.0

        self.completed_trades.append(
            CompletedTrade(
                session_date=signal.session_date,
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                exit_price=exit_price,
                exit_reason=reason,
                contracts=signal.contracts,
                risk_per_contract_usd=signal.risk_per_contract_usd,
                pnl_usd=pnl_usd,
                r_multiple=r_multiple,
            )
        )
        self._open = None


def run_backtest(config: BotConfig, spec: ContractSpec, bars_df) -> "BacktestReport":
    from orb_bot.backtest.data_loader import iter_session_days
    from orb_bot.backtest.report import BacktestReport

    clock = SessionClock(config.session)
    all_trades: list[CompletedTrade] = []
    all_no_trade_days: list[tuple[date, str]] = []

    for day, bars in iter_session_days(bars_df, clock):
        executor = SimExecutor(spec.tick_value)
        strategy = OrbStrategy(config, spec, executor, clock)

        last_close = None
        for bar in bars:
            ts, o, h, l, c = bar["timestamp"], bar["open"], bar["high"], bar["low"], bar["close"]
            strategy.on_market_data(ts, high=h, low=l, open_=o, close=c)
            executor.resolve_bar(h, l, c)
            last_close = c

        if executor.has_open_trade() and last_close is not None:
            executor.force_close_eod(last_close)

        all_trades.extend(executor.completed_trades)
        all_no_trade_days.extend(executor.no_trade_days)

    return BacktestReport(trades=all_trades, no_trade_days=all_no_trade_days)
