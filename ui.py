"""Desktop dashboard: python ui.py opens the TradeBot window (--browser: a browser tab instead).

View of journal.db (today, win rate, equity curve, decisions, trade history), a form that
rewrites .env, and Start / Stop for bot.py. Listens on 127.0.0.1 only.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import ALIVE, APP_DIR, DEFAULTS, ENV_PATH, JOURNAL_PATH, STOP_FLAG, load_config
from journal import Journal, summarize
from risk import SECONDS_PER_DAY, day_start

PORT = int(os.environ.get("TRADEBOT_PORT", "8765"))  # change it if another program already uses 8765
PAGE = Path(__file__).with_name("ui.html")
TEMPLATE_PATH = ENV_PATH.with_name(".env.example")
LOG_PATH = APP_DIR / "bot.log"
SECRETS = ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN")  # never sent to the page, only "set" or not
# ponytail: one loop of the bot (an AI call included) must finish within this, or it shows as stopped
ALIVE_SECONDS = 120
SAVE_LOCK = threading.RLock()  # re-entrant: arm() and switch_source() hold it around save_settings()
BOT_LOCK = threading.Lock()
bot_process = None  # the bot this dashboard started, if any


def bot_running(now=None):
    """A bot started here that has not exited, or any bot (start.bat, watchdog) touching bot.alive."""
    now = time.time() if now is None else now
    if bot_process is not None and bot_process.poll() is None:
        return True
    try:
        return now - ALIVE.stat().st_mtime < ALIVE_SECONDS
    except FileNotFoundError:
        return False


def start_bot():
    global bot_process
    with BOT_LOCK:  # two quick clicks must not start two bots trading the same account
        if bot_running():
            raise ValueError("the bot is already running")
        STOP_FLAG.unlink(missing_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as log:
            # sys.executable is python, or TradeBot.exe which runs bot.py the same way (launcher.py)
            bot_process = subprocess.Popen(
                [sys.executable, "bot.py"], cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )


def stop_bot():
    """Ask any running bot to finish its loop and exit cleanly; open positions keep their SL/TP."""
    STOP_FLAG.touch()


def bot_status(lines=40):
    try:
        log = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except FileNotFoundError:
        log = []
    return {"running": bot_running(), "stopping": STOP_FLAG.exists() and bot_running(), "log": log,
            "configured": ENV_PATH.exists()}  # the Setup page's "settings saved" step


def history(journal, limit=200):
    """Newest trades first, open ones included, with the reason and the lesson the AI wrote."""
    return journal._rows(
        "SELECT id, source, symbol, side, lot, entry, exit, opened_at, closed_at, profit, outcome,"
        " brain, reason, lesson FROM trades ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC LIMIT ?",
        (limit,),
    )


def dashboard(journal, days, now=None):
    now = time.time() if now is None else now
    since = now - days * SECONDS_PER_DAY
    trades = [trade for trade in journal.closed_trades() if trade["closed_at"] >= since]
    totals = summarize(trades)
    running, equity = 0.0, []
    for trade in sorted(trades, key=lambda trade: (trade["closed_at"], trade["id"])):
        running += trade["profit"]
        equity.append([trade["closed_at"], round(running, 2)])
    # ponytail: "today" is the UTC day of this PC's clock, the bot uses broker server time; pass server time if they drift apart
    today = journal.profit_since(day_start(int(now)))
    decisions = journal._rows(
        "SELECT at, action, reason, source FROM decisions WHERE kind = 'decide' ORDER BY id DESC LIMIT 20"
    )
    start = day_start(int(now))
    return {
        "trades_today": journal.trades_opened_since(start, "paper") + journal.trades_opened_since(start, "mt5"),
        "today": round(today, 2),
        "open": len(journal.open_trades()),
        "totals": totals,
        "equity": equity,
        "decisions": decisions,
        "ai_spend": round(journal.spend(), 2),
    }


TRUST_TRADES = 100  # closed trades before a record is worth judging, the Trust Ladder's top


def agents(journal, config):
    """One card per strategy: armed or not, its record, and its latest decisions.

    Trades are matched by the "name: reason" text the rules brain writes; the AI brains
    word their own reasons, so their trades show up under no strategy.
    """
    from strategies import STRATEGIES, selected  # here: strategies imports config, keep ui light at import
    armed = set(selected(config))
    trades = journal.closed_trades()
    decisions = journal._rows("SELECT at, action, reason, source FROM decisions WHERE kind = 'decide' ORDER BY id DESC LIMIT 500")
    cards = []
    for name, function in STRATEGIES.items():
        mine = [trade for trade in trades if (trade["reason"] or "").startswith(name + ":")]
        live = [trade for trade in mine if trade["source"] != "replay"]
        cards.append({
            "name": name,
            "about": (function.__doc__ or "").strip().splitlines()[0] if function.__doc__ else "",
            "armed": name in armed,
            "totals": summarize(mine),
            "live_trades": len(live),
            "trust": min(1.0, len(mine) / TRUST_TRADES),
            "decisions": [d for d in decisions if (d["reason"] or "").startswith(name + ":")][:20],
        })
    return cards


def arm(name, armed, path=ENV_PATH):
    """Add or remove one strategy from STRATEGY in .env."""
    from strategies import STRATEGIES, selected
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name}")
    with SAVE_LOCK:  # read and write as one step: two quick arms must both stick
        names = [n for n in selected(read_env(path)) if n != name] + ([name] if armed else [])
        if not names:
            raise ValueError("keep at least one strategy armed, or Stop the bot instead")
        ordered = [n for n in STRATEGIES if n in names]
        save_settings({"STRATEGY": "all" if len(ordered) == len(STRATEGIES) else ",".join(ordered)}, path)


def signal_sources(journal, path=ENV_PATH):
    """The Signals page: each source's switch and setup, and every outside signal on record."""
    import signals
    from strategies import STRATEGIES, selected
    values = read_env(path)
    return {
        "strategies": {"armed": len(selected(values)), "total": len(STRATEGIES), "brain": values["BRAIN"]},
        "webhook": {"on": values["SIGNAL_WEBHOOK"] == "on", "url": signals.webhook_url(values["WEBHOOK_TOPIC"]) if values["WEBHOOK_TOPIC"] else ""},
        "telegram": {"on": values["SIGNAL_TELEGRAM"] == "on", "ready": bool(values["TELEGRAM_TOKEN"] and values["TELEGRAM_CHAT_ID"])},
        "history": journal._rows(
            "SELECT at, source, action, reason FROM decisions WHERE kind = 'signal' ORDER BY id DESC LIMIT 200"
        ),
    }


def switch_source(source, on, path=ENV_PATH):
    """Turn the webhook or Telegram source on or off; the webhook gets a secret topic the first time."""
    import signals
    if source == "webhook":
        with SAVE_LOCK:  # the topic check and the save are one step
            changes = {"SIGNAL_WEBHOOK": "on" if on else "off"}
            if on and not read_env(path)["WEBHOOK_TOPIC"]:
                changes["WEBHOOK_TOPIC"] = signals.new_topic()
            save_settings(changes, path)
    elif source == "telegram":
        save_settings({"SIGNAL_TELEGRAM": "on" if on else "off"}, path)
    else:
        raise ValueError(f"unknown signal source {source}")


def rotate_topic(path=ENV_PATH):
    """New secret webhook topic: the old URL stops working, paste the new one into TradingView."""
    import signals
    save_settings({"WEBHOOK_TOPIC": signals.new_topic()}, path)


def ping_webhook(path=ENV_PATH, opener=urllib.request.urlopen):
    """Send "test ping" through the relay: a running bot logs it as skipped (no buy or sell), nothing trades."""
    import signals
    topic = read_env(path)["WEBHOOK_TOPIC"]
    if not topic:
        raise ValueError("turn the TradingView source on first")
    try:
        with opener(urllib.request.Request(signals.webhook_url(topic), data=b"test ping", method="POST"), timeout=10):
            pass
    except OSError as error:
        raise ValueError(f"could not reach ntfy.sh: {error}") from None


def broker_login(body):
    """Log MT5 into the account from the form. Refused while a bot trades: it would switch under it."""
    if bot_running():
        raise ValueError("Stop the bot before switching accounts")
    import broker
    broker.login(body.get("login", ""), body.get("password", ""), body.get("server", ""))
    return broker.status()


def read_env(path=ENV_PATH):
    values = dict(DEFAULTS)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    return values


def public_settings(path=ENV_PATH):
    values = read_env(path)
    for key in SECRETS:
        values[key] = "set" if values.get(key) else ""
    return values


def save_settings(changes, path=ENV_PATH):
    """Merge the form into .env, keeping comments. Raises ValueError with a message the page shows."""
    if not isinstance(changes, dict):
        raise ValueError("expected an object of settings")
    from wizard import render_env  # here: wizard pulls in MetaTrader5
    # Read, merge, validate and write as one step: two quick saves must not drop each other's change.
    with SAVE_LOCK:
        values = read_env(path)
        for key, value in changes.items():
            if key not in DEFAULTS:
                raise ValueError(f"unknown setting {key}")
            value = str(value).strip()
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} must be one line")
            if key in SECRETS and value in ("", "set"):
                continue  # blank or untouched secret field keeps the stored one
            values[key] = value
        template = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE_PATH.read_text(encoding="utf-8")
        text = render_env(template, values)
        missing = [key for key in values if not any(line.startswith(key + "=") for line in text.splitlines())]
        text += "".join(f"{key}={values[key]}\n" for key in missing)
        candidate = path.with_name(".env.check")
        candidate.write_text(text, encoding="utf-8")
        try:
            load_config(candidate)  # same validation the bot runs at start
        except SystemExit as error:
            raise ValueError(str(error)) from None
        finally:
            candidate.unlink()
        path.write_text(text, encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def trusted(self):
        """Only this page may call the API: blocks other sites and DNS rebinding."""
        allowed = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        origin = self.headers.get("Origin")
        # A cross-site <img> or no-cors fetch sends no Origin, but browsers mark it in Sec-Fetch-Site.
        site = self.headers.get("Sec-Fetch-Site")
        return (self.headers.get("Host") in allowed and (origin is None or origin.split("//")[-1] in allowed)
                and site in (None, "same-origin", "none"))

    def reply(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # an updated install must never show the old page
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.trusted():
            return self.reply(403, {"error": "forbidden"})
        try:
            self.route_get(urlparse(self.path))
        except ValueError as error:  # e.g. ?days=abc
            self.reply(400, {"error": str(error)})
        except Exception as error:  # MT5, sqlite, disk: say what failed instead of dropping the connection
            self.reply(500, {"error": f"{type(error).__name__}: {error}"})

    def route_get(self, url):
        if url.path == "/":
            return self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/dashboard":
            days = int(parse_qs(url.query).get("days", ["30"])[0])
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, dashboard(journal, days))
            finally:
                journal.close()
        if url.path == "/api/history":
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, history(journal))
            finally:
                journal.close()
        if url.path == "/api/signals":
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, signal_sources(journal))
            finally:
                journal.close()
        if url.path == "/api/agents":
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, agents(journal, read_env()))
            finally:
                journal.close()
        if url.path == "/api/settings":
            return self.reply(200, public_settings())
        if url.path == "/api/bot":
            return self.reply(200, bot_status())
        if url.path in ("/api/broker", "/api/broker/symbols"):
            import broker  # here: the MetaTrader5 package only loads once the page asks for it
            return self.reply(200, broker.status() if url.path == "/api/broker" else broker.symbols())
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        # JSON content type forces a CORS preflight, which other sites cannot pass
        if not self.trusted() or self.headers.get("Content-Type") != "application/json":
            return self.reply(403, {"error": "forbidden"})
        body = None
        actions = {
            "/api/settings": lambda: (save_settings(body), public_settings())[1],
            "/api/bot/start": lambda: (start_bot(), bot_status())[1],
            "/api/bot/stop": lambda: (stop_bot(), bot_status())[1],
            "/api/agents/arm": lambda: (arm(body.get("name"), bool(body.get("armed"))), public_settings())[1],
            "/api/signals/switch": lambda: (switch_source(body.get("source"), bool(body.get("on"))), public_settings())[1],
            "/api/signals/rotate": lambda: (rotate_topic(), public_settings())[1],
            "/api/signals/ping": lambda: (ping_webhook(), {"sent": True})[1],
            "/api/broker/login": lambda: broker_login(body),  # the password is not stored or logged
            "/api/broker/install": lambda: (__import__("broker").start_install(), {"started": True})[1],
        }
        action = actions.get(urlparse(self.path).path)
        if action is None:
            return self.reply(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            self.reply(200, action())
        except ValueError as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:  # e.g. the bot could not be started: show why
            self.reply(500, {"error": f"{type(error).__name__}: {error}"})


def open_window(url):
    """Own app window (WebView2 through pywebview); False when it cannot open, so the caller uses a browser."""
    import ctypes
    try:
        import webview
        window = webview.create_window("TradeBot", url, width=1360, height=860, min_size=(900, 600), background_color="#0b1019")
        if sys.platform == "win32":  # the console behind the window has nothing to show
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        # A shortcut's minimized/hidden start applies to the first window shown: bring this one up regardless.
        # gui pinned to WebView2: without it pywebview may fall back to the old IE engine, which breaks the page.
        webview.start(lambda: (window.restore(), window.show()), gui="edgechromium")  # blocks until closed
        return True
    except Exception as error:  # no pywebview or no WebView2 runtime (older Windows 10): use the browser
        print("app window unavailable, opening the browser instead:", error)
        if sys.platform == "win32":
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 5)  # show the console again
        return False


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already open: show that one instead of failing
        server = None
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Dashboard at {url}")
    if "--no-browser" not in sys.argv:
        if "--browser" not in sys.argv and open_window(url):
            return  # the window closed, the dashboard goes with it
        webbrowser.open(url)
    if server:
        print("Close this window to stop the dashboard.")
        threading.Event().wait()


if __name__ == "__main__":
    main()
