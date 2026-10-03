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


def stop_levels(side, price, point, digits, config):
    """(stop loss, take profit) for a position opened at price."""
    direction = 1 if side == "buy" else -1
    stop_loss = round(price - direction * config["SL_POINTS"] * point, digits)
    take_profit = round(price + direction * config["TP_POINTS"] * point, digits)
    return stop_loss, take_profit
