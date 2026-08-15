#!/bin/sh
# Restore a backup into a scratch database and prove the data came back
# (PRODUCT.md §16.10, Day 15's criterion 3).
#
#   infra/backup/restore-drill.sh [dump-file]
#
# **The point of this script is the word "tested".** §16.10 asks for "Postgres PITR
# backups, **tested restores**", and Day 15's criterion is explicit that a
# configured backup is not a passing answer. A dump file is a hypothesis: it is
# either a database or a truncated write or an error message with a `.dump`
# extension, and the only way to know is to put it back.
#
# It restores into a **scratch** database with a name that could not be mistaken
# for anything real, never over the source, and drops it at the end. There is no
# flag in this script that restores over a live database: that is a deliberate,
# careful, out-of-hours operation with somebody watching, not something a drill
# should be one typo away from.
#
# The verification is `manage.py restore_drill`, which points Django at the
# scratch database and asks the questions that matter — is a child's history
# there, do their memories decrypt, does an adventure still have its beats. Row
# counts alone would pass on a database full of ciphertext nobody can read, which
# is the failure mode a memory-encryption key makes possible (§11.10).
#
# Environment: DATABASE_URL, PG_RUN — as in `backup.sh` — and PYTHON.
#
# On a deployment where everything runs in containers, all three are container
# prefixes and the whole drill is:
#
#   cd ~/app
#   export DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' .env | tail -1)"
#   export PG_RUN="docker compose -f $PWD/infra/docker-compose.yml exec -T db"
#   export PYTHON="docker compose -f $PWD/infra/docker-compose.yml exec -T -e DATABASE_URL web python"
#   sh infra/backup/restore-drill.sh
#
# Two details in that `PYTHON` line, both of which fail in ways that do not look
# like their cause:
#
# `$PWD` — the compose file path must be **absolute**. The verification step runs
# after a `cd backend`, so a relative `-f infra/docker-compose.yml` is resolved
# from there and docker reports `backend/infra/docker-compose.yml: no such file`,
# which reads like a broken repository rather than a wrong prefix.
#
# `-e DATABASE_URL` — load-bearing, see the note by the verification step near
# the end of this file.

set -eu

DUMP="${1:-}"
PG_RUN="${PG_RUN:-}"
SCRATCH="${SCRATCH_DB:-primer_restore_drill}"
# The interpreter that has Django installed. Same escape hatch as `PG_RUN` above
# and for the same reason: a bare `python` is whatever is first on PATH, which on
# a machine whose dependencies live in a virtualenv is an interpreter without
# Django. The drill then restores the dump correctly and dies on the *verification*
# — the half that makes it a drill — leaving `launch_check.durability.restore_drill`
# permanently unclosable for a reason that has nothing to do with the backup.
PYTHON="${PYTHON:-python}"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is not set." >&2
    exit 2
fi

if [ -z "$DUMP" ]; then
    DUMP=$(ls -t backups/primer-*.dump 2>/dev/null | head -1 || true)
fi
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "No dump to restore. Run infra/backup/backup.sh first." >&2
    exit 2
fi

# The maintenance connection: same server, `postgres` database, so the scratch
# database can be created and dropped.
ADMIN_URL=$(printf '%s' "$DATABASE_URL" | sed 's#/[^/?]*\(?.*\)\?$#/postgres#')

echo "== Restore drill =="
echo "  dump    $DUMP"
echo "  scratch $SCRATCH"

# shellcheck disable=SC2086
$PG_RUN psql -v ON_ERROR_STOP=1 -q "$ADMIN_URL" -c "DROP DATABASE IF EXISTS $SCRATCH;"
# shellcheck disable=SC2086
$PG_RUN psql -v ON_ERROR_STOP=1 -q "$ADMIN_URL" -c "CREATE DATABASE $SCRATCH;"

SCRATCH_URL=$(printf '%s' "$DATABASE_URL" | sed "s#/[^/?]*\(?.*\)\?\$#/$SCRATCH#")

# pgvector must exist before the restore, because the dump's column types
# reference it. On a managed provider this is the one thing a restore into a
# fresh database gets wrong, and it fails with a type error that reads like
# corruption.
# shellcheck disable=SC2086
$PG_RUN psql -v ON_ERROR_STOP=1 -q "$SCRATCH_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "-- restoring"
# `--exit-on-error` is the difference between a drill and a fiction: without it
# pg_restore reports "errors ignored on restore: 41" and exits 0, and a drill that
# passes on a partial restore is worse than no drill.
# shellcheck disable=SC2086
$PG_RUN pg_restore --no-owner --no-privileges --exit-on-error \
    --dbname="$SCRATCH_URL" < "$DUMP"

echo "-- verifying (through Django, against the restored copy)"
# `|| STATUS=$?` rather than a bare call: under `set -e` a failing verification
# would exit the script *here*, which skips the drop below and leaves a stale
# scratch database on the server. The next run would then begin by dropping a
# database somebody may since have gone to look at — and a failed drill is
# precisely when somebody goes to look. Capturing the status keeps the cleanup on
# both paths, and `exit $STATUS` at the end still fails the drill.
STATUS=0
cd backend
# Unquoted, exactly like `PG_RUN` above and for the same reason: on a deployment
# the only interpreter with Django is inside a container, so `PYTHON` has to be
# able to be a multi-word prefix. Quoted, it was one command name with spaces in
# it, and the drill died with "command not found" *after* a successful restore —
# failing the half that makes it a drill.
#
# When `PYTHON` is a `docker exec`, pass `-e DATABASE_URL` in it: the prefix
# below sets the variable in *this* shell, and `-e VAR` with no value is what
# forwards it into the container. Without that the container falls back to its
# own `.env` and verifies the **live** database instead of the restored copy —
# read-only, so nothing breaks, but it is a drill that reports a pass without
# ever having looked at the dump.
# shellcheck disable=SC2086
DATABASE_URL="$SCRATCH_URL" $PYTHON manage.py restore_drill || STATUS=$?
cd ..

echo "-- dropping the scratch database"
# shellcheck disable=SC2086
$PG_RUN psql -q "$ADMIN_URL" -c "DROP DATABASE IF EXISTS $SCRATCH;" || true

exit $STATUS
