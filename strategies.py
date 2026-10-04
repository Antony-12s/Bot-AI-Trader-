"""Rule-based setups. Edit or add to this file to change what the rules look for.

Every setup takes closed candles (dicts with time, open, high, low, close, spread; oldest
first) and returns (signal, reason): "buy", "sell" or None. STRATEGY in .env picks one by
name, a comma-separated list, or "all". With BRAIN=rules the setup trades on its own; with
BRAIN=hybrid it only proposes, and the AI brain decides.

None of these are proven. Run `python replay.py history.csv --compare` on your own data
before trusting any of them.
"""
import indicators

CANDLES_NEEDED = 200  # EMA50 needs a few multiples of its period to settle


def closes(candles):
    return [candle["close"] for candle in candles]


def ma_cross(candles, config):
    """Fast SMA(10) crossing the slow SMA(30). The original sample strategy."""
    prices = closes(candles)
    if len(prices) < 31:
        return None, "not enough candles"
    fast, slow = indicators.sma(prices, 10), indicators.sma(prices, 30)
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        return "buy", "fast MA crossed above slow MA"
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        return "sell", "fast MA crossed below slow MA"
    return None, "no MA cross"


def trend_pullback(candles, config):
    """Trend following: EMA50 points the way, price dips into EMA20 and closes back in the trend's direction."""
    prices = closes(candles)
    ema20, ema50 = indicators.ema(prices, 20), indicators.ema(prices, 50)
    rsi = indicators.rsi(prices)
    if len(ema50) < 6 or not rsi:
        return None, "not enough candles"
    last = candles[-1]
    recent = list(zip(candles[-4:-1], ema20[-4:-1]))  # the three candles before this one
    uptrend = ema50[-1] > ema50[-6] and prices[-1] > ema50[-1]
    downtrend = ema50[-1] < ema50[-6] and prices[-1] < ema50[-1]
    dipped = any(candle["low"] <= value for candle, value in recent)
    popped = any(candle["high"] >= value for candle, value in recent)
    if uptrend and dipped and prices[-1] > ema20[-1] and last["close"] > last["open"] and 40 <= rsi[-1] <= 65:
        return "buy", "uptrend, pullback to EMA20 and a bullish close"
    if downtrend and popped and prices[-1] < ema20[-1] and last["close"] < last["open"] and 35 <= rsi[-1] <= 60:
        return "sell", "downtrend, pullback to EMA20 and a bearish close"
    return None, "no pullback setup"


def bollinger_breakout(candles, config):
    """Volatility breakout: the bands had shrunk to half their 20-candle widest, then a close escapes them."""
    prices = closes(candles)
    lower, middle, upper = indicators.bollinger(prices, 20)
    if len(middle) < 21:
        return None, "not enough candles"
    widths = [(top - bottom) / mid for bottom, mid, top in zip(lower, middle, upper)]
    squeezed = widths[-2] <= 0.5 * max(widths[-21:-1])  # the bands had shrunk to half their recent widest
    if squeezed and prices[-1] > upper[-1]:
        return "buy", "breakout above squeezed Bollinger bands"
    if squeezed and prices[-1] < lower[-1]:
        return "sell", "breakdown below squeezed Bollinger bands"
    return None, "no squeeze breakout"


def rsi_reversion(candles, config):
    """Mean reversion: RSI leaves an extreme while price sits outside the Bollinger bands."""
    prices = closes(candles)
    rsi = indicators.rsi(prices)
    lower, middle, upper = indicators.bollinger(prices, 20)
    if len(rsi) < 2 or len(lower) < 2:
        return None, "not enough candles"
    if rsi[-2] < 30 <= rsi[-1] and prices[-2] < lower[-2]:
        return "buy", "oversold: RSI turned up from below 30 under the lower band"
    if rsi[-2] > 70 >= rsi[-1] and prices[-2] > upper[-2]:
        return "sell", "overbought: RSI turned down from above 70 over the upper band"
    return None, "no reversion setup"


STRATEGIES = {
    "ma_cross": ma_cross,
    "trend_pullback": trend_pullback,
    "bollinger_breakout": bollinger_breakout,
    "rsi_reversion": rsi_reversion,
}


def selected(config):
    """Strategy names chosen by STRATEGY in .env."""
    names = config.get("STRATEGY", "all")
    if names == "all":
        return list(STRATEGIES)
    return [name.strip() for name in names.split(",") if name.strip()]


def candidates(candles, config):
    """[(name, signal, reason)] for every selected setup that fires on the last candle."""
    found = []
    for name in selected(config):
        signal, reason = STRATEGIES[name](candles, config)
        if signal:
            found.append((name, signal, reason))
    return found


def decide(candles, config):
    """The rules brain: trade the setup that fires, hold when none or when setups disagree."""
    found = candidates(candles, config)
    if not found:
        return None, "no setup"
    if len({signal for _, signal, _ in found}) > 1:
        return None, "setups disagree: " + ", ".join(f"{name} {signal}" for name, signal, _ in found)
    name, signal, reason = found[0]
    return signal, f"{name}: {reason}"
