"""AI brain: asks Claude for buy / sell / hold once per closed candle, and learns from the result.

Enabled with BRAIN=ai in .env. The model only picks the direction: lot size, stop loss,
take profit, the loss limit and the API budget are fixed by the bot and .env. Every failure
(bad key, network, refusal, odd reply) means hold, never a trade.

Learning goes through the journal, not through the model's weights (Claude cannot be
trained from here). On every decision the brain reads its own statistics, the playbook it
distilled and the lessons from its recent trades. After each closed trade it writes one
lesson (reflect), and every DISTILL_EVERY closed trades it rewrites the playbook (distill).
"""
import json
from collections import namedtuple

import anthropic

import indicators
from config import candle_seconds

MODEL = "claude-opus-5-5"
EFFORT = "medium"  # low | medium | high: higher thinks longer and costs more per candle
CANDLES_NEEDED = 150  # ema / macd need a few multiples of their period to settle
CLOSES_SHOWN = 30
READINGS_SHOWN = 3
PATH_SHOWN = 40  # closes of the price path shown when reflecting on a closed trade
DISTILL_EVERY = 10  # closed trades between playbook rewrites

# USD per million tokens for claude-opus-5-5 on the Claude API, October 2026. When the
# fallback serves another model the real bill differs a little: treat spend as an estimate.
PRICES = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20}

Decision = namedtuple("Decision", "signal reason cost_usd")

# Edit these to change how the AI trades and reviews itself.
DECIDE_SYSTEM = """You are the entry-decision module of an automated trading bot, and its only trader: no human reviews your calls.

Each time a candle closes you receive your own track record (statistics, the playbook you distilled from earlier trades, lessons from your recent trades) and the current market: recent close prices and indicator readings for one symbol. Decide whether the bot should open a new market position now: buy, sell, or hold.

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


def snapshot(closes, config):
    """Text view of the market handed to the model. Deliberately carries no dates."""
    macd_line, macd_signal, macd_histogram = indicators.macd(closes)
    lower_band, middle_band, upper_band = indicators.bollinger(closes)
    readings = {
        "sma_10": indicators.sma(closes, 10),
        "sma_30": indicators.sma(closes, 30),
        "ema_50": indicators.ema(closes, 50),
        "rsi_14": indicators.rsi(closes),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "bollinger_lower": lower_band,
        "bollinger_middle": middle_band,
        "bollinger_upper": upper_band,
    }
    lines = [
        f"symbol: {config['SYMBOL']}",
        f"timeframe: {config['TIMEFRAME']}",
        f"last {CLOSES_SHOWN} closes, oldest first: {listed(closes[-CLOSES_SHOWN:])}",
        f"indicators, last {READINGS_SHOWN} values each, oldest first:",
    ]
    lines += [f"{name}: {listed(values[-READINGS_SHOWN:])}" for name, values in readings.items()]
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
    """One structured call to Claude. Returns (answer dict or None, problem or None, cost_usd)."""
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
        return None, "AI key rejected, check ANTHROPIC_API_KEY in .env", 0.0
    except anthropic.APIStatusError as error:
        return None, f"AI API error {error.status_code}", 0.0
    except Exception as error:  # a broken brain must hold: never crash the bot, never trade
        return None, f"AI call failed ({type(error).__name__})", 0.0
    cost = cost_usd(getattr(response, "usage", None))
    if response.stop_reason != "end_turn":
        return None, f"AI gave no answer ({response.stop_reason})", cost
    try:
        answer = json.loads(next(block.text for block in response.content if block.type == "text"))
        if any(key not in answer for key in schema["required"]):
            raise KeyError("incomplete answer")
    except Exception as error:
        return None, f"AI reply unreadable ({type(error).__name__})", cost
    return answer, None, cost


def decide(closes, config, experience=""):
    """Decision(signal, reason, cost_usd): signal is "buy", "sell" or None for hold."""
    if len(closes) < CANDLES_NEEDED:
        return Decision(None, f"need {CANDLES_NEEDED} closed candles, got {len(closes)}", 0.0)
    text = (experience + "\n\n" if experience else "") + "Market now:\n" + snapshot(closes, config)
    answer, problem, cost = ask(DECIDE_SYSTEM, text, DECISION_SCHEMA, config, EFFORT)
    if answer is None:
        return Decision(None, problem, cost)
    signal = answer["action"] if answer["action"] in ("buy", "sell") else None
    return Decision(signal, "AI: " + str(answer["reason"]).strip(), cost)


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
    closed_count = len(journal.closed_trades())
    playbook = journal.playbook()
    if closed_count - (playbook["trades_seen"] if playbook else 0) < DISTILL_EVERY:
        return lesson, None
    text, problem, cost = distill(journal.experience_text(lessons=DISTILL_EVERY), config)
    journal.record_decision(source, run, "distill", trade["closed_at"], reason=problem, cost_usd=cost)
    if text:
        journal.save_playbook(text, closed_count)
    return lesson, text
