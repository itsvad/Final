"""Summary statistics + CSV export for a backtest run."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orb_bot.backtest.engine import CompletedTrade


@dataclass
class BacktestReport:
    trades: list[CompletedTrade] = field(default_factory=list)
    no_trade_days: list[tuple] = field(default_factory=list)

    @property
    def total_sessions(self) -> int:
        return len(self.trades) + len(self.no_trade_days)

    @property
    def wins(self) -> list[CompletedTrade]:
        return [t for t in self.trades if t.pnl_usd > 0]

    @property
    def losses(self) -> list[CompletedTrade]:
        return [t for t in self.trades if t.pnl_usd <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / len(self.trades) if self.trades else 0.0

    @property
    def total_pnl_usd(self) -> float:
        return sum(t.pnl_usd for t in self.trades)

    @property
    def total_r(self) -> float:
        return sum(t.r_multiple for t in self.trades)

    @property
    def avg_r(self) -> float:
        return self.total_r / len(self.trades) if self.trades else 0.0

    def equity_curve(self, starting_equity: float = 0.0) -> list[float]:
        equity = starting_equity
        curve = [equity]
        for t in sorted(self.trades, key=lambda t: t.session_date):
            equity += t.pnl_usd
            curve.append(equity)
        return curve

    def max_drawdown_usd(self, starting_equity: float = 0.0) -> float:
        curve = self.equity_curve(starting_equity)
        peak = curve[0]
        max_dd = 0.0
        for value in curve:
            peak = max(peak, value)
            max_dd = min(max_dd, value - peak)
        return max_dd

    def summary(self) -> str:
        lines = [
            f"Sessions evaluated:  {self.total_sessions}",
            f"Trades taken:        {len(self.trades)}",
            f"No-trade sessions:   {len(self.no_trade_days)}",
            f"Wins / Losses:       {len(self.wins)} / {len(self.losses)}",
            f"Win rate:            {self.win_rate:.1%}",
            f"Total P&L:           ${self.total_pnl_usd:,.2f}",
            f"Total R:             {self.total_r:+.2f}R",
            f"Average R per trade: {self.avg_r:+.2f}R",
            f"Max drawdown:        ${self.max_drawdown_usd():,.2f}",
        ]
        return "\n".join(lines)

    def to_csv(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(asdict(self.trades[0]).keys()) if self.trades else [
            "session_date", "direction", "entry_price", "stop_price", "target_price",
            "exit_price", "exit_reason", "contracts", "risk_per_contract_usd",
            "pnl_usd", "r_multiple",
        ]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for t in self.trades:
                row = asdict(t)
                row["session_date"] = row["session_date"].isoformat()
                writer.writerow(row)
