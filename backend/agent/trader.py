"""
Alpaca Paper Trading Executor
===============================
Submits market orders to Alpaca's paper trading sandbox.

Paper trading = real market data, simulated money.
The API key in .env must be a PAPER trading key — never a live key.

Design choices:
  - Market orders (not limit) because news-driven trades prioritize speed
  - TimeInForce.DAY cancels unfilled orders at market close automatically
  - paper=True is hardcoded — this line cannot accidentally go live
  - Returns structured failure metadata instead of raising; the pipeline keeps running
"""

import logging
import os
from dataclasses import asdict, dataclass
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import config

log = logging.getLogger("agent.trader")


@dataclass
class OrderResult:
    submitted: bool
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _floatish(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _boolish(value) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


class AlpacaTrader:
    """
    Wraps alpaca-py's TradingClient for paper trading.
    Always operates in paper=True mode — no real money can ever be at risk.
    """

    def __init__(self) -> None:
        self._dry_run = os.environ.get("MOCK_ALPACA", "false").lower() == "true"
        self._client = TradingClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
            paper=True,  # Hardcoded — this service must never touch live funds
        )
        if self._dry_run:
            log.info("Alpaca trader initialized in MOCK mode (Dry run)")
        else:
            log.info("Alpaca trader initialized (paper trading mode)")

    def place_order(
        self,
        ticker: str,
        action: str,
        quantity: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Submit a market order and return structured Alpaca execution metadata.
        Failed orders return submitted=False — errors are logged but don't crash the pipeline.

        Common failure reasons:
          - Market is closed (paper trading still works after hours for some assets)
          - Trying to SELL a ticker we don't hold
          - Account has insufficient buying power
        """
        qty = quantity if quantity is not None else config.ORDER_QTY
        side = OrderSide.BUY if action == "BUY" else OrderSide.SELL

        if self._dry_run:
            import uuid

            mock_id = str(uuid.uuid4())
            log.info(
                "MOCK ORDER (Dry Run): %s %d %s → order_id=%s",
                action,
                qty,
                ticker,
                mock_id,
            )
            return OrderResult(
                submitted=True,
                order_id=mock_id,
                client_order_id=client_order_id,
                status="accepted",
            )

        order_request = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,  # auto-cancels at end of day if unfilled
            client_order_id=client_order_id,  # Alpaca rejects duplicates — idempotent
        )

        try:
            order = self._client.submit_order(order_data=order_request)
            log.info(
                "Order submitted: %s %d %s → order_id=%s",
                action,
                qty,
                ticker,
                order.id,
            )
            return OrderResult(
                submitted=True,
                order_id=str(order.id),
                client_order_id=client_order_id,
                status=str(getattr(order, "status", "") or ""),
            )

        except APIError as e:
            # Log and continue — we record the analysis in Supabase either way
            log.warning("Order failed for %s %s: %s", action, ticker, e)
            return OrderResult(
                submitted=False,
                client_order_id=client_order_id,
                error=str(e),
            )

    def get_account_context(self) -> Optional[dict]:
        """Small, JSON-safe account snapshot used by risk gating and prompts."""
        try:
            account = self._client.get_account()
            return {
                "status": str(getattr(account, "status", "") or ""),
                "currency": str(getattr(account, "currency", "") or ""),
                "trading_blocked": _boolish(getattr(account, "trading_blocked", None)),
                "transfers_blocked": _boolish(
                    getattr(account, "transfers_blocked", None)
                ),
                "account_blocked": _boolish(getattr(account, "account_blocked", None)),
                "shorting_enabled": _boolish(
                    getattr(account, "shorting_enabled", None)
                ),
                "pattern_day_trader": _boolish(
                    getattr(account, "pattern_day_trader", None)
                ),
                "buying_power": _floatish(getattr(account, "buying_power", None)),
                "regt_buying_power": _floatish(
                    getattr(account, "regt_buying_power", None)
                ),
                "daytrading_buying_power": _floatish(
                    getattr(account, "daytrading_buying_power", None)
                ),
                "cash": _floatish(getattr(account, "cash", None)),
                "portfolio_value": _floatish(getattr(account, "portfolio_value", None)),
                "equity": _floatish(getattr(account, "equity", None)),
                "last_equity": _floatish(getattr(account, "last_equity", None)),
                "maintenance_margin": _floatish(
                    getattr(account, "maintenance_margin", None)
                ),
                "daytrade_count": getattr(account, "daytrade_count", None),
            }
        except Exception as exc:
            log.warning("Could not fetch Alpaca account context: %s", exc)
            return None

    def get_position_context(self, ticker: str) -> dict:
        """Return the current ticker position, or an explicit flat position."""
        flat = {
            "symbol": ticker,
            "qty": 0.0,
            "side": "flat",
            "market_value": 0.0,
            "cost_basis": 0.0,
            "avg_entry_price": None,
            "current_price": None,
            "unrealized_pl": 0.0,
            "unrealized_plpc": 0.0,
            "unrealized_intraday_pl": 0.0,
            "unrealized_intraday_plpc": 0.0,
        }

        try:
            position = self._client.get_open_position(ticker)
            return {
                "symbol": str(getattr(position, "symbol", ticker)),
                "qty": _floatish(getattr(position, "qty", None)) or 0.0,
                "side": str(getattr(position, "side", "") or "long"),
                "market_value": _floatish(getattr(position, "market_value", None)),
                "cost_basis": _floatish(getattr(position, "cost_basis", None)),
                "avg_entry_price": _floatish(
                    getattr(position, "avg_entry_price", None)
                ),
                "current_price": _floatish(getattr(position, "current_price", None)),
                "unrealized_pl": _floatish(getattr(position, "unrealized_pl", None)),
                "unrealized_plpc": _floatish(
                    getattr(position, "unrealized_plpc", None)
                ),
                "unrealized_intraday_pl": _floatish(
                    getattr(position, "unrealized_intraday_pl", None)
                ),
                "unrealized_intraday_plpc": _floatish(
                    getattr(position, "unrealized_intraday_plpc", None)
                ),
            }
        except APIError as exc:
            status_code = getattr(exc, "status_code", None)
            message = str(exc).lower()
            missing_position = (
                "position does not exist" in message or "not found" in message
            )
            if status_code not in (404, 422) and not missing_position:
                log.warning("Could not fetch Alpaca position for %s: %s", ticker, exc)
            return flat
        except Exception as exc:
            log.warning("Could not fetch Alpaca position for %s: %s", ticker, exc)
            return flat
