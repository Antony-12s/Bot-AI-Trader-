"""The built-in analyst: reads a strategy written in plain English and checks it on the candles.

No AI and no key: each clause must match one of the phrasings below, or it is reported as not
understood. A side (buy or sell) trades only when all its clauses were understood and all hold
now; if both sides hold, or neither, the answer is hold.

    "Buy when the 10-candle average crosses above the 30-candle average. Sell on the opposite cross."
    "Buy when price is above EMA 50, RSI is between 40 and 65 and the candle is green. Sell on the mirror image."

Understood: RSI (above / below / between / crosses), price against a moving average (SMA or EMA,
"20-candle average", "EMA(20)", "SMA 50"), one average against another (above / crosses), an
average rising or falling, MACD against its signal line or zero, the histogram, green / red
candles, engulfing candles, Bollinger bands, and a close beyond the previous candle's high / low.
"""
import re

import indicators

ABOVE = r"(?:above|over|higher than|greater than|>)"
BELOW = r"(?:below|under|beneath|lower than|less than|<)"
CROSS_UP = r"(?:cross(?:es|ed)?|climb(?:s|ed)?|ris(?:es|e)|rose|mov(?:es|ed|e)|break(?:s)?|broke|go(?:es)?|went|turn(?:s|ed)?)\s+(?:back\s+)?(?:above|over|up through)"
CROSS_DOWN = r"(?:cross(?:es|ed)?|drop(?:s|ped)?|fall(?:s)?|fell|mov(?:es|ed|e)|break(?:s)?|broke|go(?:es)?|went|turn(?:s|ed)?)\s+(?:back\s+)?(?:below|under|down through)"
MA = r"((?:sma|ema)\d+)"
NUMBER = r"(\d+(?:\.\d+)?)"
# words that carry no condition of their own: what is left of a clause after its match must be only these
FILLER = set("""when if then and the a an is are was has have had its it price close closes closed closing candle candles
bar bars current now just also only line value level of on at to buy sell long short go enter open trade take
position new last latest this that back while with both reading just again already still recent""".split())


def _ma_tokens(text):
    """"the 20-candle average", "EMA(20)", "50 ema", "sma 50" -> sma20 / ema20 / ema50 / sma50."""
    def named(kind, period):
        return ("ema" if kind and kind.startswith(("ema", "exp")) else "sma") + period
    text = re.sub(r"(?:the\s+)?(\d+)[\s-]*(?:candles?|periods?|bars?|days?)?[\s-]*(simple|exponential)?\s*(ema|sma|ma|moving average|average)\b",
                  lambda m: named(m.group(2) or m.group(3), m.group(1)), text)
    text = re.sub(r"\b(?:the\s+)?(ema|sma|ma)\s*\(?\s*(\d+)(?:\s*\))?", lambda m: named(m.group(1), m.group(2)), text)
    text = re.sub(r"\brsi\s*\(?\s*14(?:\s*\))?", "rsi", text)  # the standard RSI(14); other periods stay unread
    return re.sub(r"\b(?:price|line)\s+", "", text)  # "the 10-candle average price crosses" -> "sma10 crosses"


# (pattern, condition builder); tried in order, the first match reads the clause
PATTERNS = [
    (rf"rsi\D*?{NUMBER}\s*(?:to|-)\s*{NUMBER}", lambda m: ("rsi_between", *sorted((float(m[1]), float(m[2]))))),
    (rf"rsi\D*?{CROSS_UP}\s*{NUMBER}", lambda m: ("rsi_cross", ">", float(m[1]))),
    (rf"rsi\D*?{CROSS_DOWN}\s*{NUMBER}", lambda m: ("rsi_cross", "<", float(m[1]))),
    (rf"rsi\D*?{ABOVE}\s*{NUMBER}", lambda m: ("rsi", ">", float(m[1]))),
    (rf"rsi\D*?{BELOW}\s*{NUMBER}", lambda m: ("rsi", "<", float(m[1]))),
    (r"rsi\D*?overbought", lambda m: ("rsi", ">", 70.0)),
    (r"rsi\D*?oversold", lambda m: ("rsi", "<", 30.0)),
    (rf"{MA}\s+{CROSS_UP}\s+{MA}", lambda m: ("ma_cross", ">", m[1], m[2])),
    (rf"{MA}\s+{CROSS_DOWN}\s+{MA}", lambda m: ("ma_cross", "<", m[1], m[2])),
    (rf"{MA}\s+(?:is\s+)?{ABOVE}\s+{MA}", lambda m: ("ma_vs_ma", ">", m[1], m[2])),
    (rf"{MA}\s+(?:is\s+)?{BELOW}\s+{MA}", lambda m: ("ma_vs_ma", "<", m[1], m[2])),
    (rf"{MA}\s+(?:is\s+)?(?:rising|pointing up|sloping up|turning up|going up)", lambda m: ("ma_slope", ">", m[1])),
    (rf"{MA}\s+(?:is\s+)?(?:falling|pointing down|sloping down|turning down|going down)", lambda m: ("ma_slope", "<", m[1])),
    (rf"{CROSS_UP}\s+{MA}", lambda m: ("price_cross", ">", m[1])),
    (rf"{CROSS_DOWN}\s+{MA}", lambda m: ("price_cross", "<", m[1])),
    (rf"{ABOVE}\s+{MA}", lambda m: ("price_vs_ma", ">", m[1])),
    (rf"{BELOW}\s+{MA}", lambda m: ("price_vs_ma", "<", m[1])),
    (rf"macd\s+(?:line\s+)?{CROSS_UP}\s+(?:its\s+)?signal", lambda m: ("macd_cross", ">")),
    (rf"macd\s+(?:line\s+)?{CROSS_DOWN}\s+(?:its\s+)?signal", lambda m: ("macd_cross", "<")),
    (rf"macd\s+(?:line\s+)?(?:is\s+)?{ABOVE}\s+(?:its\s+)?signal", lambda m: ("macd_vs_signal", ">")),
    (rf"macd\s+(?:line\s+)?(?:is\s+)?{BELOW}\s+(?:its\s+)?signal", lambda m: ("macd_vs_signal", "<")),
    (r"(?:macd\s+)?histogram\s+(?:is\s+)?(?:positive|above (?:zero|0))", lambda m: ("histogram", ">")),
    (r"(?:macd\s+)?histogram\s+(?:is\s+)?(?:negative|below (?:zero|0))", lambda m: ("histogram", "<")),
    (rf"macd\s+(?:line\s+)?(?:is\s+)?{ABOVE}\s+(?:zero|0)", lambda m: ("macd_vs_zero", ">")),
    (rf"macd\s+(?:line\s+)?(?:is\s+)?{BELOW}\s+(?:zero|0)", lambda m: ("macd_vs_zero", "<")),
    (r"bullish engulfing", lambda m: ("engulfing", "bull")),
    (r"bearish engulfing", lambda m: ("engulfing", "bear")),
    (r"(?:green|bullish|up)\s+candle|candle\s+(?:is\s+|closes\s+)?(?:green|bullish)", lambda m: ("candle", "green")),
    (r"(?:red|bearish|down)\s+candle|candle\s+(?:is\s+|closes\s+)?(?:red|bearish)", lambda m: ("candle", "red")),
    (rf"{ABOVE}\s+(?:the\s+)?(upper|middle|lower)\s+(?:bollinger\s+)?band", lambda m: ("band", ">", m[1])),
    (rf"{BELOW}\s+(?:the\s+)?(upper|middle|lower)\s+(?:bollinger\s+)?band", lambda m: ("band", "<", m[1])),
    (rf"{ABOVE}\s+(?:the\s+)?(?:previous|prior|last)\s+(?:candle'?s?\s+|bar'?s?\s+)?high", lambda m: ("previous", ">")),
    (rf"{BELOW}\s+(?:the\s+)?(?:previous|prior|last)\s+(?:candle'?s?\s+|bar'?s?\s+)?low", lambda m: ("previous", "<")),
]
MIRROR_WORDS = re.compile(r"\b(mirror|opposite|reverse|vice versa|other way)\b")
BUY_WORDS, SELL_WORDS = {"buy", "buys", "long", "longs"}, {"sell", "sells", "short", "shorts"}


def read_clause(clause):
    """A condition tuple, or None when the clause says something not understood."""
    for pattern, build in PATTERNS:
        match = re.search(pattern, clause)
        if match:
            rest = (clause[:match.start()] + " " + clause[match.end():]).replace("'s", " ")
            if set(re.findall(r"[a-z]+", rest)) <= FILLER:
                return build(match)
            return None  # it says more than the match: guessing would trade on half a rule
    return None


def parse(text):
    """{"buy": [conditions], "sell": [conditions], "unknown": {"buy": [...], "sell": [...]}}."""
    rules = {"buy": [], "sell": [], "unknown": {"buy": [], "sell": []}}
    mirrored = set()
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text.lower().replace("–", "-")):
        sentence = sentence.strip(" .!?;")
        if not sentence:
            continue
        words = set(re.findall(r"[a-z]+", sentence))
        sides = [side for side, names in (("buy", BUY_WORDS), ("sell", SELL_WORDS)) if words & names] or ["buy", "sell"]
        if MIRROR_WORDS.search(sentence) and len(sides) == 1:
            mirrored.add(sides[0])
            sentence = re.sub(r".*?\b(mirror|opposite|reverse|vice versa|other way)\b[^,]*", "", sentence)
        body = re.sub(r"^\s*(?:and\s+)?(?:buy|sell|go long|go short|long|short|enter)\w*\s+(?:when|if|once|on|after)?\s*", "", sentence)
        body = re.sub(r"between\s+(\d+(?:\.\d+)?)\s+and\s+", r"between \1 to ", _ma_tokens(body))
        for clause in re.split(r",|\band\b|\bwhile\b|\bwith\b|\bplus\b|\balso\b|\bafter\b|\bwhen\b", body):
            clause = clause.strip()
            if not set(re.findall(r"[a-z]+", clause)) - FILLER:
                continue  # nothing but filler words, e.g. "sell" left from "sell on the opposite cross"
            condition = read_clause(clause)
            for side in sides:
                (rules[side] if condition else rules["unknown"][side]).append(condition or clause)
    for side, other in (("buy", "sell"), ("sell", "buy")):
        if side in mirrored:
            rules[side] += [mirror(condition) for condition in rules[other]]
            rules["unknown"][side] += rules["unknown"][other]
    return rules


def mirror(condition):
    """The same condition for the other side: above <-> below, RSI 30 <-> 70, green <-> red."""
    kind, *args = condition
    flip = {">": "<", "<": ">"}
    if kind == "rsi_between":
        return (kind, 100 - args[1], 100 - args[0])
    if kind in ("rsi", "rsi_cross"):
        return (kind, flip[args[0]], 100 - args[1])
    if kind == "candle":
        return (kind, "red" if args[0] == "green" else "green")
    if kind == "engulfing":
        return (kind, "bear" if args[0] == "bull" else "bull")
    if kind == "band":
        return (kind, flip[args[0]], {"upper": "lower", "lower": "upper"}.get(args[1], args[1]))
    return (kind, flip[args[0]], *args[1:])


def describe(condition):
    """A condition in words, for the app and the decision log."""
    kind, *args = condition
    side = lambda op: "above" if op == ">" else "below"
    ma = lambda token: f"{token[:3].upper()}({token[3:]})"
    return {
        "rsi_between": lambda: f"RSI between {args[0]:g} and {args[1]:g}",
        "rsi": lambda: f"RSI {side(args[0])} {args[1]:g}",
        "rsi_cross": lambda: f"RSI crosses {side(args[0])} {args[1]:g}",
        "ma_cross": lambda: f"{ma(args[1])} crosses {side(args[0])} {ma(args[2])}",
        "ma_vs_ma": lambda: f"{ma(args[1])} {side(args[0])} {ma(args[2])}",
        "ma_slope": lambda: f"{ma(args[1])} {'rising' if args[0] == '>' else 'falling'}",
        "price_cross": lambda: f"price crosses {side(args[0])} {ma(args[1])}",
        "price_vs_ma": lambda: f"price {side(args[0])} {ma(args[1])}",
        "macd_cross": lambda: f"MACD crosses {side(args[0])} its signal line",
        "macd_vs_signal": lambda: f"MACD {side(args[0])} its signal line",
        "macd_vs_zero": lambda: f"MACD {side(args[0])} zero",
        "histogram": lambda: f"MACD histogram {'positive' if args[0] == '>' else 'negative'}",
        "engulfing": lambda: f"{'bullish' if args[0] == 'bull' else 'bearish'} engulfing candle",
        "candle": lambda: f"{args[0]} candle",
        "band": lambda: f"close {side(args[0])} the {args[1]} Bollinger band",
        "previous": lambda: f"close {'above the previous high' if args[0] == '>' else 'below the previous low'}",
    }[kind]()


def holds(condition, candles):
    """Does the condition hold on the last closed candle? Too little history reads as no."""
    kind, *args = condition
    prices = [candle["close"] for candle in candles]
    beyond = lambda op, a, b: a > b if op == ">" else a < b
    crossed = lambda op, before, now: beyond(op, now[1], now[0]) and not beyond(op, before[1], before[0])

    def line(token):
        return (indicators.ema if token.startswith("ema") else indicators.sma)(prices, int(token[3:]))

    try:
        if kind.startswith("rsi"):
            rsi = indicators.rsi(prices)
            if kind == "rsi_between":
                return args[0] <= rsi[-1] <= args[1]
            if kind == "rsi":
                return beyond(args[0], rsi[-1], args[1])
            return crossed(args[0], (args[1], rsi[-2]), (args[1], rsi[-1]))
        if kind in ("ma_cross", "ma_vs_ma"):
            first, second = line(args[1]), line(args[2])
            if kind == "ma_vs_ma":
                return beyond(args[0], first[-1], second[-1])
            return crossed(args[0], (second[-2], first[-2]), (second[-1], first[-1]))
        if kind == "ma_slope":
            values = line(args[1])
            return beyond(args[0], values[-1], values[-6])  # over the last 5 candles, like trend_pullback's EMA50
        if kind in ("price_cross", "price_vs_ma"):
            values = line(args[1])
            if kind == "price_vs_ma":
                return beyond(args[0], prices[-1], values[-1])
            return crossed(args[0], (values[-2], prices[-2]), (values[-1], prices[-1]))
        if kind.startswith("macd") or kind == "histogram":
            macd_line, signal, histogram = indicators.macd(prices)
            if kind == "histogram":
                return beyond(args[0], histogram[-1], 0)
            if kind == "macd_vs_zero":
                return beyond(args[0], macd_line[-1], 0)
            if kind == "macd_vs_signal":
                return beyond(args[0], macd_line[-1], signal[-1])
            return crossed(args[0], (signal[-2], macd_line[-2]), (signal[-1], macd_line[-1]))
        last, before = candles[-1], candles[-2]
        if kind == "candle":
            return last["close"] > last["open"] if args[0] == "green" else last["close"] < last["open"]
        if kind == "engulfing":
            if args[0] == "bull":
                return before["close"] < before["open"] < last["close"] and last["open"] <= before["close"] and last["close"] > last["open"]
            return before["close"] > before["open"] > last["close"] and last["open"] >= before["close"] and last["close"] < last["open"]
        if kind == "band":
            lower, middle, upper = indicators.bollinger(prices, 20)
            return beyond(args[0], prices[-1], {"lower": lower, "middle": middle, "upper": upper}[args[1]][-1])
        if kind == "previous":
            return last["close"] > before["high"] if args[0] == ">" else last["close"] < before["low"]
    except IndexError:
        return False
    raise ValueError(f"unknown condition {kind}")


def summary(text):
    """What the analyst understood, for the agent wizard: conditions in words and what it could not read."""
    rules = parse(text)
    return {side: [describe(c) for c in rules[side]] for side in ("buy", "sell")} | {"unknown": sorted(
        set(rules["unknown"]["buy"]) | set(rules["unknown"]["sell"]))}


def decide(text, candles):
    """(signal or None, reason) on the last closed candle."""
    rules = parse(text)
    ready = [side for side in ("buy", "sell") if rules[side] and not rules["unknown"][side]]
    if not ready:
        return None, "no rule the built-in analyst can read: rewrite it, or pick an AI analyst"
    met = [side for side in ready if all(holds(condition, candles) for condition in rules[side])]
    if len(met) != 1:
        return None, "both sides' rules hold: hold" if met else "rules not met"
    side = met[0]
    return side, "; ".join(describe(condition) for condition in rules[side])
