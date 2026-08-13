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
# Environment: DATABASE_URL, PG_RUN — as in `backup.sh`.

set -eu

DUMP="${1:-}"
PG_RUN="${PG_RUN:-}"
SCRATCH="${SCRATCH_DB:-primer_restore_drill}"

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
DATABASE_URL="$SCRATCH_URL" python manage.py restore_drill || STATUS=$?
cd ..

echo "-- dropping the scratch database"
# shellcheck disable=SC2086
$PG_RUN psql -q "$ADMIN_URL" -c "DROP DATABASE IF EXISTS $SCRATCH;" || true

exit $STATUS
