#!/usr/bin/env bash
#
# What is production actually running, and is it answering? Read-only.
#
#     make deploy-status
#     gcloud compute ssh app-server --zone=asia-south1-a --command='bash -s' < infra/deploy-status.sh
#
# The question this answers is the one that used to need four commands and a
# reminder about which of them lies. Now that pushing to main deploys, it is also
# the question "did my push land?" — and the answer is a SHA you can compare with
# `git rev-parse HEAD` on your laptop, not a vibe about whether CI was green.
#
# Prints no secrets, changes nothing, and never exits non-zero on a missing probe
# — same discipline as `prod-facts.sh`, and for the same reason: its output is
# meant to be pasted back verbatim.

APP_DIR="${APP_DIR:-$HOME/app}"

say() { printf '  %-10s %s\n' "$1" "$2"; }

echo "deploy-status  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

cd "$APP_DIR" 2>/dev/null || { echo "  no app directory at $APP_DIR"; exit 0; }

say "commit" "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
say "subject" "$(git log -1 --format=%s 2>/dev/null || echo unknown)"
say "authored" "$(git log -1 --format=%ci 2>/dev/null || echo unknown)"
say "branch" "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

if [ -f .deploy/last-deploy.json ]; then
    echo
    echo "  last deploy recorded by infra/deploy.sh:"
    sed 's/^/    /' .deploy/last-deploy.json
else
    echo
    echo "  no .deploy/last-deploy.json — this box has not been deployed by the"
    echo "  pipeline yet (or was last updated by a hand-typed git pull)."
fi

# Behind the remote? The question after "did my push land" is "how far behind is
# it", and `git fetch` is read-only.
git fetch --quiet --prune origin main 2>/dev/null || true
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
echo
say "vs main" "$BEHIND commit(s) behind origin/main"

echo
HOST_HEADER="$(sed -n 's/^ALLOWED_HOSTS=//p' .env 2>/dev/null | tail -n 1 | cut -d, -f1)"
[ -n "$HOST_HEADER" ] || HOST_HEADER="primer.yourcaly.com"

# The headers are the whole point. Without `X-Forwarded-Proto: https` this is a
# 301 from SecurityMiddleware whether the app is healthy or gone.
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -H 'X-Forwarded-Proto: https' -H "Host: $HOST_HEADER" \
    http://127.0.0.1:8000/api/health/ 2>/dev/null || echo 000)"
say "api" "$CODE  (200 = serving; 000 = nothing listening)"

PUBLIC="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    "https://$HOST_HEADER/api/health/" 2>/dev/null || echo 000)"
say "via caddy" "$PUBLIC  (200 = the reverse proxy agrees)"

if [ -f frontend/dist/index.html ]; then
    say "pwa" "frontend/dist/index.html present ($(find frontend/dist -type f 2>/dev/null | wc -l | tr -d ' ') files)"
else
    say "pwa" "MISSING — Caddy has nothing to serve on the catch-all"
fi

echo
echo "  containers:"
# Indented with sed rather than in the format string: docker strips leading
# whitespace out of --format, so the alignment silently disappears.
docker ps --filter name=primer --format '{{.Names}}  {{.Status}}' 2>/dev/null \
    | sed 's/^/    /' \
    || echo "    (docker not reachable as this user)"

echo
say "disk" "$(df -h / | awk 'NR==2{print $4" free of "$2" ("$5" used)"}')"
