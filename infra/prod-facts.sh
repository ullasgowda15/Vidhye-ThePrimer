#!/usr/bin/env bash
#
# Report how this box is actually running. Read-only — changes nothing.
#
#   bash infra/prod-facts.sh                 # on the box
#   gcloud compute ssh app-server --zone=asia-south1-a --command='bash -s' < infra/prod-facts.sh
#
# **Why this exists.** Every deploy conversation so far has re-derived the same
# facts by guessing and being wrong: the runbook says the app lives in
# `/srv/primer` and it lives in `~/app`; it says Caddy may be in compose and it is
# on the host; a check against `127.0.0.1:8000` returned 301 and looked like a
# missing route when it was `SECURE_SSL_REDIRECT` firing before URL resolution.
# Guessing wastes a round trip per fact, and a round trip here is a person typing
# on a phone-sized terminal. This asks the box instead, once.
#
# Its output is meant to be **pasted back verbatim**, so it is disciplined about
# two things:
#
# * **It never prints a secret.** Env files are reported as key names with
#   `set`/`empty` — never values — and the one place a value is unavoidable
#   (`ALLOWED_HOSTS`, `DEBUG`) is a value that is safe to say out loud. If you can
#   see a credential in this output, that is a bug in this script; tell me.
# * **It never fails the run.** Every probe is `|| true`-guarded, because a box
#   missing docker should still report everything else rather than exiting on the
#   first absent command.

APP_DIR="${APP_DIR:-$HOME/app}"

hr()  { printf '\n=== %s %s\n' "$1" "$(printf '=%.0s' $(seq 1 $((60 - ${#1}))))"; }
say() { printf '  %s\n' "$*"; }

echo "prod-facts  $(date -u +%Y-%m-%dT%H:%M:%SZ)"

hr "HOST"
say "hostname   $(hostname)"
say "os         $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo unknown)"
say "kernel     $(uname -r)"
say "user       $(whoami)"
say "uptime     $(uptime -p 2>/dev/null || true)"
say "cpu/mem    $(nproc) vCPU / $(free -h 2>/dev/null | awk '/^Mem:/{print $2}')"
say "disk       $(df -h / | awk 'NR==2{print $3" used of "$2" ("$5")"}')"

hr "GCP"
# The metadata server, so this works without gcloud installed on the box.
MD="http://metadata.google.internal/computeMetadata/v1"
H=(-H "Metadata-Flavor: Google" -s --max-time 2)
say "instance   $(curl "${H[@]}" "$MD/instance/name" || echo n/a)"
say "zone       $(curl "${H[@]}" "$MD/instance/zone" | awk -F/ '{print $NF}' || echo n/a)"
say "type       $(curl "${H[@]}" "$MD/instance/machine-type" | awk -F/ '{print $NF}' || echo n/a)"
say "project    $(curl "${H[@]}" "$MD/project/project-id" || echo n/a)"
say "ext ip     $(curl "${H[@]}" "$MD/instance/network-interfaces/0/access-configs/0/external-ip" || echo n/a)"

hr "APP DIRECTORY"
say "APP_DIR    $APP_DIR"
if [[ -d "$APP_DIR" ]]; then
    say "exists     yes"
    if git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        say "git        YES — deploys can be 'git pull'"
        say "remote     $(git -C "$APP_DIR" remote get-url origin 2>/dev/null || echo none)"
        say "branch     $(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
        say "commit     $(git -C "$APP_DIR" log --oneline -1 2>/dev/null)"
        say "dirty      $(git -C "$APP_DIR" status --porcelain 2>/dev/null | wc -l) modified/untracked paths"
        say "behind     $(git -C "$APP_DIR" rev-list --count HEAD..@{u} 2>/dev/null || echo 'no upstream / not fetched')"
    else
        say "git        NO — this is a copied tree, deploys are NOT 'git pull'"
    fi
    say "venv       $([[ -x "$APP_DIR/.venv/bin/python" ]] && "$APP_DIR/.venv/bin/python" -V 2>&1 || echo 'no .venv/bin/python')"
    say "frontend   $([[ -d "$APP_DIR/frontend/dist" ]] && echo "dist present, $(find "$APP_DIR/frontend/dist" -type f 2>/dev/null | wc -l) files, built $(date -r "$APP_DIR/frontend/dist" -u +%Y-%m-%dT%H:%MZ 2>/dev/null)" || echo 'NO dist — PWA not built here')"
    say "top level  $(ls -A "$APP_DIR" | tr '\n' ' ')"
else
    say "exists     NO — wrong APP_DIR. Re-run as: APP_DIR=/path bash prod-facts.sh"
fi

hr "PROCESSES / SERVICES"
say "-- systemd units mentioning primer/caddy/uvicorn/celery/gunicorn --"
systemctl list-units --type=service --state=running --no-pager --plain 2>/dev/null \
    | grep -iE 'primer|caddy|uvicorn|celery|gunicorn|postgres|redis|docker' | sed 's/^/    /' || say "    none"
say "-- docker containers --"
if command -v docker >/dev/null 2>&1; then
    (docker ps --format '    {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo "    docker present but not readable by $(whoami) — try with sudo")
else
    say "    docker not installed"
fi
say "-- listening ports --"
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | awk 'NR>1{print "    "$4"  "$NF}' | sort -u | head -20

hr "WEB TIER"
say "-- who answers :8000 --"
for path in /api/health/ /admax /admin/metrics; do
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
        -H 'X-Forwarded-Proto: https' -H "Host: ${PRIMER_DOMAIN:-localhost}" \
        "http://127.0.0.1:8000$path" 2>/dev/null || echo 000)
    say "    $code  $path"
done
say "    (302 on /admax = route deployed. 404 = old code. 000 = nothing listening.)"

hr "CADDY"
CADDYFILE="$(systemctl show caddy -p ExecStart --no-pager 2>/dev/null | grep -oP '(?<=--config )\S+' | head -1)"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
say "loads      $CADDYFILE"
say "running    $(systemctl is-active caddy 2>/dev/null || echo 'not a systemd service')"
say "version    $(caddy version 2>/dev/null | head -1 || echo 'caddy not on PATH')"
if [[ -r "$CADDYFILE" ]]; then
    say "-- config (no secrets in a Caddyfile; paste it back) --"
    sed 's/^/    /' "$CADDYFILE"
    say "-- routes for /admax? --"
    grep -c admax "$CADDYFILE" | sed 's/^/    matches: /'
else
    say "    not readable as $(whoami) — re-run this section with sudo"
fi

hr "ENV (NAMES ONLY — no values are printed)"
for candidate in "$APP_DIR/.env" "$APP_DIR/backend/.env" "$HOME/.env" /etc/primer/.env; do
    [[ -f "$candidate" ]] || continue
    say "file       $candidate  ($(stat -c '%a %U:%G' "$candidate" 2>/dev/null))"
    # Names and set/empty only. A value never leaves the box.
    awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/ {
        name=$1; rest=substr($0, index($0,"=")+1);
        gsub(/^[ \t]+|[ \t]+$/, "", rest);
        printf "    %-38s %s\n", name, (length(rest) ? "set" : "EMPTY")
    }' "$candidate"
    # The two values that are safe to say out loud and that decide behaviour.
    say "    -- safe values --"
    grep -E '^(DEBUG|ALLOWED_HOSTS|DJANGO_SETTINGS_MODULE|MEDIA_STORAGE|AI_PROVIDER_ORDER|GOOGLE_CLOUD_LOCATION|SECURE_SSL_REDIRECT)=' "$candidate" 2>/dev/null | sed 's/^/    /'
done
[[ -f "$APP_DIR/.env" || -f "$APP_DIR/backend/.env" ]] || say "no .env found in the usual places — where does the app read config from?"

hr "DATABASE"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q db; then
    say "postgres in docker: $(docker ps --filter name=db --format '{{.Image}} {{.Status}}' 2>/dev/null)"
fi
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    say "-- unapplied migrations (empty list = fully migrated) --"
    (cd "$APP_DIR/backend" && "$APP_DIR/.venv/bin/python" manage.py showmigrations --plan 2>/dev/null \
        | grep -v '\[X\]' | head -20 | sed 's/^/    /') || say "    could not run manage.py"
fi

hr "DEPLOY ARTEFACTS"
say "-- anything that looks like a deploy script --"
ls -1 "$APP_DIR"/*.sh "$APP_DIR"/infra/*.sh "$HOME"/*.sh 2>/dev/null | sed 's/^/    /' || say "    none"
say "-- systemd unit files for this app --"
ls -1 /etc/systemd/system/*.service 2>/dev/null | grep -viE 'systemd|dbus|network|ssh|snap' | sed 's/^/    /' || say "    none"

hr "END"
echo "Paste everything above back. It contains no credential values."
