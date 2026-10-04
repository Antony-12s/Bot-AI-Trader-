"""Risk rules the brains cannot touch: when a trade is allowed and where its stops go.

Pure functions, shared by the live bot, the paper tracker and the replay.
"""
SECONDS_PER_DAY = 86400


def day_start(server_time):
    """Midnight (broker server clock) of the day containing server_time."""
    return server_time - server_time % SECONDS_PER_DAY


def block_reason(paused, open_position_count, pnl_today, spread_points, config, ai_spent_today=0.0):
    """Return why a new trade is not allowed right now, or None when it is."""
    if paused:
        return "paused"
    if open_position_count:
        return "position already open"
    if pnl_today <= -config["MAX_DAILY_LOSS"]:
        return f"daily loss limit hit ({pnl_today:.2f})"
    if spread_points > config["MAX_SPREAD_POINTS"]:
        return f"spread too wide ({spread_points} points)"
    if config["BRAIN"] == "ai" and ai_spent_today >= config["AI_BUDGET_USD"]:
        return f"AI budget for today spent (${ai_spent_today:.2f} of ${config['AI_BUDGET_USD']:.2f})"
    return None


def account_error(mode, is_demo_account):
    """Return why this account must not be traded in this mode, or None."""
    if mode == "demo" and not is_demo_account:
        return "MODE=demo but the logged-in MT5 account is not a demo account, refusing to trade"
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
