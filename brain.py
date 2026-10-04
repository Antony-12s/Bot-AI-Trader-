"""Which brain decides, and how.

rules  = strategies.py trades its setups on its own, free.
ai     = ai_strategy.py (Claude) decides every candle from the market and its own record.
hybrid = strategies.py proposes setups, Claude takes one or holds; candles without a setup
         cost nothing, and a direction no setup proposed is never traded.
"""
import ai_strategy
import strategies

LEARNING = ("ai", "hybrid")


def learns(config):
    """Does this brain keep a track record and reflect on its trades?"""
    return config["BRAIN"] in LEARNING


def candles_needed(config):
    return ai_strategy.CANDLES_NEEDED if learns(config) else strategies.CANDLES_NEEDED


def decide(candles, config, experience=""):
    """(signal, reason, cost_usd) for the latest closed candle."""
    if config["BRAIN"] == "rules":
        signal, reason = strategies.decide(candles, config)
        return signal, reason, 0.0
    if config["BRAIN"] == "ai":
        return ai_strategy.decide(candles, config, experience)
    found = strategies.candidates(candles, config)
    if not found:
        return None, "no setup for the AI to judge", 0.0
    return ai_strategy.decide(candles, config, experience, candidates=found)
