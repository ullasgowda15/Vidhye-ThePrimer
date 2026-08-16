#!/usr/bin/env bash
#
# Make /admax reachable in production.
#
#   bash infra/admax-setup.sh
#   bash infra/admax-setup.sh --enroll you@example.com
#
# The problem this fixes: the site's Caddyfile proxies only `/api/*` to Django.
# Everything else falls through to the SPA catch-all (`try_files {path}
# /index.html`), so a request for /admax reaches the built PWA and React Router
# answers "404 Not Found" — Django never sees it. The dashboard is fine; the edge
# never routes to it.
#
# What this does, in order, stopping at the first thing that fails:
#
#   1. finds the Caddyfile systemd actually loads (not the one you assume);
#   2. backs it up, and inserts a `handle /admax*` block above the catch-all —
#      `handle` blocks are mutually exclusive, so anything below a catch-all is
#      dead code;
#   3. validates, and **restores the backup if validation fails** — a reverse
#      proxy that will not parse is an outage, so a bad edit never reaches reload;
#   4. reloads Caddy (reload, not restart: zero downtime, no dropped TLS);
#   5. proves the fix with a real request, rather than reporting that a command
#      exited 0.
#
# Safe to re-run. Every step checks for the work already being done and skips it,
# so this is also the script to run on a future box.

set -euo pipefail

ENROLL_EMAIL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --enroll) ENROLL_EMAIL="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

APP_DIR="${APP_DIR:-$HOME/app}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }

bold "1. Locating the Caddyfile systemd actually loads"

# Read it off the unit rather than assuming /etc/caddy/Caddyfile. A config that
# is edited but not loaded is the single most common way this fix appears to do
# nothing, so the path is discovered instead of guessed.
CADDYFILE="$(systemctl show caddy -p ExecStart --no-pager 2>/dev/null \
    | grep -oP '(?<=--config )\S+' | head -1 || true)"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"

[[ -f "$CADDYFILE" ]] || die "no Caddyfile at $CADDYFILE — is Caddy installed on this host?"
ok "$CADDYFILE"

DOMAIN="$(grep -oP '^\s*\K[a-z0-9.-]+\.[a-z]{2,}(?=\s*\{)' "$CADDYFILE" | head -1 || true)"
[[ -n "$DOMAIN" ]] && ok "site: $DOMAIN" || warn "could not read the domain; will skip the HTTPS check"

bold "2. Checking Django is up and has the route"

# With SECURE_SSL_REDIRECT=1, a plain-HTTP request is 301'd by SecurityMiddleware
# *before* URL resolution — so without these headers this check returns 301
# whether or not /admax exists, which is worse than no check at all.
DJANGO_CODE="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H 'X-Forwarded-Proto: https' -H "Host: ${DOMAIN:-localhost}" \
    "http://127.0.0.1:${BACKEND_PORT}/admax" 2>/dev/null || echo 000)"

case "$DJANGO_CODE" in
    302) ok "Django answers 302 (redirect to the sign-in) — the route is deployed" ;;
    404) die "Django answers 404: this box is running code without /admax.
        Run:  cd $APP_DIR && git pull && make migrate
        then restart the web tier, and run this script again." ;;
    000) die "nothing is listening on 127.0.0.1:${BACKEND_PORT} — start the web tier first." ;;
    *)   warn "Django answered $DJANGO_CODE (expected 302). Continuing, but check this." ;;
esac

bold "3. Adding the /admax route to Caddy"

BACKUP="${CADDYFILE}.bak.$(date +%Y%m%d-%H%M%S)"
sudo cp "$CADDYFILE" "$BACKUP"
ok "backed up to $BACKUP"

INSERTED="$(sudo python3 - "$CADDYFILE" "$BACKEND_PORT" <<'PY'
import re, sys, pathlib

path, port = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text()

if re.search(r'handle\s+/admax', text):
    print("skip")
    raise SystemExit

# The catch-all is `handle {` with no path matcher. The new block goes *above*
# it: handle blocks are mutually exclusive and the catch-all matches everything,
# so a block placed after it can never run.
match = re.search(r'^([ \t]*)handle\s*\{', text, re.M)
if not match:
    print("nocatchall")
    raise SystemExit

indent = match.group(1)
block = (
    f"{indent}handle /admax* {{\n"
    f"{indent}    reverse_proxy 127.0.0.1:{port}\n"
    f"{indent}}}\n\n"
)
path.write_text(text[: match.start()] + block + text[match.start() :])
print("inserted")
PY
)"

case "$INSERTED" in
    inserted)   ok "inserted a 'handle /admax*' block above the catch-all" ;;
    skip)       ok "already present — nothing to change" ;;
    nocatchall) die "no catch-all 'handle {' block found in $CADDYFILE.
        Your config has a shape this script does not recognise. Add this inside
        the '$DOMAIN' block, above whichever block serves the SPA:

            handle /admax* {
                reverse_proxy 127.0.0.1:${BACKEND_PORT}
            }" ;;
    *)          die "unexpected result from the edit step: '$INSERTED'" ;;
esac

bold "4. Validating, then reloading"

# The rollback is the point. A Caddyfile that does not parse takes the whole site
# down, and this script must never be the cause of that.
if ! sudo caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
    sudo cp "$BACKUP" "$CADDYFILE"
    die "the edited config failed validation — original restored from $BACKUP.
        Nothing was reloaded and the site is untouched. Run:
          sudo caddy validate --config $CADDYFILE
        to see what it objects to."
fi
ok "config is valid"

# Confirm the rule survived adaptation. Validation passing does not prove our
# block compiled into a route — a block in the wrong site would validate fine.
if [[ "$(sudo caddy adapt --config "$CADDYFILE" 2>/dev/null | grep -c admax || true)" == "0" ]]; then
    warn "the compiled config contains no /admax rule — check it is in the right site block"
fi

sudo systemctl reload caddy
ok "caddy reloaded"

bold "5. Proving it works"

if [[ -n "$DOMAIN" ]]; then
    sleep 1
    VIA_CADDY="$(curl -sS -o /dev/null -w '%{http_code}' "https://${DOMAIN}/admax" 2>/dev/null || echo 000)"
    case "$VIA_CADDY" in
        302) ok "https://${DOMAIN}/admax -> 302. Routing is fixed." ;;
        200) die "still 200: Caddy is serving the PWA for /admax.
        The block is in the file but not taking effect — most likely it is in a
        different site block than the one serving ${DOMAIN}. Send me the output of:
          sudo cat $CADDYFILE" ;;
        *)   warn "got $VIA_CADDY — check https://${DOMAIN}/admax by hand" ;;
    esac
fi

bold "6. Second factor"

# Mirrors `make migrate`: manage.py runs from the repo venv. Falls back to the
# compose service if this box runs the web tier in Docker instead.
run_manage() {
    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
        (cd "$APP_DIR/backend" && "$APP_DIR/.venv/bin/python" manage.py "$@")
    else
        docker compose -f "$APP_DIR/infra/docker-compose.yml" exec -T web python manage.py "$@"
    fi
}

if [[ -n "$ENROLL_EMAIL" ]]; then
    echo
    # `|| true`: being enrolled already is a normal outcome, not a failure, and
    # must not abort the script under `set -e` after everything else succeeded.
    run_manage admax_enroll "$ENROLL_EMAIL" || true
    echo
    warn "the seed above is shown ONCE and is not recoverable — add it to your"
    warn "authenticator app now. If it is lost, re-run with --reset."
else
    echo "  Not enrolled yet? Run:"
    echo "    bash infra/admax-setup.sh --enroll you@example.com"
fi

echo
bold "Done."
[[ -n "$DOMAIN" ]] && echo "  Sign in at https://${DOMAIN}/admax"
echo "  If the browser still shows the React 404 after a 302 above, it is the PWA"
echo "  service worker — open the URL in a private window."
