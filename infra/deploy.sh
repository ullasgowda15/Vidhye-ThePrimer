#!/usr/bin/env bash
#
# Put a commit into production. Runs **on the box**, and is the only thing that
# touches production code (§16.10).
#
#     DEPLOY_SHA=<sha> bash infra/deploy.sh
#     DEPLOY_SHA=<sha> DIST_TARBALL=/tmp/frontend-dist.tar.gz bash infra/deploy.sh
#     DRY_RUN=1 bash infra/deploy.sh          # says what it would do, changes nothing
#
# `.github/workflows/deploy.yml` calls this over SSH once the eval gate is green.
# A person can call it by hand for the same result — that is deliberate. A deploy
# path that only exists inside CI is a deploy path you cannot use at 2am when CI
# is the thing that is broken.
#
# ---------------------------------------------------------------------------
# What it knows that a hand-typed `git pull` does not
# ---------------------------------------------------------------------------
# Every one of these is a mistake that has actually been made on this box, or is
# one `Docs/Runbooks/Production-Facts.md` warns about:
#
# * **It deploys an exact SHA, not `main`.** `git pull` fetches whatever landed
#   since, so what ships is not what the gate graded. §19.7 is only true if the
#   commit that passed the evals is the commit that runs.
# * **It refuses to go backwards.** A queued deploy of a superseded commit would
#   otherwise silently un-ship the newer one.
# * **It restarts only what changed.** A docs-only commit restarts nothing; a
#   frontend-only commit swaps a directory and never touches a container. This is
#   the whole point of the exercise — "after every smallest change" should not
#   mean a two-minute image rebuild.
# * **It migrates on the new image before the new code serves**, not after.
# * **It swaps `frontend/dist` atomically.** Caddy serves that directory straight
#   off disk, so an in-place rebuild there means a window in which a child's
#   browser asks for a chunk that no longer exists.
# * **It probes the way that cannot lie.** `SECURE_SSL_REDIRECT=1` 301s plain
#   HTTP before URL resolution, so a naive `curl 127.0.0.1:8000` reports 301
#   whether the app is healthy, broken, or absent.
# * **It rolls back by itself** when the probe fails, and says what it could not
#   undo.
# * **It prunes images.** 29 GB of disk and an image per deploy is a disk-full
#   outage some weeks out, and disk-full with the database on the same disk is
#   the bad kind.
#
# It does *not* touch `.env` — `infra/push-credentials.sh` owns credentials, and
# a deploy that rewrote configuration would make every push a chance to break the
# providers. The two scripts are separate on purpose.

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/app}"
DEPLOY_SHA="${DEPLOY_SHA:-}"
DEPLOY_REF="${DEPLOY_REF:-main}"
DIST_TARBALL="${DIST_TARBALL:-}"
FORCE="${FORCE:-0}"
DRY_RUN="${DRY_RUN:-0}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

STATE_DIR="$APP_DIR/.deploy"
LOG_DIR="$STATE_DIR/log"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# `-p primer`, explicitly. The containers were created under the project name
# `primer`, but Compose derives the project from the invoking directory — which
# here is `app` — unless the file names one. The file does now, and saying it
# again costs nothing and survives someone copying this call somewhere else.
COMPOSE=(docker compose -f infra/docker-compose.yml -p primer)

step() { printf '\n==> %s\n' "$*"; }
say()  { printf '    %s\n' "$*"; }
warn() { printf '    !! %s\n' "$*" >&2; }
die()  { printf '\nDEPLOY FAILED: %s\n' "$*" >&2; exit 1; }

would() {
    if [ "$DRY_RUN" = "1" ]; then
        say "[dry run] would: $*"
        return 0
    fi
    "$@"
}

cd "$APP_DIR" 2>/dev/null || die "no app directory at $APP_DIR"
git rev-parse --git-dir >/dev/null 2>&1 || die "$APP_DIR is not a git clone; deploys assume one"
command -v docker >/dev/null 2>&1 || die "docker is not on PATH"

mkdir -p "$STATE_DIR" "$LOG_DIR"

# One deploy at a time. Two overlapping image builds on a 2-vCPU box is how you
# get a half-built image serving traffic. `-w` rather than `-n`: a queued deploy
# should wait its turn, not be dropped.
#
# `flock` is util-linux, so it is always on the Ubuntu box and never in Git Bash
# on Windows — where the only thing anyone runs is `DRY_RUN=1`. Missing it is
# worth one line of warning, not a refusal: the alternative is a confusing
# "another deploy holds the lock" on a machine that has never deployed anything.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$STATE_DIR/lock"
    flock -w 900 9 || die "another deploy has held the lock for 15 minutes; look in $LOG_DIR"
else
    warn "no flock on this machine — running without the concurrency lock"
fi

echo "deploy  $STAMP  (dry run: $DRY_RUN)"

# ---------------------------------------------------------------------------
# 1. Work out what we are deploying, and refuse to move backwards
# ---------------------------------------------------------------------------
step "fetching"
PREV_SHA="$(git rev-parse HEAD)"
say "currently at $PREV_SHA"

would git fetch --quiet --prune --tags origin "$DEPLOY_REF"

if [ -z "$DEPLOY_SHA" ]; then
    DEPLOY_SHA="$(git rev-parse "origin/$DEPLOY_REF")"
    say "no DEPLOY_SHA given; taking the head of origin/$DEPLOY_REF"
fi

# A tag or an abbreviated sha is a reasonable thing for a person to type. And a
# commit that is not on `$DEPLOY_REF` at all is reasonable for CI to pass: a `v*`
# tag can point anywhere, which is the whole reason the gate re-runs on it.
if ! git rev-parse --verify --quiet "${DEPLOY_SHA}^{commit}" >/dev/null 2>&1; then
    say "$DEPLOY_SHA is not in this clone yet; fetching it directly"
    git fetch --quiet origin "$DEPLOY_SHA" 2>/dev/null || true
fi
DEPLOY_SHA="$(git rev-parse --verify "${DEPLOY_SHA}^{commit}" 2>/dev/null)" \
    || die "no such commit on this box: $DEPLOY_SHA (is it pushed?)"

say "deploying    $DEPLOY_SHA"
say "             $(git log -1 --format=%s "$DEPLOY_SHA")"

SKIP_TO_HEALTH=0
if [ "$DEPLOY_SHA" = "$PREV_SHA" ] && [ "$FORCE" != "1" ]; then
    say "already at this commit; verifying health and stopping"
    SKIP_TO_HEALTH=1
elif git merge-base --is-ancestor "$DEPLOY_SHA" "$PREV_SHA" 2>/dev/null; then
    # The guard against a queued deploy of a superseded commit. If what we were
    # asked to ship is an ancestor of what is already running, shipping it is a
    # rollback nobody asked for.
    if [ "$FORCE" = "1" ]; then
        warn "$DEPLOY_SHA is behind what is running — proceeding because FORCE=1"
    else
        say "$DEPLOY_SHA is an ancestor of what is already deployed."
        say "Nothing to do; a newer commit got here first. (FORCE=1 to go back anyway.)"
        exit 0
    fi
fi

backend_changed=0
frontend_changed=0

if [ "$SKIP_TO_HEALTH" = "0" ]; then
    # -----------------------------------------------------------------------
    # 2. Move the working tree, keeping anything hand-edited on the box
    # -----------------------------------------------------------------------
    step "checking out $DEPLOY_SHA"

    # `.env` and friends are gitignored, so a hard reset cannot touch them. What
    # it *can* touch is a tracked file somebody edited on the box in a hurry —
    # which has happened, and which silently reverting would make undebuggable.
    # Keep it as a patch and carry on: a deploy that stops for this is a deploy
    # that stops at 2am.
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        PATCH="$STATE_DIR/local-changes-$STAMP.patch"
        git diff > "$PATCH" || true
        warn "tracked files were modified on the box; saved to $PATCH before resetting"
        git status --short --untracked-files=no | sed 's/^/       /' >&2
    fi

    # Stay on the branch when the commit is on it, so that a person who later
    # types `git pull` on the box gets a sensible answer rather than a lecture
    # about detached HEAD. A tagged commit that was never on `main` gets detached
    # instead — moving `main` to point at it would be a lie about the branch.
    if git merge-base --is-ancestor "$DEPLOY_SHA" "origin/$DEPLOY_REF" 2>/dev/null; then
        would git checkout --quiet -B "$DEPLOY_REF" "$DEPLOY_SHA"
    else
        warn "$DEPLOY_SHA is not on origin/$DEPLOY_REF; checking out detached"
        would git checkout --quiet --detach "$DEPLOY_SHA"
    fi

    # Compose interpolates `${VERTEX_SA_PATH}` against a `.env` in the **compose
    # file's own directory**, not the repo root (see the long note in
    # docker-compose.yml). Without this link both containers mount a placeholder
    # path and Vertex fails with CONSUMER_INVALID. Asserting it every deploy
    # costs nothing, and its absence is otherwise invisible until a child gets a
    # story with no pictures.
    if [ ! -e infra/.env ]; then
        warn "infra/.env was missing — linking it to ../.env so compose can interpolate"
        would ln -sf ../.env infra/.env
    fi

    # -----------------------------------------------------------------------
    # 3. Decide what actually has to move
    # -----------------------------------------------------------------------
    step "what changed"
    CHANGED="$(git diff --name-only "$PREV_SHA" "$DEPLOY_SHA" 2>/dev/null || echo ALL)"

    if [ "$CHANGED" = "ALL" ] || [ "$FORCE" = "1" ]; then
        say "rebuilding everything (FORCE, or no common history with what was running)"
        backend_changed=1
        frontend_changed=1
    else
        if echo "$CHANGED" | grep -qE '^(backend/|infra/Dockerfile\.backend|infra/docker-compose\.yml)'; then
            backend_changed=1
        fi
        if echo "$CHANGED" | grep -qE '^frontend/'; then
            frontend_changed=1
        fi
        say "$(echo "$CHANGED" | grep -c .) files changed"
        say "backend:  $([ "$backend_changed" = 1 ] && echo 'rebuild + restart' || echo 'untouched')"
        say "frontend: $([ "$frontend_changed" = 1 ] && echo 'rebuild + swap' || echo 'untouched')"
    fi

    # A tarball handed to us by CI is the frontend changing, whatever the diff says.
    [ -n "$DIST_TARBALL" ] && frontend_changed=1

    # -----------------------------------------------------------------------
    # 4. Frontend: swap the directory Caddy serves, atomically
    # -----------------------------------------------------------------------
    if [ "$frontend_changed" = "1" ]; then
        step "installing the built PWA"
        if [ -n "$DIST_TARBALL" ]; then
            [ -f "$DIST_TARBALL" ] || die "DIST_TARBALL=$DIST_TARBALL does not exist"
            say "from $DIST_TARBALL (built by CI — this box never runs npm)"
            would rm -rf frontend/dist.incoming
            would mkdir -p frontend/dist.incoming
            would tar -xzf "$DIST_TARBALL" -C frontend/dist.incoming
            if [ "$DRY_RUN" != "1" ] && [ ! -f frontend/dist.incoming/index.html ]; then
                die "the tarball has no index.html at its root; refusing to swap"
            fi
        elif command -v npm >/dev/null 2>&1; then
            warn "no tarball supplied; building on the box (slow, and 3.8 GiB is tight)"
            would sh -c 'cd frontend && npm ci --no-audit --no-fund && npm run build'
            would rm -rf frontend/dist.incoming
            would cp -r frontend/dist frontend/dist.incoming
        else
            die "frontend changed, no DIST_TARBALL, and no npm on this box"
        fi

        # Two renames, so the directory Caddy is reading is never half-written.
        if [ "$DRY_RUN" != "1" ]; then
            rm -rf frontend/dist.previous
            [ -d frontend/dist ] && mv frontend/dist frontend/dist.previous
            mv frontend/dist.incoming frontend/dist
            say "swapped; the build it replaced is in frontend/dist.previous"
        fi
    fi

    # -----------------------------------------------------------------------
    # 5. Backend: build, migrate on the new image, then recreate
    # -----------------------------------------------------------------------
    if [ "$backend_changed" = "1" ]; then
        step "building the backend image"
        would "${COMPOSE[@]}" build web worker

        step "migrating"
        # On a one-off container of the **new** image, before any new code serves
        # a request. `--no-deps` because db and redis are already up and healthy;
        # letting compose start them here would be a second opinion about a
        # running database.
        would "${COMPOSE[@]}" run --rm --no-deps web python manage.py migrate --noinput

        step "restarting web and worker"
        would "${COMPOSE[@]}" up -d --force-recreate web worker
    else
        step "backend untouched — no rebuild, no restart"
    fi
fi

# ---------------------------------------------------------------------------
# 6. Is it actually serving?
# ---------------------------------------------------------------------------
step "health"

# The first entry of ALLOWED_HOSTS, because the probe has to send a Host header
# the app will accept, or it gets a 400 that looks exactly like a dead app.
HOST_HEADER="$(sed -n 's/^ALLOWED_HOSTS=//p' .env 2>/dev/null | tail -n 1 | cut -d, -f1)"
[ -n "$HOST_HEADER" ] || HOST_HEADER="primer.yourcaly.com"

probe() {
    # `X-Forwarded-Proto: https` is not decoration. Without it SecurityMiddleware
    # 301s before URL resolution and every answer is 301 — healthy, broken or
    # gone. See Production-Facts.md, "Probing it without being lied to".
    curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H 'X-Forwarded-Proto: https' -H "Host: $HOST_HEADER" \
        http://127.0.0.1:8000/api/health/ 2>/dev/null || echo 000
}

healthy=0
code=000
if [ "$DRY_RUN" = "1" ]; then
    say "[dry run] would probe http://127.0.0.1:8000/api/health/ for ${HEALTH_TIMEOUT}s"
    healthy=1
else
    started=$(date +%s)
    deadline=$(( started + HEALTH_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        code="$(probe)"
        if [ "$code" = "200" ]; then
            healthy=1
            say "api/health/ -> 200 after $(( $(date +%s) - started ))s"
            break
        fi
        sleep 3
    done
    [ "$healthy" = "1" ] || warn "api/health/ -> $code for ${HEALTH_TIMEOUT}s"
fi

# The PWA half. Caddy reads `frontend/dist` off disk, so a bad swap shows up here
# and nowhere else — the API would stay perfectly green while every child gets a
# blank page.
if [ "$DRY_RUN" != "1" ] && [ ! -f frontend/dist/index.html ]; then
    warn "frontend/dist/index.html is missing — Caddy has nothing to serve"
    healthy=0
fi

# ---------------------------------------------------------------------------
# 7. Roll back if it is not
# ---------------------------------------------------------------------------
if [ "$healthy" != "1" ]; then
    warn "rolling back to $PREV_SHA"
    git reset --hard --quiet "$PREV_SHA" || true

    # Only if *this* run swapped it. `dist.previous` survives between deploys, so
    # an unguarded restore here would undo a frontend change from some earlier,
    # perfectly good deploy because a later backend-only one failed its probe.
    if [ "$frontend_changed" = "1" ] && [ -d frontend/dist.previous ]; then
        rm -rf frontend/dist.broken
        mv frontend/dist frontend/dist.broken 2>/dev/null || true
        mv frontend/dist.previous frontend/dist
        say "restored the PWA build this deploy replaced"
    fi
    if [ "$backend_changed" = "1" ]; then
        "${COMPOSE[@]}" build web worker || true
        "${COMPOSE[@]}" up -d --force-recreate web worker || true
        sleep 10
    fi
    say "after rollback: api/health/ -> $(probe)"
    cat >&2 <<'ROLLBACK'

The deploy was reverted. Two things it did NOT undo, because it cannot:

  * **migrations already applied.** If the failure was a migration, the schema is
    now ahead of the code that is running. Check with:
        docker exec -it primer-web-1 python manage.py showmigrations
  * **anything the new code wrote** to the database or the media bucket.

The logs from the run that failed:
    docker logs --tail 200 primer-web-1
ROLLBACK
    die "health check failed; $DEPLOY_SHA was not kept"
fi

# ---------------------------------------------------------------------------
# 8. Housekeeping and a record
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" != "1" ]; then
    step "housekeeping"

    # An image per deploy on a 29 GB disk fills it in a couple of months, and the
    # database is on the same disk. Dangling only — never `-a`, which would
    # cheerfully delete the pgvector and redis images this box is running on.
    docker image prune -f >/dev/null 2>&1 || true
    say "pruned dangling images; $(df -h / | awk 'NR==2{print $4" free of "$2}')"

    # Keep the last thirty of each and no more.
    ls -1t "$LOG_DIR"/*.log 2>/dev/null | tail -n +31 | xargs -r rm -f
    ls -1t "$STATE_DIR"/local-changes-*.patch 2>/dev/null | tail -n +31 | xargs -r rm -f

    {
        printf '{\n'
        printf '  "sha": "%s",\n' "$DEPLOY_SHA"
        printf '  "previous_sha": "%s",\n' "$PREV_SHA"
        printf '  "deployed_at": "%s",\n' "$STAMP"
        printf '  "by": "%s"\n' "${DEPLOY_ACTOR:-$(whoami)}"
        printf '}\n'
    } > "$STATE_DIR/last-deploy.json"
fi

step "live"
say "$DEPLOY_SHA is serving on https://$HOST_HEADER"
if [ "$SKIP_TO_HEALTH" = "0" ] && [ "$PREV_SHA" != "$DEPLOY_SHA" ]; then
    say "to undo:  DEPLOY_SHA=$PREV_SHA FORCE=1 bash infra/deploy.sh"
fi
