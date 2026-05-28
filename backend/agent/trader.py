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
from types import SimpleNamespace
from typing import Optional

try:
    from alpaca.common.exceptions import APIError
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopLossRequest,
        StopOrderRequest,
        TakeProfitRequest,
    )
except ModuleNotFoundError:
    APIError = Exception  # type: ignore[assignment]
    TradingClient = None  # type: ignore[assignment]
    OrderClass = SimpleNamespace(BRACKET="bracket", SIMPLE="simple")  # type: ignore[assignment]
    OrderSide = SimpleNamespace(BUY="buy", SELL="sell")
    TimeInForce = SimpleNamespace(DAY="day", GTC="gtc", IOC="ioc")

    class MarketOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class LimitOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class StopOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class TakeProfitRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class StopLossRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

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
        if TradingClient is None:
            raise RuntimeError(
                "alpaca-py is not installed. Install backend/agent requirements before running the agent."
            )
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
        limit_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> OrderResult:
        """
        Submit a market or limit order and return structured Alpaca execution metadata.
        Failed orders return submitted=False — errors are logged but don't crash the pipeline.

        When limit_price is provided, submits a limit IOC order instead of market DAY.

        When take_profit_price and/or stop_loss_price are provided AND action is BUY,
        submits a native Alpaca bracket order (order_class=BRACKET) that atomically
        attaches take-profit and stop-loss legs to the primary order. This avoids
        the "potential wash trade" error that occurs when submitting separate orders.
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

        # Determine if this should be a bracket order
        use_bracket = (
            action == "BUY"
            and take_profit_price is not None
            and stop_loss_price is not None
            and take_profit_price > 0
            and stop_loss_price > 0
        )

        # Use limit order if price provided, otherwise market order
        if limit_price is not None and limit_price > 0:
            order_kwargs = dict(
                symbol=ticker,
                qty=qty,
                side=side,
                limit_price=round(limit_price, 2),
                time_in_force=TimeInForce.GTC if use_bracket else TimeInForce.IOC,
                client_order_id=client_order_id,
            )
            if use_bracket:
                order_kwargs["order_class"] = OrderClass.BRACKET
                order_kwargs["take_profit"] = TakeProfitRequest(
                    limit_price=round(take_profit_price, 2)
                )
                order_kwargs["stop_loss"] = StopLossRequest(
                    stop_price=round(stop_loss_price, 2)
                )
                log.info(
                    "Submitting BRACKET LIMIT order: %s %d %s @ $%.2f "
                    "(TP=$%.2f, SL=$%.2f)",
                    action, qty, ticker, limit_price,
                    take_profit_price, stop_loss_price,
                )
            else:
                log.info(
                    "Submitting LIMIT order: %s %d %s @ $%.2f",
                    action, qty, ticker, limit_price,
                )
            order_request = LimitOrderRequest(**order_kwargs)
        else:
            order_kwargs = dict(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.GTC if use_bracket else TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            if use_bracket:
                order_kwargs["order_class"] = OrderClass.BRACKET
                order_kwargs["take_profit"] = TakeProfitRequest(
                    limit_price=round(take_profit_price, 2)
                )
                order_kwargs["stop_loss"] = StopLossRequest(
                    stop_price=round(stop_loss_price, 2)
                )
                log.info(
                    "Submitting BRACKET MARKET order: %s %d %s "
                    "(TP=$%.2f, SL=$%.2f)",
                    action, qty, ticker,
                    take_profit_price, stop_loss_price,
                )
            order_request = MarketOrderRequest(**order_kwargs)

        try:
            order = self._client.submit_order(order_data=order_request)
            order_id = str(getattr(order, "id", "") or "")
            status = str(getattr(order, "status", "") or "")
            lookup_error: Optional[str] = None

            if not order_id and client_order_id:
                try:
                    order = self._client.get_order_by_client_id(client_order_id)
                    order_id = str(getattr(order, "id", "") or "")
                    status = str(getattr(order, "status", "") or status or "")
                except Exception as exc:
                    lookup_error = str(exc)

            if not order_id:
                error = "Alpaca order submission returned no order_id."
                if lookup_error:
                    error = f"{error} Lookup by client_order_id failed: {lookup_error}"
                log.error(
                    "Order submission for %s %s returned no Alpaca order_id "
                    "(client_order_id=%s status=%s)",
                    action,
                    ticker,
                    client_order_id,
                    status or "unknown",
                )
                return OrderResult(
                    submitted=False,
                    client_order_id=client_order_id,
                    status=status or None,
                    error=error,
                )

            order_type_label = "BRACKET" if use_bracket else "SIMPLE"
            log.info(
                "%s order submitted: %s %d %s → order_id=%s",
                order_type_label,
                action,
                qty,
                ticker,
                order_id,
            )
            return OrderResult(
                submitted=True,
                order_id=order_id,
                client_order_id=client_order_id,
                status=status,
            )

        except APIError as e:
            # Log and continue — we record the analysis in Supabase either way
            log.warning("Order failed for %s %s: %s", action, ticker, e)
            return OrderResult(
                submitted=False,
                client_order_id=client_order_id,
                error=str(e),
            )

    def place_bracket_orders(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
    ) -> dict:
        """
        DEPRECATED: Bracket legs are now attached atomically via place_order().

        This method is kept for backwards compatibility but now only computes
        and returns the bracket price levels. It does NOT submit separate orders
        (which Alpaca rejects as "potential wash trade").

        The actual bracket submission happens in place_order() when
        take_profit_price and stop_loss_price are provided.
        """
        tp_price = round(entry_price * (1 + take_profit_pct), 2)
        sl_price = round(entry_price * (1 - stop_loss_pct), 2)

        log.info(
            "Bracket prices computed for %s: entry=$%.2f TP=$%.2f (+%.1f%%) SL=$%.2f (-%.1f%%)",
            ticker, entry_price, tp_price, take_profit_pct * 100,
            sl_price, stop_loss_pct * 100,
        )

        return {
            "take_profit_price": tp_price,
            "stop_loss_price": sl_price,
            "entry_price": entry_price,
            "method": "atomic_bracket",
            "status": "attached_to_primary_order",
            "errors": [],
        }

    def verify_fill(self, order_id: str) -> dict:
        """
        Check the fill status of an order.

        Returns filled quantity, average price, and status.
        Non-blocking — returns whatever status is current.
        """
        if self._dry_run:
            return {
                "filled_qty": 0,
                "filled_avg_price": 0,
                "status": "mock",
            }
        try:
            order = self._client.get_order_by_id(order_id)
            return {
                "filled_qty": _floatish(getattr(order, "filled_qty", None)) or 0,
                "filled_avg_price": _floatish(getattr(order, "filled_avg_price", None)) or 0,
                "status": str(getattr(order, "status", "") or ""),
            }
        except Exception as exc:
            log.warning("Could not verify fill for order %s: %s", order_id, exc)
            return {
                "filled_qty": 0,
                "filled_avg_price": 0,
                "status": "unknown",
                "error": str(exc),
            }

    def get_all_positions(self) -> list[dict]:
        """Return all open positions as a list of dicts."""
        try:
            positions = self._client.get_all_positions()
            return [
                {
                    "symbol": str(getattr(p, "symbol", "") or ""),
                    "qty": _floatish(getattr(p, "qty", None)) or 0.0,
                    "side": str(getattr(p, "side", "") or "long"),
                    "market_value": _floatish(getattr(p, "market_value", None)),
                    "cost_basis": _floatish(getattr(p, "cost_basis", None)),
                    "avg_entry_price": _floatish(getattr(p, "avg_entry_price", None)),
                    "current_price": _floatish(getattr(p, "current_price", None)),
                    "unrealized_pl": _floatish(getattr(p, "unrealized_pl", None)),
                    "unrealized_plpc": _floatish(getattr(p, "unrealized_plpc", None)),
                }
                for p in positions
            ]
        except Exception as exc:
            log.warning("Could not fetch all positions: %s", exc)
            return []

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

    def get_open_orders(self, ticker: Optional[str] = None) -> list[dict]:
        """Return open orders, optionally filtered by ticker symbol."""
        if self._dry_run:
            return []
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            request_params = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[ticker] if ticker else None,
            )
            orders = self._client.get_orders(filter=request_params)
            return [
                {
                    "id": str(getattr(o, "id", "") or ""),
                    "symbol": str(getattr(o, "symbol", "") or ""),
                    "side": str(getattr(o, "side", "") or ""),
                    "type": str(getattr(o, "type", "") or ""),
                    "qty": _floatish(getattr(o, "qty", None)) or 0.0,
                    "stop_price": _floatish(getattr(o, "stop_price", None)),
                    "limit_price": _floatish(getattr(o, "limit_price", None)),
                    "status": str(getattr(o, "status", "") or ""),
                    "order_class": str(getattr(o, "order_class", "") or ""),
                    "legs": [
                        {
                            "id": str(getattr(leg, "id", "") or ""),
                            "type": str(getattr(leg, "type", "") or ""),
                            "side": str(getattr(leg, "side", "") or ""),
                            "stop_price": _floatish(getattr(leg, "stop_price", None)),
                            "limit_price": _floatish(getattr(leg, "limit_price", None)),
                            "status": str(getattr(leg, "status", "") or ""),
                        }
                        for leg in (getattr(o, "legs", None) or [])
                    ],
                }
                for o in orders
            ]
        except Exception as exc:
            log.warning("Could not fetch open orders%s: %s",
                        f" for {ticker}" if ticker else "", exc)
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True on success."""
        if self._dry_run:
            log.info("MOCK CANCEL (Dry Run): order_id=%s", order_id)
            return True
        try:
            self._client.cancel_order_by_id(order_id)
            log.info("Cancelled order: %s", order_id)
            return True
        except Exception as exc:
            log.warning("Could not cancel order %s: %s", order_id, exc)
            return False

    def place_stop_order(
        self,
        ticker: str,
        quantity: int,
        stop_price: float,
        side: str = "sell",
    ) -> OrderResult:
        """Place a standalone stop order (used for trailing stop replacement)."""
        if self._dry_run:
            import uuid
            mock_id = str(uuid.uuid4())
            log.info(
                "MOCK STOP ORDER (Dry Run): %s %d %s @ stop=$%.2f → order_id=%s",
                side, quantity, ticker, stop_price, mock_id,
            )
            return OrderResult(
                submitted=True,
                order_id=mock_id,
                status="accepted",
            )

        order_side = OrderSide.SELL if side.lower() == "sell" else OrderSide.BUY
        try:
            order_request = StopOrderRequest(
                symbol=ticker,
                qty=quantity,
                side=order_side,
                stop_price=round(stop_price, 2),
                time_in_force=TimeInForce.GTC,
            )
            order = self._client.submit_order(order_data=order_request)
            order_id = str(getattr(order, "id", "") or "")
            status = str(getattr(order, "status", "") or "")
            log.info(
                "Stop order submitted: %s %d %s @ stop=$%.2f → order_id=%s",
                side, quantity, ticker, stop_price, order_id,
            )
            return OrderResult(
                submitted=True,
                order_id=order_id,
                status=status,
            )
        except APIError as e:
            log.warning("Stop order failed for %s %s: %s", side, ticker, e)
            return OrderResult(submitted=False, error=str(e))

