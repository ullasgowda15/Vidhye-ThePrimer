# Before You Show a Family — Readiness Gate + YC Application

> Two parts. **Part A** is the hard gate: nothing here is optional because it's inconvenient — these are real children. **Part B** is what to update in your YC application now that the MVP exists and is going into a closed alpha.
>
> Sequence: finish the **local manual test** → run the **Day-1–15 audit** and clear P0/P1 → **deploy** (`Deploy-GCP.md`) → **production manual test** → clear **Part A** below → onboard families (`Launch.md §5`).

---

## Part A — The hard gate (every box before any family)

### Product proven
- [ ] `docs/Test/manual_testing.md` A–L passed **locally with real keys**.
- [ ] Part M passed **on production**, including the **day-two greeting observed live at least once**. *(A launch that hasn't seen the day-two payoff hasn't tested the product.)*
- [ ] The onboarding loop (signup → consent → child → companion) completes in **under 4 minutes** on a real device.

### Safety & audit
- [ ] Day-1–15 audit (`docs/Audit/Day1-15-audit-brief.md`) run, report written, **no open P0 or P1** (fixed or proven absent).
- [ ] Safety incident queue (`/admin/` → Safety incidents) is reachable and you have a plan to **work it daily**. Alerts wired on high/critical.
- [ ] `make launch-check --strict` exits **0** on production.
- [ ] The independent safety grader is on (`SAFETY_CLASSIFIER_ENABLED=true`); image moderation on; the fallback pool exists. (`launch_check` covers these.)

### Data safety & durability
- [ ] **Backups running:** daily `pg_dump` cron to storage that is *not* the DB provider; **Postgres PITR** on; **R2 bucket versioning** on.
- [ ] **Restore drill passed against the managed Postgres** (not just compose) — and shown to fail on a wrong encryption key.
- [ ] `MEMORY_ENCRYPTION_KEY` is a standalone key, stored **separately from the backups**.
- [ ] **Delete works completely** — a deleted child/account leaves no rows, no memory vectors, and **no media in R2** (verified in Part M / the audit). Export works and contains only that family's data.

### Cost & spend safety
- [ ] `INFERENCE_BUDGET_USD_PER_WINDOW` set to a real number; the budget breaker trips to warm content, and a `budget.tripped` alert exists.
- [ ] GCP budget alerts (`$50/$150/$250`) and an OpenAI usage cap are set. **All four child-data routes — beats, safety, memory, embeddings — are on paid, data-excluded tiers** (§13.8, `Hosting.md §3`); `launch_check`'s `privacy.provider_terms` row is `OK`.

### Deploy & security hygiene
- [ ] HTTPS with a valid cert; `DEBUG=0`; `ALLOWED_HOSTS`/`CORS` correct; HSTS + secure cookies on.
- [ ] **Same-site** PWA + API (parent login + refresh confirmed working in Part M).
- [ ] No secrets in the repo or its history; `.env` is `600` and gitignored.
- [ ] The ship gate proven to block: forced-regression turns CI red, and the CI **red row** where `release` is skipped is recorded.

### Per family, before the tablet reaches the child (`Launch.md §5`)
- [ ] Parent has **consented** (the flow enforces it).
- [ ] Parent knows this is a **closed alpha**, and how to **reach you**.
- [ ] Parent knows about `/parent/privacy` — **export and delete are both real**.
- [ ] A feedback channel exists, with the standing question that is the North Star (§22.1): **"Did they ask to come back?"**

> **Stop-the-line (from `Launch.md`):** any high/critical safety incident; the breaker reading `NOT ENFORCING` (Redis unreadable = no cap at all); day-2 return below 40% is a *product* finding to sit with, not a bug to hide.

---

## Part B — Update your YC application

You now have something most applications don't at this stage: **a built, safety-audited MVP going into a real closed alpha.** Lead with that. (These are suggestions — the wording is yours. If you paste me the actual YC questions/your current answers, I'll help draft each one specifically.)

### What to change now
- **Progress / "what have you built":** move from "building" to **"MVP built and in closed alpha with N families."** Name the loop concretely: a parent sets up a companion in ~3 minutes; the child plays a personalized, illustrated, voiced, branching adventure; **the next day the companion greets them by name and references what they did yesterday**; the parent reads an honest summary of what their child learned — with learning happening invisibly, under the story, and a safety layer in front of every beat.
- **The one-line hook:** lead with the moat — *a learning companion that actually remembers your child*, and teaches without a single quiz or score. That "remembered" moment is the retention thesis; make it the sentence they can't forget.
- **Traction / metrics:** you already instrument the numbers YC cares about (§22.3) — **activation, day-2 return (your North Star), week-1 retention, completion rate.** As soon as the alpha has a few days of real data, put the real numbers in. Even N=10–25 with a strong day-2-return number is a compelling signal for a kids' product.
- **The demo:** record a **60–90 second screen capture** of the loop, and make sure it shows the **day-two greeting** — that's the emotional beat that sells it. A crisp demo of "the companion remembered" is worth more than any slide.
- **Why now / why you:** solo founder shipped a full, safety-first product in a 15-day sprint with an independent review + audit on every stage. That's a credible execution story — say it plainly.

### Be honest about stage (YC rewards this)
- It's an **alpha**, N is small, and the thing you're testing is the **retention thesis** (do children ask to come back). Frame the alpha as the experiment that answers it.
- Name the real risks you're managing — child safety (and how: independent grader, ceilings, no raw errors, deletion that actually deletes) and cost at scale (and how: provider-agnostic router, free-tier-first, budget breaker). Showing you've thought about safety for a children's product is a differentiator, not a liability.

### What YC will ask that you should have an answer ready for
- **Distribution:** how do the *next* 100 families find you after these 15? Have a one-line plan.
- **Unit economics:** you can answer this now — cost/session from `manage.py metrics`, and how it trends with cheaper models. Have the number.
- **The 10x question:** why a personalized, remembering companion beats YouTube Kids / a generic app for a 3–7-year-old and their parent.
- **Regulatory:** COPPA/GDPR for children's data — you can point to real controls (consent gating, encryption, verified deletion, data export), which most applicants can't.

### Before you hit submit / send an update
- [ ] Demo video recorded (loop + day-two greeting).
- [ ] Real early metrics from the alpha included, even if N is tiny.
- [ ] The safety + privacy story stated (it's a strength for a kids' product).
- [ ] One-line answers ready for distribution, unit economics, and the 10x question.

> Want help drafting the actual answers? Share the specific YC questions (or your current draft) and I'll write tight, honest responses grounded in what we actually built — including pulling the real metric numbers once the alpha is running.
