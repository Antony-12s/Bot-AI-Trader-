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
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import ALIVE, APP_DIR, DEFAULTS, ENV_PATH, JOURNAL_PATH, STOP_FLAG, load_config
from journal import Journal, summarize
from mt5_proxy import call as mt5_call  # every MetaTrader5 call runs in a helper process (see mt5_proxy)
from risk import SECONDS_PER_DAY, day_start

PORT = int(os.environ.get("TRADEBOT_PORT", "8765"))  # change it if another program already uses 8765
PAGE = Path(__file__).with_name("ui.html")
VENDOR = Path(__file__).with_name("vendor")  # third-party files shipped with the app (lightweight-charts, Apache-2.0)
TEMPLATE_PATH = ENV_PATH.with_name(".env.example")
LOG_PATH = APP_DIR / "bot.log"
SECRETS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TELEGRAM_TOKEN")  # never sent to the page, only "set" or not
# ponytail: one loop of the bot (an AI call included) must finish within this, or it shows as stopped
ALIVE_SECONDS = 120
SAVE_LOCK = threading.RLock()  # re-entrant: switch_source() holds it around save_settings()
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


def signal_sources(journal, path=ENV_PATH):
    """The Signals page: each source's switch and setup, and every outside signal on record."""
    import agents
    import signals
    values = read_env(path)
    mine = agents.load(path.with_name("agents.json"))
    return {
        "agents": {"auto": sum(a["mode"] == "auto" for a in mine), "total": len(mine)},
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


CHART_TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def chart(journal, symbol, timeframe, count, candles_for):
    """Candles plus the bot's entries and exits on them, and the open trade's entry / stop / target lines."""
    if timeframe not in CHART_TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe}")
    bars = candles_for(symbol, timeframe, max(2, min(count, 2000)))
    first, step = bars[0]["time"], CHART_TIMEFRAMES[timeframe]
    snap = lambda t: t - (t - first) % step  # markers must sit on a candle's own time
    trades = journal._rows("SELECT side, entry, sl, tp, exit, profit, opened_at, closed_at FROM trades WHERE symbol = ?"
                           " AND COALESCE(closed_at, opened_at) >= ? ORDER BY opened_at", (symbol, first))
    marks = []
    for trade in trades:
        if trade["opened_at"] >= first:
            marks.append({"time": snap(trade["opened_at"]), "kind": "open", "side": trade["side"], "price": trade["entry"]})
        if trade["closed_at"]:
            marks.append({"time": snap(trade["closed_at"]), "kind": "close", "side": trade["side"], "profit": trade["profit"]})
    open_trade = next((t for t in reversed(trades) if t["closed_at"] is None), None)
    return {"symbol": symbol, "timeframe": timeframe, "candles": bars, "marks": sorted(marks, key=lambda m: m["time"]),
            "open": open_trade and {k: open_trade[k] for k in ("side", "entry", "sl", "tp")}}


def test_ai_key(body, path=ENV_PATH):
    """List the models a key can use: the key typed on the page, or the stored one when it is blank."""
    import ai_strategy
    provider = body.get("provider")
    if provider not in ai_strategy.KEYS:
        raise ValueError(f"unknown AI provider {provider}")
    key = str(body.get("key") or "").strip() or read_env(path)[ai_strategy.KEYS[provider]]
    return {"models": ai_strategy.list_models(provider, key), "default": ai_strategy.DEFAULT_MODELS[provider]}


def my_agents(journal, path=None):
    """The AI agents page: every agent with its record and latest calls."""
    import agents
    cards = []
    for agent in agents.load(path):
        source = "agent:" + agent["id"]
        trades = [t for t in journal.closed_trades() if t["brain"] == source]
        calls = journal._rows("SELECT at, action, reason FROM decisions WHERE source = ? AND kind = 'agent'"
                              " ORDER BY id DESC LIMIT 20", (source,))
        cards.append(dict(agent, totals=summarize(trades), open=len([t for t in journal.open_trades() if t["brain"] == source]),
                          decisions=calls))
    return cards


def save_agent(raw, path=None):
    """Create or update one agent from the wizard; the bot picks it up on its next loop."""
    import agents
    with SAVE_LOCK:
        agent = agents.clean(raw)
        existing = agents.load(path)
        replaced = [agent if a["id"] == agent["id"] else a for a in existing]
        agents.store(replaced if any(a["id"] == agent["id"] for a in existing) else existing + [agent], path)
    return agent


def set_agent_mode(agent_id, mode, path=None):
    """Watch / suggest / auto from the agents list, without opening the wizard."""
    import agents
    with SAVE_LOCK:
        found = [a for a in agents.load(path) if a["id"] == agent_id]
        if not found:
            raise ValueError("no such agent")
        return save_agent(dict(found[0], mode=mode), path)


def understand(raw):
    """What the built-in analyst reads in the wizard's strategy text, shown as it is typed."""
    import agents
    import plainrules
    text = str(raw.get("strategy") or "")
    return dict(plainrules.summary(text), exact=agents.runs_template({"template": raw.get("template") or "", "strategy": text}))


def delete_agent(agent_id, path=None):
    import agents
    with SAVE_LOCK:
        agents.store([a for a in agents.load(path) if a["id"] != agent_id], path)


def test_telegram(body, path=ENV_PATH, opener=urllib.request.urlopen):
    """Check the bot token (getMe) and that the bot can see the chat (getChat). Sends no message."""
    stored = read_env(path)
    token = str(body.get("token") or "").strip() or stored["TELEGRAM_TOKEN"]
    chat = str(body.get("chat_id") or "").strip() or stored["TELEGRAM_CHAT_ID"]
    if not token:
        raise ValueError("paste the token from @BotFather first")

    def call(method, **params):
        url = f"https://api.telegram.org/bot{token}/{method}?" + urllib.parse.urlencode(params)
        try:
            with opener(url, timeout=15) as response:
                return json.loads(response.read())["result"]
        except urllib.error.HTTPError as error:  # its text carries the status only, never the token in the URL
            raise ValueError({401: "the token was rejected: copy it again from @BotFather",
                              400: "the bot cannot see that chat: message your bot once, then use your own chat id",
                              403: "the bot cannot see that chat: message your bot once, then use your own chat id"}
                             .get(error.code, f"Telegram answered with error {error.code}")) from None
        except OSError as error:
            raise ValueError(f"could not reach Telegram ({type(error).__name__})") from None

    me = call("getMe")
    result = {"bot": "@" + me.get("username", "")}
    if chat:
        seen = call("getChat", chat_id=chat)
        result["chat"] = seen.get("title") or " ".join(filter(None, (seen.get("first_name"), seen.get("last_name"))))
    return result


def broker_login(body, path=ENV_PATH):
    """Log MT5 into the account from the form. Refused while a bot trades: it would switch under it."""
    if bot_running():
        raise ValueError("Stop the bot before switching accounts")
    kind = mt5_call("login", body.get("login", ""), body.get("password", ""), body.get("server", ""))
    # filed under what the broker says it is (a real account can never land in the Demo slot); MODE is not changed
    save_settings({f"{kind.upper()}_LOGIN": str(body.get("login")).strip(), f"{kind.upper()}_SERVER": str(body.get("server")).strip()}, path)
    return dict(mt5_call("status"), saved_as=kind)


def switch_mode(mode, path=ENV_PATH):
    """Demo / Live in the header: move TradeBot's MT5 to that mode's account, then save MODE."""
    if mode not in ("demo", "live"):
        raise ValueError("choose demo or live")
    if bot_running():
        raise ValueError("Stop the bot before switching between Demo and Live")
    values = read_env(path)
    mt5_call("switch", values[f"{mode.upper()}_LOGIN"], values[f"{mode.upper()}_SERVER"], mode)
    save_settings({"MODE": mode}, path)
    return public_settings(path)


def follow_account(live_ok=False, path=ENV_PATH):
    """Demo / Live follows the account MetaTrader 5 is logged in to, and files it in that mode's slot.
    Demo is followed at once; a real account only with live_ok (the user said yes), else {"ask": login}.
    Until then MODE stays demo and the bot refuses the real account (risk.account_error)."""
    account = mt5_call("status")
    if not account.get("logged_in"):
        return {}
    kind = "demo" if account["demo"] else "live"
    if read_env(path)["MODE"] == kind:
        return {}
    if kind == "live" and not live_ok:
        return {"ask": account["login"], "server": account["server"]}
    save_settings({"MODE": kind, f"{kind.upper()}_LOGIN": str(account["login"]), f"{kind.upper()}_SERVER": account["server"]}, path)
    return {"settings": public_settings(path)}


def open_terminal():
    """Bring MT5 forward. Windows only lets the program the user just clicked raise another window, so this
    one lets any process do it for the moment, then the helper process (which talks to MT5) does it."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    return mt5_call("open_terminal")


def broker_page():
    values = read_env()
    return dict(mt5_call("status"), accounts={mode: {"login": values[f"{mode.upper()}_LOGIN"], "server": values[f"{mode.upper()}_SERVER"]}
                                           for mode in ("demo", "live")})


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
        # the template next to the app, else the copy bundled inside it (a folder run without the installer)
        source = path if path.exists() else TEMPLATE_PATH if TEMPLATE_PATH.exists() else Path(__file__).with_name(".env.example")
        template = source.read_text(encoding="utf-8")
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
        if url.path == "/api/myagents":
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, my_agents(journal))
            finally:
                journal.close()
        if url.path == "/api/templates":  # the agent wizard's ready strategies
            return self.reply(200, __import__("strategies").PLAIN)
        if url.path == "/api/settings":
            return self.reply(200, public_settings())
        if url.path == "/api/bot":
            return self.reply(200, bot_status())
        if url.path == "/vendor/lightweight-charts.js":  # bundled, so the chart works offline
            return self.reply(200, VENDOR.joinpath("lightweight-charts.js").read_bytes(), "text/javascript; charset=utf-8")
        if url.path == "/api/chart":
            query = parse_qs(url.query)
            settings = read_env()
            journal = Journal(JOURNAL_PATH)
            try:
                return self.reply(200, chart(journal, query.get("sym", [settings["SYMBOL"]])[0], query.get("tf", [settings["TIMEFRAME"]])[0],
                                             int(query.get("count", ["300"])[0]), lambda *a: mt5_call("candles", *a)))
            finally:
                journal.close()
        if url.path == "/api/app":
            return self.reply(200, {"run_at_login": __import__("desktop").run_at_login()})
        if url.path in ("/api/broker", "/api/broker/symbols"):
            return self.reply(200, broker_page() if url.path == "/api/broker" else mt5_call("symbols"))
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
            "/api/signals/switch": lambda: (switch_source(body.get("source"), bool(body.get("on"))), public_settings())[1],
            "/api/signals/rotate": lambda: (rotate_topic(), public_settings())[1],
            "/api/signals/ping": lambda: (ping_webhook(), {"sent": True})[1],
            "/api/broker/login": lambda: broker_login(body),  # the password is not stored or logged
            "/api/ai/test": lambda: test_ai_key(body),  # the key is only sent to its own AI company
            "/api/telegram/test": lambda: test_telegram(body),
            "/api/myagents/save": lambda: save_agent(body),
            "/api/myagents/mode": lambda: set_agent_mode(body.get("id"), body.get("mode")),
            "/api/myagents/delete": lambda: (delete_agent(body.get("id")), {"deleted": True})[1],
            "/api/myagents/understand": lambda: understand(body),
            "/api/app/show": lambda: (show_window and show_window(), {"shown": bool(show_window)})[1],
            "/api/broker/install": lambda: (mt5_call("start_install"), {"started": True})[1],
            "/api/broker/mode": lambda: switch_mode(body.get("mode")),
            "/api/broker/own": lambda: (mt5_call("setup_own_terminal"), {"started": True})[1],
            "/api/broker/open": lambda: {"result": open_terminal()},
            "/api/broker/follow": lambda: follow_account(body.get("live") is True),
            "/api/broker/show": lambda: {"windows": mt5_call("show_terminal", bool(body.get("show")))},
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
    last_problem = None
    while True:
        for step in (lambda: watch_bot(time.time()),
                     lambda: mt5_call("keep_hidden")):  # TradeBot's own MT5 stays hidden unless Show MT5
            try:
                step()
            except Exception as error:  # the watchdog must outlive any surprise; the windowed app has no console
                problem = f"watchdog: {type(error).__name__}: {error}"
                if problem != last_problem:
                    log_line(problem)
                    last_problem = problem
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
        try:  # once: the strategies the old Strategies page armed become agents, before the bot starts
            __import__("agents").migrate(read_env() if ENV_PATH.exists() else None)
        except Exception as error:  # a bad .env must not keep the window from opening
            print("could not move the armed strategies into agents:", error)
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
