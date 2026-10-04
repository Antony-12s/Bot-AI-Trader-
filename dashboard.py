"""One window for the whole thing: MT5 status, live price, Start / Stop / Pause, stats, playbook, log.

    dashboard.bat   (or: python dashboard.py)

The dashboard starts bot.py as a child process and shows its output, so MT5 itself can stay
minimised. Pause and Stop work through pause.flag / stop.flag, which the bot checks every
few seconds. It needs the Tk that ships with python.org's Windows installer; nothing extra.
"""
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from config import JOURNAL_PATH, load_config
from journal import Journal, summarize

HERE = Path(__file__).resolve().parent
PAUSE_FLAG = HERE / "pause.flag"
STOP_FLAG = HERE / "stop.flag"
REFRESH_MS = 1000
LOG_LINES = 400


class BotProcess:
    """bot.py as a child process whose output lines queue up for the window."""

    def __init__(self, command=None):
        self.command = command or [sys.executable, "-u", str(HERE / "bot.py")]
        self.process = None
        self.lines = queue.Queue()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.running:
            return
        self.process = subprocess.Popen(
            self.command, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(f"[bot exited with code {self.process.wait()}]")

    def stop(self):
        """Ask the bot to finish its loop; it leaves stop.flag behind so the watchdog stays down too."""
        if self.running:
            STOP_FLAG.touch()

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                return out


def account_line(account, mt5):
    if account is None:
        return "MT5: connected, no account logged in"
    kind = "demo" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL MONEY"
    return f"MT5: {account.login} @ {account.server} ({kind})  balance {account.balance:.2f} {account.currency}"


def price_line(symbol, tick, digits):
    if tick is None:
        return f"{symbol}: no price (market closed or symbol missing)"
    when = datetime.utcfromtimestamp(tick.time).strftime("%H:%M:%S")
    return f"{symbol}: bid {tick.bid:.{digits}f}  ask {tick.ask:.{digits}f}  spread {round((tick.ask - tick.bid) / 10 ** -digits)} pts  ({when} server time)"


def stats_lines(journal):
    trades = journal.closed_trades()
    totals = summarize(trades)
    open_paper = len(journal.open_trades("paper"))
    open_mt5 = len(journal.open_trades("mt5"))
    lines = [
        f"trades {totals['trades']}   wins {totals['wins']}   losses {totals['losses']}   win rate {totals['win_rate']:.0%}",
        f"net {totals['net']:+.2f}   profit factor {'n/a' if totals['profit_factor'] is None else totals['profit_factor']}"
        f"   max drawdown {totals['max_drawdown']:.2f}",
        f"open now: paper {open_paper}, broker {open_mt5}   AI spend total ${journal.spend():.2f}",
    ]
    playbook = journal.playbook()
    lines.append("playbook: " + (playbook["text"].replace("\n", " | ") if playbook else "none yet"))
    recent = journal.recent_lessons(1)
    if recent:
        lines.append(f"last lesson: {recent[0]['lesson']}")
    return lines


def main():
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    try:
        import MetaTrader5 as mt5
    except ImportError:  # the window still works for log and stats
        mt5 = None

    config = load_config()
    bot = BotProcess()
    journal = Journal(JOURNAL_PATH)
    mt5_ready = bool(mt5 and mt5.initialize())

    root = tk.Tk()
    root.title(f"Bot AI Trader  [{config['MODE']} / {config['BRAIN']} / {config['STRATEGY']}]")
    root.geometry("900x640")
    root.minsize(700, 480)

    status = tk.Frame(root, padx=10, pady=8)
    status.pack(fill="x")
    light = tk.Canvas(status, width=18, height=18, highlightthickness=0)
    light.pack(side="left")
    dot = light.create_oval(2, 2, 16, 16, fill="grey")
    connection = tk.Label(status, text="MT5: connecting ...", anchor="w", font=("Segoe UI", 10))
    connection.pack(side="left", padx=8)
    price = tk.Label(root, text="", anchor="w", font=("Consolas", 11), padx=10)
    price.pack(fill="x")

    buttons = tk.Frame(root, padx=10, pady=6)
    buttons.pack(fill="x")
    font = ("Segoe UI", 11, "bold")
    start_button = tk.Button(buttons, text="Start bot", width=12, font=font, bg="#2e7d32", fg="white", command=lambda: bot.start())
    start_button.pack(side="left", padx=4)
    stop_button = tk.Button(buttons, text="Stop bot", width=12, font=font, command=lambda: bot.stop())
    stop_button.pack(side="left", padx=4)

    def toggle_pause():
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink()
        else:
            PAUSE_FLAG.touch()

    pause_button = tk.Button(buttons, text="Pause", width=12, font=font, command=toggle_pause)
    pause_button.pack(side="left", padx=4)

    def open_report():
        result = subprocess.run([sys.executable, str(HERE / "report.py")], cwd=str(HERE), capture_output=True, text=True)
        window = tk.Toplevel(root)
        window.title("Report")
        text = scrolledtext.ScrolledText(window, width=100, height=36, font=("Consolas", 10))
        text.pack(fill="both", expand=True)
        text.insert("end", result.stdout or result.stderr)
        text.configure(state="disabled")

    tk.Button(buttons, text="Report", width=10, font=font, command=open_report).pack(side="left", padx=4)
    if sys.platform == "win32":
        import os
        tk.Button(buttons, text="Train", width=10, font=font, command=lambda: os.startfile(str(HERE / "train.bat"))).pack(side="left", padx=4)
        tk.Button(buttons, text="Settings", width=10, font=font, command=lambda: os.startfile(str(HERE / "settings.bat"))).pack(side="left", padx=4)
    bot_state = tk.Label(buttons, text="bot: stopped", font=("Segoe UI", 10), padx=12)
    bot_state.pack(side="left")

    stats = tk.Label(root, text="", anchor="w", justify="left", font=("Consolas", 10), padx=10, pady=4)
    stats.pack(fill="x")
    log = scrolledtext.ScrolledText(root, font=("Consolas", 10), state="disabled")
    log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def append_log(lines):
        if not lines:
            return
        log.configure(state="normal")
        for line in lines:
            log.insert("end", line + "\n")
        excess = int(log.index("end-1c").split(".")[0]) - LOG_LINES
        if excess > 0:
            log.delete("1.0", f"{excess}.0")
        log.see("end")
        log.configure(state="disabled")

    def refresh():
        nonlocal mt5_ready
        append_log(bot.drain())
        if mt5 is None:
            light.itemconfigure(dot, fill="grey")
            connection.configure(text="MT5: the MetaTrader5 package is not installed on this machine (Windows only)")
        else:
            if not mt5_ready:
                mt5_ready = mt5.initialize()
            terminal = mt5.terminal_info() if mt5_ready else None
            if terminal is None:
                mt5_ready = False
                light.itemconfigure(dot, fill="#c62828")
                connection.configure(text="MT5: not connected. Open the MT5 terminal and log in; this keeps retrying.")
                price.configure(text="")
            else:
                light.itemconfigure(dot, fill="#2e7d32")
                connection.configure(text=account_line(mt5.account_info(), mt5))
                info = mt5.symbol_info(config["SYMBOL"])
                price.configure(text=price_line(config["SYMBOL"], mt5.symbol_info_tick(config["SYMBOL"]), info.digits if info else 2))
        paused = PAUSE_FLAG.exists()
        pause_button.configure(text="Resume" if paused else "Pause", bg="#f9a825" if paused else root.cget("bg"))
        running = bot.running
        bot_state.configure(text=("bot: running" + (" (paused)" if paused else "")) if running else "bot: stopped",
                            fg="#2e7d32" if running else "#c62828")
        start_button.configure(state="disabled" if running else "normal")
        stop_button.configure(state="normal" if running else "disabled")
        try:
            stats.configure(text="\n".join(stats_lines(journal)))
        except Exception as error:  # the bot may be writing the journal right now
            stats.configure(text=f"stats unavailable: {error}")
        root.after(REFRESH_MS, refresh)

    def on_close():
        if bot.running and not messagebox.askyesno("Bot AI Trader", "The bot is still running. Stop it and close?"):
            return
        bot.stop()
        journal.close()
        if mt5 is not None:
            mt5.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    append_log([f"dashboard ready. MODE={config['MODE']} BRAIN={config['BRAIN']} STRATEGY={config['STRATEGY']} SYMBOL={config['SYMBOL']}",
                "Start bot runs bot.py and shows its output here. MT5 can stay minimised."])
    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
