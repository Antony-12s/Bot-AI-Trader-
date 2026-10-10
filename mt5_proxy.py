"""Runs broker.py in a helper process, so a slow MT5 never freezes the app window.

The MetaTrader5 library keeps Python's GIL while it waits on the terminal (measured on
2026-10-07: another thread got 1 tick in 6.6 s during mt5.initialize), and it can wait up to
its timeout. In the app process that stalled the WebView window into "Not Responding". In
its own process only that process waits; the app waits on a pipe, which frees the GIL.

The helper is `python mt5_proxy.py` (TradeBot.exe mt5_proxy.py when installed), like bot.py;
requests and replies are pickled over its stdin / stdout.
"""
import pickle
import queue
import subprocess
import sys
import threading
from pathlib import Path

INLINE = False  # tests: call broker directly, in this process
CALL_TIMEOUT = 90  # seconds; a login may take the library's full 60 s
lock = threading.Lock()  # one call at a time, like broker.LOCK
worker = {"process": None, "replies": None}


def _command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "mt5_proxy.py"]  # launcher.py runs this module
    return [sys.executable, str(Path(__file__))]


def _start():
    process = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    replies = queue.Queue()

    def read():  # a thread, so a stuck helper can be timed out instead of blocking forever
        try:
            while True:
                replies.put(pickle.load(process.stdout))
        except (EOFError, OSError, pickle.UnpicklingError):
            replies.put((False, ("RuntimeError", "the MetaTrader 5 helper stopped")))

    threading.Thread(target=read, daemon=True).start()
    worker.update(process=process, replies=replies)


def call(name, *args, **kwargs):
    """broker.<name>(*args, **kwargs), run in the helper process. Its ValueErrors come back as ValueError."""
    if INLINE:
        import broker
        return getattr(broker, name)(*args, **kwargs)
    with lock:
        if worker["process"] is None or worker["process"].poll() is not None:
            _start()
        process = worker["process"]
        try:
            pickle.dump((name, args, kwargs), process.stdin)
            process.stdin.flush()
            ok, value = worker["replies"].get(timeout=CALL_TIMEOUT)
        except (queue.Empty, OSError):
            process.kill()  # stuck or gone: the next call starts a fresh helper
            worker.update(process=None, replies=None)
            raise ValueError("MetaTrader 5 did not answer in time; it may be waiting on a dialog: press Show MT5") from None
    if ok:
        return value
    kind, text = value
    raise ValueError(text) if kind == "ValueError" else RuntimeError(f"{kind}: {text}")


def stop():
    """End the helper (tests, and anything that wants a fresh one)."""
    with lock:
        process = worker["process"]
        worker.update(process=None, replies=None)
    if process is not None and process.poll() is None:
        process.kill()
        process.wait()


def serve():
    """The helper's loop: read a request, call broker, write the reply. stdout carries only replies."""
    requests, replies = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must not corrupt the pickle stream
    import broker
    while True:
        try:
            name, args, kwargs = pickle.load(requests)
        except EOFError:
            return  # the app closed: so does the helper
        try:
            reply = (True, getattr(broker, name)(*args, **kwargs))
        except Exception as error:  # sent back and raised in the app; the helper keeps serving
            reply = (False, (type(error).__name__, str(error)))
        pickle.dump(reply, replies)
        replies.flush()


if __name__ == "__main__":
    serve()
