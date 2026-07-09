"""CSV trade journal.

Every signal (taken or skipped), every no-trade day, and every exit
(flatten or fill) gets a row so you have a full audit trail of what the
bot decided, when, and why - entry time, exit time, total risk, and a
human-readable explanation of the entry logic.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from orb_bot.orb_strategy import TradeSignal

FIELDNAMES = [
    "logged_at",
    "session_date",
    "event",
    "direction",
    "entry_time",
    "entry_price",
    "stop_price",
    "target_price",
    "contracts",
    "risk_per_contract_usd",
    "total_risk_usd",
    "or_high",
    "or_low",
    "exit_time",
    "exit_price",
    "note",
]


class TradeLog:
    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=FIELDNAMES).writeheader()

    def _write_row(self, row: dict) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    def log_signal(self, signal: TradeSignal) -> None:
        row = asdict(signal)
        row["session_date"] = signal.session_date.isoformat()
        row["entry_time"] = signal.signal_time.isoformat()
        row["logged_at"] = datetime.now(timezone.utc).isoformat()
        row["event"] = "trade_entry"
        row["note"] = signal.reason
        row.pop("signal_time", None)
        row.pop("reason", None)
        self._write_row(row)

    def log_no_trade(self, session_date: date, reason: str) -> None:
        self._write_row(
            {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "session_date": session_date.isoformat() if session_date else "",
                "event": "no_trade",
                "note": reason,
            }
        )

    def log_exit(
        self,
        session_date: date,
        exit_time: datetime,
        exit_price: float,
        reason: str,
        event: str = "trade_exit",
    ) -> None:
        self._write_row(
            {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "session_date": session_date.isoformat() if session_date else "",
                "event": event,
                "exit_time": exit_time.isoformat() if exit_time else "",
                "exit_price": exit_price,
                "note": reason,
            }
        )

    def log_flatten(self, session_date: date, exit_time: datetime, price: float, reason: str) -> None:
        self.log_exit(session_date, exit_time, price, reason, event="flatten")
