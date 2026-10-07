"""Buy / sell signals from outside the bot: TradingView alerts and Telegram /buy /sell.

TradingView can only post webhooks to a public URL, so alerts go to a secret ntfy.sh topic
(free, no account) and the bot polls it. The topic name is the secret: anyone who knows it
can send signals, so it is long and random. Signals only say buy or sell; the bot's own
risk rules, lot and stops still apply to every one of them.
"""
import json
import re
import secrets
import urllib.request

NTFY = "https://ntfy.sh"
SIDES = {"buy": "buy", "long": "buy", "sell": "sell", "short": "sell"}
EXITS = {"close", "exit", "flat", "cover", "tp", "sl"}  # "close long" must not open a short: the bot's own stops exit
MAX_AGE_SECONDS = 60  # a signal older than this (MT5 was down, the relay lagged) is skipped, never traded late


def new_topic():
    return "tradebot-" + secrets.token_urlsafe(18)


def webhook_url(topic):
    return f"{NTFY}/{topic}"


def parse(text):
    """("buy" | "sell", None) for a tradeable message, or (None, why it was skipped).

    Accepts plain text ("buy", "SELL XAUUSD") and TradingView JSON ({"action": "buy"}).
    Exit wording ("close long", "exit") is skipped: positions close on the bot's own SL/TP.
    """
    words = set(re.findall(r"[a-z]+", text.lower()))
    shown = text.strip()[:60]
    if words & EXITS:
        return None, f"exit signal, not traded: {shown!r}"
    sides = {SIDES[word] for word in words if word in SIDES}
    if len(sides) == 1:
        return sides.pop(), None
    return None, f"both buy and sell in {shown!r}" if sides else f"no buy or sell in {shown!r}"


def too_old(sent_at, now):
    """Why a signal sent at sent_at (unix time) is too old to trade at now, or None."""
    age = int(now - sent_at)
    return f"arrived {age} s after it was sent, too late to trade" if age > MAX_AGE_SECONDS else None


def poll(topic, since, opener=urllib.request.urlopen):
    """([(text, sent_at unix time)], the cursor to pass next time). since: a message id or a unix time."""
    with opener(f"{NTFY}/{topic}/json?poll=1&since={since}", timeout=10) as response:
        events = [json.loads(line) for line in response.read().decode("utf-8").splitlines() if line.strip()]
    messages = [event for event in events if event.get("event") == "message"]
    return [(message.get("message", ""), message.get("time", 0)) for message in messages], (messages[-1]["id"] if messages else since)
