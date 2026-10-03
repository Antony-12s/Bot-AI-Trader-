"""Trading signal. This is the only file to edit when changing the strategy.

Building blocks live in indicators.py: sma, ema, rsi, macd, bollinger.
"""
from indicators import sma

FAST_PERIOD = 10
SLOW_PERIOD = 30
# How many closed candles the bot fetches for decide(). Raise it when using
# ema / rsi / macd: they need 3-4x their period to settle.
CANDLES_NEEDED = SLOW_PERIOD + 1


def decide(closes):
    """Return "buy", "sell" or None from closed-candle close prices, oldest first.

    Sample moving-average cross. It exists to exercise the pipeline end to end,
    it is not a tested or profitable strategy.
    """
    if len(closes) < SLOW_PERIOD + 1:
        return None
    fast, slow = sma(closes, FAST_PERIOD), sma(closes, SLOW_PERIOD)
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        return "buy"
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        return "sell"
    return None
