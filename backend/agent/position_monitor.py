"""
Position Monitor
=================
Background daemon that actively manages open positions with trailing stops.

Runs as a daemon thread alongside the main consumer loop. Periodically:
  1. Fetches all open positions from Alpaca
  2. For each position in profit above the activation threshold:
     - Computes the ideal trailing stop price
     - Finds existing stop orders for the symbol
     - If the new stop is higher, cancels the old one and places a new stop order
  3. Logs all trailing stop adjustments for audit

This turns the previously dead-code `compute_trailing_stop()` from
position_manager.py into a live position management system.

All features are gated behind config.TRAILING_STOPS_ENABLED.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import config
from position_manager import compute_trailing_stop

log = logging.getLogger("agent.position_monitor")

# How often to check positions (seconds)
MONITOR_INTERVAL = 60

# Stale-entry reaper: cancel unfilled BUY entry orders older than this so a
# missed catalyst entry doesn't linger under GTC and fill weeks later on dead
# news. Runs independently of trailing stops. (See README Bug Log: BUG-2026-06-08-02)
REAP_STALE_ENTRIES = True
STALE_ENTRY_MAX_AGE_SECONDS = 600  # 10 minutes

# Track the current trailing stop order ID per symbol so we can cancel/replace
_trailing_stop_orders: dict[str, str] = {}
# Track the current stop price per symbol to avoid redundant API calls
_current_stop_prices: dict[str, float] = {}


def _find_existing_stop_order(
    orders: list[dict], symbol: str, stop_side: str = "sell"
) -> Optional[dict]:
    """Find the active protective stop order for a symbol.

    ``stop_side`` is the side of the protective order: "sell" guards a long,
    "buy" (buy-to-cover) guards a short.
    """
    stop_side = stop_side.lower()
    for order in orders:
        order_type = str(order.get("type", "")).lower()
        order_side = str(order.get("side", "")).lower()
        order_symbol = str(order.get("symbol", "")).upper()
        order_status = str(order.get("status", "")).lower()

        if (
            order_symbol == symbol.upper()
            and order_side == stop_side
            and order_type == "stop"
            and order_status in ("new", "accepted", "pending_new", "held")
        ):
            return order

        # Also check bracket order legs
        for leg in order.get("legs", []):
            leg_type = str(leg.get("type", "")).lower()
            leg_side = str(leg.get("side", "")).lower()
            leg_status = str(leg.get("status", "")).lower()
            if (
                order_symbol == symbol.upper()
                and leg_side == stop_side
                and leg_type == "stop"
                and leg_status in ("new", "accepted", "pending_new", "held")
            ):
                return {**leg, "symbol": order_symbol}

    return None


def _manage_trailing_stop(
    trader,
    symbol: str,
    entry_price: float,
    current_price: float,
    position_qty: int,
    side: str = "long",
) -> None:
    """Check and update trailing stop for one position (long or short)."""
    is_short = str(side).lower() == "short"
    # A long is protected by a SELL stop below price; a short by a BUY-to-cover
    # stop above price. The "better" direction flips accordingly.
    stop_side = "buy" if is_short else "sell"
    current_stop = _current_stop_prices.get(symbol)

    trailing_result = compute_trailing_stop(
        entry_price=entry_price,
        current_price=current_price,
        current_stop=current_stop,
        trail_pct=config.TRAILING_STOP_PCT,
        activation_profit_pct=config.TRAILING_STOP_ACTIVATION_PCT,
        side="short" if is_short else "long",
    )

    if not trailing_result.should_tighten:
        return

    new_stop = trailing_result.current_stop

    def _is_better(new: float, old: float) -> bool:
        # Long ratchets up (higher sell stop); short ratchets down (lower buy stop).
        return new < old if is_short else new > old

    log.info(
        "Trailing stop [%s/%s]: tightening $%.2f → $%.2f (price=$%.2f entry=$%.2f)",
        symbol,
        "short" if is_short else "long",
        current_stop or 0.0,
        new_stop,
        current_price,
        entry_price,
    )

    # Find and cancel existing stop order
    existing_stop_id = _trailing_stop_orders.get(symbol)
    if existing_stop_id:
        cancelled = trader.cancel_order(existing_stop_id)
        if cancelled:
            log.info("Cancelled old stop order %s for %s", existing_stop_id, symbol)
        else:
            # If we can't cancel, try to find and cancel by searching open orders
            open_orders = trader.get_open_orders(symbol)
            existing = _find_existing_stop_order(open_orders, symbol, stop_side)
            if existing and existing.get("id"):
                trader.cancel_order(existing["id"])
    else:
        # No tracked stop order — search for bracket leg or standalone stop
        open_orders = trader.get_open_orders(symbol)
        existing = _find_existing_stop_order(open_orders, symbol, stop_side)
        if existing and existing.get("id"):
            old_stop_price = existing.get("stop_price")
            if old_stop_price and not _is_better(new_stop, old_stop_price):
                # New stop isn't better than the existing one
                _current_stop_prices[symbol] = old_stop_price
                return
            trader.cancel_order(existing["id"])
            log.info(
                "Cancelled existing stop order %s for %s (was $%s)",
                existing["id"],
                symbol,
                old_stop_price,
            )

    # Small delay to ensure the cancel is processed
    time.sleep(0.3)

    # Place new stop order at the tighter level
    result = trader.place_stop_order(
        ticker=symbol,
        quantity=position_qty,
        stop_price=new_stop,
        side=stop_side,
    )

    if result.submitted and result.order_id:
        _trailing_stop_orders[symbol] = result.order_id
        _current_stop_prices[symbol] = new_stop
        log.info(
            "Trailing stop [%s]: new stop order %s @ $%.2f (reason: %s)",
            symbol,
            result.order_id,
            new_stop,
            trailing_result.reason,
        )
    else:
        log.warning(
            "Trailing stop [%s]: failed to place new stop order: %s",
            symbol,
            result.error,
        )


def _monitor_loop(trader, lock=None) -> None:
    """Main monitoring loop — runs forever in a daemon thread."""
    log.info(
        "Position monitor started (interval=%ds, trail=%.1f%%, activation=%.1f%%)",
        MONITOR_INTERVAL,
        config.TRAILING_STOP_PCT * 100,
        config.TRAILING_STOP_ACTIVATION_PCT * 100,
    )

    while True:
        try:
            # Singleton guard: only the replica holding the leader lock manages
            # orders. Two replicas reaping/replacing the same stops would race.
            if lock is not None and not lock.acquire_or_renew():
                _trailing_stop_orders.clear()
                _current_stop_prices.clear()
                time.sleep(MONITOR_INTERVAL)
                continue
            # Reap stale unfilled entry orders first — this must run even when
            # trailing stops are off, because a zombie entry has no position.
            if REAP_STALE_ENTRIES:
                try:
                    reaped = trader.reap_stale_entry_orders(STALE_ENTRY_MAX_AGE_SECONDS)
                    if reaped:
                        log.info("Reaped %d stale unfilled entry order(s)", reaped)
                except Exception as exc:
                    log.warning("Stale-entry reap failed: %s", exc)

            # Re-check config in case it was hot-reloaded
            if not config.TRAILING_STOPS_ENABLED:
                time.sleep(MONITOR_INTERVAL)
                continue

            positions = trader.get_all_positions()
            if not positions:
                # Clean up tracking for closed positions
                _trailing_stop_orders.clear()
                _current_stop_prices.clear()
                time.sleep(MONITOR_INTERVAL)
                continue

            open_symbols = set()
            for pos in positions:
                symbol = str(pos.get("symbol", ""))
                qty = float(pos.get("qty", 0))
                side = str(pos.get("side", "")).lower()
                entry_price = float(pos.get("avg_entry_price") or 0)
                current_price = float(pos.get("current_price") or 0)

                # Alpaca reports a short's qty as negative; trail on the absolute
                # size. Skip anything that isn't a clean long/short with prices.
                qty_abs = abs(qty)
                if not symbol or qty_abs <= 0 or side not in ("long", "short"):
                    continue
                if entry_price <= 0 or current_price <= 0:
                    continue

                open_symbols.add(symbol)
                _manage_trailing_stop(
                    trader=trader,
                    symbol=symbol,
                    entry_price=entry_price,
                    current_price=current_price,
                    position_qty=int(qty_abs),
                    side=side,
                )

            # Clean up tracking for positions that were closed
            closed = set(_trailing_stop_orders.keys()) - open_symbols
            for sym in closed:
                _trailing_stop_orders.pop(sym, None)
                _current_stop_prices.pop(sym, None)

        except Exception as exc:
            log.error("Position monitor error: %s", exc, exc_info=True)

        time.sleep(MONITOR_INTERVAL)


def start_position_monitor(trader, lock=None) -> Optional[threading.Thread]:
    """
    Start the position monitor as a daemon thread.

    Returns the thread if trailing stops are enabled, otherwise None.
    The thread is set as a daemon so it dies with the main process.

    ``lock`` is an optional shared.singleton_lock.RedisLeaderLock; when given,
    only the replica holding it actually manages orders, making multi-replica
    deployments safe.
    """
    if not config.TRAILING_STOPS_ENABLED and not REAP_STALE_ENTRIES:
        log.info("Trailing stops + stale-entry reaper disabled — position monitor not started")
        return None

    thread = threading.Thread(
        target=_monitor_loop,
        args=(trader, lock),
        name="position-monitor",
        daemon=True,
    )
    thread.start()
    log.info("Position monitor thread started")
    return thread

