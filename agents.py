"""User-made agents: a strategy in plain English, the markets it watches, and what it may do.

Kept in agents.json next to the app; the bot re-reads it every loop, so a change applies at
once. Each agent decides with the built-in analyst (free: a template's own code, or plainrules.py
reading the written rules) or an AI (Claude, GPT, Gemini), and then:
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
import plainrules
import strategies
from config import APP_DIR

AGENTS_PATH = APP_DIR / "agents.json"
BRAKE_LOSSES = 4  # default: an Auto-trade agent goes back to Watch only after this many losses in a row
MODES = ("watch", "suggest", "auto")
RUNS = ("candle", "timer")
ANALYSTS = ("rules", "saved", "claude", "openai", "gemini")  # saved = the AI order from Settings
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
MAX_MARKETS = 20  # every market is a separate MT5 read (and an AI call) each time the agent runs

AGENT_SYSTEM = """You are a trading agent inside an automated trading bot. The owner wrote your strategy in plain words; follow it exactly and do not add ideas of your own.

Each time you run you get the strategy, and for one market: recent candles, indicator readings and the higher-timeframe trend. Decide buy, sell or hold for a new market position now, how confident you are (0 to 100) that the strategy's conditions are met, and why, in one short sentence.

Position size, stop loss, take profit and daily limits are the bot's, not yours. Hold whenever the strategy's conditions are not clearly met.

You may also get your own track record and the lessons you drew from your closed trades. Use them to judge how sure you are and to avoid repeating a mistake, but the owner's strategy always comes first: a lesson never makes you trade against it."""

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
    if not text and template in strategies.PLAIN:
        text = strategies.PLAIN[template][2]  # a template left blank means its own words
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
        "brake_losses": int(raw.get("brake_losses", BRAKE_LOSSES) or 0),
    }
    problems = []
    if not name:
        problems.append("give it a name")
    if template and template not in strategies.PLAIN:
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
    if not 0 <= agent["brake_losses"] <= 20:
        problems.append("pause after 0 (never) to 20 losses in a row")
    if not 0 <= agent["min_confidence"] <= 100:
        problems.append("minimum confidence is 0 to 100")
    if agent["mode"] not in MODES:
        problems.append("what it may do: watch, suggest or auto")
    if agent["analyst"] not in ANALYSTS:
        problems.append("pick an analyst")
    if agent["analyst"] == "rules" and not runs_template(agent):
        rules = plainrules.parse(text)
        if not any(rules[side] and not rules["unknown"][side] for side in ("buy", "sell")):
            problems.append("the built-in analyst cannot read a whole buy or sell rule here"
                            + (f" (not understood: {'; '.join(sorted(set(rules['unknown']['buy'] + rules['unknown']['sell'])))})"
                               if rules["unknown"]["buy"] or rules["unknown"]["sell"] else "")
                            + ": rewrite it, or pick an AI analyst")
    if agent["analyst"] == "rules" and agent["runs"] == "timer":
        problems.append("rules run on closed candles only (a timer would repeat the same call)")
    if problems:
        raise ValueError("; ".join(problems))
    return agent


def runs_template(agent):
    """A template whose rule text is unchanged runs the template's own code, exactly; edited text is read instead."""
    template = agent.get("template")
    # written-rules templates have no code: the built-in analyst reads their words, edited or not
    return template in strategies.STRATEGIES and agent.get("strategy", "").strip() in ("", strategies.PLAIN[template][2])


def migrate(values, path=None):
    """Once: the strategies the old Strategies page armed (STRATEGY in .env) become auto-trading agents
    on the bot's market, added to any agents already made, so nothing that traded stops. values is
    None on a fresh install (no .env yet): nothing to move. The bot itself no longer trades STRATEGY."""
    path = path or AGENTS_PATH
    marker = path.with_name("strategies.moved")
    if marker.exists():
        return []
    made = [clean({
        "name": strategies.PLAIN[name][0], "template": name, "strategy": strategies.PLAIN[name][2],
        "markets": [values["SYMBOL"]], "timeframe": values["TIMEFRAME"], "mode": "auto",
        "analyst": "rules" if values.get("BRAIN", "rules") == "rules" else "saved",  # ai / hybrid: the AI judges the setups
    }) for name in (strategies.selected(values) if values else []) if name in strategies.STRATEGIES]
    if made:
        store(load(path) + made, path)
    marker.write_text("STRATEGY moved into agents.json\n", encoding="utf-8")
    return made


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


def ai_config(agent, config):
    """The bot's config with this agent's AI: "saved" uses the order in Settings."""
    return config if agent["analyst"] == "saved" else dict(config, AI_PROVIDER=agent["analyst"])


def after_trade(agent, trade, journal, config, path_closes, run, spent=0.0, store_path=None):
    """When one of the agent's trades closes: an AI agent writes a lesson it reads before its next calls,
    and an agent on Auto-trade that just lost brake_losses times in a row is switched to Watch only.
    Returns lines for the owner's notice."""
    source, notes = "agent:" + agent["id"], []
    if agent["analyst"] != "rules" and spent < config["AI_BUDGET_USD"]:
        lesson, problem, cost = ai_strategy.reflect(trade, path_closes, ai_config(agent, config))
        journal.record_decision(source, run, "reflect", trade["closed_at"], reason=problem, cost_usd=cost)
        if lesson:
            journal.add_lesson(trade["id"], lesson)
            notes.append(f"lesson: {lesson}")
    streak = 0
    for past in reversed(journal.learned_trades((source,))):
        if past["profit"] >= 0:
            break
        streak += 1
    if agent["mode"] == "auto" and agent["brake_losses"] and streak >= agent["brake_losses"]:
        # ponytail: the bot rewrites agents.json while the app may save it too; a save in that same
        # instant can undo the pause. A shared lock file if that ever bites.
        store([dict(a, mode="watch") if a["id"] == agent["id"] else a for a in load(store_path)], store_path)
        why = f"lost {streak} trades in a row: switched to Watch only. Look at its record, then turn Auto-trade back on if you still trust it"
        journal.record_decision(source, run, "agent", trade["closed_at"], "pause", f"{trade['symbol']} · 0% · {why}")
        notes.append(f"Agent {agent['name']} {why}.")
    return notes


def decide(agent, candles, config, journal_spent=0.0, experience=""):
    """(signal or None, confidence, reason, cost_usd) for one market's closed candles.

    experience: the agent's own track record and lessons (journal.experience_text), for an AI analyst.

    The enforced numbers are checked here, after the analyst: an RSI outside the strategy's range or
    a confidence under the minimum turns any call into a hold.
    """
    candidates = (strategies.candidates(candles, dict(config, STRATEGY=agent["template"]))
                  if agent["template"] in strategies.STRATEGIES else [])
    if agent["analyst"] == "rules" and runs_template(agent):
        if not candidates:
            return None, 0, "no setup", 0.0
        name, signal, why = candidates[0]
        signal, confidence, reason, cost = signal, 100, f"{name}: {why}", 0.0
    elif agent["analyst"] == "rules":
        signal, why = plainrules.decide(agent["strategy"], candles)
        if not signal:
            return None, 0, why, 0.0
        confidence, reason, cost = 100, "rules: " + why, 0.0
    else:
        if journal_spent >= config["AI_BUDGET_USD"]:
            return None, 0, f"AI budget for today spent (${journal_spent:.2f})", 0.0
        parts = [f"Your strategy, in the owner's words:\n{agent['strategy'] or 'Trade the setups below.'}"]
        if experience:
            parts.append(experience)
        if candidates:
            parts.append("Setups the strategy's template found on this candle:\n"
                         + "\n".join(f"- {name}: {signal} ({why})" for name, signal, why in candidates))
        parts.append("Market now:\n" + ai_strategy.snapshot(candles, config))
        answer, problem, cost = ai_strategy.ask(AGENT_SYSTEM, "\n\n".join(parts), AGENT_SCHEMA, ai_config(agent, config),
                                                ai_strategy.EFFORT)
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
