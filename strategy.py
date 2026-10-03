"""Rule-based brain (BRAIN=rules). Edit this file to change the rules.

Building blocks live in indicators.py: sma, ema, rsi, macd, bollinger.
"""
from indicators import sma

FAST_PERIOD = 10
SLOW_PERIOD = 30
# How many closed candles the bot fetches for decide(). Raise it when using
# ema / rsi / macd: they need 3-4x their period to settle.
CANDLES_NEEDED = SLOW_PERIOD + 1


def decide(closes, config=None):
    """Return (signal, reason): signal is "buy", "sell" or None for hold.

    closes are closed-candle close prices, oldest first. Sample moving-average
    cross: it exists to exercise the pipeline end to end, it is not a tested or
    profitable strategy.
    """
    if len(closes) < SLOW_PERIOD + 1:
        return None, "not enough candles"
    fast, slow = sma(closes, FAST_PERIOD), sma(closes, SLOW_PERIOD)
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        return "buy", "fast MA crossed above slow MA"
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        return "sell", "fast MA crossed below slow MA"
    return None, "no MA cross"
