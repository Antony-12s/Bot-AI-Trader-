"""Local web dashboard: python ui.py, then the browser opens http://127.0.0.1:8765

Read-only view of journal.db (today, win rate, equity curve, recent decisions) and a form
that rewrites .env. Listens on 127.0.0.1 only; it does not start or stop the bot.
"""
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import DEFAULTS, ENV_PATH, JOURNAL_PATH, load_config
from journal import Journal, summarize
from risk import SECONDS_PER_DAY, day_start

PORT = 8765
PAGE = Path(__file__).with_name("ui.html")
TEMPLATE_PATH = ENV_PATH.with_name(".env.example")
SECRETS = ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN")  # never sent to the page, only "set" or not
SAVE_LOCK = threading.Lock()


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
        if url.path == "/api/settings":
            return self.reply(200, public_settings())
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.trusted() or self.headers.get("Content-Type") != "application/json":
            return self.reply(403, {"error": "forbidden"})
        if urlparse(self.path).path != "/api/settings":
            return self.reply(404, {"error": "not found"})
        try:
            save_settings(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
        except ValueError as error:
            return self.reply(400, {"error": str(error)})
        self.reply(200, public_settings())


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Dashboard at {url}  (close this window to stop it)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
