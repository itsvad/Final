"""Minimal Tradovate REST + WebSocket client.

Implements just what the ORB bot needs:
  - password-grant authentication + token renewal
  - front-month contract lookup
  - placing an entry order that automatically sends a stop-loss / take
    profit OCO bracket on fill (Tradovate's "placeOSO" endpoint)
  - a real-time quote WebSocket feed (last-traded price) used to drive
    the strategy's on_market_data() calls

Tradovate's WebSocket protocol frames every message with a one-letter
type prefix ('o' open, 'h' heartbeat, 'a' array-of-messages, 'c' close)
and every client request is "<endpoint>\\n<requestId>\\n<query>\\n<body>".
This is documented behavior of the Tradovate API (the same protocol
used by their own web trader and community SDKs), not something specific
to this bot.

NOTE: This talks to a real broker. `account.dry_run: true` in
config.yaml (the default) makes TradovateExecutor log intended orders
instead of sending them. Test thoroughly on `account.environment: demo`
before ever flipping dry_run off or pointing at "live".
"""

from __future__ import annotations

import json
import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Optional

import requests
import websocket  # from websocket-client

from orb_bot.config import BotConfig, TradovateCredentials
from orb_bot.orb_strategy import TradeExecutor, TradeSignal
from orb_bot.trade_log import TradeLog

logger = logging.getLogger(__name__)

REST_BASE = {
    "demo": "https://demo.tradovateapi.com/v1",
    "live": "https://live.tradovateapi.com/v1",
}
WS_BASE = {
    "demo": "wss://demo.tradovateapi.com/v1/websocket",
    "live": "wss://live.tradovateapi.com/v1/websocket",
}
MD_WS_URL = "wss://md.tradovateapi.com/v1/websocket"


class TradovateAuthError(RuntimeError):
    pass


class TradovateOrderError(RuntimeError):
    pass


@dataclass
class AuthTokens:
    access_token: str
    md_access_token: str
    expiration: datetime


class TradovateREST:
    """Thin wrapper around the Tradovate REST API."""

    def __init__(self, credentials: TradovateCredentials, environment: str) -> None:
        if environment not in REST_BASE:
            raise ValueError(f"Unknown environment {environment!r}")
        self.credentials = credentials
        self.environment = environment
        self.base_url = REST_BASE[environment]
        self.session = requests.Session()
        self.tokens: Optional[AuthTokens] = None
        self.user_id: Optional[int] = None

    # -- auth ----------------------------------------------------------

    def authenticate(self) -> AuthTokens:
        payload = {
            "name": self.credentials.username,
            "password": self.credentials.password,
            "appId": self.credentials.app_id,
            "appVersion": self.credentials.app_version,
            "cid": self.credentials.cid,
            "sec": self.credentials.sec,
        }
        if self.credentials.device_id:
            payload["deviceId"] = self.credentials.device_id

        resp = self.session.post(f"{self.base_url}/auth/accesstokenrequest", json=payload, timeout=15)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "accessToken" not in data:
            raise TradovateAuthError(f"Tradovate authentication failed: {data or resp.text}")

        expiration = datetime.now(timezone.utc)
        self.tokens = AuthTokens(
            access_token=data["accessToken"],
            md_access_token=data.get("mdAccessToken", data["accessToken"]),
            expiration=expiration,
        )
        self.user_id = data.get("userId")
        self.session.headers.update({"Authorization": f"Bearer {self.tokens.access_token}"})
        logger.info("Authenticated with Tradovate (%s)", self.base_url)
        return self.tokens

    def renew(self) -> AuthTokens:
        resp = self.session.post(f"{self.base_url}/auth/renewaccesstoken", timeout=15)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "accessToken" not in data:
            raise TradovateAuthError(f"Tradovate token renewal failed: {data or resp.text}")
        self.tokens.access_token = data["accessToken"]
        self.tokens.md_access_token = data.get("mdAccessToken", self.tokens.md_access_token)
        self.session.headers.update({"Authorization": f"Bearer {self.tokens.access_token}"})
        return self.tokens

    # -- account / contract lookups -------------------------------------

    def list_accounts(self) -> list[dict]:
        resp = self.session.get(f"{self.base_url}/account/list", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def resolve_account_id(self, account_spec: Optional[str]) -> int:
        accounts = self.list_accounts()
        if not accounts:
            raise TradovateAuthError("No Tradovate accounts found for this login")
        if account_spec:
            for acct in accounts:
                if acct.get("name") == account_spec:
                    return acct["id"]
            raise TradovateAuthError(f"No account named {account_spec!r} found")
        if len(accounts) > 1:
            raise TradovateAuthError(
                "Multiple Tradovate accounts found; set account.account_spec in config.yaml "
                f"to one of: {[a.get('name') for a in accounts]}"
            )
        return accounts[0]["id"]

    def find_front_month_contract(self, root_symbol: str) -> dict:
        """Best-effort front-month contract lookup via /contract/suggest."""
        resp = self.session.get(
            f"{self.base_url}/contract/suggest",
            params={"t": root_symbol, "l": 10},
            timeout=15,
        )
        resp.raise_for_status()
        suggestions = resp.json()
        if not suggestions:
            raise TradovateOrderError(f"No contracts found for symbol {root_symbol!r}")
        # Tradovate returns suggestions ordered with the nearest expiration first.
        return suggestions[0]

    def get_contract_by_name(self, contract_name: str) -> dict:
        resp = self.session.get(
            f"{self.base_url}/contract/find", params={"name": contract_name}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            raise TradovateOrderError(f"Contract {contract_name!r} not found")
        return data

    # -- orders ----------------------------------------------------------

    def place_oso_bracket(
        self,
        account_id: int,
        account_spec: str,
        symbol: str,
        action: str,
        order_qty: int,
        entry_order_type: str,
        entry_price: Optional[float],
        stop_trigger_price: Optional[float],
        bracket_stop_price: float,
        bracket_target_price: float,
    ) -> dict:
        """Place an entry order that OSOs an OCO stop-loss/take-profit pair.

        entry_order_type: "Stop" or "StopLimit" (Tradovate order type names).
        entry_price: limit price, only used for StopLimit entries.
        stop_trigger_price: the stop trigger price for the entry order.
        """
        exit_action = "Sell" if action == "Buy" else "Buy"

        body: dict = {
            "accountSpec": account_spec,
            "accountId": account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": order_qty,
            "orderType": entry_order_type,
            "timeInForce": "Day",
            "isAutomated": True,
            "bracket1": {
                "action": exit_action,
                "orderType": "Stop",
                "stopPrice": bracket_stop_price,
            },
            "bracket2": {
                "action": exit_action,
                "orderType": "Limit",
                "price": bracket_target_price,
            },
        }
        if stop_trigger_price is not None:
            body["stopPrice"] = stop_trigger_price
        if entry_order_type == "StopLimit" and entry_price is not None:
            body["price"] = entry_price

        resp = self.session.post(f"{self.base_url}/order/placeOSO", json=body, timeout=15)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or data.get("failureReason"):
            raise TradovateOrderError(f"placeOSO failed: {data or resp.text}")
        logger.info("Placed OSO bracket order: %s", data)
        return data

    def cancel_order(self, order_id: int) -> None:
        resp = self.session.post(f"{self.base_url}/order/cancelorder", json={"orderId": order_id}, timeout=15)
        if resp.status_code != 200:
            logger.warning("Failed to cancel order %s: %s", order_id, resp.text)

    def liquidate_position(self, account_id: int, contract_id: int) -> None:
        """Flatten any open position for this account/contract at market.

        Tradovate cancels the position's still-working bracket orders as
        part of liquidation, so callers don't need to cancel them separately.
        """
        resp = self.session.post(
            f"{self.base_url}/order/liquidateposition",
            json={"accountId": account_id, "contractId": contract_id},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Failed to liquidate position (account=%s contract=%s): %s", account_id, contract_id, resp.text)


class TradovateQuoteStream:
    """Streams last-traded price for one contract symbol via the market-data WS."""

    def __init__(self, md_access_token: str, on_price: Callable[[datetime, float], None]) -> None:
        self.md_access_token = md_access_token
        self.on_price = on_price
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._req_id = 0
        self._authorized = threading.Event()
        self._symbol: Optional[str] = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send_request(self, endpoint: str, query: str = "", body: Optional[dict] = None) -> None:
        body_str = json.dumps(body) if body is not None else ""
        frame = f"{endpoint}\n{self._next_id()}\n{query}\n{body_str}"
        self._ws.send(frame)

    def _on_open(self, ws) -> None:
        logger.info("Market data WebSocket connected")

    def _on_message(self, ws, message: str) -> None:
        if not message:
            return
        frame_type, payload = message[0], message[1:]
        if frame_type == "o":
            # Tradovate expects the raw token as the body for authorize, not JSON.
            frame = f"authorize\n{self._next_id()}\n\n{self.md_access_token}"
            ws.send(frame)
            return
        if frame_type == "h":
            return  # heartbeat, no response required on the md socket
        if frame_type == "c":
            logger.warning("Market data WebSocket closed by server: %s", payload)
            return
        if frame_type != "a":
            return

        try:
            messages = json.loads(payload)
        except json.JSONDecodeError:
            return

        for msg in messages:
            if "i" in msg:
                # response to one of our requests
                if msg.get("s") == 200 and not self._authorized.is_set():
                    self._authorized.set()
                    if self._symbol:
                        self._send_request("md/subscribeQuote", body={"symbol": self._symbol})
                continue
            if msg.get("e") == "md":
                self._handle_quote(msg.get("d", {}))

    def _handle_quote(self, data: dict) -> None:
        for quote in data.get("quotes", []):
            entries = quote.get("entries", {})
            trade = entries.get("Trade")
            if trade and "price" in trade:
                ts = datetime.now(timezone.utc)
                self.on_price(ts, float(trade["price"]))

    def _on_error(self, ws, error) -> None:
        logger.error("Market data WebSocket error: %s", error)

    def _on_close(self, ws, status, msg) -> None:
        logger.warning("Market data WebSocket closed: %s %s", status, msg)

    def start(self, symbol: str) -> None:
        self._symbol = symbol
        self._ws = websocket.WebSocketApp(
            MD_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._ws:
            self._ws.close()


class TradovateUserDataStream:
    """Streams this account's order/fill events so the bot can log exactly
    when and at what price the broker-side bracket (stop/target) closed a
    trade. Uses the same envelope protocol as TradovateQuoteStream but
    against the account's trading WebSocket and `user/syncrequest`
    (Tradovate's documented mechanism for real-time order/fill updates).

    Best-effort: the exact shape of "fill" entity events hasn't been
    verified against a live account here, so `on_fill` is deliberately
    tolerant of missing fields. Verify this logs correctly on a demo
    account before relying on it for anything beyond a convenience journal.
    """

    def __init__(
        self,
        environment: str,
        access_token: str,
        user_id: int,
        on_fill: Callable[[dict], None],
    ) -> None:
        self.ws_url = WS_BASE[environment]
        self.access_token = access_token
        self.user_id = user_id
        self.on_fill = on_fill
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._req_id = 0
        self._authorized = threading.Event()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _on_open(self, ws) -> None:
        logger.info("User data WebSocket connected")

    def _on_message(self, ws, message: str) -> None:
        if not message:
            return
        frame_type, payload = message[0], message[1:]
        if frame_type == "o":
            ws.send(f"authorize\n{self._next_id()}\n\n{self.access_token}")
            return
        if frame_type in ("h", "c"):
            return
        if frame_type != "a":
            return

        try:
            messages = json.loads(payload)
        except json.JSONDecodeError:
            return

        for msg in messages:
            if "i" in msg:
                if msg.get("s") == 200 and not self._authorized.is_set():
                    self._authorized.set()
                    body = json.dumps({"users": [self.user_id]})
                    ws.send(f"user/syncrequest\n{self._next_id()}\n\n{body}")
                continue
            entity_type = msg.get("e")
            if entity_type in ("fill", "props") and msg.get("d"):
                self._handle_entity(msg["d"])

    def _handle_entity(self, data: dict) -> None:
        # Fills can arrive either as a top-level "fill" event or nested in a
        # sync/props payload under an "fills" list, depending on context.
        fills = data.get("fills") if isinstance(data, dict) else None
        if fills:
            for fill in fills:
                self.on_fill(fill)
        elif "contractId" in data:
            self.on_fill(data)

    def _on_error(self, ws, error) -> None:
        logger.error("User data WebSocket error: %s", error)

    def _on_close(self, ws, status, msg) -> None:
        logger.warning("User data WebSocket closed: %s %s", status, msg)

    def start(self) -> None:
        self._ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._ws:
            self._ws.close()


class TradovateExecutor(TradeExecutor):
    """TradeExecutor that places real (or dry-run) orders on Tradovate."""

    def __init__(
        self,
        rest: TradovateREST,
        trade_log: TradeLog,
        account_id: int,
        account_spec: str,
        contract_id: int,
        contract_name: str,
        entry_order_type: str,
        stop_limit_offset_ticks: int,
        tick_size: float,
        dry_run: bool = True,
    ) -> None:
        self.rest = rest
        self.trade_log = trade_log
        self.account_id = account_id
        self.account_spec = account_spec
        self.contract_id = contract_id
        self.contract_name = contract_name
        self.entry_order_type = entry_order_type
        self.stop_limit_offset_ticks = stop_limit_offset_ticks
        self.tick_size = tick_size
        self.dry_run = dry_run
        self._working_entry_order_id: Optional[int] = None
        self._open_signal: Optional[TradeSignal] = None

    def enter(self, signal: TradeSignal) -> None:
        action = "Buy" if signal.direction == "long" else "Sell"
        order_type = "Stop" if self.entry_order_type == "stop_market" else "StopLimit"

        entry_limit_price = None
        if order_type == "StopLimit":
            slippage = self.stop_limit_offset_ticks * self.tick_size
            entry_limit_price = (
                signal.entry_price + slippage if action == "Buy" else signal.entry_price - slippage
            )

        self.trade_log.log_signal(signal)
        self._open_signal = signal

        if self.dry_run:
            logger.info(
                "[DRY RUN] Would place %s %s x%d entry=%.4f stop=%.4f target=%.4f",
                action,
                self.contract_name,
                signal.contracts,
                signal.entry_price,
                signal.stop_price,
                signal.target_price,
            )
            return

        result = self.rest.place_oso_bracket(
            account_id=self.account_id,
            account_spec=self.account_spec,
            symbol=self.contract_name,
            action=action,
            order_qty=signal.contracts,
            entry_order_type=order_type,
            entry_price=entry_limit_price,
            stop_trigger_price=signal.entry_price,
            bracket_stop_price=signal.stop_price,
            bracket_target_price=signal.target_price,
        )
        self._working_entry_order_id = result.get("orderId")

    def no_trade_today(self, session_date: date, reason: str) -> None:
        self.trade_log.log_no_trade(session_date, reason)
        logger.info("No trade for %s: %s", session_date, reason)

    def flatten(self, session_date: date, ts: datetime, price: float, reason: str) -> None:
        self.trade_log.log_flatten(session_date, ts, price, reason)
        self._open_signal = None

        if self.dry_run:
            logger.info("[DRY RUN] Would flatten %s at ~%.4f: %s", self.contract_name, price, reason)
            return

        logger.info("Flattening %s: %s", self.contract_name, reason)
        if self._working_entry_order_id is not None:
            # Harmless if the entry already filled or was already cancelled -
            # cancelling a non-working order is a no-op on Tradovate's side.
            self.rest.cancel_order(self._working_entry_order_id)
            self._working_entry_order_id = None
        self.rest.liquidate_position(self.account_id, self.contract_id)

    def on_order_fill(self, contract_id: int, action: str, fill_price: float, fill_time: datetime) -> None:
        """Best-effort live exit tracking for the bracket's stop/target leg.

        Wired up (in live_runner.py) to Tradovate's account WebSocket via
        TradovateUserDataStream. Since the bracket's stop/target orders are
        created and filled entirely on Tradovate's side, this is the only
        way the bot observes *when* and *at what price* an open trade
        actually closed - verify this fires correctly on a demo account
        before relying on it; the trade_log entry logged by `enter()` and
        the position/orders visible in the Tradovate app are the ultimate
        source of truth regardless.
        """
        signal = self._open_signal
        if signal is None or contract_id != self.contract_id:
            return
        exit_action = "Sell" if signal.direction == "long" else "Buy"
        if action != exit_action:
            return  # this fill is the entry leg, not an exit

        near_stop = abs(fill_price - signal.stop_price) <= abs(fill_price - signal.target_price)
        reason = "Stop-loss hit" if near_stop else "Take-profit target hit"
        self.trade_log.log_exit(signal.session_date, fill_time, fill_price, reason, event="trade_exit")
        logger.info("Trade closed for %s: %s at %.4f", signal.session_date, reason, fill_price)
        self._open_signal = None
