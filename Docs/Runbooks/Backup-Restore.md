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
| **Off-provider dumps** | *"the account is gone / compromised / the project was deleted"* | `infra/backup/backup.sh`, run on a schedule, written somewhere that is not the database provider. |

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

On a machine with no Postgres client tools (a Windows dev box, for instance),
point the script at the ones in the compose stack:

```bash
export PG_RUN="docker compose -f infra/docker-compose.yml exec -T db"
```

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

> Both rows are needed. A drill that has only ever passed has not been shown to
> be capable of failing.

**Not yet drilled:** a restore against a *production* managed Postgres, because
production is not provisioned yet (see `Docs/Runbooks/Launch.md`). The scripts
are provider-agnostic — `DATABASE_URL` and standard client tools — but the first
run against a real provider will find provider-specific friction (role
ownership, the `vector` extension needing to be enabled by the provider rather
than by `CREATE EXTENSION`), and it must happen **before** the first family is
onboarded, not after.

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
4. **Then `manage.py launch_check`** against the restored deployment before
   letting a child back in.
5. **Write down what happened**, here, with the timestamps.
