"""Desktop dashboard: python ui.py opens the TradeBot window (--browser: a browser tab instead).

View of journal.db (today, win rate, equity curve, decisions, trade history), a form that
rewrites .env, and Start / Stop for bot.py. Listens on 127.0.0.1 only.
"""
import json
import os
import socket
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
SECRETS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TELEGRAM_TOKEN")  # never sent to the page, only "set" or not
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


def start_bot(append=False):
    """append: a watchdog restart keeps the log of the run that died, above the new one."""
    global bot_process
    with BOT_LOCK:  # two quick clicks must not start two bots trading the same account
        if bot_running():
            raise ValueError("the bot is already running")
        STOP_FLAG.unlink(missing_ok=True)
        watchdog["started"] = time.time()
        with open(LOG_PATH, "a" if append else "w", encoding="utf-8") as log:
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


def test_ai_key(body, path=ENV_PATH):
    """List the models a key can use: the key typed on the page, or the stored one when it is blank."""
    import ai_strategy
    provider = body.get("provider")
    if provider not in ai_strategy.KEYS:
        raise ValueError(f"unknown AI provider {provider}")
    key = str(body.get("key") or "").strip() or read_env(path)[ai_strategy.KEYS[provider]]
    return {"models": ai_strategy.list_models(provider, key), "default": ai_strategy.DEFAULT_MODELS[provider]}


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
        if url.path == "/api/app":
            return self.reply(200, {"run_at_login": __import__("desktop").run_at_login()})
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
            "/api/ai/test": lambda: test_ai_key(body),  # the key is only sent to its own AI company
            "/api/app/show": lambda: (show_window and show_window(), {"shown": bool(show_window)})[1],
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


class Server(ThreadingHTTPServer):
    """One app per port. The stdlib default (SO_REUSEADDR) lets a second app bind the same port on
    Windows, so both answer at random and the second never learns the first is open."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


RESTART_SECONDS = 30  # wait before restarting a bot that died on its own
QUICK_CRASH_SECONDS = 120  # a bot that dies sooner than this after a start counts as a quick crash
CRASH_LIMIT = 5  # quick crashes in a row before the watchdog gives up (a bad setting would loop forever)
watchdog = {"crashes": 0, "started": 0.0, "restart_at": None}


def log_line(text):
    with open(LOG_PATH, "a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def watch_bot(now):
    """One watchdog step for the bot this app started. Returns what it did (for the log and the tests)."""
    global bot_process
    if watchdog["restart_at"] is not None and now >= watchdog["restart_at"]:
        watchdog["restart_at"] = None
        if STOP_FLAG.exists():
            return "stopped"  # the user pressed Stop while it waited
        try:
            start_bot(append=True)
        except ValueError:  # another bot is already running (start.bat, a second app)
            return "already running"
        watchdog["started"] = now
        return "restarted"
    process = bot_process
    if process is None or process.poll() is None:
        return "ok"
    bot_process = None
    # Its heartbeat outlives a hard kill and would make the restart think a bot still runs.
    # A real second bot is still refused by bot.py's own single-instance lock.
    ALIVE.unlink(missing_ok=True)
    if STOP_FLAG.exists():  # bot.py leaves it when it stops on request (Stop, /stop)
        watchdog["crashes"] = 0
        return "stopped"
    quick = now - watchdog["started"] < QUICK_CRASH_SECONDS
    watchdog["crashes"] = watchdog["crashes"] + 1 if quick else 1
    if watchdog["crashes"] > CRASH_LIMIT:
        watchdog["crashes"] = 0
        log_line(f"the bot stopped {CRASH_LIMIT + 1} times in a row right after starting; not restarting it. Read the lines above, fix it, press Start.")
        return "gave up"
    watchdog["restart_at"] = now + RESTART_SECONDS
    log_line(f"the bot stopped unexpectedly (exit code {process.returncode}); restarting it in {RESTART_SECONDS} s")
    return "restarting"


def run_watchdog():
    while True:
        try:
            watch_bot(time.time())
        except Exception as error:  # the watchdog must outlive any surprise
            print("watchdog:", error)
        time.sleep(2)


def start_app_bot():
    """The 'start the bot when TradeBot opens' setting."""
    if read_env().get("AUTO_START_BOT") == "on" and not bot_running():
        try:
            start_bot()
            watchdog["started"] = time.time()
        except ValueError:
            pass


show_window = None  # set once the app window exists: a second launch asks this one to come forward


def ask_running_app_to_show():
    """Another TradeBot already holds the port: bring its window forward instead of opening a second one."""
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/show", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except OSError:
        return False


def open_window(url, background=False):
    """Own app window (WebView2 through pywebview) with a tray icon; closing it hides to the tray.

    False when it cannot open, so the caller uses a browser instead.
    """
    global show_window
    import ctypes
    try:
        import webview
        window = webview.create_window("TradeBot", url, width=1360, height=860, min_size=(900, 600),
                                       background_color="#0b1019", hidden=background)
        quitting = threading.Event()

        def bring_forward():
            window.show()
            window.restore()

        def quit_app():
            quitting.set()
            window.destroy()

        show_window = bring_forward
        if sys.platform == "win32":  # running from source: the console behind the window has nothing to show
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        try:
            import desktop
            icon = desktop.tray(bring_forward, lambda: start_bot() if not bot_running() else None, stop_bot,
                                quit_app, lambda: (stop_bot(), quit_app()))
        except Exception as error:  # no tray (pystray missing): closing the window quits as before
            print("tray icon unavailable:", error)
            icon = None
            quitting.set()
        # With a tray, the window's X hides it and the app keeps running; quitting is in the tray menu.
        window.events.closing += lambda: True if quitting.is_set() else (window.hide(), False)[1]
        # A shortcut's minimized/hidden start applies to the first window shown: bring this one up regardless.
        # gui pinned to WebView2: without it pywebview may fall back to the old IE engine, which breaks the page.
        webview.start(None if background else bring_forward, gui="edgechromium")  # blocks until quit
        if icon:
            icon.stop()
        return True
    except Exception as error:  # no pywebview or no WebView2 runtime (older Windows 10): use the browser
        print("app window unavailable, opening the browser instead:", error)
        if sys.platform == "win32":
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 5)  # show the console again
        return False


def main():
    url = f"http://127.0.0.1:{PORT}"
    background = "--background" in sys.argv  # started with Windows: straight to the tray
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError:  # already open: bring that one forward instead of a second app
        if not background and ask_running_app_to_show():
            return
        server = None
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=run_watchdog, daemon=True).start()
        start_app_bot()
        print(f"Dashboard at {url}")
    if "--no-browser" not in sys.argv:
        if "--browser" not in sys.argv and open_window(url, background):
            return  # quit from the tray; a bot started here keeps running unless "Stop the bot and quit"
        webbrowser.open(url)
    if server:
        print("Close this window to stop the dashboard.")
        threading.Event().wait()


if __name__ == "__main__":
    main()
