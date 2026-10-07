"""High-impact news blackout: no new trades just before and after big economic releases.

The week's calendar comes from the ForexFactory feed that FairEconomy publishes (unofficial, free,
this week only), fetched at most once an hour. A market is touched by an event when the event's
currency is one of the market's two (MT5's currency_base and currency_profit: GOLD is XAU / USD).
If the feed cannot be read, nothing is blocked and the bot says so once: a dead feed must not stop
trading, but the owner should know the filter is off.
"""
import json
import time
import urllib.request
from datetime import datetime

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
REFRESH_SECONDS = 3600
cache = {"events": [], "fetched": 0.0, "problem": None}


def parse(feed):
    """[(unix time, currency, title)] of the high-impact events in the feed's JSON."""
    events = []
    for item in feed:
        if item.get("impact") != "High":
            continue
        try:
            at = datetime.fromisoformat(item["date"]).timestamp()
        except (KeyError, ValueError):
            continue
        events.append((at, item.get("country", ""), item.get("title", "")))
    return sorted(events)


def events(now=None, opener=urllib.request.urlopen):
    """This week's high-impact events, from the cache while it is under an hour old."""
    now = time.time() if now is None else now
    if now - cache["fetched"] >= REFRESH_SECONDS:
        cache["fetched"] = now  # also after a failure: retry in an hour, not on every loop
        try:
            request = urllib.request.Request(FEED, headers={"User-Agent": "TradeBot"})
            with opener(request, timeout=15) as response:
                cache.update(events=parse(json.load(response)), problem=None)
        except Exception as error:  # offline, feed moved: keep the last list, say why
            cache["problem"] = f"news calendar unavailable ({error.__class__.__name__}): the news pause is off"
    return cache["events"]


def blackout(currencies, minutes, now=None, opener=urllib.request.urlopen):
    """Why a market with these currencies must not open a trade now, or None."""
    wanted = {currency.upper() for currency in currencies if currency}
    if not minutes or not wanted:
        return None  # off, or a market whose currencies MT5 did not say: no feed read for nothing
    now = time.time() if now is None else now
    for at, currency, title in events(now, opener):
        if currency.upper() in wanted and abs(at - now) <= minutes * 60:
            when = f"in {round((at - now) / 60)} min" if at > now else f"{round((now - at) / 60)} min ago"
            return f"news: {currency} {title} {when}"
    return None
