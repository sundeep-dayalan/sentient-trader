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

# ── Invariant thresholds (for the health gauges published to /metrics) ──────
# An unfilled entry older than this is a "zombie" — mirrors the reaper policy.
INVARIANT_STALE_ENTRY_SECONDS = 600

# How often to check positions (seconds)
MONITOR_INTERVAL = 60

# ── Watchdog ──────────────────────────────────────────────────────────────────
# The monitor loop makes network calls; a single wedged call silently disabled
# the entire safety loop for days while the consumer kept trading (README Bug
# Log: BUG-2026-07-13-01). Every iteration stamps a heartbeat; a watchdog
# thread respawns the loop when the heartbeat goes stale. The generation token
# makes a later-unwedged zombie loop exit instead of double-managing orders.
MONITOR_HEARTBEAT_STALL_SECONDS = 600  # 10 min without an iteration = wedged
WATCHDOG_CHECK_SECONDS = 60
_heartbeat_epoch = [0.0]
_monitor_generation = [0]
# Iterations between periodic "still alive" INFO logs (~30 min at 60s/loop),
# so a silent log stream is distinguishable from a dead thread.
HEARTBEAT_LOG_EVERY_N_ITERATIONS = 30

# Stale-entry reaper: cancel unfilled BUY entry orders older than this so a
# missed catalyst entry doesn't linger under GTC and fill weeks later on dead
# news. Runs independently of trailing stops. (See README Bug Log: BUG-2026-06-08-02)
REAP_STALE_ENTRIES = True
STALE_ENTRY_MAX_AGE_SECONDS = 600  # 10 minutes

# Time-exit pacing: at the first open after downtime the whole aged book
# qualifies for closing at once; each close costs ~4-5 API calls, and Alpaca's
# limit is 200 req/min. Capping submissions per iteration turns a 350-call
# thundering herd at 09:30 into a calm drain over a few minutes — positions
# beyond the cap simply retry next minute.
MAX_TIME_EXITS_PER_ITERATION = 8

# Action counters, published with worker health so Prometheus can graph what
# the monitor DOES, not just that it is alive — the BUG-2026-07-14-01 lesson
# ("the loop is alive" is not "the loop is acting").
_action_counters = {
    "time_exits_submitted": 0,
    "stops_placed": 0,
    "stops_replaced": 0,
    "orders_reaped": 0,
}

# Protective-stop reconciliation: every open position should have a working
# stop on its protective side (longs a SELL stop, shorts a BUY stop). Positions
# can end up naked through several paths — a bracket that fell back to a simple
# order, a trailing replacement whose re-place failed, a close that cancelled
# legs and then errored. This pass sweeps positions with free (unheld) shares
# and re-arms a policy stop. (See README Bug Log: BUG-2026-07-10-05)
RECONCILE_PROTECTIVE_STOPS = True
RECONCILE_INTERVAL_SECONDS = 300  # sweep at most every 5 minutes
_last_reconcile_epoch: float = 0.0

# Track the current trailing stop order ID per symbol so we can cancel/replace
_trailing_stop_orders: dict[str, str] = {}
# Track the current stop price per symbol to avoid redundant API calls
_current_stop_prices: dict[str, float] = {}
# Time-based exit: when each open position's hold clock started (epoch seconds),
# anchored to the real entry fill when Alpaca can tell us. Survives across loop
# iterations so the age keeps accumulating.
_position_first_seen: dict[str, float] = {}
# Symbols for which a time-based close has already been submitted — avoids
# firing a second close while the first is still settling.
_closing_positions: set[str] = set()


def _clear_tracking() -> None:
    """Forget all per-symbol state (used when there are no open positions)."""
    _trailing_stop_orders.clear()
    _current_stop_prices.clear()
    _position_first_seen.clear()
    _closing_positions.clear()


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

    # Locate the working protective stop (tracked id, else broker search).
    # A bracket's stop leg counts — its OCO take-profit sibling holds the
    # shares, so it can only be MOVED via atomic replace, never cancel+place:
    # the cancel frees nothing (the TP sibling survives and keeps holding the
    # shares) and the re-place is rejected, leaving the position stop-less.
    # That exact race stripped 66 positions of protection in one boot sweep
    # (README Bug Log: BUG-2026-07-14-02).
    existing_stop_id = _trailing_stop_orders.get(symbol)
    if not existing_stop_id:
        open_orders = trader.get_open_orders(symbol)
        existing = _find_existing_stop_order(open_orders, symbol, stop_side)
        if existing and existing.get("id"):
            old_stop_price = existing.get("stop_price")
            if old_stop_price and not _is_better(new_stop, old_stop_price):
                # New stop isn't better than the existing one
                _current_stop_prices[symbol] = old_stop_price
                return
            existing_stop_id = existing["id"]

    if existing_stop_id:
        # Atomic broker-side replace: the old stop keeps working until the
        # replacement is accepted — no unprotected window, TP sibling intact.
        result = trader.replace_stop_order(existing_stop_id, new_stop)
        if result.submitted and result.order_id:
            _trailing_stop_orders[symbol] = result.order_id
            _current_stop_prices[symbol] = new_stop
            _action_counters["stops_replaced"] += 1
            log.info(
                "Trailing stop [%s]: replaced stop %s → %s @ $%.2f (reason: %s)",
                symbol, existing_stop_id, result.order_id, new_stop,
                trailing_result.reason,
            )
        else:
            # Replace failed → the OLD stop is still live and protecting the
            # position; just clear tracking so the next loop re-derives state
            # from the broker rather than a stale cache.
            log.warning(
                "Trailing stop [%s]: replace failed (old stop still active): %s",
                symbol, result.error,
            )
            _trailing_stop_orders.pop(symbol, None)
            _current_stop_prices.pop(symbol, None)
        return

    # No stop exists at all — place a fresh one. If this is rejected (e.g.
    # shares held by a lone TP leg) nothing was lost, and the reconciliation
    # sweep escalates the stop-less state.
    result = trader.place_stop_order(
        ticker=symbol,
        quantity=position_qty,
        stop_price=new_stop,
        side=stop_side,
    )
    if result.submitted and result.order_id:
        _trailing_stop_orders[symbol] = result.order_id
        _current_stop_prices[symbol] = new_stop
        _action_counters["stops_placed"] += 1
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
        _trailing_stop_orders.pop(symbol, None)
        _current_stop_prices.pop(symbol, None)


def _maybe_time_exit(trader, symbol: str, side: str,
                     submit_budget: list[int] | None = None) -> bool:
    """Flatten the position if it has been held past the max-hold window.

    News-driven entries earn their edge fast and then give it back: measured
    returns peak within the first ~15-60 min and decay to a small loss by the
    close (README Bug Log: BUG-2026-06-25-03). Holding to a fixed clock harvests
    the early move instead of round-tripping it.

    Returns True when the position is being closed (so the caller skips
    trailing-stop work for it). The hold clock is anchored to the actual entry
    fill when Alpaca reports one, falling back to first-observed time so a
    process restart doesn't reset every position to zero.
    """
    # A close is already in flight — wait for it to settle, don't double-submit.
    if symbol in _closing_positions:
        return True

    now = time.time()
    entry_epoch = _position_first_seen.get(symbol)
    if entry_epoch is None:
        entry_time = trader.get_position_entry_time(symbol, side)
        try:
            entry_epoch = entry_time.timestamp() if entry_time is not None else now
        except Exception:
            entry_epoch = now
        _position_first_seen[symbol] = entry_epoch

    age = now - entry_epoch
    if age < config.MAX_POSITION_HOLD_SECONDS:
        return False

    # Per-iteration pacing: defer past-due closes beyond the budget to the
    # next minute instead of bursting through the broker's rate limit.
    if submit_budget is not None:
        if submit_budget[0] <= 0:
            return False
        submit_budget[0] -= 1

    log.info(
        "Time-based exit [%s/%s]: held %.0fs ≥ %ds limit — flattening to lock in "
        "the early move before it decays.",
        symbol, side, age, config.MAX_POSITION_HOLD_SECONDS,
    )
    result = trader.close_position(symbol)
    if result.submitted:
        _closing_positions.add(symbol)
        _action_counters["time_exits_submitted"] += 1
        return True
    log.warning("Time-based exit [%s]: close failed: %s", symbol, result.error)
    return False


def _ensure_protective_stops(trader, positions: list[dict]) -> None:
    """Re-arm a policy stop on any position whose shares aren't protected.

    Trigger: ``qty_available`` — shares not held by *any* working order. A
    position with free whole shares has, at minimum, that many shares exposed
    with no stop (measured in prod: several longs and one short 11% underwater
    sat naked for days). For each such symbol we double-check the broker's open
    orders for an active protective stop, then place a GTC stop for the free
    shares at the policy distance (``config.STOP_LOSS_PCT``) from the *less
    punitive* of entry vs current price:

      - in profit → entry-anchored (a normal initial stop);
      - in loss   → current-anchored, i.e. the position gets the policy
        distance of room from *here* rather than being force-liquidated the
        instant the stop is placed. This bounds future downside without
        retroactively realizing the existing loss in one shot — the
        deliberately conservative choice for an automated repair.

    Shares locked by working orders that are *not* stops (e.g. a lone
    take-profit leg after its sibling stop died) can't be re-protected without
    cancelling those orders, which is too destructive for automation — that
    state is logged CRITICAL for the operator instead.
    """
    stop_pct = getattr(config, "STOP_LOSS_PCT", 0.03)
    for pos in positions:
        symbol = str(pos.get("symbol", ""))
        side = str(pos.get("side", "")).lower()
        qty = float(pos.get("qty", 0) or 0)
        entry = float(pos.get("avg_entry_price") or 0)
        current = float(pos.get("current_price") or 0)
        if not symbol or side not in ("long", "short") or entry <= 0 or current <= 0:
            continue
        if symbol in _closing_positions:
            continue

        qty_available = pos.get("qty_available")
        if qty_available is None:
            continue  # broker didn't report it; nothing safe to infer
        free_shares = int(abs(float(qty_available)))

        # Confirm against the broker: is there genuinely no protective stop?
        is_short = side == "short"
        stop_side = "buy" if is_short else "sell"
        try:
            open_orders = trader.get_open_orders(symbol)
        except Exception as exc:
            log.warning("Stop reconciliation: open-orders fetch failed for %s: %s",
                        symbol, exc)
            continue
        if _find_existing_stop_order(open_orders, symbol, stop_side):
            continue  # a stop exists (it just doesn't hold all the shares)

        if free_shares < 1:
            # Every share is held by working orders, yet none of them is a
            # protective stop (e.g. a lone take-profit leg whose sibling stop
            # was cancelled). Cancelling the operator's live orders to make
            # room for a stop is too destructive for automation — escalate.
            if abs(qty) >= 1:
                log.critical(
                    "POSITION HAS NO STOP: %s [%s] shares are held by working "
                    "orders but none is a protective stop — operator action "
                    "required (likely a lone take-profit leg).", symbol, side,
                )
            continue

        if is_short:
            anchor = max(entry, current)
            stop_price = round(anchor * (1 + stop_pct), 2)
        else:
            anchor = min(entry, current)
            stop_price = round(anchor * (1 - stop_pct), 2)

        log.warning(
            "Stop reconciliation [%s/%s]: %d unprotected share(s) "
            "(entry=$%.2f current=$%.2f) — placing %s stop @ $%.2f",
            symbol, side, free_shares, entry, current, stop_side.upper(), stop_price,
        )
        result = trader.place_stop_order(
            ticker=symbol,
            quantity=free_shares,
            stop_price=stop_price,
            side=stop_side,
        )
        if result.submitted and result.order_id:
            _trailing_stop_orders[symbol] = result.order_id
            _current_stop_prices[symbol] = stop_price
            _action_counters["stops_placed"] += 1
            continue

        # Stale-mark recovery: if the rejection carries Alpaca's live price,
        # re-anchor the stop to it and retry once — otherwise this loop would
        # retry the same phantom anchor every sweep while the position sits
        # unprotected (observed with WST after a -9% after-hours move).
        live = _market_price_from_error(result.error)
        if live and live > 0:
            retry_stop = round(live * (1 + stop_pct), 2) if is_short \
                else round(live * (1 - stop_pct), 2)
            log.warning(
                "Stop reconciliation [%s]: stale mark (pos=$%.2f live=$%.2f) — "
                "re-anchoring stop to $%.2f", symbol, current, live, retry_stop,
            )
            retry = trader.place_stop_order(
                ticker=symbol, quantity=free_shares,
                stop_price=retry_stop, side=stop_side,
            )
            if retry.submitted and retry.order_id:
                _trailing_stop_orders[symbol] = retry.order_id
                _current_stop_prices[symbol] = retry_stop
                _action_counters["stops_placed"] += 1
                continue
            result = retry

        log.critical(
            "POSITION MAY BE UNPROTECTED: reconciliation could not place a "
            "stop for %s (%d shares): %s", symbol, free_shares, result.error,
        )


def compute_invariants(positions: list[dict], open_orders: list[dict],
                       *, now_epoch: float,
                       stale_entry_seconds: float = INVARIANT_STALE_ENTRY_SECONDS) -> dict:
    """Pure computation of the account-level safety invariants.

    These are the checks that would have caught most of the bug history within
    hours instead of weeks (README Bug Log: BUG-2026-06-10-01, -07-01-01,
    -07-10-05, -07-13-01, -07-13-02). Published as numeric worker-health fields
    so the API's generic /metrics exporter surfaces them to Prometheus with no
    API changes:

      positions_without_stop  — positions where qty_available == qty: no working
                                order holds ANY share, therefore no stop exists.
                                (A stop would hold shares and reduce
                                qty_available — this proxy needs no per-symbol
                                order lookups.)
      stale_entry_orders      — unfilled *_to_open orders older than the reaper
                                policy window (zombies).
      open_positions          — book size; a runaway count means time-based
                                exit stopped working.
    """
    naked = 0
    for p in positions:
        qty = float(p.get("qty") or 0)
        qty_available = p.get("qty_available")
        if qty_available is None or abs(qty) < 1:
            continue
        if abs(float(qty_available)) >= abs(qty):
            naked += 1

    zombies = 0
    for o in open_orders:
        if o.get("position_intent") not in ("buy_to_open", "sell_to_open"):
            continue
        if (o.get("filled_qty") or 0.0) > 0:
            continue
        created = o.get("created_at")
        try:
            age = now_epoch - created.timestamp()
        except Exception:
            continue
        if age > stale_entry_seconds:
            zombies += 1

    return {
        "positions_without_stop": naked,
        "stale_entry_orders": zombies,
        "open_positions": len(positions),
    }


def _market_price_from_error(error: str | None) -> float | None:
    """Extract the live price Alpaca embeds in stop-price rejections.

    When our position mark is stale (observed: WST marked $357 while really
    trading $328 after hours), the stop we compute is on the wrong side of the
    live price and Alpaca rejects it — but the rejection itself carries
    ``"market_price":"328.28"``. Re-anchoring to that value converts a
    retry-forever failure into an immediate correct placement.
    """
    import re
    if not error:
        return None
    match = re.search(r'"market_price"\s*:\s*"?([0-9.]+)"?', error)
    try:
        return float(match.group(1)) if match else None
    except (TypeError, ValueError):
        return None


def _publish_health(health_redis, state: dict) -> None:
    """Best-effort worker-health write; the monitor must never die over Redis."""
    if health_redis is None:
        return
    try:
        from shared.worker_health import write_worker_state
        write_worker_state(health_redis, "position-monitor", state)
    except Exception as exc:
        log.debug("Position monitor health write failed: %s", exc)


def _monitor_loop(trader, lock=None, generation: int = 0, health_redis=None) -> None:
    """Main monitoring loop — runs in a daemon thread until superseded.

    Exits when the watchdog bumps ``_monitor_generation`` past this loop's
    ``generation`` (i.e. a replacement was spawned because this one appeared
    wedged) so a zombie that later un-wedges can't double-manage orders.
    """
    log.info(
        "Position monitor started (generation=%d, interval=%ds, trail=%.1f%%, activation=%.1f%%)",
        generation,
        MONITOR_INTERVAL,
        config.TRAILING_STOP_PCT * 100,
        config.TRAILING_STOP_ACTIVATION_PCT * 100,
    )

    iterations = 0
    while _monitor_generation[0] == generation:
        _heartbeat_epoch[0] = time.time()
        iterations += 1
        if iterations % HEARTBEAT_LOG_EVERY_N_ITERATIONS == 0:
            log.info("Position monitor heartbeat: generation=%d iteration=%d",
                     generation, iterations)
        try:
            # Singleton guard: only the replica holding the leader lock manages
            # orders. Two replicas reaping/replacing the same stops would race.
            if lock is not None and not lock.acquire_or_renew():
                _clear_tracking()
                time.sleep(MONITOR_INTERVAL)
                continue

            # Heartbeat for /metrics: published only by the ACTIVE (leader)
            # monitor, so a stale `position-monitor` heartbeat means nobody is
            # managing stops — exactly the condition worth paging on. This is
            # the external-observer fix for the silent-death bug family
            # (README Bug Log: BUG-2026-07-13-01).
            _publish_health(health_redis, {
                "status": "healthy",
                "phase": "sweeping",
                "generation": generation,
                "iterations": iterations,
            })
            # Reap stale unfilled entry orders first — this must run even when
            # trailing stops are off, because a zombie entry has no position.
            if REAP_STALE_ENTRIES:
                try:
                    reaped = trader.reap_stale_entry_orders(STALE_ENTRY_MAX_AGE_SECONDS)
                    if reaped:
                        _action_counters["orders_reaped"] += reaped
                        log.info("Reaped %d stale unfilled entry order(s)", reaped)
                except Exception as exc:
                    log.warning("Stale-entry reap failed: %s", exc)

            # The position-management features below need the open positions.
            # Re-check config each loop in case it was hot-reloaded.
            trailing_on = config.TRAILING_STOPS_ENABLED
            time_exit_on = config.TIME_BASED_EXIT_ENABLED
            reconcile_on = RECONCILE_PROTECTIVE_STOPS and (
                config.BRACKET_ORDERS_ENABLED or trailing_on
            )
            if not trailing_on and not time_exit_on and not reconcile_on:
                _clear_tracking()
                time.sleep(MONITOR_INTERVAL)
                continue

            positions = trader.get_all_positions()

            # Safety-invariant gauges for /metrics (naked positions, zombie
            # entries, book size). Computed from data this loop fetches anyway,
            # published via worker health so the API's generic exporter picks
            # them up with no API changes.
            _publish_health(health_redis, {
                "status": "healthy",
                "phase": "sweeping",
                "generation": generation,
                "iterations": iterations,
                **compute_invariants(
                    positions, trader.get_open_orders(), now_epoch=time.time()
                ),
                **_action_counters,
            })

            if not positions:
                # Clean up tracking for closed positions
                _clear_tracking()
                time.sleep(MONITOR_INTERVAL)
                continue

            # Time-based exits only fire while the market is verifiably open:
            # an off-hours close cancels the position's protective legs and
            # then has its market liquidation rejected, leaving the position
            # naked overnight. close_position() enforces the same invariant
            # internally; checking here just avoids churning against it.
            # (README Bug Log: BUG-2026-07-10-03)
            market_open = None
            if time_exit_on:
                try:
                    market_open = trader.is_market_open()
                except Exception:
                    market_open = None

            time_exit_budget = [MAX_TIME_EXITS_PER_ITERATION]
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

                # Time-based exit takes priority: if we're flattening the
                # position, there's no point also re-trailing its stop.
                if (
                    time_exit_on
                    and market_open is True
                    and _maybe_time_exit(trader, symbol, side, time_exit_budget)
                ):
                    continue

                if trailing_on:
                    _manage_trailing_stop(
                        trader=trader,
                        symbol=symbol,
                        entry_price=entry_price,
                        current_price=current_price,
                        position_qty=int(qty_abs),
                        side=side,
                    )

            # Protective-stop reconciliation sweep (throttled). Runs after the
            # exit/trailing passes so it sees their results, and regardless of
            # market hours — a GTC stop placed off-hours queues and protects
            # the position from the next open.
            global _last_reconcile_epoch
            if (
                reconcile_on
                and time.time() - _last_reconcile_epoch >= RECONCILE_INTERVAL_SECONDS
            ):
                _last_reconcile_epoch = time.time()
                try:
                    _ensure_protective_stops(trader, positions)
                except Exception as exc:
                    log.warning("Protective-stop reconciliation failed: %s", exc)

            # Clean up tracking for positions that were closed (out of the open
            # set entirely — both trailing state and time-exit clocks).
            closed = (
                set(_trailing_stop_orders)
                | set(_position_first_seen)
                | _closing_positions
            ) - open_symbols
            for sym in closed:
                _trailing_stop_orders.pop(sym, None)
                _current_stop_prices.pop(sym, None)
                _position_first_seen.pop(sym, None)
                _closing_positions.discard(sym)

        except Exception as exc:
            log.error("Position monitor error: %s", exc, exc_info=True)

        time.sleep(MONITOR_INTERVAL)

    log.warning(
        "Position monitor generation %d exiting — superseded by a newer "
        "generation after a stale heartbeat.", generation,
    )


def _watchdog_loop(trader, lock=None, health_redis=None) -> None:
    """Respawn the monitor loop when its heartbeat goes stale.

    Pure supervision — does no order management itself, so it makes no network
    calls and cannot wedge the way the monitor can. The abandoned thread is a
    daemon; if it ever un-wedges, the generation check makes it exit.
    """
    while True:
        time.sleep(WATCHDOG_CHECK_SECONDS)
        age = time.time() - _heartbeat_epoch[0]
        if age <= MONITOR_HEARTBEAT_STALL_SECONDS:
            continue
        _monitor_generation[0] += 1
        generation = _monitor_generation[0]
        log.critical(
            "POSITION MONITOR WEDGED: no heartbeat for %.0fs (limit %ds) — "
            "respawning as generation %d. The safety loop (stops, exits, "
            "reconciliation) was NOT running during that window.",
            age, MONITOR_HEARTBEAT_STALL_SECONDS, generation,
        )
        _heartbeat_epoch[0] = time.time()
        threading.Thread(
            target=_monitor_loop,
            args=(trader, lock, generation, health_redis),
            name=f"position-monitor-g{generation}",
            daemon=True,
        ).start()


def start_position_monitor(trader, lock=None, health_redis=None) -> Optional[threading.Thread]:
    """
    Start the position monitor as a daemon thread.

    Returns the thread if trailing stops are enabled, otherwise None.
    The thread is set as a daemon so it dies with the main process.

    ``lock`` is an optional shared.singleton_lock.RedisLeaderLock; when given,
    only the replica holding it actually manages orders, making multi-replica
    deployments safe.
    """
    if (
        not config.TRAILING_STOPS_ENABLED
        and not config.TIME_BASED_EXIT_ENABLED
        and not REAP_STALE_ENTRIES
    ):
        log.info(
            "Trailing stops + time-based exit + stale-entry reaper all disabled "
            "— position monitor not started"
        )
        return None

    _heartbeat_epoch[0] = time.time()
    thread = threading.Thread(
        target=_monitor_loop,
        args=(trader, lock, _monitor_generation[0], health_redis),
        name="position-monitor",
        daemon=True,
    )
    thread.start()
    watchdog = threading.Thread(
        target=_watchdog_loop,
        args=(trader, lock, health_redis),
        name="position-monitor-watchdog",
        daemon=True,
    )
    watchdog.start()
    log.info("Position monitor thread started (with heartbeat watchdog)")
    return thread

