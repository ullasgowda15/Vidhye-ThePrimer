#!/usr/bin/env bash
#
# Run something on app-server, over whichever transport actually works here.
#
#     bash infra/remote.sh 'cd ~/app && git log -1'     # a command
#     bash infra/remote.sh < some-script.sh             # a script, over stdin
#     bash infra/remote.sh --target                     # print user@host and exit
#
# ---------------------------------------------------------------------------
# Why this exists rather than everything just calling `gcloud compute ssh`
# ---------------------------------------------------------------------------
# On Windows, `gcloud compute ssh` does not use OpenSSH — it drives **PuTTY**
# (`plink.exe`). Plink asks about an uncached host key with a prompt that wants a
# real console, and every script in this directory talks to the box by piping a
# script into `bash -s`. Piping is not a console, so the prompt is answered by
# whatever is on stdin, the connection is abandoned, and what you see is a
# command that exits instantly having done nothing. That cost an afternoon on
# 21 August 2026.
#
# OpenSSH has none of that problem, and gcloud has already put a usable key on
# disk at `~/.ssh/google_compute_engine` as a side effect of its first run. So:
# use OpenSSH when it works, fall back to `gcloud compute ssh` when it does not
# (a fresh machine, or a box only reachable through IAP), and say which.
#
# ---------------------------------------------------------------------------
# The username trap that goes with it
# ---------------------------------------------------------------------------
# `gcloud compute ssh` logs in as your **local** username unless told otherwise.
# On this laptop that is `DELL`, so its first run registered the new key in
# project metadata as `DELL:ssh-rsa …` — and the box's app lives in
# `/home/ullasgowda3523`. The login succeeds and lands in the wrong home, or
# fails outright.
#
# So the remote user is derived from the **gcloud account**
# (`ullasgowda3523@gmail.com` -> `ullasgowda3523`), which is GCE's own convention,
# and `REMOTE_USER=` overrides it.

set -euo pipefail

INSTANCE="${INSTANCE:-app-server}"
ZONE="${ZONE:-asia-south1-a}"
GCLOUD_KEY="${GCLOUD_KEY:-$HOME/.ssh/google_compute_engine}"
REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_USER="${REMOTE_USER:-}"

note() { [ "${QUIET:-0}" = "1" ] || printf '%s\n' "$*" >&2; }
die()  { printf 'remote.sh: %s\n' "$*" >&2; exit 1; }

gcloud_bin() {
    if command -v gcloud >/dev/null 2>&1; then
        echo gcloud
    # The Windows installer puts gcloud on the *user* PATH, which a shell started
    # before the install will not have. Finding it anyway saves a "restart your
    # terminal" round trip.
    elif [ -f "${LOCALAPPDATA:-}/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd" ]; then
        echo "$LOCALAPPDATA/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
    else
        echo ""
    fi
}
GCLOUD="$(gcloud_bin)"

if [ -z "$REMOTE_HOST" ]; then
    [ -n "$GCLOUD" ] || die "gcloud is not installed, and REMOTE_HOST is not set"
    REMOTE_HOST="$("$GCLOUD" compute instances describe "$INSTANCE" --zone "$ZONE" \
        --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null | tr -d '\r')"
    [ -n "$REMOTE_HOST" ] || die "could not read $INSTANCE's external IP from gcloud"
fi

if [ -z "$REMOTE_USER" ] && [ -n "$GCLOUD" ]; then
    REMOTE_USER="$("$GCLOUD" config get-value account 2>/dev/null | tr -d '\r' | cut -d@ -f1)"
fi
[ -n "$REMOTE_USER" ] || die "could not work out the remote username; set REMOTE_USER="

if [ "${1:-}" = "--target" ]; then
    printf '%s@%s\n' "$REMOTE_USER" "$REMOTE_HOST"
    exit 0
fi

# ---------------------------------------------------------------------------
# Pick a transport
# ---------------------------------------------------------------------------
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new)
TRANSPORT="${TRANSPORT:-}"

if [ -z "$TRANSPORT" ]; then
    # `</dev/null` is load-bearing. Without it this probe inherits our stdin, and
    # ssh forwards stdin to the remote command — so probing swallows the very
    # script the caller piped in, and the real connection then runs an empty one.
    # Silently: you get a clean exit and no output.
    if [ -f "$GCLOUD_KEY" ] && command -v ssh >/dev/null 2>&1 \
       && ssh -i "$GCLOUD_KEY" "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" true </dev/null 2>/dev/null; then
        TRANSPORT=ssh
    else
        TRANSPORT=gcloud
    fi
fi

run_ssh() {
    if [ "$#" -gt 0 ]; then
        ssh -i "$GCLOUD_KEY" "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" "$@"
    else
        ssh -i "$GCLOUD_KEY" "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" 'bash -s'
    fi
}

run_gcloud() {
    [ -n "$GCLOUD" ] || die "no OpenSSH route and no gcloud either"
    # `$REMOTE_USER@$INSTANCE`, never the bare instance name — see the username
    # trap in the header.
    if [ "$#" -gt 0 ]; then
        "$GCLOUD" compute ssh "$REMOTE_USER@$INSTANCE" --zone "$ZONE" --command "$*"
    else
        "$GCLOUD" compute ssh "$REMOTE_USER@$INSTANCE" --zone "$ZONE" --command 'bash -s'
    fi
}

note "remote.sh: $REMOTE_USER@$REMOTE_HOST via $TRANSPORT"
case "$TRANSPORT" in
    ssh)    run_ssh "$@" ;;
    gcloud) run_gcloud "$@" ;;
    *)      die "unknown TRANSPORT=$TRANSPORT (use 'ssh' or 'gcloud')" ;;
esac
