"""User-made agents: a strategy in plain English, the markets it watches, and what it may do.

Kept in agents.json next to the app; the bot re-reads it every loop, so a change applies at
once. Each agent decides with a rules template (free) or an AI (Claude, GPT, Gemini), and then:
  watch   - records its calls, never trades
  suggest - records and announces them (Telegram, the app), never trades
  auto    - trades them, through exactly the same risk rules as everything else
Numbers it can enforce are enforced: an RSI range written in the strategy, and the minimum
confidence. Lot size, stop loss and take profit stay the bot's own (Settings), whatever the text says.
"""
import json
import re
import time
import uuid

import ai_strategy
import indicators
import strategies
from config import APP_DIR

AGENTS_PATH = APP_DIR / "agents.json"
MODES = ("watch", "suggest", "auto")
RUNS = ("candle", "timer")
ANALYSTS = ("rules", "saved", "claude", "openai", "gemini")  # saved = the AI order from Settings
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
MAX_MARKETS = 20  # every market is a separate MT5 read (and an AI call) each time the agent runs

AGENT_SYSTEM = """You are a trading agent inside an automated trading bot. The owner wrote your strategy in plain words; follow it exactly and do not add ideas of your own.

Each time you run you get the strategy, and for one market: recent candles, indicator readings and the higher-timeframe trend. Decide buy, sell or hold for a new market position now, how confident you are (0 to 100) that the strategy's conditions are met, and why, in one short sentence.

Position size, stop loss, take profit and daily limits are the bot's, not yours. Hold whenever the strategy's conditions are not clearly met."""

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "confidence": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["action", "confidence", "reason"],
    "additionalProperties": False,
}


def load(path=None):
    path = path or AGENTS_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def store(agents, path=None):
    path = path or AGENTS_PATH
    part = path.with_suffix(".json.part")
    part.write_text(json.dumps(agents, indent=2), encoding="utf-8")
    part.replace(path)  # a bot reading mid-write never sees half a file


def clean(raw):
    """A validated agent dict from the wizard's form. Raises ValueError with what to fix."""
    if not isinstance(raw, dict):
        raise ValueError("expected an agent")
    name = str(raw.get("name", "")).strip()[:60]
    template = raw.get("template") or ""
    text = str(raw.get("strategy", "")).strip()[:2000]
    markets = [str(m).strip() for m in raw.get("markets", []) if str(m).strip()]
    agent = {
        "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
        "name": name,
        "template": template,
        "strategy": text,
        "markets": list(dict.fromkeys(markets)),  # keep order, drop repeats
        "timeframe": raw.get("timeframe", "M15"),
        "runs": raw.get("runs", "candle"),
        "every_minutes": int(raw.get("every_minutes") or 15),
        "min_confidence": int(raw.get("min_confidence") or 0),
        "mode": raw.get("mode", "watch"),
        "analyst": raw.get("analyst", "rules" if template else "saved"),
        "created_at": int(raw.get("created_at") or time.time()),
    }
    problems = []
    if not name:
        problems.append("give it a name")
    if template and template not in strategies.STRATEGIES:
        problems.append(f"unknown template {template}")
    if not template and not text:
        problems.append("describe the strategy")
    if not agent["markets"]:
        problems.append("pick at least one market")
    if len(agent["markets"]) > MAX_MARKETS:
        problems.append(f"at most {MAX_MARKETS} markets")
    if agent["timeframe"] not in TIMEFRAMES:
        problems.append("pick a timeframe")
    if agent["runs"] not in RUNS or not 1 <= agent["every_minutes"] <= 1440:
        problems.append("runs: every closed candle, or every 1 to 1440 minutes")
    if not 0 <= agent["min_confidence"] <= 100:
        problems.append("minimum confidence is 0 to 100")
    if agent["mode"] not in MODES:
        problems.append("what it may do: watch, suggest or auto")
    if agent["analyst"] not in ANALYSTS:
        problems.append("pick an analyst")
    if agent["analyst"] == "rules" and not template:
        problems.append("a blank strategy needs an AI analyst: rules only run a template")
    if agent["analyst"] == "rules" and agent["runs"] == "timer":
        problems.append("rules run on closed candles only (a timer would repeat the same call)")
    if problems:
        raise ValueError("; ".join(problems))
    return agent


def rsi_range(text):
    """(low, high) when the text says "RSI between A and B" (or "RSI is 45-65"); else None."""
    match = re.search(r"rsi[^.\d]{0,20}?(\d+(?:\.\d+)?)\s*(?:and|to|-|–)\s*(\d+(?:\.\d+)?)", text.lower())
    if not match:
        return None
    low, high = sorted((float(match.group(1)), float(match.group(2))))
    return (low, high) if 0 <= low < high <= 100 else None


def rsi_ranges(text):
    """{"buy": range or None, "sell": range or None}, sentence by sentence.

    "Buy when ... RSI between 40 and 65. Sell on the mirror image." limits buys only: a range in a
    sentence about buying (or selling) binds that side; a sentence naming neither binds both.
    """
    ranges = {"buy": None, "sell": None}
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", text):
        band = rsi_range(sentence)
        if not band:
            continue
        words = set(re.findall(r"[a-z]+", sentence.lower()))
        sides = [side for side, names in (("buy", {"buy", "buys", "long", "longs"}), ("sell", {"sell", "sells", "short", "shorts"}))
                 if words & names] or ["buy", "sell"]
        for side in sides:
            ranges[side] = ranges[side] or band
    return ranges


def decide(agent, candles, config, journal_spent=0.0):
    """(signal or None, confidence, reason, cost_usd) for one market's closed candles.

    The enforced numbers are checked here, after the analyst: an RSI outside the strategy's range or
    a confidence under the minimum turns any call into a hold.
    """
    candidates = strategies.candidates(candles, dict(config, STRATEGY=agent["template"])) if agent["template"] else []
    if agent["analyst"] == "rules":
        if not candidates:
            return None, 0, "no setup", 0.0
        name, signal, why = candidates[0]
        signal, confidence, reason, cost = signal, 100, f"{name}: {why}", 0.0
    else:
        if journal_spent >= config["AI_BUDGET_USD"]:
            return None, 0, f"AI budget for today spent (${journal_spent:.2f})", 0.0
        ai_config = config if agent["analyst"] == "saved" else dict(config, AI_PROVIDER=agent["analyst"])
        parts = [f"Your strategy, in the owner's words:\n{agent['strategy'] or 'Trade the setups below.'}"]
        if candidates:
            parts.append("Setups the strategy's template found on this candle:\n"
                         + "\n".join(f"- {name}: {signal} ({why})" for name, signal, why in candidates))
        parts.append("Market now:\n" + ai_strategy.snapshot(candles, config))
        answer, problem, cost = ai_strategy.ask(AGENT_SYSTEM, "\n\n".join(parts), AGENT_SCHEMA, ai_config, ai_strategy.EFFORT)
        if answer is None:
            return None, 0, problem, cost
        signal = answer["action"] if answer["action"] in ("buy", "sell") else None
        confidence = max(0, min(100, int(answer["confidence"])))
        reason = "AI: " + str(answer["reason"]).strip()
    if signal and confidence < agent["min_confidence"]:
        return None, confidence, f"{reason} (held: confidence {confidence} under {agent['min_confidence']})", cost
    band = rsi_ranges(agent["strategy"])[signal] if signal else None
    if band:
        rsi = indicators.rsi([candle["close"] for candle in candles])
        if rsi and not band[0] <= rsi[-1] <= band[1]:
            return None, confidence, f"{reason} (held: RSI {rsi[-1]:.0f} outside your {band[0]:g}-{band[1]:g})", cost
    return signal, confidence, reason, cost
