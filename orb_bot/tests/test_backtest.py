from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from orb_bot.backtest.engine import run_backtest
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

NY = ZoneInfo("America/New_York")
MES_SPEC = ContractSpec(symbol="MES", description="Micro E-mini S&P 500", tick_size=0.25, tick_value=1.25)


def make_config(risk_amount_usd=600.0):
    return BotConfig(
        account=AccountConfig(),
        contract=ContractConfig(symbol="MES"),
        session=SessionConfig(
            opening_range_start=time(9, 30),
            opening_range_end=time(9, 45),
            trading_window_start=time(9, 45),
            trading_window_end=time(11, 30),
            max_trades_per_session=1,
        ),
        strategy=StrategyConfig(reward_risk_ratio=1.0, entry_trigger_ticks=1, stop_ticks=1),
        risk=RiskConfig(risk_amount_usd=risk_amount_usd, max_contracts=10, min_contracts=1),
        logging=LoggingConfig(),
        credentials=TradovateCredentials(),
    )


def bar(day, hour, minute, o, h, l, c):
    return {
        "timestamp": datetime(2026, 7, day, hour, minute, tzinfo=NY),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def test_backtest_winning_long_trade_hits_target():
    rows = [
        bar(9, 9, 30, 5000, 5002, 4995, 5000),
        bar(9, 9, 44, 5000, 5002, 4994, 5000),  # OR: high=5002 low=4994
        bar(9, 9, 50, 5002.3, 5003.0, 5002.25, 5002.9),  # breakout long, entry 5002.25
        bar(9, 10, 0, 5003.0, 5011.0, 5002.5, 5010.0),   # target hit (entry+risk=5010.75? check)
    ]
    df = pd.DataFrame(rows)
    config = make_config()
    report = run_backtest(config, MES_SPEC, df)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.direction == "long"
    assert trade.exit_reason == "target"
    assert trade.pnl_usd > 0
    assert trade.r_multiple == 1.0


def test_backtest_losing_long_trade_hits_stop():
    rows = [
        bar(9, 9, 30, 5000, 5002, 4995, 5000),
        bar(9, 9, 44, 5000, 5002, 4994, 5000),
        bar(9, 9, 50, 5002.3, 5003.0, 5002.25, 5002.9),  # breakout long
        bar(9, 10, 0, 5002.5, 5002.6, 4990.0, 4991.0),   # crashes through stop
    ]
    df = pd.DataFrame(rows)
    config = make_config()
    report = run_backtest(config, MES_SPEC, df)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.pnl_usd < 0
    assert trade.r_multiple == -1.0


def test_backtest_no_trade_day_recorded():
    rows = [
        bar(9, 9, 30, 5000, 5002, 4995, 5000),
        bar(9, 9, 44, 5000, 5002, 4994, 5000),
        bar(9, 10, 0, 5000, 5001, 4996, 5000),   # stays inside range
        bar(9, 11, 30, 5000, 5001, 4997, 5000),  # window closes
    ]
    df = pd.DataFrame(rows)
    config = make_config()
    report = run_backtest(config, MES_SPEC, df)

    assert len(report.trades) == 0
    assert len(report.no_trade_days) == 1


def test_backtest_eod_force_close_when_unresolved():
    rows = [
        bar(9, 9, 30, 5000, 5002, 4995, 5000),
        bar(9, 9, 44, 5000, 5002, 4994, 5000),
        bar(9, 9, 50, 5002.3, 5003.0, 5002.25, 5002.9),  # breakout long
        bar(9, 11, 29, 5003.0, 5005.0, 5001.0, 5004.0),  # never hits stop or target
    ]
    df = pd.DataFrame(rows)
    config = make_config()
    report = run_backtest(config, MES_SPEC, df)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == "eod_close"
    assert trade.exit_price == 5004.0


def test_backtest_multiple_days_independent_sessions():
    rows = [
        # Day 9: winning long
        bar(9, 9, 30, 5000, 5002, 4995, 5000),
        bar(9, 9, 44, 5000, 5002, 4994, 5000),
        bar(9, 9, 50, 5002.3, 5003.0, 5002.25, 5002.9),
        bar(9, 10, 0, 5003.0, 5011.0, 5002.5, 5010.0),
        # Day 10: no trade
        bar(10, 9, 30, 100, 102, 95, 100),
        bar(10, 9, 44, 100, 102, 94, 100),
        bar(10, 11, 30, 100, 101, 96, 100),
    ]
    df = pd.DataFrame(rows)
    config = make_config()
    report = run_backtest(config, MES_SPEC, df)

    assert len(report.trades) == 1
    assert len(report.no_trade_days) == 1
    assert report.total_sessions == 2
