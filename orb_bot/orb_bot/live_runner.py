"""Wires the Tradovate client to the OrbStrategy for live/paper trading."""

from __future__ import annotations

import logging
import time as time_module
from datetime import datetime, timezone

from orb_bot.config import BotConfig, load_config
from orb_bot.contracts import resolve_spec
from orb_bot.orb_strategy import OrbStrategy
from orb_bot.session import SessionClock
from orb_bot.trade_log import TradeLog
from orb_bot.tradovate_client import (
    TradovateExecutor,
    TradovateQuoteStream,
    TradovateREST,
    TradovateUserDataStream,
)

logger = logging.getLogger(__name__)

# Re-feed the last known price into the strategy at this cadence even if no
# new trade prints occur, so window-closed / no-trade-today logic still
# fires on time.
HEARTBEAT_SECONDS = 15
TOKEN_RENEW_SECONDS = 60 * 60  # renew well before Tradovate's ~80 min expiry


class LiveRunner:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        logging.basicConfig(level=config.logging.level)

        self.spec = resolve_spec(
            config.contract.symbol, config.contract.tick_size, config.contract.tick_value
        )
        self.rest = TradovateREST(config.credentials, config.account.environment)
        self.trade_log = TradeLog(config.logging.trade_log_csv)
        self._last_price: float | None = None
        self._running = False

    def _resolve_contract(self) -> tuple[str, int]:
        if self.config.contract.contract_name:
            contract = self.rest.get_contract_by_name(self.config.contract.contract_name)
        else:
            contract = self.rest.find_front_month_contract(self.config.contract.symbol)
        name = contract.get("name") or contract.get("Symbol")
        contract_id = contract["id"]
        logger.info("Resolved contract: %s (id=%s)", name, contract_id)
        return name, contract_id

    def _on_price(self, ts: datetime, price: float) -> None:
        self._last_price = price
        self.strategy.on_market_data(ts, high=price, low=price, close=price)

    def _on_fill(self, fill: dict) -> None:
        contract_id = fill.get("contractId")
        action = fill.get("action")
        price = fill.get("price")
        if contract_id is None or action is None or price is None:
            return
        ts_raw = fill.get("timestamp")
        try:
            fill_time = (
                datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts_raw
                else datetime.now(timezone.utc)
            )
        except ValueError:
            fill_time = datetime.now(timezone.utc)
        self.executor.on_order_fill(contract_id, action, float(price), fill_time)

    def _heartbeat_loop(self) -> None:
        while self._running:
            time_module.sleep(HEARTBEAT_SECONDS)
            if self._last_price is not None:
                self.strategy.on_market_data(
                    datetime.now(timezone.utc),
                    high=self._last_price,
                    low=self._last_price,
                    close=self._last_price,
                )

    def _token_renew_loop(self) -> None:
        while self._running:
            time_module.sleep(TOKEN_RENEW_SECONDS)
            try:
                self.rest.renew()
                logger.info("Renewed Tradovate access token")
            except Exception:
                logger.exception("Failed to renew Tradovate access token")

    def run(self) -> None:
        import threading

        self.rest.authenticate()
        account_id = self.rest.resolve_account_id(self.config.account.account_spec)
        account_spec = self.config.account.account_spec or self.rest.list_accounts()[0]["name"]
        contract_name, contract_id = self._resolve_contract()

        self.executor = TradovateExecutor(
            rest=self.rest,
            trade_log=self.trade_log,
            account_id=account_id,
            account_spec=account_spec,
            contract_id=contract_id,
            contract_name=contract_name,
            entry_order_type=self.config.strategy.entry_order_type,
            stop_limit_offset_ticks=self.config.strategy.stop_limit_offset_ticks,
            tick_size=self.spec.tick_size,
            dry_run=self.config.account.dry_run,
        )
        self.strategy = OrbStrategy(
            self.config, self.spec, self.executor, SessionClock(self.config.session)
        )

        if self.config.account.dry_run:
            logger.warning("Running in DRY RUN mode - no real orders will be sent.")

        stream = TradovateQuoteStream(self.rest.tokens.md_access_token, self._on_price)
        stream.start(contract_name)

        user_stream = None
        if not self.config.account.dry_run and self.rest.user_id is not None:
            user_stream = TradovateUserDataStream(
                self.config.account.environment, self.rest.tokens.access_token, self.rest.user_id, self._on_fill
            )
            user_stream.start()

        self._running = True
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        renew_thread = threading.Thread(target=self._token_renew_loop, daemon=True)
        heartbeat_thread.start()
        renew_thread.start()

        try:
            while self._running:
                time_module.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down live runner")
        finally:
            self._running = False
            stream.stop()
            if user_stream is not None:
                user_stream.stop()


def main() -> None:
    config = load_config()
    LiveRunner(config).run()


if __name__ == "__main__":
    main()
