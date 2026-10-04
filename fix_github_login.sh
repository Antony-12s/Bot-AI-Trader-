#!/bin/sh
# Mac / Linux teammate gets 403 or "Permission denied to <someone else>" on git push:
# the computer remembered another GitHub account. This forgets saved GitHub logins and
# signs you in again. Run it from Terminal:   sh fix_github_login.sh
# Sign in with the account that was invited to the repository.
cd "$(dirname "$0")" || exit 1
REPO_URL="https://github.com/Antony-12s/Bot-AI-Trader-.git"

echo "Forgetting saved GitHub logins ..."
printf 'protocol=https\nhost=github.com\n\n' | git credential reject 2>/dev/null
printf 'protocol=https\nhost=github.com\n\n' | git credential-osxkeychain erase 2>/dev/null

# An SSH remote uses an SSH key, and a key belongs to exactly one GitHub account.
# HTTPS lets the sign-in below decide who you are.
case "$(git remote get-url origin 2>/dev/null)" in
    git@github.com:*|ssh://*) git remote set-url origin "$REPO_URL"; echo "Switched the remote from SSH to HTTPS." ;;
esac

if command -v gh >/dev/null 2>&1; then
    gh auth logout -h github.com >/dev/null 2>&1
    echo "Signing in through the browser: pick the account that was invited."
    gh auth login -h github.com -p https -w && gh auth setup-git
else
    echo
    echo "GitHub CLI (gh) is not installed, so there are two ways to sign in:"
    echo "  1. Install it with:  brew install gh   and run this script again (browser sign-in, easiest)."
    echo "  2. Or on the next git push type the invited username and, as the password, a Personal"
    echo "     Access Token from github.com > Settings > Developer settings > Tokens (classic), tick 'repo'."
fi

echo
if git fetch origin; then
    echo "Signed in. git push works now."
else
    echo "Still refused. At github.com check the account name at the top right: it must be the invited one."
fi
