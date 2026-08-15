# Runbook — backups and the restore drill

> §16.10: *"Memory durability is a top SLO (losing a child's history breaks the
> core promise). Postgres PITR backups, **tested restores**, media redundancy."*

**The rule this runbook exists to enforce: a backup nobody has restored is a
hypothesis.** Day 15's criterion 3 says so explicitly — a configured backup is
not a passing answer.

---

## The two layers, and why both

| | What it protects against | Where it lives |
|---|---|---|
| **Provider PITR** | *"we dropped a table at 14:32"* | A checkbox on the managed Postgres. Continuous WAL archiving with a retention window. |
| **Off-provider dumps** | *"the account is gone / compromised / the project was deleted"* | `infra/backup/backup.sh`, run on a schedule, written somewhere that is not the database provider. Copied off the box by `manage.py backup_upload` — see below. |

PITR beats anything a shell script can do for the recent past — turn it on, and
DEPLOY.md says where. The dumps are the second line, and they are the ones this
runbook drills, because they are the ones with a human in the loop.

**Media is not in either.** Illustrations and audio are objects in a bucket
(§16.6), and their redundancy is the bucket's: enable versioning and
cross-region replication on it. The database holds the *URLs*, so a restored
database with an empty bucket is a child's whole history pointing at nothing —
which is why `restore_drill` counts media rows with URLs and why the bucket's
own replication is a launch item, not an afterthought.

---

## Taking a backup

```bash
export DATABASE_URL=postgres://...
sh infra/backup/backup.sh backups          # or: make backup
```

`-Fc` (custom format) rather than plain SQL, so `pg_restore` can pull one table
out of it at 3am rather than making somebody edit a multi-gigabyte text file.

A dump under 1KB is refused and deleted: that is an error message with a `.dump`
extension, and a backup job that reports success on one is worse than no backup
job at all.

**Dumps expire after `BACKUP_RETENTION_DAYS` (default 7).** The script prunes its
own `primer-*.dump` files after a *successful* dump — never before one, so a
failed run cannot be the run that deletes the history. That number is not
housekeeping: `apps/accounts/privacy.py` states it to parents, so a copy of a
family who deleted their account is gone within that window, and
`manage.py launch_check`'s `durability.backup_freshness` row reads the newest
dump's age against `BACKUP_MAX_AGE_HOURS` so a dead cron is visible before a
family is let in. Keep the shell default, the setting and the sentence in step.

On a machine with no Postgres client tools (a Windows dev box, for instance),
point the script at the ones in the compose stack:

```bash
export PG_RUN="docker compose -f infra/docker-compose.yml exec -T db"
```

### Getting it off the box

`infra/backup/cron-backup.sh` calls `manage.py backup_upload` straight after a
successful dump, so the nightly job produces both copies. To run it by hand:

```bash
docker compose -f infra/docker-compose.yml exec -T web \
    python manage.py backup_upload /backups/primer-<stamp>.dump --prune
```

**It needs a separate, private bucket.** `BACKUP_S3_BUCKET` must never be
`AWS_STORAGE_BUCKET_NAME`: the media bucket is served publicly through
`MEDIA_CDN_BASE_URL` so a child's illustrations load without a credential, and a
dump is every family in the product. S3 accepts the upload, the CDN serves it,
and nothing downstream notices — so both `backup_upload` and
`launch_check.durability.backup_offsite` refuse when the two names match.

The upload is **deliberately not fatal** to the cron. A bucket having a bad night
must not turn a successful local dump into a failed backup run: the copy that
exists is worth more than the complaint about the copy that does not. Watch
`durability.backup_offsite` for the configuration and the log for the uploads.

`--prune` sweeps the same `BACKUP_RETENTION_DAYS` window over the remote prefix,
scoped three ways — to `BACKUP_S3_PREFIX`, to the `primer-*.dump` naming, and to
age. Without it a permanent remote copy would outlive what the disclosure
promises parents, which is the copy nobody remembers exists.

To create the bucket: Cloudflare dashboard → R2 → **Create bucket**, name it
something that is obviously not the media one (`primer-backups`), and **do not
enable public access or a custom domain on it**. The existing R2 token is
account-scoped, so no second credential is needed — though giving the backup
bucket its own is the better posture, since the media credentials sit in a
container serving public traffic.

---

## The restore drill

**Run it after every schema migration, and at least monthly.** A dump is only as
good as the schema it was taken against, and the drill is what turns "we have
backups" into a fact.

```bash
sh infra/backup/restore-drill.sh            # or: make restore-drill
```

It creates a scratch database, restores into it, runs `manage.py restore_drill`
against the *copy*, and drops it. There is deliberately no flag in that script
that restores over a live database.

`pg_restore --exit-on-error` is load-bearing: without it, a partial restore
prints `errors ignored on restore: 41` and exits 0, and the drill passes on a
database that is missing a third of its tables.

### What the verification actually asks

Row counts are the least of it. `manage.py restore_drill` asks the four things
somebody actually cares about the morning after losing a database:

1. **is everybody still here** — parents, children, companions, adventures;
2. **is a child's history still here** — beats *joined to* an adventure joined to
   a child, because a restore that kept the rows and lost the foreign keys has
   identical counts and none of the value;
3. **can we still read their memories** — `memory.content` is encrypted at rest
   (§11.10), so a perfectly restored copy read with the wrong
   `MEMORY_ENCRYPTION_KEY` is a table of ciphertext: full, correct, and
   worthless. This is the check that cannot be done with SQL.
4. **do the pictures resolve** — media rows with URLs.

> The encryption check is checked against `crypto.UNREADABLE`, the sentinel the
> decrypt path returns on a wrong key — **not** by inspecting the decrypted
> string. The first version of this command asked "does it contain a space", and
> the sentinel (`[unreadable: encrypted with a different key]`) contains four, so
> the drill passed with a deliberately wrong key. That was found by running the
> negative case, which is the only reason to run one.

---

## Drill log

| Date | Dump | Restored | Verified | Result |
|---|---|---|---|---|
| 2026-08-13 | `primer-20260813T022013Z.dump` (205,135 bytes, local dev DB) | `primer_restore_drill` on the compose Postgres | 7 parents, 4 children, 15 adventures, 50 beats, 36 choices, 15 memories, 118 media; 2 children with a playable history; **15/15 sampled memories decrypted**; 118/118 media rows with URLs | ✅ **the data came back** |
| 2026-08-13 | same dump | same | re-run with a deliberately wrong `MEMORY_ENCRYPTION_KEY` | ✅ **drill failed, exit 1** — `15 of 15 sampled memories are undecryptable`. The negative case, which is what makes the positive one mean something. |
| 2026-08-14 | `primer-20260814T072420Z.dump` (369,422 bytes, local dev DB) | `primer_restore_drill` on the compose Postgres | 7 parents, 4 children, 4 companions, 17 adventures, 60 beats, 44 choices, 20 memories, 151 media, 25 events; 2 children with a playable history; **20/20 sampled memories decrypted**; 151/151 media rows with URLs | ✅ **the data came back** |

| 2026-08-15 | `primer-20260815T121805Z.dump` (236,819 bytes, **the production database on `app-server`**) | `primer_restore_drill` on the same compose Postgres | 3 parents, 3 children, 3 companions, 4 adventures, 15 beats, 11 choices, 8 memories, 61 media, 49 events; 3 children with a playable history; **8/8 sampled memories decrypted**; 61/61 media rows with URLs | ✅ **the data came back** |

| 2026-08-15 | same dump | same | re-run with a deliberately wrong `MEMORY_ENCRYPTION_KEY` | ✅ **drill failed, exit 1** — `8 of 8 sampled memories are undecryptable`, and the report says why: *"the restore is fine and MEMORY_ENCRYPTION_KEY is wrong"*. The negative case on production. |

> Both rows are needed. A drill that has only ever passed has not been shown to
> be capable of failing.

**Why the 2026-08-15 row is the one that counts.** Every row above it was taken
against a development database on a laptop. This is the first restore of the
*production* database, which is what the "Not yet drilled" note below used to
say was outstanding, and it postdates the last schema change (`learning.0003`
and `learning.0004`, applied to production earlier the same day) — so
`launch_check.durability.restore_drill` is satisfied rather than argued with.

Two things it settled that were open questions rather than expectations:

- **The `vector` extension was not the problem it was predicted to be.** The
  runbook warned that a managed provider would need it enabled out of band. This
  deployment runs `pgvector/pgvector:pg16` in compose, so `CREATE EXTENSION IF
  NOT EXISTS vector` in the drill was sufficient and the restore was clean under
  `--exit-on-error`. That prediction still stands for a future move to a managed
  Postgres.
- **Role ownership was not an issue either**, because `backup.sh` dumps with
  `--no-owner --no-privileges` and the restore uses the same flags.

The drill on this box needs all three of `DATABASE_URL`, `PG_RUN` and `PYTHON`
as container prefixes; the invocation is at the top of
`infra/backup/restore-drill.sh`. Two bugs were found running it for the first
time, both of which failed *after* a successful restore and so looked like data
problems: `PYTHON` was quoted where `PG_RUN` was not, so it could not be a
container prefix at all, and the compose path inside it has to be absolute
because the verification runs after a `cd backend`.

**The negative case was run on production the same day** (the fourth row above),
so this drill is not one that has only ever passed. To repeat it, generate a
valid but different Fernet key and hand it to the verifier alone:

```bash
export WRONG=$(docker compose -f $PWD/infra/docker-compose.yml exec -T web \
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | tr -d '\r')
export PYTHON="docker compose -f $PWD/infra/docker-compose.yml exec -T \
    -e DATABASE_URL -e MEMORY_ENCRYPTION_KEY=$WRONG web python"
```

The wrong key reaches the *drill's* verifier and nothing else — it is passed per
`docker exec`, never written to `.env` — so the live containers keep the real one
throughout. `unset WRONG PYTHON` afterwards.

The failure to look for is specific: row counts identical to the passing run,
`3 children with a playable history`, `61/61 media rows with a URL`, and then
`8 of 8 sampled memories are undecryptable`. A database that is complete,
correctly linked, and unreadable is §11.10's worst case, and it is the one state
no amount of SQL would have reported as wrong.

**Why the 2026-08-14 row exists.** `launch_check.durability.restore_drill` asks
that the last entry here postdate the last schema change, and it did not: the
newest migration (`accounts.0003_deletionreceipt`) landed at 13:57 on the 13th,
after the dump the two rows above were taken from. So that row was re-earned
against a dump containing the current schema rather than argued about.

Re-running it also found why nobody had: `restore-drill.sh` invoked a bare
`python`, which on a machine whose dependencies live in a virtualenv is an
interpreter without Django. The restore succeeded and the *verification* — the
half that makes it a drill — died on `ModuleNotFoundError`. It now takes a
`PYTHON` override the way it already took `PG_RUN`, and `make restore-drill`
passes the venv interpreter, so the documented command works as written.

**Drilled on production as of 2026-08-15** — see the row above. That closes what
this paragraph used to say was outstanding.

**Still not drilled:** a restore against a *managed* Postgres. Production today
is `pgvector/pgvector:pg16` in compose on the same VM as the app, so the drill
above exercised the scripts but not a provider. The friction predicted here —
role ownership, `vector` needing to be enabled by the provider rather than by
`CREATE EXTENSION` — is still unproven and still likely, and it will land
whenever the database moves off the box (the Day-90 Hetzner migration in
`Docs/Hosting.md §8` is the obvious moment). Re-drill on the first run there.

**The off-provider copy is live as of 2026-08-15.** Every nightly dump is
uploaded to the private `primer-backups` R2 bucket under `db/`, with the same
seven-day retention swept at both ends, and `launch_check.durability.backup_offsite`
reports the configuration. Verified on the day: `404` from the media CDN
hostname for the dump's key, which is what proves the backup bucket is not
sitting behind a public URL.

It has **its own R2 token**, scoped to `Object Read & Write` on that bucket
alone. The media token turned out to be bucket-scoped rather than account-wide —
the first upload failed `AccessDenied` — and a second token was the right answer
anyway: the media credentials live in a container that serves public traffic and
have no business being able to write to the backups.

---

## If you are actually restoring, not drilling

1. **Stop the writers first.** Scale the web and worker processes to zero.
   Restoring under live traffic gives you a database that disagrees with itself.
2. **Restore into a new database, then repoint.** Never over the live one. The
   broken database is evidence, and it is also the fallback if the restore is
   worse than the damage.
3. **Run `manage.py restore_drill` before repointing.** Same command, same
   questions. If memories do not decrypt, the problem is the key and not the
   dump, and repointing would make it everybody's problem.
4. **Re-apply every deletion the dump predates. This step is not optional.**
   A restore brings back families who asked to be erased between the dump and the
   disaster, and `apps/accounts/privacy.py` tells parents in writing that a
   deleted family is never restored from a copy. That sentence is true because
   this step exists, so skipping it makes the disclosure false rather than merely
   making the ops work sloppy.

   The receipts are how you know who: `accounts_deletionreceipt` is append-only,
   is *not* cascaded by a parent delete, and every row carries the deleted
   parent's id and the timestamp.

   ```sql
   -- Every account deleted after the dump was taken. Re-delete each one.
   SELECT parent_id_at_deletion, deleted_at
     FROM accounts_deletionreceipt
    WHERE deleted_at > '<the dump's timestamp>'
    ORDER BY deleted_at;
   ```

   Delete each parent through the ordinary path (`manage.py shell` →
   `User.objects.get(pk=…).delete()`, having first run
   `media_service.delete_child_media` for each child, exactly as
   `AccountDeleteView` does) so the object storage goes too. Then record the
   re-deletions in the drill log below.

   *Caveat, stated rather than glossed:* the receipt table is restored **from the
   same dump**, so receipts written after the dump are not in the restored
   database. Take the list from the surviving production database or from the
   receipts in the newest dump *before* you repoint, and keep it outside the
   database while you work.
5. **Then `manage.py launch_check`** against the restored deployment before
   letting a child back in.
6. **Write down what happened**, here, with the timestamps — including the
   re-applied deletions from step 4.
