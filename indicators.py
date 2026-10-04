"""Technical indicators on plain lists of prices, oldest first.

Every function returns only the values it can compute, so a result is shorter
than its input and lines up with it at the end: result[-1] belongs to prices[-1],
result[-2] to the candle before. Too little data gives an empty list.

EMA-based values (ema, rsi, macd) need history to settle: feed them at least
3-4x their period, or they will not match what the MT5 chart shows.
"""
import statistics


def sma(prices, period):
    """Simple moving average."""
    return [sum(prices[end - period:end]) / period for end in range(period, len(prices) + 1)]


def ema(prices, period):
    """Exponential moving average, seeded with the SMA of the first `period` prices."""
    if len(prices) < period:
        return []
    weight = 2 / (period + 1)
    values = [sum(prices[:period]) / period]
    for price in prices[period:]:
        values.append(values[-1] + weight * (price - values[-1]))
    return values


def rsi(prices, period=14):
    """Relative Strength Index (0-100) with Wilder smoothing."""
    changes = [after - before for before, after in zip(prices, prices[1:])]
    if len(changes) < period:
        return []

    def strength_index(gain, loss):
        if loss == 0:
            return 100.0 if gain else 50.0  # flat market reads neutral
        return 100 - 100 / (1 + gain / loss)

    average_gain = sum(max(change, 0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0) for change in changes[:period]) / period
    values = [strength_index(average_gain, average_loss)]
    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        values.append(strength_index(average_gain, average_loss))
    return values


def macd(prices, fast=12, slow=26, signal=9):
    """Return (macd_line, signal_line, histogram).

    Textbook MACD with an EMA signal line. The MT5 built-in MACD window smooths
    its signal line differently, so expect small differences against the chart.
    """
    fast_ema, slow_ema = ema(prices, fast), ema(prices, slow)
    macd_line = [quick - lagging for quick, lagging in zip(fast_ema[-len(slow_ema):], slow_ema)]
    signal_line = ema(macd_line, signal)
    histogram = [line - smoothed for line, smoothed in zip(macd_line[-len(signal_line):], signal_line)]
    return macd_line, signal_line, histogram


def bollinger(prices, period=20, deviations=2.0):
    """Return (lower, middle, upper) bands using population standard deviation."""
    middle = sma(prices, period)
    widths = [deviations * statistics.pstdev(prices[start:start + period]) for start in range(len(middle))]
    lower = [mean - width for mean, width in zip(middle, widths)]
    upper = [mean + width for mean, width in zip(middle, widths)]
    return lower, middle, upper


# --- candle-based ------------------------------------------------------------------
# These take candles: mappings with open, high, low, close (and time for resample), oldest first.

def true_ranges(candles):
    """True range of every candle after the first: the widest of the bar and the gap from the last close."""
    return [
        max(now["high"] - now["low"], abs(now["high"] - before["close"]), abs(now["low"] - before["close"]))
        for before, now in zip(candles, candles[1:])
    ]


def atr(candles, period=14):
    """Average True Range with Wilder smoothing; empty until period + 1 candles exist."""
    ranges = true_ranges(candles)
    if len(ranges) < period:
        return []
    values = [sum(ranges[:period]) / period]
    for value in ranges[period:]:
        values.append((values[-1] * (period - 1) + value) / period)
    return values


def resample(candles, factor, seconds):
    """Merge candles of `seconds` length into bars `factor` times longer, aligned to the clock.

    The newest bar may still be forming. Candles are dicts; the result has the same keys.
    """
    bars = []
    span = factor * seconds
    for candle in candles:
        start = candle["time"] - candle["time"] % span
        if bars and bars[-1]["time"] == start:
            bar = bars[-1]
            bar["high"] = max(bar["high"], candle["high"])
            bar["low"] = min(bar["low"], candle["low"])
            bar["close"] = candle["close"]
            bar["spread"] = candle["spread"]
        else:
            bars.append({
                "time": start, "open": candle["open"], "high": candle["high"], "low": candle["low"],
                "close": candle["close"], "spread": candle["spread"],
            })
    return bars
