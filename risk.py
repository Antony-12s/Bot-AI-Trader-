"""Risk rules the brains cannot touch: when a trade is allowed and where its stops go.

Pure functions, shared by the live bot, the paper tracker and the replay.
"""
SECONDS_PER_DAY = 86400


def day_start(server_time):
    """Midnight (broker server clock) of the day containing server_time."""
    return server_time - server_time % SECONDS_PER_DAY


def block_reason(paused, open_position_count, pnl_today, spread_points, config, ai_spent_today=0.0, trades_today=0):
    """Return why a new trade is not allowed right now, or None when it is."""
    if paused:
        return "paused"
    if open_position_count:
        return "position already open"
    if pnl_today <= -config["MAX_DAILY_LOSS"]:
        return f"daily loss limit hit ({pnl_today:.2f})"
    if config.get("MAX_TRADES_PER_DAY") and trades_today >= config["MAX_TRADES_PER_DAY"]:
        return f"daily trade limit hit ({trades_today} of {config['MAX_TRADES_PER_DAY']})"
    if spread_points > config["MAX_SPREAD_POINTS"]:
        return f"spread too wide ({spread_points} points)"
    if config["BRAIN"] in ("ai", "hybrid") and ai_spent_today >= config["AI_BUDGET_USD"]:
        return f"AI budget for today spent (${ai_spent_today:.2f} of ${config['AI_BUDGET_USD']:.2f})"
    return None


def account_error(mode, is_demo_account, login=None, live_login=""):
    """Return why this account must not be traded in this mode, or None.

    Live trades the real account the owner approved (LIVE_LOGIN), not whichever real account MT5
    happens to be on: another one waits for the owner's yes in the app."""
    if mode == "demo" and not is_demo_account:
        return "MODE=demo but the logged-in MT5 account is not a demo account, refusing to trade"
    if mode == "live" and not is_demo_account and live_login and login is not None and str(login) != str(live_login):
        return (f"MODE=live is for real account {live_login}, but MT5 is on real account {login}: "
                "refusing to trade it until you approve it in the app")
    return None


def stop_distances(config, point, atr_value=None, floor_points=0):
    """(stop loss points, take profit points) for the next trade.

    ATR multiples (SL_ATR, TP_ATR) when both are set and an ATR is known, else the fixed
    SL_POINTS / TP_POINTS. The stop never comes closer than floor_points (the broker's
    minimum stop distance, or a couple of spreads).
    """
    if config["SL_ATR"] > 0 and config["TP_ATR"] > 0 and atr_value:
        stop = round(atr_value * config["SL_ATR"] / point)
        target = round(atr_value * config["TP_ATR"] / point)
    else:
        stop, target = config["SL_POINTS"], config["TP_POINTS"]
    return max(1, stop, floor_points), max(1, target)


def stop_levels(side, price, point, digits, stop_points, target_points):
    """(stop loss, take profit) prices for a position opened at price."""
    direction = 1 if side == "buy" else -1
    stop_loss = round(price - direction * stop_points * point, digits)
    take_profit = round(price + direction * target_points * point, digits)
    return stop_loss, take_profit


def risk_lot(balance, risk_percent, stop_points, point, loss_per_point_per_lot, volume_min, volume_max, volume_step):
    """(lot, None) sized so the stop loses risk_percent of balance, or (None, why not).

    loss_per_point_per_lot is what one point against a 1-lot position costs in the account's
    currency (MT5: trade_tick_value / trade_tick_size * point). Rounded down to the broker's step;
    a lot under the broker's minimum is refused, not rounded up: that would risk more than asked.
    """
    risk_money = balance * risk_percent / 100
    loss_per_lot = stop_points * loss_per_point_per_lot
    if risk_money <= 0 or loss_per_lot <= 0:
        return None, "cannot size the order: no balance or no stop distance"
    steps = int(risk_money / loss_per_lot / volume_step + 1e-9)
    lot = round(min(steps * volume_step, volume_max), 8)
    if lot < volume_min:
        return None, (f"{risk_percent:g}% of {balance:.2f} is {risk_money:.2f}, less than the smallest lot ({volume_min:g})"
                      f" loses at this stop ({volume_min * loss_per_lot:.2f}): skipped")
    return lot, None


def moved_stop(side, entry, first_stop, best_price, stop, config, digits):
    """The stop after break-even and trailing, or None to leave it where it is.

    Both count in R, the trade's first stop distance: BREAKEVEN_R moves the stop to the entry once
    price has gone that many R in favour; TRAIL_R keeps it that many R behind the best price.
    A stop only ever moves in the trade's favour. 0 turns either off.
    """
    risk = abs(entry - first_stop)
    if not risk:
        return None
    sign = 1 if side == "buy" else -1
    gained = (best_price - entry) * sign
    wanted = [stop]
    if config.get("BREAKEVEN_R") and gained >= config["BREAKEVEN_R"] * risk:
        wanted.append(entry)
    if config.get("TRAIL_R"):
        wanted.append(best_price - sign * config["TRAIL_R"] * risk)
    best = round(max(wanted, key=lambda level: level * sign), digits)
    return best if (best - stop) * sign > 10 ** -digits / 2 else None
