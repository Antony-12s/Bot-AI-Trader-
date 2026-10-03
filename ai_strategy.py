"""AI brain: asks Claude for buy / sell / hold once per closed candle.

Enabled with BRAIN=ai in .env. The model only picks the direction: lot size,
stop loss, take profit and the loss limits stay hard-coded in bot.py.
Every failure (bad key, network, refusal, odd reply) means hold, never a trade.
"""
import json

import anthropic

import indicators

MODEL = "claude-opus-5-5"
EFFORT = "medium"  # low | medium | high: higher thinks longer and costs more per candle
CANDLES_NEEDED = 150  # ema / macd need a few multiples of their period to settle
CLOSES_SHOWN = 30
READINGS_SHOWN = 3

# Edit this to change how the AI trades.
SYSTEM_PROMPT = """You are the entry-decision module of an automated trading bot.

Each time a candle closes you receive recent close prices and indicator readings
for one symbol. Decide whether the bot should open a new market position now:
buy, sell, or hold.

Position size, stop loss, take profit and the daily loss limit are fixed by the
bot and are not your concern. The bot holds at most one position and there is
none open when you are asked. Holding is always acceptable, and it is the right
call when the picture is unclear.

Give the reason in one short sentence. It is sent to the owner's phone."""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}


def snapshot(closes, config):
    """Text view of the market handed to the model."""
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

    def listed(values):
        return ", ".join(f"{value:.6g}" for value in values)

    lines = [
        f"symbol: {config['SYMBOL']}",
        f"timeframe: {config['TIMEFRAME']}",
        f"last {CLOSES_SHOWN} closes, oldest first: {listed(closes[-CLOSES_SHOWN:])}",
        f"indicators, last {READINGS_SHOWN} values each, oldest first:",
    ]
    lines += [f"{name}: {listed(values[-READINGS_SHOWN:])}" for name, values in readings.items()]
    return "\n".join(lines)


def decide(closes, config):
    """Return (signal, reason): signal is "buy", "sell" or None for hold."""
    if len(closes) < CANDLES_NEEDED:
        return None, f"need {CANDLES_NEEDED} closed candles, got {len(closes)}"
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
            system=SYSTEM_PROMPT,
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": DECISION_SCHEMA},
            },
            messages=[{"role": "user", "content": snapshot(closes, config)}],
        )
        if response.stop_reason != "end_turn":
            return None, f"AI gave no decision ({response.stop_reason})"
        decision = json.loads(next(block.text for block in response.content if block.type == "text"))
        action, reason = decision["action"], decision["reason"]
    except anthropic.AuthenticationError:
        return None, "AI key rejected, check ANTHROPIC_API_KEY in .env"
    except anthropic.APIStatusError as error:
        return None, f"AI API error {error.status_code}"
    except Exception as error:  # a broken brain must hold: never crash the bot, never trade
        return None, f"AI call failed ({type(error).__name__})"
    return (action if action in ("buy", "sell") else None), "AI: " + reason
