"""Log the bot into a cTrader ID once: OAuth in the browser, then CTRADER_* lines into .env.

    python ctrader_auth.py

Before this, register an app at https://openapi.ctrader.com (Open API > Applications):
the redirect URI must be exactly  http://localhost:8765/callback  and the app must be
activated. This script opens the cTrader login page, catches the redirect on localhost
(or lets you paste the redirected URL), swaps the code for tokens, lists the trading
accounts that cTrader ID owns and writes the chosen one into .env. Nothing here places
an order, and the tokens stay in .env, which git ignores.
"""
import getpass
import http.server
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from config import ENV_PATH, load_config, save_env_values

AUTH_URL = "https://openapi.ctrader.com/apps/auth"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
CATCH_SECONDS = 300


def authorize_url(client_id, redirect_uri=REDIRECT_URI):
    query = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "scope": "trading"})
    return f"{AUTH_URL}?{query}"


def code_from_url(url):
    """The authorization code in a redirected URL (or a bare code typed in)."""
    url = url.strip()
    if not url:
        return ""
    query = urllib.parse.urlparse(url).query
    codes = urllib.parse.parse_qs(query).get("code")
    return codes[0] if codes else ("" if "://" in url else url)


def token_request(client_id, client_secret, code=None, refresh_token=None, redirect_uri=REDIRECT_URI):
    """The token endpoint URL for an authorization code, or for a refresh."""
    params = {"client_id": client_id, "client_secret": client_secret}
    if refresh_token:
        params.update(grant_type="refresh_token", refresh_token=refresh_token)
    else:
        params.update(grant_type="authorization_code", code=code, redirect_uri=redirect_uri)
    return f"{TOKEN_URL}?{urllib.parse.urlencode(params)}"


def parse_tokens(payload):
    """access and refresh token from the endpoint's JSON (it answers in camelCase, accept snake_case too)."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    error = data.get("errorCode") or data.get("error")
    if error:
        raise SystemExit(f"cTrader refused the token request: {error} {data.get('description') or data.get('error_description') or ''}".strip())
    access = data.get("accessToken") or data.get("access_token")
    refresh = data.get("refreshToken") or data.get("refresh_token") or ""
    if not access:
        raise SystemExit(f"no access token in the reply: {data}")
    return {"CTRADER_ACCESS_TOKEN": access, "CTRADER_REFRESH_TOKEN": refresh}


def fetch_tokens(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return parse_tokens(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"token request failed: HTTP {error.code} {error.read()[:300]!r}")


class _Catcher(http.server.BaseHTTPRequestHandler):
    code = ""
    done = threading.Event()

    def do_GET(self):  # noqa: N802 (http.server naming)
        _Catcher.code = code_from_url(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Logged in. You can close this tab and go back to the terminal." if _Catcher.code else b"No code in this request.")
        if _Catcher.code:
            _Catcher.done.set()

    def log_message(self, *args):  # keep the terminal quiet
        pass


def catch_code(seconds=CATCH_SECONDS):
    """Wait for the browser to be redirected to localhost; '' when nothing arrived in time."""
    try:
        server = http.server.HTTPServer(("localhost", REDIRECT_PORT), _Catcher)
    except OSError as error:
        print(f"  cannot listen on port {REDIRECT_PORT} ({error}); paste the redirected URL instead")
        return ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Catcher.done.wait(seconds)
    server.shutdown()
    return _Catcher.code


def list_accounts(client_id, client_secret, access_token):
    """(ctidTraderAccountId, isLive, traderLogin) of every account the token covers; needs the gateway."""
    import broker_ctrader
    from ctrader_open_api.messages import OpenApiMessages_pb2 as api

    accounts = []
    for env in ("demo", "live"):  # both gateways should list every account of the cTrader ID; ask both to be sure
        client = broker_ctrader.Client(broker_ctrader.HOSTS[env])
        try:
            client.open()
            client.request(api.ProtoOAApplicationAuthReq(clientId=client_id, clientSecret=client_secret))
            reply = client.request(api.ProtoOAGetAccountListByAccessTokenReq(accessToken=access_token))
            for account in reply.ctidTraderAccount:
                entry = (int(account.ctidTraderAccountId), bool(account.isLive), int(account.traderLogin))
                if entry not in accounts:
                    accounts.append(entry)
        except (broker_ctrader.ApiError, OSError) as error:
            print(f"  {env} gateway: {error}")
        finally:
            client.close()
    return accounts


def choose_account(accounts, ask=input):
    if not accounts:
        raise SystemExit("this cTrader ID has no trading accounts yet; open a demo account in cTrader first")
    print("Trading accounts of this cTrader ID:")
    for index, (account_id, is_live, login) in enumerate(accounts, 1):
        print(f"  {index}. account {login}  ({'LIVE' if is_live else 'demo'})  id {account_id}")
    while True:
        answer = ask("Which one should the bot trade? [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(accounts):
            return accounts[int(answer) - 1]
        print("  type the number of the account")


def main():
    config = load_config()
    print("cTrader login for Bot AI Trader\n")
    client_id = config["CTRADER_CLIENT_ID"] or input("Client ID of your app at openapi.ctrader.com: ").strip()
    client_secret = config["CTRADER_CLIENT_SECRET"] or getpass.getpass("Client Secret (typing stays hidden): ").strip()
    if not client_id or not client_secret:
        raise SystemExit("both Client ID and Client Secret are needed; register an app at https://openapi.ctrader.com")
    url = authorize_url(client_id)
    print(f"\nOpening the cTrader login page. If no browser opens, visit this URL yourself:\n{url}\n")
    webbrowser.open(url)
    print(f"Waiting up to {CATCH_SECONDS // 60} minutes for the login to come back to {REDIRECT_URI} ...")
    code = catch_code()
    if not code:
        code = code_from_url(input("Paste the URL the browser was sent to (it contains code=...): "))
    if not code:
        raise SystemExit("no authorization code; run this again")
    tokens = fetch_tokens(token_request(client_id, client_secret, code=code))
    print("  tokens received")
    account_id, is_live, login = choose_account(list_accounts(client_id, client_secret, tokens["CTRADER_ACCESS_TOKEN"]))
    values = {
        "BROKER": "ctrader",
        "CTRADER_ENV": "live" if is_live else "demo",
        "CTRADER_CLIENT_ID": client_id,
        "CTRADER_CLIENT_SECRET": client_secret,
        "CTRADER_ACCOUNT_ID": str(account_id),
        **tokens,
    }
    save_env_values(values)
    print(f"\nSaved to {ENV_PATH.name}: account {login} ({values['CTRADER_ENV']}), BROKER=ctrader.")
    print("MODE in .env is untouched: dry stays dry. Run the wizard (settings.bat / python wizard.py) to pick the symbol.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
