#!/bin/sh
# The nightly dump, as cron runs it (PRODUCT.md §16.10).
#
#   crontab -e
#   17 3 * * * /home/YOU/app/infra/backup/cron-backup.sh >> /home/YOU/backup.log 2>&1
#
# `backup.sh` is the thing that takes a dump and is written to be run by a human
# with an environment. This is the wrapper that gives it one, because cron does
# not: no `DATABASE_URL`, no working directory, and a `PATH` of `/usr/bin:/bin`.
# A cron entry that calls `backup.sh` directly fails on the first line every
# night, and the only place that shows up is `launch_check` calling the backups
# stale some days later — which is exactly how a box ends up with no dumps at all
# while somebody believes it has seven.
#
# **Where the database credentials come from.** `.env`, read at run time, never
# copied here. This box's Postgres is reachable as `db:5432` on the compose
# network and is published only on loopback, so the value in `.env` is already
# the right one and duplicating it into a second file is how the two drift.
#
# **Why the dump runs inside the container.** The host has no Postgres client
# tools, and installing a `pg_dump` whose major version has to be kept in step
# with the server by hand is a worse problem than this prefix. `PG_RUN` is
# `backup.sh`'s own documented seam for exactly this. The `>` redirect is
# evaluated by *this* shell, so the bytes land on the host disk even though
# `pg_dump` ran in the container.
#
# **This is one of two layers, and the weaker one.** A dump on the same disk as
# the database survives `DROP TABLE`, a bad migration and a corrupted index. It
# does not survive losing the disk. `Docs/Runbooks/Backup-Restore.md` calls the
# off-provider copy the second line of defence and it is still owed; until it
# exists, this protects against the likely failures and not the total one.

set -eu

# cron gives us almost nothing. Docker lives in /usr/bin on Ubuntu; the rest is
# here so a hand-run of this script behaves the same as the scheduled one.
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH

APP_DIR="${APP_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}"
cd "$APP_DIR"

[ -f .env ] || { echo "no .env in $APP_DIR — cannot find the database" >&2; exit 2; }

# Last occurrence wins, matching how the app reads the file.
DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' .env | tail -n 1)"
[ -n "$DATABASE_URL" ] || { echo "DATABASE_URL is not set in $APP_DIR/.env" >&2; exit 2; }
export DATABASE_URL

# Keep retention in step with `settings.BACKUP_RETENTION_DAYS`, which is the
# number the privacy disclosure states to parents. If `.env` overrides it, this
# picks that up; otherwise both sides use their own default of 7.
RETENTION="$(sed -n 's/^BACKUP_RETENTION_DAYS=//p' .env | tail -n 1)"
[ -z "$RETENTION" ] || export BACKUP_RETENTION_DAYS="$RETENTION"

PG_RUN="docker compose -f infra/docker-compose.yml exec -T db"
export PG_RUN

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) backup starting ==="
sh infra/backup/backup.sh "$APP_DIR/backups"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) backup finished ==="
