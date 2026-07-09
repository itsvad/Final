"""Simple local read-only dashboard for the live ORB bot.

Just polls two files the bot itself writes - status.json (current
session state) and trades.csv (the trade journal) - and serves them as
JSON to a static single-page frontend. No database, no bot control
(start/stop/place orders) from here; it's a monitor, not a cockpit.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, send_from_directory

from orb_bot.config import load_config

app = Flask(__name__, static_folder="static", static_url_path="")

STATE: dict = {"status_path": None, "trades_path": None}


def _read_status() -> Optional[dict]:
    path = STATE["status_path"]
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_trade_rows() -> list[dict]:
    path = STATE["trades_path"]
    if not path or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _to_float(value) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_pnl(entry: dict, exit_row: dict) -> tuple[Optional[float], Optional[float]]:
    direction = entry.get("direction")
    entry_price = _to_float(entry.get("entry_price"))
    stop_price = _to_float(entry.get("stop_price"))
    contracts = _to_float(entry.get("contracts"))
    risk_per_contract = _to_float(entry.get("risk_per_contract_usd"))
    exit_price = _to_float(exit_row.get("exit_price"))
    if None in (entry_price, stop_price, contracts, risk_per_contract, exit_price) or entry_price == stop_price:
        return None, None
    sign = 1 if direction == "long" else -1
    price_pnl = (exit_price - entry_price) * sign
    dollars_per_point = risk_per_contract / abs(entry_price - stop_price)
    pnl_usd = price_pnl * dollars_per_point * contracts
    risk_usd = risk_per_contract * contracts
    r_multiple = (pnl_usd / risk_usd) if risk_usd else None
    return round(pnl_usd, 2), (round(r_multiple, 3) if r_multiple is not None else None)


@app.get("/api/status")
def api_status():
    return jsonify(_read_status() or {})


@app.get("/api/trades")
def api_trades():
    rows = _read_trade_rows()

    by_day: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        day = row.get("session_date") or "unknown"
        if day not in by_day:
            by_day[day] = []
            order.append(day)
        by_day[day].append(row)

    trades = []
    for day in order:
        events = by_day[day]
        entries = [r for r in events if r.get("event") == "trade_entry"]
        exits = [r for r in events if r.get("event") in ("trade_exit", "flatten")]
        no_trades = [r for r in events if r.get("event") == "no_trade"]

        for i, entry in enumerate(entries):
            exit_row = exits[i] if i < len(exits) else None
            trade = {
                "session_date": day,
                "direction": entry.get("direction"),
                "entry_time": entry.get("entry_time"),
                "entry_price": _to_float(entry.get("entry_price")),
                "stop_price": _to_float(entry.get("stop_price")),
                "target_price": _to_float(entry.get("target_price")),
                "contracts": _to_float(entry.get("contracts")),
                "risk_per_contract_usd": _to_float(entry.get("risk_per_contract_usd")),
                "total_risk_usd": _to_float(entry.get("total_risk_usd")),
                "or_high": _to_float(entry.get("or_high")),
                "or_low": _to_float(entry.get("or_low")),
                "reason": entry.get("note"),
                "exit_time": exit_row.get("exit_time") if exit_row else None,
                "exit_price": _to_float(exit_row.get("exit_price")) if exit_row else None,
                "exit_note": exit_row.get("note") if exit_row else None,
                "status": "open" if exit_row is None else "closed",
                "pnl_usd": None,
                "r_multiple": None,
            }
            if exit_row is not None:
                pnl_usd, r_multiple = _compute_pnl(entry, exit_row)
                trade["pnl_usd"] = pnl_usd
                trade["r_multiple"] = r_multiple
            trades.append(trade)

        for nt in no_trades:
            trades.append(
                {
                    "session_date": day,
                    "status": "no_trade",
                    "reason": nt.get("note"),
                    "logged_at": nt.get("logged_at"),
                }
            )

    trades.sort(key=lambda t: (t["session_date"], t.get("entry_time") or ""), reverse=True)
    return jsonify(trades)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dashboard for the ORB bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (keep 127.0.0.1 for local-only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    config = load_config(args.config)
    STATE["status_path"] = Path(config.logging.status_json)
    STATE["trades_path"] = Path(config.logging.trade_log_csv)

    print(f"ORB bot dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
