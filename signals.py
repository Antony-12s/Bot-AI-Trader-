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


def new_topic():
    return "tradebot-" + secrets.token_urlsafe(18)


def webhook_url(topic):
    return f"{NTFY}/{topic}"


def parse(text):
    """("buy" | "sell", None) for a tradeable message, or (None, why it was skipped).

    Accepts plain text ("buy", "SELL XAUUSD") and TradingView JSON ({"action": "buy"}).
    """
    sides = {SIDES[word] for word in re.findall(r"[a-z]+", text.lower()) if word in SIDES}
    if len(sides) == 1:
        return sides.pop(), None
    shown = text.strip()[:60]
    return None, f"both buy and sell in {shown!r}" if sides else f"no buy or sell in {shown!r}"


def poll(topic, since, opener=urllib.request.urlopen):
    """(new message texts, the cursor to pass next time). since: a message id or a unix time."""
    with opener(f"{NTFY}/{topic}/json?poll=1&since={since}", timeout=10) as response:
        events = [json.loads(line) for line in response.read().decode("utf-8").splitlines() if line.strip()]
    messages = [event for event in events if event.get("event") == "message"]
    return [message.get("message", "") for message in messages], (messages[-1]["id"] if messages else since)
