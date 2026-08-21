# Deploys

**Pushing to `main` deploys.** That is the whole procedure. This file is for the
five minutes of setup, and for the days something goes wrong.

Until 21 August 2026 this was not true: `deploy.yml` existed, looked like a
pipeline, and its release step was an `echo` that exited 0. Every release for
weeks was green and shipped nothing, and getting a change live meant ten commands
typed on the box. If you are reading an older document that says deploys are
manual — `Production-Facts.md` before this date, or the `deploy-is-manual` note —
it is describing that.

---

## Setup, once

```bash
bash infra/deploy-key-setup.sh
```

Run it from anywhere `gcloud` is authenticated — laptop or Cloud Shell, it makes
no difference, because everything it does to the box it does over
`gcloud compute ssh`. It:

1. generates a dedicated ed25519 key pair;
2. appends the public half to `~/.ssh/authorized_keys` on `app-server`;
3. proves the key can log in and run a command, *before* telling you it worked;
4. records the host key so the workflow is not trusting-on-first-use forever;
5. stores the private half as an **environment secret** on `production`, via the
   `gh` CLI if you have it, or prints it for pasting if you do not;
6. deletes the private half from your machine.

Safe to re-run — it replaces its own line in `authorized_keys` rather than
growing the file. Revoking it is one line, which the script prints when it
finishes; your own SSH access is unaffected.

### The four secrets

On **Settings → Environments → production → Environment secrets**, not repository
secrets. An environment secret is only readable by a job that declares
`environment: production`, which is the release job and nothing else — so a
workflow added later, or one running from a fork's pull request, cannot read a
live production login.

| | |
|---|---|
| `DEPLOY_SSH_KEY` | the private half, `BEGIN`/`END` lines included |
| `DEPLOY_HOST` | `34.180.8.71` |
| `DEPLOY_USER` | `ullasgowda3523` |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan` output. Optional; without it the workflow warns and accepts whatever answers |

Optional repository **variable** `DEPLOY_APP_DIR` if the app ever moves off
`~/app`.

---

## What happens on a push

```
push to main
   │
   ├─ Gate ──────────── the whole of ci.yml on this exact commit:
   │                    Evals, Backend, Frontend, Dependencies, image build
   │                    red anywhere  ->  Release never starts
   │
   └─ Release ───────── only if the gate is green
        ├─ take the PWA the Frontend job already built and tested
        ├─ scp it and infra/deploy.sh to the box
        └─ run infra/deploy.sh there
```

`infra/deploy.sh` is the interesting half, and it is readable — every branch in it
is a mistake that was made on this box at least once. In order:

1. **Fetch, and refuse to move backwards.** It deploys the SHA the gate graded,
   not `origin/main` at the moment somebody got round to typing `git pull`. If
   the SHA it was handed is an *ancestor* of what is already running, it exits 0
   and changes nothing — that is a queued deploy of a superseded commit, and
   shipping it would un-ship the newer work.
2. **Reset the tree**, saving a patch first if anything tracked had been edited
   on the box. It does not stop for that; a deploy that stops at 2am for a stray
   edit is worse than one that keeps a copy and carries on.
3. **Work out what actually changed.** A docs-only commit restarts nothing. A
   frontend-only commit swaps a directory and never touches a container. Only
   `backend/`, `Dockerfile.backend` or `docker-compose.yml` cause a rebuild.
   This is the point of the exercise — "after every smallest change" should not
   mean a two-minute image rebuild on a 2-vCPU box.
4. **Swap `frontend/dist` atomically**, via two renames. Caddy serves that
   directory straight off disk, so an in-place rebuild means a window where a
   child's browser asks for a chunk that no longer exists.
5. **Migrate on the new image, then recreate** — in that order, so no new code
   serves a request against the old schema.
6. **Probe `/api/health/`** with `X-Forwarded-Proto: https` and a real `Host`.
   Without those headers `SECURE_SSL_REDIRECT` returns 301 before URL resolution
   and the probe reports 301 whether the app is healthy, broken or absent.
7. **Revert by itself** if the probe fails: previous commit back, previous PWA
   build back, rebuild, restart, and a message naming the two things it cannot
   undo (migrations already applied, and anything the new code wrote).
8. **Prune dangling images.** An image per deploy on a 29 GB disk shared with
   Postgres fills it in a couple of months.

The PWA is built on the GitHub runner, never on the box: `app-server` is an
e2-medium whose 3.8 GiB is already shared with Postgres, Redis, Celery and the
web tier. It is also the right artifact on principle — production serves the
exact bytes the Frontend job's tests ran against.

**A deploy touches no configuration.** `.env` is owned by
`infra/push-credentials.sh`; keeping them separate means a push cannot break the
providers.

---

## Day to day

```bash
make deploy-status          # what commit is live, is it answering, is it behind main
make deploy-logs            # tail primer-web-1
```

`deploy-status` answers "did my push land?" with a SHA you can compare against
`git rev-parse HEAD`, rather than a feeling about whether CI was green.

### Rolling back

The workflow's job summary prints the command with the previous SHA already in
it. Otherwise:

```bash
make deploy-status                        # note previous_sha
make deploy-rollback SHA=<previous-sha>
```

`FORCE=1` is what makes it willing to go backwards; without it the ancestor guard
would decline.

Reverting the commit and pushing works too, and is better when the bad change is
going to stay reverted — it leaves `main` and production agreeing.

### Deploying when CI is broken

```bash
make deploy
```

Skips the gate. It exists for the afternoon GitHub is down, and it says so before
it runs. It is not the routine path, and using it routinely would make
`evals/test_gate.py` a decoration.

---

## When it goes wrong

**`Permission denied (publickey)`** — the key is not on the box, or OS Login is
enabled and rewrote `authorized_keys`. Re-run `deploy-key-setup.sh`; it warns
about OS Login explicitly and prints the `gcloud compute os-login ssh-keys add`
alternative.

**The job hangs on `ssh`** — port 22 is not reachable from GitHub's runners,
which come from arbitrary addresses.
`gcloud compute firewall-rules list --filter='allowed.ports:22'`.

**`health check failed; <sha> was not kept`** — the deploy already reverted
itself, so production is on the previous commit and serving. Read
`docker logs --tail 200 primer-web-1` for why. If the failure was a migration,
the schema is now ahead of the running code; `showmigrations` will tell you.

**A new Django route 404s with HTTP 200** — nothing to do with the deploy. Caddy's
catch-all serves the PWA, so any non-`/api`, non-`/admin`, non-`/admax` path
resolves to React Router. It needs its own `handle` block in
`/etc/caddy/Caddyfile` and a `sudo systemctl reload caddy`. Deliberately *not*
automated: the deploy has no sudo, and a pipeline that could rewrite the reverse
proxy is a pipeline that can take the site off the internet. See
`Production-Facts.md`.

**Two pushes in a row** — they queue; `cancel-in-progress` is false, because
cancelling a run between `migrate` and `up -d` is worse than either finishing or
never starting. The older one then no-ops on the ancestor guard.

---

## The gate is still the gate

§19.7 — *"a change that regresses safety or coherence beyond threshold cannot
deploy"* — is unchanged by any of this. `release` still `needs: [gate]`, `gate`
is still the whole of `ci.yml`, and `evals/test_gate.py` still asserts that chain
link by link and fails if `continue-on-error` appears in either workflow.

What changed is the argument the old file made for tags-only: that deploying
every green commit *"would make the gate the only thing between a Tuesday
afternoon refactor and a four-year-old."* The answer is that the gate is no
longer the only thing — the health probe and the automatic revert are now
downstream of it, and neither existed when that comment was written. The risk was
paid down rather than avoided.

**If you want a human back in the loop**, add a required reviewer to the
`production` environment. The release job already targets it, so every deploy
would then wait for one tap of Approve and nothing in the repository needs to
change. `v*` tags still deploy as well, so tagging remains a way to ship a
considered version.
