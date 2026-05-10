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
  - Returns None on failure instead of raising; the pipeline keeps running
"""

import logging
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import config

log = logging.getLogger("agent.trader")


class AlpacaTrader:
    """
    Wraps alpaca-py's TradingClient for paper trading.
    Always operates in paper=True mode — no real money can ever be at risk.
    """

    def __init__(self) -> None:
        self._client = TradingClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
            paper=True,  # Hardcoded — this service must never touch live funds
        )
        log.info("Alpaca trader initialized (paper trading mode)")

    def place_order(
        self,
        ticker: str,
        action: str,
        quantity: int = config.ORDER_QTY,
    ) -> Optional[str]:
        """
        Submit a market order and return the Alpaca order ID.
        Returns None if the order fails — errors are logged but don't crash the pipeline.

        Common failure reasons:
          - Market is closed (paper trading still works after hours for some assets)
          - Trying to SELL a ticker we don't hold
          - Account has insufficient buying power
        """
        side = OrderSide.BUY if action == "BUY" else OrderSide.SELL

        order_request = MarketOrderRequest(
            symbol=ticker,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.DAY,  # auto-cancels at end of day if unfilled
        )

        try:
            order = self._client.submit_order(order_data=order_request)
            log.info(
                "Order submitted: %s %d %s → order_id=%s",
                action, quantity, ticker, order.id,
            )
            return str(order.id)

        except APIError as e:
            # Log and continue — we record the analysis in Supabase either way
            log.warning("Order failed for %s %s: %s", action, ticker, e)
            return None

