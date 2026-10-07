"""AI brain: asks Claude for buy / sell / hold once per closed candle, and learns from the result.

Enabled with BRAIN=ai (Claude decides alone) or BRAIN=hybrid (the rules in strategies.py
propose setups, Claude takes one or holds). The model only picks the direction: lot size,
stops, the loss limit and the API budget are fixed by the bot and .env. Every failure (bad
key, network, refusal, odd reply) means hold, never a trade.

Learning goes through the journal, not through the model's weights (Claude cannot be
trained from here). On every decision the brain reads its own statistics, the playbook it
distilled and the lessons from its recent trades. After each closed trade it writes one
lesson (reflect), and every DISTILL_EVERY closed trades it rewrites the playbook (distill).
"""
import json
from collections import namedtuple
from datetime import datetime, timezone

import anthropic

import indicators
from config import candle_seconds

MODEL = "claude-sonnet-5-5"  # half the price of claude-opus-5-5; switch back here if the decisions get worse
EFFORT = "medium"  # low | medium | high: higher thinks longer and costs more per candle (Sonnet 5.5 defaults to high)
CANDLES_NEEDED = 300  # the higher-timeframe EMA (4 candles per bar) needs a few multiples of its period
CLOSES_SHOWN = 30
CANDLES_SHOWN = 10
READINGS_SHOWN = 3
HIGHER_FACTOR = 4  # higher-timeframe bars are this many candles long (M15 -> 60 minutes)
HIGHER_SHOWN = 8
PATH_SHOWN = 40  # closes of the price path shown when reflecting on a closed trade
DISTILL_EVERY = 10  # closed trades between playbook rewrites

# USD per million tokens for claude-sonnet-5-5 on the Claude API, October 2026. When the
# fallback serves another model the real bill differs a little: treat spend as an estimate.
PRICES = {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.20}

Decision = namedtuple("Decision", "signal reason cost_usd")

# Edit these to change how the AI trades and reviews itself.
DECIDE_SYSTEM = """You are the entry-decision module of an automated trading bot, and its only trader: no human reviews your calls.

Each time a candle closes you receive your own track record (statistics, the playbook you distilled from earlier trades, lessons from your recent trades) and the current market: recent candles, indicator readings and the higher-timeframe trend for one symbol. Decide whether the bot should open a new market position now: buy, sell, or hold.

Sometimes the bot's rules hand you candidate setups they found on this candle. Then your job is to judge them: take one of the proposed directions, or hold. A direction no setup proposed will not be traded.

Position size, stop loss, take profit, the daily loss limit and the API budget are fixed by the bot and are not your concern. The bot holds at most one position and there is none open when you are asked. Holding is always acceptable, and it is the right call when the picture is unclear or your record says this kind of setup loses.

Follow your playbook unless the lessons written since it contradict it. Give the reason in one short sentence; it is logged and sent to the owner's phone."""

REFLECT_SYSTEM = """You are the review module of an automated trading bot. A trade the bot opened on your decision has just closed. You get what you saw at entry, the reason you gave, how price moved afterwards and the result.

Write the one lesson worth remembering at the next similar setup: specific to this situation, one or two sentences. If the trade was simply the normal cost of a sound decision, say so in one sentence instead of inventing a lesson."""

DISTILL_SYSTEM = """You are the review module of an automated trading bot. Rewrite the bot's playbook from its current playbook, its statistics and the lessons since the last rewrite.

At most 8 numbered rules, one line each, each specific enough to act on at a candle close. Keep rules the record supports, drop or change rules it contradicts, add rules the lessons justify. Output only the numbered list."""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}
LESSON_SCHEMA = {
    "type": "object",
    "properties": {"lesson": {"type": "string"}},
    "required": ["lesson"],
    "additionalProperties": False,
}
PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {"playbook": {"type": "string"}},
    "required": ["playbook"],
    "additionalProperties": False,
}


def listed(values):
    return ", ".join(f"{value:.6g}" for value in values)


def snapshot(candles, config):
    """Text view of the market handed to the model. Deliberately carries no dates."""
    prices = [candle["close"] for candle in candles]
    macd_line, macd_signal, macd_histogram = indicators.macd(prices)
    lower_band, middle_band, upper_band = indicators.bollinger(prices)
    readings = {
        "sma_10": indicators.sma(prices, 10),
        "sma_30": indicators.sma(prices, 30),
        "ema_20": indicators.ema(prices, 20),
        "ema_50": indicators.ema(prices, 50),
        "rsi_14": indicators.rsi(prices),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "bollinger_lower": lower_band,
        "bollinger_middle": middle_band,
        "bollinger_upper": upper_band,
        f"atr_{config.get('ATR_PERIOD', 14)}": indicators.atr(candles, config.get("ATR_PERIOD", 14)),
    }
    last = candles[-1]
    when = datetime.fromtimestamp(int(last["time"]), timezone.utc)
    seconds = candle_seconds(config)
    higher = indicators.resample(candles, HIGHER_FACTOR, seconds)
    higher_ema = indicators.ema([bar["close"] for bar in higher], 20)
    if len(higher_ema) >= 4:
        trend = "up" if higher_ema[-1] > higher_ema[-4] else "down" if higher_ema[-1] < higher_ema[-4] else "flat"
    else:
        trend = "unknown"
    lines = [
        f"symbol: {config['SYMBOL']}",
        f"timeframe: {config['TIMEFRAME']}",
        f"last closed candle: {when:%A} {when:%H:%M} broker server time, spread {last['spread']} points",
        f"last {CLOSES_SHOWN} closes, oldest first: {listed(prices[-CLOSES_SHOWN:])}",
        f"last {CANDLES_SHOWN} candles as open/high/low/close, oldest first: " + "; ".join(
            f"{c['open']:.6g}/{c['high']:.6g}/{c['low']:.6g}/{c['close']:.6g}" for c in candles[-CANDLES_SHOWN:]
        ),
        f"indicators, last {READINGS_SHOWN} values each, oldest first:",
    ]
    lines += [f"{name}: {listed(values[-READINGS_SHOWN:])}" for name, values in readings.items()]
    lines.append(
        f"higher timeframe ({HIGHER_FACTOR * seconds // 60}-minute bars, newest may be unfinished):"
        f" last {HIGHER_SHOWN} closes {listed([bar['close'] for bar in higher[-HIGHER_SHOWN:]])};"
        f" ema_20 {listed(higher_ema[-READINGS_SHOWN:])}; trend {trend}"
    )
    return "\n".join(lines)


def cost_usd(usage):
    """Estimated USD cost of one response from its usage block (0 when unknown)."""
    def tokens(name):
        value = getattr(usage, name, 0)
        return value if isinstance(value, (int, float)) else 0

    if usage is None:
        return 0.0
    cost = (
        tokens("input_tokens") * PRICES["input"]
        + tokens("output_tokens") * PRICES["output"]
        + tokens("cache_creation_input_tokens") * PRICES["cache_write"]
        + tokens("cache_read_input_tokens") * PRICES["cache_read"]
    )
    return round(cost / 1_000_000, 6)


def ask(system, text, schema, config, effort):
    """One structured call to Claude. Returns (answer dict or None, problem or None, cost_usd).

    Every problem string starts with "AI error" so callers can tell a failed call from a hold.
    """
    try:
        client = anthropic.Anthropic(api_key=config["ANTHROPIC_API_KEY"] or None, timeout=120.0)
        # ponytail: blocking call, the bot ignores Telegram while Claude thinks;
        # move it to a thread if that ever hurts
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # if a safety classifier declines the request, Anthropic re-runs it on a fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.AuthenticationError:
        return None, "AI error: key rejected, check ANTHROPIC_API_KEY in .env", 0.0
    except anthropic.APIStatusError as error:
        return None, f"AI error: API status {error.status_code}", 0.0
    except Exception as error:  # a broken brain must hold: never crash the bot, never trade
        return None, f"AI error: call failed ({type(error).__name__})", 0.0
    cost = cost_usd(getattr(response, "usage", None))
    if response.stop_reason != "end_turn":
        return None, f"AI error: no answer ({response.stop_reason})", cost
    try:
        answer = json.loads(next(block.text for block in response.content if block.type == "text"))
        if any(key not in answer for key in schema["required"]):
            raise KeyError("incomplete answer")
    except Exception as error:
        return None, f"AI error: reply unreadable ({type(error).__name__})", cost
    return answer, None, cost


def decide(candles, config, experience="", candidates=None):
    """Decision(signal, reason, cost_usd): signal is "buy", "sell" or None for hold.

    candidates, when given, are the rules' (name, signal, reason) setups for this candle;
    the model may then only take one of their directions.
    """
    if len(candles) < CANDLES_NEEDED:
        return Decision(None, f"need {CANDLES_NEEDED} closed candles, got {len(candles)}", 0.0)
    parts = [experience] if experience else []
    if candidates:
        parts.append(
            "Setups the bot's rules found on this candle:\n"
            + "\n".join(f"- {name}: {signal} ({reason})" for name, signal, reason in candidates)
            + "\nTake one of these directions or hold. A direction no setup proposed counts as hold."
        )
    parts.append("Market now:\n" + snapshot(candles, config))
    answer, problem, cost = ask(DECIDE_SYSTEM, "\n\n".join(parts), DECISION_SCHEMA, config, EFFORT)
    if answer is None:
        return Decision(None, problem, cost)
    signal = answer["action"] if answer["action"] in ("buy", "sell") else None
    reason = "AI: " + str(answer["reason"]).strip()
    if signal and candidates and signal not in {proposed for _, proposed, _ in candidates}:
        return Decision(None, f"AI: wanted to {signal} without a setup, held instead; {reason[4:]}", cost)
    return Decision(signal, reason, cost)


def reflect(trade, path_closes, config):
    """(lesson or None, problem or None, cost_usd) for a closed trade dict from the journal."""
    held = max(1, round((trade["closed_at"] - trade["opened_at"]) / candle_seconds(config)))
    ended_by = {"sl": "the stop loss", "tp": "the take profit"}.get(trade["outcome"], "the broker or the owner")
    lines = [
        f"Trade: {trade['side']} {trade['lot']} {trade['symbol']} at {trade['entry']},"
        f" stop {trade['sl']}, target {trade['tp']}.",
        f"Your reason at entry: {trade.get('reason') or 'not recorded'}",
    ]
    if trade.get("snapshot"):
        lines += ["What you saw at entry:", trade["snapshot"]]
    if path_closes:
        lines.append(f"Closes after entry, oldest first: {listed(path_closes[-PATH_SHOWN:])}")
    lines.append(
        f"Result: closed at {trade['exit']} after {held} candles by {ended_by}, profit {trade['profit']:+.2f}."
    )
    answer, problem, cost = ask(REFLECT_SYSTEM, "\n".join(lines), LESSON_SCHEMA, config, "low")
    return (str(answer["lesson"]).strip() if answer else None), problem, cost


def distill(experience, config):
    """(new playbook or None, problem or None, cost_usd). experience is journal.experience_text()."""
    text = experience + "\n\nRewrite the playbook now."
    answer, problem, cost = ask(DISTILL_SYSTEM, text, PLAYBOOK_SCHEMA, config, EFFORT)
    return (str(answer["playbook"]).strip() if answer else None), problem, cost


def review(journal, trade, path_closes, config, source, run, spent=0.0):
    """Learn from a closed trade: store a lesson, rewrite the playbook every DISTILL_EVERY trades.

    Returns (lesson or None, new playbook or None). Skips everything once `spent`
    (USD already spent in this budget window) reaches AI_BUDGET_USD.
    """
    if spent >= config["AI_BUDGET_USD"]:
        return None, None
    lesson, problem, cost = reflect(trade, path_closes, config)
    journal.record_decision(source, run, "reflect", trade["closed_at"], reason=problem, cost_usd=cost)
    if lesson:
        journal.add_lesson(trade["id"], lesson)
    closed_count = len(journal.learned_trades())
    playbook = journal.playbook()
    if closed_count - (playbook["trades_seen"] if playbook else 0) < DISTILL_EVERY:
        return lesson, None
    text, problem, cost = distill(journal.experience_text(lessons=DISTILL_EVERY), config)
    journal.record_decision(source, run, "distill", trade["closed_at"], reason=problem, cost_usd=cost)
    if text:
        journal.save_playbook(text, closed_count)
    return lesson, text
