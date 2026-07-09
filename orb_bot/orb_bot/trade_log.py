"""CSV trade journal.

Every signal (taken or skipped) and every no-trade day gets a row so
you have a full audit trail of what the bot decided and why.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from orb_bot.orb_strategy import TradeSignal

FIELDNAMES = [
    "logged_at",
    "session_date",
    "event",
    "direction",
    "entry_price",
    "stop_price",
    "target_price",
    "contracts",
    "risk_per_contract_usd",
    "total_risk_usd",
    "or_high",
    "or_low",
    "flatten_price",
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
        from datetime import datetime, timezone

        row = asdict(signal)
        row["session_date"] = signal.session_date.isoformat()
        row["logged_at"] = datetime.now(timezone.utc).isoformat()
        row["event"] = "trade_signal"
        row.pop("signal_time", None)
        self._write_row(row)

    def log_no_trade(self, session_date, reason: str) -> None:
        from datetime import datetime, timezone

        self._write_row(
            {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "session_date": session_date.isoformat() if session_date else "",
                "event": "no_trade",
                "note": reason,
            }
        )

    def log_flatten(self, session_date, price: float, reason: str) -> None:
        from datetime import datetime, timezone

        self._write_row(
            {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "session_date": session_date.isoformat() if session_date else "",
                "event": "flatten",
                "flatten_price": price,
                "note": reason,
            }
        )
