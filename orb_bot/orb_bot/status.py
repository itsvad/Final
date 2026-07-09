"""Writes the live bot's current state to a JSON file the local dashboard polls.

Kept deliberately dumb: one small JSON blob overwritten atomically on an
interval. No database, no server-side state beyond this file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class BotStatus:
    updated_at: str
    running: bool
    environment: str
    dry_run: bool
    symbol: str
    contract_name: Optional[str]
    risk_amount_usd: float
    reward_risk_ratio: float
    max_trades_per_session: int
    opening_range_start: str
    opening_range_end: str
    trading_window_start: str
    trading_window_end: str
    force_close_time: str
    session_date: Optional[str]
    or_high: Optional[float]
    or_low: Optional[float]
    or_locked: bool
    trades_taken: int
    standing_down: bool
    last_price: Optional[float]
    last_price_at: Optional[str]


class StatusWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, status: BotStatus) -> None:
        # Write to a temp file then rename so the dashboard never reads a
        # half-written file mid-poll.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def write_stopped(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        data["running"] = False
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
