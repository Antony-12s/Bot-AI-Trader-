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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import ALIVE, APP_DIR, DEFAULTS, ENV_PATH, JOURNAL_PATH, STOP_FLAG, load_config
from journal import Journal, summarize
from risk import SECONDS_PER_DAY, day_start

PORT = 8765
PAGE = Path(__file__).with_name("ui.html")
TEMPLATE_PATH = ENV_PATH.with_name(".env.example")
LOG_PATH = APP_DIR / "bot.log"
SECRETS = ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN")  # never sent to the page, only "set" or not
# ponytail: one loop of the bot (an AI call included) must finish within this, or it shows as stopped
ALIVE_SECONDS = 120
SAVE_LOCK = threading.Lock()
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
    return {"running": bot_running(), "stopping": STOP_FLAG.exists() and bot_running(), "log": log}


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
    return {
        "today": round(today, 2),
        "open": len(journal.open_trades()),
        "totals": totals,
        "equity": equity,
        "decisions": decisions,
        "ai_spend": round(journal.spend(), 2),
    }


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
    from wizard import render_env  # here: wizard pulls in MetaTrader5
    template = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE_PATH.read_text(encoding="utf-8")
    text = render_env(template, values)
    missing = [key for key in values if not any(line.startswith(key + "=") for line in text.splitlines())]
    text += "".join(f"{key}={values[key]}\n" for key in missing)
    candidate = path.with_name(".env.check")
    with SAVE_LOCK:  # a double-clicked Save must not validate or unlink the other request's file
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
        return self.headers.get("Host") in allowed and (origin is None or origin.split("//")[-1] in allowed)

    def reply(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.trusted():
            return self.reply(403, {"error": "forbidden"})
        url = urlparse(self.path)
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
        if url.path == "/api/settings":
            return self.reply(200, public_settings())
        if url.path == "/api/bot":
            return self.reply(200, bot_status())
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
        }
        action = actions.get(urlparse(self.path).path)
        if action is None:
            return self.reply(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.reply(200, action())
        except ValueError as error:
            self.reply(400, {"error": str(error)})


def open_window(url):
    """Own app window (WebView2 through pywebview); False when pywebview is missing."""
    try:
        import webview
    except ImportError:
        return False
    window = webview.create_window("TradeBot", url, width=1360, height=860, min_size=(900, 600), background_color="#0b1019")
    if sys.platform == "win32":  # the console behind the window has nothing to show
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    # A shortcut's minimized/hidden start applies to the first window shown: bring this one up regardless.
    webview.start(lambda: (window.restore(), window.show()))  # blocks until closed; a bot started here keeps running
    return True


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
