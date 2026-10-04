"""Paper fills: open a virtual position and close it from candle highs and lows.

MT5 candles are bid prices. A buy opens at the ask (bid + spread) and exits at the bid,
a sell opens at the bid and exits at the ask. Shared by the dry-mode paper tracker in
bot.py and by replay.py, so both learn from the same fill rules.

Simplifications, all on the pessimistic side or neutral: a stop is filled exactly at its
level (a real gap fills worse), and when one candle spans both the stop and the target
the stop is taken, because the candle does not say which came first.
"""
import risk


def open_position(side, bid, spread_points, point, digits, config, opened_at, reason="", atr_value=None):
    """Virtual position opened right after a candle closed, at the current bid/ask.

    Stops come from risk.stop_distances: ATR multiples when configured, never closer
    than two spreads.
    """
    ask = round(bid + spread_points * point, digits)
    entry = ask if side == "buy" else bid
    stop_points, target_points = risk.stop_distances(config, point, atr_value, floor_points=2 * spread_points)
    stop_loss, take_profit = risk.stop_levels(side, entry, point, digits, stop_points, target_points)
    return {
        "side": side,
        "lot": config["LOT"],
        "entry": entry,
        "sl": stop_loss,
        "tp": take_profit,
        "opened_at": opened_at,
        "reason": reason,
    }


def exit_price(position, high, low, spread_points, point):
    """(price, "sl" | "tp") if this candle's range touched a level, else None."""
    if position["side"] == "buy":  # exits at the bid, which is what the candle shows
        hit_stop, hit_target = low <= position["sl"], high >= position["tp"]
    else:  # exits at the ask: shift the candle up by the spread
        offset = spread_points * point
        hit_stop, hit_target = high + offset >= position["sl"], low + offset <= position["tp"]
    if hit_stop:
        return position["sl"], "sl"
    if hit_target:
        return position["tp"], "tp"
    return None


def profit(position, price, contract_size):
    """Account-currency result of closing position at price (no commission, no swap)."""
    direction = 1 if position["side"] == "buy" else -1
    return round(direction * (price - position["entry"]) * contract_size * position["lot"], 2)
