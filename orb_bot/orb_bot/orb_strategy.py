"""Core Opening Range Breakout (ORB) strategy state machine.

This class contains all of the trading logic and is completely
decoupled from Tradovate / live vs. backtest concerns:

  - It is fed price updates via `on_market_data(ts, high, low, open_)`.
    For tick/quote data pass high == low == last price. For OHLC bars
    pass the bar's high/low (and optionally open, used only to break
    ties when a single bar's range spans both breakout levels).
  - It marks the opening-range high/low during the configured OR
    window.
  - Once the OR is locked, it watches for a tick above the OR high
    (long) or a tick below the OR low (short) during the trading
    window.
  - On breakout it sizes the trade from the configured risk amount and
    emits a TradeSignal via the `executor` it was given. Only one
    signal is emitted per session (configurable).
  - If no breakout occurs before the trading window ends, it stands
    down for the rest of the day.

It knows nothing about HTTP, WebSockets, or Tradovate order types -
that lives in the executor implementation (live TradovateExecutor or
backtest SimExecutor) passed into the constructor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Protocol

from orb_bot.config import BotConfig
from orb_bot.contracts import ContractSpec
from orb_bot.position_sizer import calculate_position_size
from orb_bot.session import SessionClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeSignal:
    session_date: date
    direction: str          # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    contracts: int
    risk_per_contract_usd: float
    total_risk_usd: float
    or_high: float
    or_low: float
    signal_time: datetime


class TradeExecutor(Protocol):
    """Implemented by TradovateExecutor (live) and SimExecutor (backtest)."""

    def enter(self, signal: TradeSignal) -> None: ...

    def no_trade_today(self, session_date: date, reason: str) -> None: ...


@dataclass
class _DayState:
    session_date: Optional[date] = None
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_locked: bool = False
    trades_taken: int = 0
    standing_down: bool = False       # no more signals will be evaluated today
    window_closed_logged: bool = False


class OrbStrategy:
    def __init__(
        self,
        config: BotConfig,
        contract_spec: ContractSpec,
        executor: TradeExecutor,
        clock: Optional[SessionClock] = None,
    ) -> None:
        self.config = config
        self.spec = contract_spec
        self.executor = executor
        self.clock = clock or SessionClock(config.session)
        self.state = _DayState()

    # -- day bookkeeping -------------------------------------------------

    def _ensure_day(self, ts: datetime) -> None:
        today = self.clock.session_date(ts)
        if self.state.session_date != today:
            self.state = _DayState(session_date=today)
            logger.info("New session day: %s", today)

    def _stand_down(self, reason: str) -> None:
        if not self.state.standing_down:
            self.state.standing_down = True
            logger.info("Standing down for %s: %s", self.state.session_date, reason)
            # Only notify the executor of a "no trade" outcome if a trade
            # wasn't actually taken - standing down after a fill is just
            # bookkeeping, not a no-trade session.
            if self.state.trades_taken == 0:
                self.executor.no_trade_today(self.state.session_date, reason)

    # -- main entry point --------------------------------------------------

    def on_market_data(
        self,
        ts: datetime,
        high: float,
        low: float,
        open_: Optional[float] = None,
    ) -> None:
        self._ensure_day(ts)
        state = self.state

        if state.standing_down:
            return

        if self.clock.in_opening_range(ts):
            self._update_opening_range(high, low)
            return

        if not state.or_locked:
            if self.clock.opening_range_complete(ts):
                self._lock_opening_range()
            else:
                # Before the OR window even opened (e.g. pre-market tick).
                return

        if state.trades_taken >= self.config.session.max_trades_per_session:
            self._stand_down("max trades for session already taken")
            return

        if self.clock.in_trading_window(ts):
            self._check_breakout(ts, high, low, open_)
        elif self.clock.trading_window_passed(ts):
            if not state.window_closed_logged:
                state.window_closed_logged = True
                self._stand_down(
                    "trading window closed with no breakout - no trade today"
                )

    # -- opening range -----------------------------------------------------

    def _update_opening_range(self, high: float, low: float) -> None:
        state = self.state
        state.or_high = high if state.or_high is None else max(state.or_high, high)
        state.or_low = low if state.or_low is None else min(state.or_low, low)

    def _lock_opening_range(self) -> None:
        state = self.state
        state.or_locked = True
        if state.or_high is None or state.or_low is None:
            self._stand_down("no price data observed during opening range window")
            return
        logger.info(
            "Opening range locked for %s: high=%.4f low=%.4f",
            state.session_date,
            state.or_high,
            state.or_low,
        )

    # -- breakout detection --------------------------------------------------

    def _check_breakout(
        self, ts: datetime, high: float, low: float, open_: Optional[float]
    ) -> None:
        state = self.state
        if state.or_high is None or state.or_low is None:
            return

        tick = self.spec.tick_size
        entry_offset = self.config.strategy.entry_trigger_ticks * tick
        long_trigger = state.or_high + entry_offset
        short_trigger = state.or_low - entry_offset

        long_hit = high >= long_trigger
        short_hit = low <= short_trigger

        if long_hit and short_hit:
            # A single bar spanned both trigger levels (only possible
            # with coarse bar data, never with tick data since high==low
            # there). Use the open price to guess which side printed
            # first; default to long if we have no way to tell.
            if open_ is not None and abs(open_ - short_trigger) < abs(open_ - long_trigger):
                long_hit, short_hit = False, True
            else:
                long_hit, short_hit = True, False

        if long_hit:
            self._enter("long", ts)
        elif short_hit:
            self._enter("short", ts)

    def _enter(self, direction: str, ts: datetime) -> None:
        state = self.state
        tick = self.spec.tick_size
        entry_offset = self.config.strategy.entry_trigger_ticks * tick
        stop_offset = self.config.strategy.stop_ticks * tick
        rr = self.config.strategy.reward_risk_ratio

        if direction == "long":
            entry_price = state.or_high + entry_offset
            stop_price = state.or_low - stop_offset
        else:
            entry_price = state.or_low - entry_offset
            stop_price = state.or_high + stop_offset

        stop_distance = abs(entry_price - stop_price)
        stop_distance_ticks = round(stop_distance / tick)

        sizing = calculate_position_size(
            stop_distance_ticks=stop_distance_ticks,
            tick_value=self.spec.tick_value,
            risk_amount_usd=self.config.risk.risk_amount_usd,
            min_contracts=self.config.risk.min_contracts,
            max_contracts=self.config.risk.max_contracts,
        )

        if not sizing.tradeable:
            # Range too wide for the configured risk budget - can't take
            # a trade in either direction today, since the OR range is fixed.
            self._stand_down(f"breakout detected but skipped: {sizing.skipped_reason}")
            return

        risk_distance = stop_distance
        if direction == "long":
            target_price = entry_price + risk_distance * rr
        else:
            target_price = entry_price - risk_distance * rr

        signal = TradeSignal(
            session_date=state.session_date,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            contracts=sizing.contracts,
            risk_per_contract_usd=sizing.risk_per_contract_usd,
            total_risk_usd=sizing.total_risk_usd,
            or_high=state.or_high,
            or_low=state.or_low,
            signal_time=ts,
        )

        state.trades_taken += 1
        logger.info(
            "ORB %s signal: entry=%.4f stop=%.4f target=%.4f contracts=%d risk=$%.2f",
            direction,
            entry_price,
            stop_price,
            target_price,
            sizing.contracts,
            sizing.total_risk_usd,
        )
        self.executor.enter(signal)

        if state.trades_taken >= self.config.session.max_trades_per_session:
            self._stand_down("trade taken - max trades per session reached")
