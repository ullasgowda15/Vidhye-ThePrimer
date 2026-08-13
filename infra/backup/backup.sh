#!/bin/sh
# Take one backup (PRODUCT.md §16.10).
#
#   infra/backup/backup.sh [destination-directory]
#
# §16.10 makes memory durability a top SLO: *"losing a child's history breaks the
# core promise"*. This is the half of that which runs on a schedule. The half that
# matters more is `restore-drill.sh`, because a backup nobody has restored is a
# hypothesis.
#
# **`-Fc`, not plain SQL.** The custom format is compressed, and — the reason it is
# here — `pg_restore` can read a *selective* restore out of it: one table, one
# schema, or the whole thing. A 3am restore of a single dropped table from a
# plain-SQL dump means editing a multi-gigabyte text file.
#
# **Point-in-time recovery is a provider setting, not this script.** Managed
# Postgres (RDS, Cloud SQL, Neon, Supabase, Render) all offer continuous WAL
# archiving with a retention window, and turning it on is a checkbox that beats
# anything a shell script can do for the *recent* past. This is the second line:
# an off-provider copy, so a deleted project or a compromised account does not
# take the backups with the database. DEPLOY.md says which to turn on where.
#
# **Dumps expire.** A dump is a complete copy of every family in the product, and
# the privacy disclosure now tells parents how long one is kept — so the pruning
# below is not tidiness, it is the mechanism that makes that sentence true. Before
# the Day 1–15 audit the disclosure said we kept no backups at all while a dump of
# every family sat in the working tree (F-5); a retention promise that nothing
# implements is the same class of over-claim, one step further along.
#
# Pruning happens *after* a successful dump, deliberately. A run that fails to
# produce a new dump must not be the run that deletes the old ones.
#
# Environment:
#   DATABASE_URL             the database to dump. Required.
#   BACKUP_RETENTION_DAYS    how long a dump is kept. Default 7, and it must match
#                            `settings.BACKUP_RETENTION_DAYS`, which is the number
#                            the disclosure states and `launch_check` reads.
#   PG_RUN                   optional prefix for the pg tools, so this works where
#                            they are not on PATH. On a dev machine with the
#                            compose stack up:
#                              PG_RUN="docker compose -f infra/docker-compose.yml exec -T db"

set -eu

DEST="${1:-backups}"
PG_RUN="${PG_RUN:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is not set. Refusing to guess which database to back up." >&2
    exit 2
fi

mkdir -p "$DEST"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/primer-$STAMP.dump"

echo "Dumping to $OUT"
# shellcheck disable=SC2086  # PG_RUN is a deliberate word-split prefix.
$PG_RUN pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > "$OUT"

BYTES=$(wc -c < "$OUT")
if [ "$BYTES" -lt 1024 ]; then
    # A dump that is a few hundred bytes is an error message, not a database, and
    # a backup job that reports success on one is worse than no backup job.
    echo "Refusing a $BYTES-byte dump: that is not a database." >&2
    rm -f "$OUT"
    exit 1
fi

echo "OK  $OUT  ($BYTES bytes)"

# Retention (§12.8). Only files this script's own naming produced are considered,
# so pointing it at a directory holding something else cannot delete that
# something else. `-mtime +N` is "older than N days" in the same units the
# disclosure and `settings.BACKUP_RETENTION_DAYS` use.
if [ "$RETENTION_DAYS" -gt 0 ]; then
    PRUNED=$(find "$DEST" -maxdepth 1 -type f -name 'primer-*.dump' \
        -mtime "+$RETENTION_DAYS" -print -delete | wc -l | tr -d ' ')
    echo "Retention: kept $RETENTION_DAYS days, removed $PRUNED expired dump(s)"
fi

echo "$OUT"
