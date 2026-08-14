# Runbook — the closed alpha launch (Milestone M2)

> §6.1: *"A parent signs up, creates a child profile and companion in ~3 minutes,
> and the child plays a short, personalized, illustrated, voiced branching
> adventure; the next day the companion greets them by name and references what
> happened yesterday; the parent can read a simple summary of what their child
> experienced and learned."*

M2 is that loop, live, for 10–25 real families. This runbook is the sequence for
getting there and the standard for stopping.

**The one rule.** Nothing on this page is optional because it is inconvenient.
The three stop-the-line conditions are stated at the bottom, and each of them has
a command that answers it.

---

## Before anything: what is done and what is not

**Done in code, verified, and gating** — these are facts, not intentions:

| | Evidence |
|---|---|
| Eval suite gates the deploy | `make evals`; green in CI on its first run (`31665610393`, 41 passed / 1 skipped). `Docs/Runbooks/Eval-Gate.md` has the drill log — **the forced-fail half is done locally on all five regressions; the CI red row is still owed** |
| Restore drill, run and shown to fail | `Docs/Runbooks/Backup-Restore.md` drill log, both rows — **against the compose Postgres, not a managed one** |
| Feature flags + prompt rollback | `make flags`, `make prompts`; `apps/common/tests/test_flags.py`, `apps/safety/tests/test_prompt_rollback.py` |
| Budget breaker → warm content | `apps/story/tests/test_degradation.py` — plays a real adventure with the breaker open |
| Every child error path degrades | `frontend/src/test/degradation.test.tsx`, `fallback.test.tsx` |
| Preflight | `make launch-check STRICT=--strict` |

**Not done, and needing the founder rather than code:**

- production is not provisioned (see `Docs/Hosting.md` — GCP VM, docker-compose,
  Cloudflare R2, moving to Hetzner around day 90);
- no real family has been onboarded;
- the restore drill has not been run against a *managed* Postgres, only against
  the compose stack.

---

## 1. Provision (founder)

Follow `DEPLOY.md` for the process shape and `Docs/Hosting.md` for the vendors.
The environment variables that are new as of Day 15 and have **no safe default**:

| Variable | Why it has no default |
|---|---|
| `INFERENCE_BUDGET_USD_PER_WINDOW` | The spend cap (§18.8). `prod.py` refuses to boot without it, because the right number comes from *this* deployment's cost/session and a plausible-looking default would be a throttle on a real family's story chosen by nobody. Size it: run `make launch-check` and `manage.py metrics` after a week of dogfooding, take cost/session × expected sessions per window, and **double it**. The breaker is a cliff, not a dial — you want it far above normal and far below a runaway. |
| `INFERENCE_BUDGET_WINDOW_S` | Defaults to 3600. An hour is long enough that a busy bedtime does not trip it and short enough that a trip clears itself the same evening. |
| `MEMORY_ENCRYPTION_KEY` | §11.10. Already required since Day 8. Generate it once, store it where the database backups are *not*. |

**And one credential to replace rather than copy:**

- [ ] **Issue a fresh Vertex service-account key, and delete the old one.** The
      key used during the build sat in a OneDrive-synced folder. No key material
      has ever been committed — the Vertex review checked every commit in the
      history, and `.gitignore` covers the filename patterns — but a key that has
      been through a sync client's cache has left the box it was issued for, and
      that is the only fact that matters. Create the new one, point
      `GOOGLE_APPLICATION_CREDENTIALS` at it **outside the repository**
      (`~/.config/primer/vertex-sa.json`, owner-read-only), confirm the app boots,
      then **delete the old key in the GCP console** — a rotation that leaves the
      old key valid is a copy, not a rotation.

Then, with the app running and before any child touches it:

```bash
python manage.py migrate
python manage.py launch_check --strict     # must exit 0
```

`--strict` is what a release should use: it refuses on warnings too, including
the restore-drill warning, which is the command's honest admission that it cannot
tell whether a backup has ever been put back.

## 2. Turn on the things that only exist in a provider's dashboard

- [ ] **Postgres PITR** — continuous WAL archiving, retention ≥ 7 days.
- [ ] **Bucket versioning + replication** on the R2/S3 bucket. A restored database
      with an empty bucket is a child's history pointing at nothing (§16.6).
- [ ] **The dump cron** — `infra/backup/backup.sh`, daily, writing somewhere that
      is not the database provider.
- [ ] **An alert on `budget.tripped`** in the log stream. The breaker writes one
      ERROR line per window and a `SafetyIncident` row; the child sees warm
      fallback content and nobody else finds out unless somebody is watching.
- [ ] **An alert on `safety.incident` at high/critical severity.** §22.5 calls a
      serious incident a stop-the-line event, which requires somebody to be told.
- [ ] **A GitHub environment named `production`**, which `deploy.yml`'s `release`
      job requires. Add yourself as a required reviewer on it: it is the last
      place a human sits between a tag and a four-year-old, and it costs one
      click per release.
- [ ] **GCP budget alerts on the Vertex project** (`Docs/Hosting.md §5`).
      `INFERENCE_BUDGET_USD_PER_WINDOW` is our own breaker and it works — over the
      cap children get cached art and pre-vetted warm beats, never an error — but
      it can only stop what it counts, and it counts what `cost.record` sees. The
      budget alert is the one that catches a runaway our meter under-reads, which
      is the whole reason it belongs to a different system from ours.
- [ ] **Decide on a Vertex data-logging exemption** (`Docs/Hosting.md §3`).
      Separate from §13.8's training exclusion, which is settled: Vertex retains
      prompts for its own **abuse monitoring**, and for this product that means a
      child's first name, fears, retrieved memories and the verbatim session
      digest sitting in a Google-side log for that period (§13.7). Google grants
      an exemption per project on request and it is the only lever there is. Read
      the current terms, decide, and write the decision down — either answer is
      defensible for a closed alpha; not having looked is not.

## 3. Run the drills against production, once

```bash
sh infra/backup/restore-drill.sh                    # against the real dump
PRIMER_EVAL_FORCE_REGRESSION=cliffhanger_close pytest evals/ -q   # must go red
```

The first will find provider-specific friction — role ownership, whether the
`vector` extension can be created by your role — and that has to happen **before**
the first family, not after.

## 4. Walk the loop yourself, as a family

The founder manual test, in production, on a tablet, as a stranger would:

1. sign up, consent, create a child, choose a companion — **under 4 minutes**;
2. hand the tablet to a child; they meet the companion and play one adventure end
   to end — illustrated, voiced, branching, and it **finishes warm**;
3. **come back tomorrow.** The companion greets them by name and references
   yesterday. This is the day-two payoff and the whole retention thesis; a launch
   that has not observed it once has not tested the product;
4. read the parent report. It should say something true and specific about what
   the child actually did.

Then `make launch-check` and `manage.py metrics` again, and read the cost/session
figure. That number sizes the breaker in step 1.

## 5. Onboard 10–25 families

**Closed, and closed on purpose.** There is deliberately no invite-code feature —
that would have been a new product feature on hardening day. The alpha is closed
by not sharing the URL, which is sufficient at this size and has no code to get
wrong.

Per family, before the tablet reaches the child:

- [ ] the parent has consented (the flow enforces this; §13.7);
- [ ] the parent knows how to reach you, and that this is an alpha;
- [ ] the parent knows about `/parent/privacy` — export and delete, both real
      (§13.8, and the Day-13 review proved the media deletion).

**Feedback channel:** one address or one group chat, and a standing question that
is not "how was it?" — ask *"did they ask to come back?"*, because that is the
North Star (§22.1) in the only form a parent can answer.

## 6. Watch, daily

```bash
python manage.py metrics --days 7
```

Read in this order, because the order is the priority:

1. **safety incidents** — must trend to ~0. Any high or critical is stop-the-line.
2. **the breaker** — `TRIPPED` means children got cached and fallback content.
   `NOT ENFORCING` means Redis is unreadable and there is no cap at all, which is
   worse than tripped and looks better.
3. **guardrails** — sitting length (over 30 min is flagged), max sittings per
   child per day, `ended_reasons`. `cap` appearing is the wellbeing control
   working (§13.6), not a failure.
4. **day-2 return** — the thesis. Below 40% with real families is a product
   finding, not a bug.
5. **cost/session** — with `unpriced_spans` beside it. An unpriced route means the
   number is missing a term.

The safety incident queue is `/admin/` → Safety incidents, filtered to open,
worst first. **Work it daily.** `launch_check` refuses to pass with an unreviewed
high-or-critical incident, which is the mechanism that stops a queue from
becoming a list.

---

## Stop the line

Three conditions. Each has a command that answers it and an action that is not
"discuss it".

| Condition | How you know | Do this |
|---|---|---|
| **A child was shown something unsafe** | A high/critical incident, or a parent tells you | Stop onboarding. `make flags ARGS="set media.illustrations off"` if it is an image; add the case to `apps/safety/redteam/corpus.py` and watch it fail; fix; `make evals`. |
| **A prompt or model change made things worse** | Evals red, or the incident rate moved | `make prompts ARGS="rollback system_identity <last good>"` or `make flags ARGS="set ai.beat_model <last good>"`. Both take effect in seconds with no deploy — that is what they are for. |
| **Spend is running away** | `budget.tripped`, or cost/session climbing | The breaker has already stopped it and children are getting cached and pre-vetted content. To spend *less* right now, pull the two levers that need no deploy: `make flags ARGS="set media.illustrations off"` and `set story.prefetch_next_beat off`. Then find the cause in `manage.py metrics`'s by-operation split. **Lowering the cap itself is a settings change and a restart** — it is not a flag, deliberately, because a cap somebody can drop in a hurry is a cap that ends up at zero. |

**What is never a valid response to any of these:** switching off the safety
classifier, the image gate, or the fear ceiling to get throughput back. There is
no feature flag that can do it (`apps/common/tests/test_flags.py` asserts the
registry stays free of one) and `prod.py` refuses to boot with them off. That is
deliberate, and it is the constraint the rest of this runbook is written inside.
