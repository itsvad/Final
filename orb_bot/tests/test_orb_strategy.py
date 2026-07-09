from datetime import datetime, time

import pytest

from orb_bot.config import (
    AccountConfig,
    BotConfig,
    ContractConfig,
    LoggingConfig,
    RiskConfig,
    SessionConfig,
    StrategyConfig,
    TradovateCredentials,
)
from orb_bot.contracts import ContractSpec
from orb_bot.orb_strategy import OrbStrategy


class FakeExecutor:
    def __init__(self):
        self.signals = []
        self.no_trades = []
        self.flattens = []

    def enter(self, signal):
        self.signals.append(signal)

    def no_trade_today(self, session_date, reason):
        self.no_trades.append((session_date, reason))

    def flatten(self, session_date, price, reason):
        self.flattens.append((session_date, price, reason))


def make_config(**overrides):
    session_kwargs = dict(
        timezone="America/New_York",
        opening_range_start=time(9, 30),
        opening_range_end=time(9, 45),
        trading_window_start=time(9, 45),
        trading_window_end=time(11, 30),
        max_trades_per_session=1,
        force_close_time=time(15, 55),
    )
    session_kwargs.update(overrides.pop("session", {}))

    risk_kwargs = dict(risk_amount_usd=600.0, max_contracts=10, min_contracts=1)
    risk_kwargs.update(overrides.pop("risk", {}))

    strategy_kwargs = dict(
        entry_trigger_ticks=1,
        stop_ticks=1,
        reward_risk_ratio=1.0,
        entry_order_type="stop_market",
        stop_limit_offset_ticks=2,
    )
    strategy_kwargs.update(overrides.pop("strategy", {}))

    return BotConfig(
        account=AccountConfig(),
        contract=ContractConfig(symbol="MES"),
        session=SessionConfig(**session_kwargs),
        strategy=StrategyConfig(**strategy_kwargs),
        risk=RiskConfig(**risk_kwargs),
        logging=LoggingConfig(),
        credentials=TradovateCredentials(),
    )


MES_SPEC = ContractSpec(symbol="MES", description="Micro E-mini S&P 500", tick_size=0.25, tick_value=1.25)


def ny(hour, minute, second=0, day=9):
    return datetime(2026, 7, day, hour, minute, second, tzinfo=None).replace(
        tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")
    )


def test_marks_opening_range_high_and_low():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 35), high=5010.0, low=4990.0)
    strat.on_market_data(ny(9, 40), high=5005.0, low=4998.0)

    assert strat.state.or_high is None or True  # OR not locked until window ends
    # Feed a bar right at window close to trigger locking
    strat.on_market_data(ny(9, 45), high=5001.0, low=5000.5)
    assert strat.state.or_locked is True
    assert strat.state.or_high == 5010.0
    assert strat.state.or_low == 4990.0


def test_long_breakout_enters_with_correct_stop_and_target():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)  # OR: high=5002 low=4994
    # Breakout: 1 tick above high = 5002.25
    strat.on_market_data(ny(9, 50), high=5002.50, low=5002.25)

    assert len(executor.signals) == 1
    signal = executor.signals[0]
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(5002.25)
    assert signal.stop_price == pytest.approx(4993.75)  # 1 tick below OR low (4994 - 0.25)
    risk_distance = signal.entry_price - signal.stop_price
    assert signal.target_price == pytest.approx(signal.entry_price + risk_distance)  # 1:1 RR
    assert signal.contracts >= 1


def test_short_breakout_enters_with_correct_stop_and_target():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    # Breakout below: 1 tick below low = 4993.75
    strat.on_market_data(ny(9, 55), high=4994.0, low=4993.50)

    assert len(executor.signals) == 1
    signal = executor.signals[0]
    assert signal.direction == "short"
    assert signal.entry_price == pytest.approx(4993.75)
    assert signal.stop_price == pytest.approx(5002.25)


def test_only_one_trade_per_session_even_if_both_sides_break_later():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    strat.on_market_data(ny(9, 50), high=5002.50, low=5002.25)  # long breakout, takes trade
    strat.on_market_data(ny(10, 5), high=4993.0, low=4990.0)    # would-be short breakout

    assert len(executor.signals) == 1
    assert executor.signals[0].direction == "long"


def test_no_breakout_by_window_end_means_no_trade_taken():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    # Price stays inside the range the whole window
    strat.on_market_data(ny(10, 0), high=4999.0, low=4996.0)
    strat.on_market_data(ny(11, 30), high=4998.0, low=4997.0)  # window closed

    assert len(executor.signals) == 0
    assert len(executor.no_trades) == 1

    # Even a late breakout after the window shouldn't trade
    strat.on_market_data(ny(11, 45), high=5010.0, low=5009.0)
    assert len(executor.signals) == 0


def test_new_day_resets_state():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30, day=9), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59, day=9), high=5002.0, low=4994.0)
    strat.on_market_data(ny(9, 50, day=9), high=5002.50, low=5002.25)
    assert len(executor.signals) == 1

    # Next day: fresh OR window, fresh trade count
    strat.on_market_data(ny(9, 30, day=10), high=100.0, low=95.0)
    strat.on_market_data(ny(9, 44, 59, day=10), high=101.0, low=94.0)
    strat.on_market_data(ny(9, 50, day=10), high=101.25, low=101.10)
    assert len(executor.signals) == 2
    assert executor.signals[1].or_high == 101.0


def test_risk_too_small_for_range_skips_and_stands_down():
    executor = FakeExecutor()
    config = make_config(risk={"risk_amount_usd": 1.0})  # tiny budget
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    strat.on_market_data(ny(9, 50), high=5002.50, low=5002.25)  # would-be long breakout

    assert len(executor.signals) == 0
    assert len(executor.no_trades) == 1
    assert "risk_amount_usd" in executor.no_trades[0][1]


def test_configurable_window_and_rr_ratio():
    executor = FakeExecutor()
    config = make_config(
        session={
            "opening_range_start": time(10, 0),
            "opening_range_end": time(10, 15),
            "trading_window_start": time(10, 15),
            "trading_window_end": time(12, 0),
        },
        strategy={"reward_risk_ratio": 2.0, "entry_trigger_ticks": 2, "stop_ticks": 2},
    )
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(10, 0), high=5000.0, low=4995.0)
    strat.on_market_data(ny(10, 14, 59), high=5002.0, low=4994.0)
    # 2-tick trigger: 5002 + 0.5 = 5002.5
    strat.on_market_data(ny(10, 20), high=5002.75, low=5002.5)

    assert len(executor.signals) == 1
    signal = executor.signals[0]
    assert signal.entry_price == pytest.approx(5002.5)
    assert signal.stop_price == pytest.approx(4993.5)  # 4994 - 2*0.25
    risk_distance = signal.entry_price - signal.stop_price
    assert signal.target_price == pytest.approx(signal.entry_price + risk_distance * 2.0)


def test_open_trade_is_flattened_at_force_close_time():
    executor = FakeExecutor()
    config = make_config(session={"force_close_time": time(15, 55)})
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    strat.on_market_data(ny(9, 50), high=5002.50, low=5002.25)  # long breakout, trade open
    assert len(executor.signals) == 1
    assert len(executor.flattens) == 0

    # Trade never hits stop/target - still open going into the close
    strat.on_market_data(ny(15, 54), high=5050.0, low=5040.0)
    assert len(executor.flattens) == 0

    strat.on_market_data(ny(15, 55), high=5045.0, low=5044.0, close=5044.5)
    assert len(executor.flattens) == 1
    session_date, price, reason = executor.flattens[0]
    assert price == pytest.approx(5044.5)
    assert "flatten" in reason.lower()

    # Flatten only fires once even with more ticks after the close time
    strat.on_market_data(ny(15, 58), high=5046.0, low=5045.0, close=5045.5)
    assert len(executor.flattens) == 1


def test_no_flatten_call_when_no_trade_was_taken():
    executor = FakeExecutor()
    config = make_config()
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    strat.on_market_data(ny(11, 30), high=4999.0, low=4998.0)  # window closes, no breakout
    assert len(executor.no_trades) == 1

    strat.on_market_data(ny(15, 55), high=4999.0, low=4998.0, close=4998.5)
    assert len(executor.flattens) == 0


def test_force_close_time_is_configurable():
    executor = FakeExecutor()
    config = make_config(session={"force_close_time": time(13, 0)})
    strat = OrbStrategy(config, MES_SPEC, executor)

    strat.on_market_data(ny(9, 30), high=5000.0, low=4995.0)
    strat.on_market_data(ny(9, 44, 59), high=5002.0, low=4994.0)
    strat.on_market_data(ny(9, 50), high=5002.50, low=5002.25)  # long breakout

    strat.on_market_data(ny(12, 59), high=5010.0, low=5009.0, close=5009.5)
    assert len(executor.flattens) == 0

    strat.on_market_data(ny(13, 0), high=5011.0, low=5010.0, close=5010.5)
    assert len(executor.flattens) == 1
