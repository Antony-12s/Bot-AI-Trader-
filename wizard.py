"""First-run setup: a few questions, the rest is read from the broker, then .env is written.

start.bat and train.bat run this when .env is missing; settings.bat runs it any time.
Enter accepts the value shown in brackets. Nothing here sends an order or spends money:
the result is always MODE=dry (paper trading) until MODE is changed by hand.
"""
import getpass
import sys

import brokers
from config import ENV_PATH, TIMEFRAME_SECONDS, load_config

TEMPLATE_PATH = ENV_PATH.with_name(".env.example")


def ask(question, default=""):
    shown = f" [{default}]" if default else ""
    answer = input(f"{question}{shown}: ").strip()
    return answer or default


def ask_number(question, default):
    while True:
        answer = ask(question, default)
        try:
            if float(answer) > 0:
                return answer
        except ValueError:
            pass
        print("  a number above 0, please")


def yes(question, default=False):
    answer = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not answer else answer in ("y", "yes")


def gold_like(names):
    """Symbol names that look like gold, the plainest first."""
    hits = [name for name in names if "XAU" in name.upper() or "GOLD" in name.upper()]
    return sorted(hits, key=lambda name: (len(name), name))


def spread_limit(spread_points, floor=50):
    """MAX_SPREAD_POINTS for a symbol whose spread is spread_points right now.

    Three times the current spread, never below the gold-sized default: BTCUSD or an exotic
    pair quoted with many decimals would otherwise be "spread too wide" on every candle.
    """
    return max(floor, 3 * int(spread_points))


def render_env(template, values):
    """The .env.example text with every KEY= line named in values filled in; comments stay."""
    lines = []
    for line in template.splitlines():
        key, separator, _ = line.partition("=")
        name = key.strip()
        if separator and not key.lstrip().startswith("#") and name in values:
            lines.append(f"{name}={values[name]}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def chat_ids(updates):
    """Distinct chat ids that messaged the bot, newest first."""
    seen = []
    for update in reversed(updates):
        chat = (update.get("message") or {}).get("chat") or {}
        if "id" in chat and str(chat["id"]) not in seen:
            seen.append(str(chat["id"]))
    return seen


def choose_symbol(broker):
    candidates = gold_like(broker.symbols())
    if candidates:
        print("Gold at this broker (any other symbol works too: EURUSD, GBPUSD, BTCUSD, US30 ... as Market Watch spells it):")
        for index, name in enumerate(candidates[:9], 1):
            print(f"  {index}. {name}")
        answer = ask("Pick a number, or type another symbol name exactly as Market Watch shows it", "1")
        name = candidates[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(candidates[:9]) else answer
    else:
        name = ask("Symbol name exactly as Market Watch shows it", "XAUUSD")
    if not broker.select_symbol(name) or broker.symbol(name) is None:
        print(f"  {name} is not a symbol at this broker, try again")
        return choose_symbol(broker)
    return name


def ask_api_key():
    """An Anthropic key that the API accepts, or None after three rejections."""
    import anthropic

    for attempt in range(3):
        key = getpass.getpass("ANTHROPIC_API_KEY (typing stays hidden): ").strip()
        if not key:
            return None
        try:
            anthropic.Anthropic(api_key=key).models.list(limit=1)
            print("  key accepted")
            return key
        except anthropic.AuthenticationError:
            print("  that key was rejected" + (", try again" if attempt < 2 else ""))
        except Exception as error:  # no network right now: keep the key, the bot will tell if it fails
            print(f"  could not check the key ({type(error).__name__}), keeping it anyway")
            return key
    return None


def ask_telegram():
    import bot  # the same urllib helper the bot uses

    token = ask("Bot token from @BotFather (Enter to skip Telegram)")
    if not token:
        return {}
    config = {"TELEGRAM_TOKEN": token}
    input("Now send any message to your bot in Telegram, then press Enter here...")
    try:
        ids = chat_ids(bot.telegram("getUpdates", config))
    except Exception as error:
        print(f"  could not reach Telegram ({type(error).__name__}); add TELEGRAM_CHAT_ID to .env later")
        return config
    if not ids:
        print("  no message seen yet; the bot prints 'ignored message from chat <id>' when you message it, put that id in .env")
        return config
    if len(ids) > 1:
        for index, chat_id in enumerate(ids, 1):
            print(f"  {index}. chat {chat_id}")
        answer = ask("Which chat is yours", "1")
        config["TELEGRAM_CHAT_ID"] = ids[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(ids) else answer
    else:
        config["TELEGRAM_CHAT_ID"] = ids[0]
    print(f"  using chat {config['TELEGRAM_CHAT_ID']}")
    return config


def main():
    print("Bot AI Trader setup. Enter accepts the value in brackets.\n")
    broker = brokers.load(load_config())
    if not broker.connect():
        print(broker.connection_hint())
        return 1
    try:
        account = broker.account()
        if account is not None:
            kind = "demo" if account.is_demo else "REAL MONEY"
            print(f"{broker.name} account {account.login} at {account.server} ({kind})\n")
        symbol = choose_symbol(broker)
        info = broker.symbol(symbol)
        values = {
            "SYMBOL": symbol,
            "CONTRACT_SIZE": f"{info.contract_size:g}",
            "FILLING": info.filling,
            "MAX_SPREAD_POINTS": str(spread_limit(info.spread_points)),
        }
        print(
            f"\n{symbol}: contract size {values['CONTRACT_SIZE']}, {info.digits} decimals,"
            f" minimum lot {info.min_lot:g}, filling {values['FILLING']}, spread now {info.spread_points} points"
            f" so MAX_SPREAD_POINTS={values['MAX_SPREAD_POINTS']} (all read from the broker)\n"
        )
        values["LOT"] = ask_number("Lot size per trade", f"{info.min_lot:g}")
        while True:
            values["TIMEFRAME"] = ask("Timeframe (M5 M15 M30 H1 H4)", "M15").upper()
            if values["TIMEFRAME"] in TIMEFRAME_SECONDS:
                break
            print("  use one of: " + " ".join(TIMEFRAME_SECONDS))
        values["MAX_DAILY_LOSS"] = ask_number("Stop opening trades for the day after losing this much (account currency)", "20")
        if yes("\nLet Claude (AI) judge the trades? Needs an API key from platform.claude.com, billed per use"):
            key = ask_api_key()
            if key:
                values["ANTHROPIC_API_KEY"] = key
                values["BRAIN"] = "hybrid"
                values["AI_BUDGET_USD"] = ask_number("Most USD the AI may spend per day", "5")
            else:
                print("  no key: staying with the free rules brain, switch BRAIN in .env later")
        if yes("\nControl the bot from Telegram?"):
            values.update(ask_telegram())
        ENV_PATH.write_text(render_env(TEMPLATE_PATH.read_text(encoding="utf-8"), values), encoding="utf-8")
        print(f"\nSaved {ENV_PATH.name}.")
        print("MODE=dry: paper trading only. Nothing goes to the broker and no real money moves")
        print("until you change MODE to demo or live in .env yourself. Run settings.bat to redo this.")
        return 0
    finally:
        broker.shutdown()


if __name__ == "__main__":
    sys.exit(main())
