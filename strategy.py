"""Trading signal. This is the only file to edit when changing the strategy."""

FAST_PERIOD = 10
SLOW_PERIOD = 30
CANDLES_NEEDED = SLOW_PERIOD + 1


def average(values):
    return sum(values) / len(values)


def decide(closes):
    """Return "buy", "sell" or None from closed-candle close prices, oldest first.

    Sample moving-average cross. It exists to exercise the pipeline end to end,
    it is not a tested or profitable strategy.
    """
    if len(closes) < CANDLES_NEEDED:
        return None
    fast_now, slow_now = average(closes[-FAST_PERIOD:]), average(closes[-SLOW_PERIOD:])
    fast_before = average(closes[-FAST_PERIOD - 1:-1])
    slow_before = average(closes[-SLOW_PERIOD - 1:-1])
    if fast_before <= slow_before and fast_now > slow_now:
        return "buy"
    if fast_before >= slow_before and fast_now < slow_now:
        return "sell"
    return None
