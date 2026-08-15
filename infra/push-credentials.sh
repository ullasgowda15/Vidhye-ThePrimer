#!/bin/sh
# Copy this machine's working provider credentials to a deployment (§16.10).
#
#     sh infra/push-credentials.sh ullasgowda3523@34.180.8.71 [remote-app-dir]
#
# `DRY_RUN=1` prints which settings would be sent and touches nothing. Run that
# first — it is the whole script except the four commands that change something.
#
# Written after the pilot ran for an afternoon with `.env` holding the *names* of
# three credentials rather than the credentials:
#
#     accounts/YOUR_CLOUDFLARE_ACCOUNT_ID/ai/run/...  404
#     text-to-speech/YOUR_ELEVENLABS_VOICE_ID         401
#     projects/<the wrong project>/...                403 CONSUMER_INVALID
#
# — while a laptop three feet away had all three working. A child got a
# pre-vetted fallback beat with no illustration and nothing spoken. The values
# were never secret from the operator; they were just never moved.
#
# So this moves them, and nothing else. It does not touch the compose file, the
# database, or any setting that is legitimately different between a laptop and a
# deployment (DEBUG, ALLOWED_HOSTS, MEDIA_STORAGE, the URLs). Only the list in
# CREDENTIALS below, only from this machine's .env, only after checking each one
# is real.
#
# Safe to re-run. The remote .env is backed up first, every time.

set -eu

REMOTE="${1:-}"
APP_DIR="${2:-app}"
DRY_RUN="${DRY_RUN:-0}"

# Every outward action goes through these two, so `DRY_RUN=1` is a guarantee
# about the whole script rather than a flag each call site remembers to check.
run_remote() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "    [dry run] would run on $REMOTE: $*"
        cat > /dev/null 2>&1 || true
        return 0
    fi
    ssh "$REMOTE" "$@"
}

copy_remote() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "    [dry run] would copy $1 -> $REMOTE:$2"
        return 0
    fi
    scp -q "$1" "$REMOTE:$2"
}

if [ -z "$REMOTE" ]; then
    echo "usage: sh infra/push-credentials.sh <user@host> [remote-app-dir]" >&2
    exit 2
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$HERE/.env"

[ -f "$ENV_FILE" ] || { echo "no .env at $ENV_FILE" >&2; exit 1; }

# The provider credentials, and only those. Everything else in .env is either
# environment-specific (so copying it would break the deployment) or not a
# credential (so copying it is noise in a diff somebody has to review).
CREDENTIALS="
GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_LOCATION
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
IMAGE_PROVIDER
IMAGE_MODEL
IMAGE_FALLBACK_PROVIDERS
GOOGLE_IMAGE_MODEL
TTS_PROVIDER
TTS_FALLBACK_PROVIDERS
ELEVENLABS_API_KEY
ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL_ID
ELEVENLABS_VOICE_MAP
GOOGLE_TTS_API_KEY
GOOGLE_TTS_VOICE
EMBEDDING_PROVIDER
EMBEDDING_MODEL
"

value_of() {
    # Everything after the first `=`, so a JSON voice map or a base64 key
    # survives intact. Last occurrence wins, matching how the app reads the file.
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# Refuse to ship the very thing this script exists to fix. A placeholder copied
# onto a deployment is worse than an empty one: empty is a `MediaConfigurationError`
# that names the setting, and a placeholder is a 404 that names the model.
BAD=""
for NAME in $CREDENTIALS; do
    VALUE=$(value_of "$NAME")
    case "$VALUE" in
        *YOUR_*|*your-*|*change-me*|*replace-me*|*'<'*|*placeholder*)
            BAD="$BAD $NAME"
            ;;
    esac
done
if [ -n "$BAD" ]; then
    echo "refusing to push: these are still placeholders locally:$BAD" >&2
    exit 1
fi

echo "==> collecting credentials from $ENV_FILE"
PAYLOAD=$(mktemp)
trap 'rm -f "$PAYLOAD"' EXIT
SENT=""
for NAME in $CREDENTIALS; do
    VALUE=$(value_of "$NAME")
    # An unset local value is skipped rather than sent as empty: blanking a
    # working remote setting because this laptop never had it is the one way a
    # sync script can make a deployment worse than it found it.
    [ -n "$VALUE" ] || continue
    printf '%s=%s\n' "$NAME" "$VALUE" >> "$PAYLOAD"
    SENT="$SENT $NAME"
done
echo "    sending:$SENT"

# The service-account JSON. Sent separately because it is a file.
#
# **Where it goes is read off the deployment, not decided here.** This used to
# copy unconditionally to `$APP_DIR/secrets/vertex-sa.json` while compose mounted
# whatever the box's own `.env` named — on the pilot that was
# `~/.config/primer/vertex-sa.json`, so every run of this script wrote a key to a
# path nothing read, reported success, and left the real one untouched. The
# script's own closing note told you to go and check the mount, which is the
# shape of a bug that has been noticed and not fixed.
#
# So: ask the remote where its key lives. `VERTEX_SA_PATH` is what
# `infra/docker-compose.yml` mounts; `GOOGLE_APPLICATION_CREDENTIALS` is what
# Django opens, and the two are the same path by construction. Falling back to
# the old location keeps a box that has neither working.
SA_LOCAL=$(value_of GOOGLE_APPLICATION_CREDENTIALS)
# Deliberately `ssh` and not `run_remote`: this is a *read*, and DRY_RUN exists to
# stop the script changing anything, not to stop it looking. Routed through
# run_remote it would capture that function's own dry-run banner as the path.
SA_REMOTE=$(
    ssh "$REMOTE" "cd '$APP_DIR' 2>/dev/null && \
        sed -n 's/^VERTEX_SA_PATH=//p;s/^GOOGLE_APPLICATION_CREDENTIALS=//p' .env \
        | grep -v '^\$' | tail -n 1" 2>/dev/null || true
)
[ -n "$SA_REMOTE" ] || SA_REMOTE="$APP_DIR/secrets/vertex-sa.json"
SA_REMOTE_DIR=$(dirname "$SA_REMOTE")

echo "==> backing up and updating $REMOTE:$APP_DIR/.env"
# The payload goes over stdin, never in argv: an ssh command line is visible in
# `ps` to every user on both machines, and these are live API keys.
run_remote "APP_DIR='$APP_DIR' sh -s" <<'REMOTE_SCRIPT' < "$PAYLOAD"
set -eu
cd "$APP_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
cp .env ".env.backup-$STAMP"
echo "    backed up to .env.backup-$STAMP"

INCOMING=$(mktemp)
cat > "$INCOMING"

# Drop every key we are about to set, then append the new values. Rewriting in
# place rather than editing lines keeps a key that appears twice from surviving
# once — which is how a file ends up with the new value above the old one and
# the old one winning.
KEYS=$(cut -d= -f1 "$INCOMING")
PATTERN=$(echo "$KEYS" | tr '\n' '|' | sed 's/|$//')
grep -Ev "^($PATTERN)=" .env > .env.next || true
cat "$INCOMING" >> .env.next
mv .env.next .env
rm -f "$INCOMING"

echo "    .env updated ($(echo "$KEYS" | wc -l | tr -d ' ') keys)"
REMOTE_SCRIPT

if [ -n "$SA_LOCAL" ] && [ -f "$SA_LOCAL" ]; then
    echo "==> copying the service-account key to $REMOTE:$SA_REMOTE"
    echo "    (path read from the deployment's own .env, so it is the one compose mounts)"
    run_remote "mkdir -p '$SA_REMOTE_DIR' && chmod 700 '$SA_REMOTE_DIR'"
    copy_remote "$SA_LOCAL" "$SA_REMOTE"
    run_remote "chmod 600 '$SA_REMOTE'"
    echo "    copied. GOOGLE_APPLICATION_CREDENTIALS is left as the deployment has it —"
    echo "    the key now sits at the path that setting already names."
else
    echo "==> no readable GOOGLE_APPLICATION_CREDENTIALS locally; leaving the remote key alone"
fi

echo "==> restarting web and worker, and clearing the media quota cooldowns"
# The cooldowns matter: every failure above wrote a 300s rest for that provider
# (apps/media/quota.py), so a box fixed at 17:59 still refuses to try until 18:04
# and looks unfixed.
run_remote "APP_DIR='$APP_DIR' sh -s" <<'REMOTE_SCRIPT'
set -eu
cd "$APP_DIR"
COMPOSE="docker compose -f infra/docker-compose.yml"
$COMPOSE up -d --force-recreate web worker
$COMPOSE exec -T web python manage.py quota --clear all || true
REMOTE_SCRIPT

echo "==> asking each vendor whether the credential now works"
run_remote "APP_DIR='$APP_DIR' sh -s" <<'REMOTE_SCRIPT'
set -eu
cd "$APP_DIR"
docker compose -f infra/docker-compose.yml exec -T web \
    python manage.py launch_check --live 2>&1 | grep -E "credentials\.|NOT READY|READY"
REMOTE_SCRIPT

cat <<'DONE'

If every credentials.* row above is OK, the next beat a child starts will have a
picture and a voice. Nothing needs to be replayed: the stream repairs a beat
whose media is missing once it is more than MEDIA_RETRY_AFTER_S (120s) old, so
reopening an adventure is enough.

If credentials.vertex:* is still FAIL with CONSUMER_INVALID, the .env is right
and the *key* is wrong: GOOGLE_APPLICATION_CREDENTIALS inside the container is
pointing at a service account for a different project. Check what the compose
file mounts, and point it at the secrets/vertex-sa.json this script just copied.
DONE
