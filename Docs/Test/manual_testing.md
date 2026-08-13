# The Primer — A–Z Manual Test

> **Purpose:** play the whole product by hand, as a real family would, and prove every feature *and* every safety guarantee works with **real providers** — the things no automated test can settle (greeting warmth, invisible learning, whether it feels like being *seen*, and that no child ever sees a spinner, error, score, or unsafe content).
>
> **How to run it (decided):** do the full pass **A–L locally with real API keys first**, fix anything it turns up, then run **Part M (production verification)** after deploy. Local-with-real-keys separates *product* bugs from *infra* bugs; testing only on prod means you can't tell which you're chasing.
>
> **Keys:** see `docs/Test/test-prerequisites.md` and `docs/Hosting.md §12`. You need real Gemini (beats free + safety/memory paid), OpenAI embeddings, Cloudflare Workers AI + R2, Google TTS (+ ElevenLabs failover). **Do not run this on the free/offline setup** — recall is lexical and "voice" is a chime, so it will lie to you about the moat.
>
> **How to use each step:** every ⬜ is a thing to *look at and judge*, not just click. Capture exact quotes (especially the two greetings) in the results table at the end.

---

## Part 0 — Bring the stack up locally (real keys)

1. Put real credentials in `.env` (per `test-prerequisites.md`). Confirm **all four child-data routes are paid/data-excluded** (no `:free` suffix): `AI_PROVIDER_ORDER=google` (the registered name — *not* `gemini`), `AI_BEAT_MODEL=gemini-2.5-flash`, `AI_SAFETY_MODEL=gemini-2.5-flash-lite` (must differ), `AI_MEMORY_MODEL=gemini-2.5-flash-lite` (set explicitly), `EMBEDDING_PROVIDER=openai`. Gemini runs on **Vertex AI**, so set `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global` (**not `asia-south1` — it does not serve Flash-Lite**, so the safety grader would 404 on every call) and `GOOGLE_APPLICATION_CREDENTIALS` pointing at the service-account JSON **outside this repo**. Plus `TTS_PROVIDER=google`, `IMAGE_PROVIDER=cloudflare`, `IMAGE_MODERATION=auto` with `IMAGE_MODERATION_PROVIDER=google` and `IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite` (never `off`), `MEDIA_STORAGE=local` (local is fine for this pass; prod uses R2).
2. Four terminals: `make up` → `make migrate` → **A:** `make run` · **B:** `make worker` · **C:** `make front`.
3. Sanity gates — do not proceed until all pass:
   - `make ping` → worker is live.
   - `make hello-beat` → a real validated beat generates (proves AI keys work).
   - `make launch-check` → read the output; blocking items are expected locally (budget, managed-PG), but `privacy.provider_terms` must be **OK** (a `:free` child-data model blocks it — drop the `:free` suffix, don't disable the check), and nothing else should error unexpectedly.
4. **Close the two owed safety checks (you now have real keys):**
   - `pytest evals/ -q --live --spotcheck` (or `make evals-live`) → the `--live` eval pass; everything about model behaviour otherwise rests on the scripted provider.
   - Confirm the **image moderator actually runs on real generated art** — with `IMAGE_MODERATION=auto` and `IMAGE_MODERATION_PROVIDER=google`, watch the logs during Part C's companion/beat art for `media.scan ... moderation=passed`. If you see `moderation=absent`, the gate did **not** run and the picture was only structurally checked. It's the fail-closed recall layer behind the F-1/F-2 companion-injection fix; a single test PNG has now proven the Vertex path, but no real illustration has been through it and nothing has ever been *blocked* live.

⬜ All four processes up; ping + hello-beat succeed; `privacy.provider_terms` OK; `--live` evals pass; image moderator observed running.

---

## Part A — Parent onboarding & consent

1. Open `http://localhost:5173`. **Sign up** as a parent.
2. **Verify email** — Day 2 logs the verification link to Terminal A's console; copy it from there and open it.
3. **Give consent** on the consent screen.
4. **Create Child A** — real name, age **5**, and a genuine interest (e.g. *frogs* or *dinosaurs*). Note the interest.

⬜ Signup → consent → child creation completes in **under 4 minutes**.
⬜ You cannot create a child *before* consenting (the flow blocks it).
⬜ No raw errors; every screen is calm and clear.

---

## Part B — Companion creation

1. **Meet the companion** (first-meeting flow). **Name it something memorable** — e.g. *Sparkle*. **Write the name down.**
2. Wait for the companion's reference art + voice to generate (the worker does this — watch Terminal B).

⬜ The companion has **illustrated** reference art (not placeholder).
⬜ The companion **speaks** (real voice, not a chime).
⬜ Naming felt warm and personal, not like a form.

---

## Part C — First adventure (full play)

1. **Start an adventure.** Play it fully to the end:
   - At the first branch (two cards), make a **deliberate, memorable choice** — write down which (e.g. *"followed the sparkling river"*).
   - When asked to **count**, count **correctly**.
2. Watch the whole beat lifecycle: plan → illustrated, voiced, branching beats → warm ending.

⬜ Every beat is **illustrated and voiced**; choices branch.
⬜ A child **never** sees a spinner, a raw error, a stack trace, or a score — a slow/failed anything still shows the companion and something to tap.
⬜ The counting moment read like **story**, not a quiz.
⬜ Latency per beat is acceptable for a small child's attention (note it).
3. **End the adventure** (this enqueues consolidation + mastery + interest + report on the worker — watch Terminal B run them).
4. Get Child A's UUID from `/admin/` → Children.

---

## Part D — Invisible learning & humane difficulty

1. In an adventure, **deliberately struggle** on a counting beat (wrong number, or retry).
   ⬜ The companion **gently helps** ("Let's count them together — I'll help you"), marks it assisted, accepts your next tap. **No failure, no mark.** The next ask **scaffolds** (fewer things / narrower).
2. On another beat, answer **quickly and correctly**.
   ⬜ The next ask **stretches** — visibly *more* things to count.
3. **Read the learning beats** (beat 2, and the one before the ending).
   ⬜ Narration never says *count / lesson / practice / quiz / test / score / number / objective* — it's a riverbank with frogs, not a worksheet.
4. `/admin/` → LearningProgress for Child A.
   ⬜ Counting mastery **moved off 0**, `evidence_count > 0` — with **no score ever shown to the child**.

---

## Part E — Memory & the day-two greeting (THE MOAT)

**Verify the deposit landed (server side):**

- `make memories CHILD=<A-uuid>` → shows written memories: the **named companion**, the **choice**, the **loved thing**.
- `make memories CHILD=<A-uuid> MEM_ARGS="--recall follow the sparkling river"` → the exact bundle a greeting would get, ranked with `sim/sal/rec`. With a **real embedding key** the relevant memory ranks up on *meaning*.

⬜ Memories written and specific. ⬜ Recall bundle looks relevant (semantic, not just shared words).

**The day-two greeting:** return to the app as Child A → tap the **star** → `/welcome/:childId`.

**"Right" only if ALL hold:**

⬜ Greets Child A **by name**.
⬜ References a **specific, real** thing from yesterday (the choice / companion name / loved thing) — **not** a generic "Welcome back!".
⬜ Feels like **being seen / reciprocity** (e.g. *"You named me Sparkle…"*), warm, in the companion's voice.
⬜ **Never** an empty "Welcome back!" with nothing specific under it.
⬜ Flows into today's new adventure.

> If this moment doesn't land emotionally, stop and note it — this is the whole retention thesis.

---

## Part F — Second child, personalization & zero cross-child bleed

1. Create **Child B** — a **different interest** (e.g. ocean/whales), name the companion something **distinct** (e.g. *Thunderclap*). Play a **different** session (different choice, different count). End it.
2. Compare Child A's and Child B's adventures:

⬜ Different **world / palette / place / inhabitants** — structurally different, not a swapped noun.
⬜ A **night-friendly** palette variant applies if you set the clock/theme to evening.
3. Play Child B a second adventure:
   ⬜ A **recurring character or place** persists and looks the **same** across the two adventures.
4. Return "day two" as **Child B** → `/welcome/:childId`:
   ⬜ Greeting references **B's** history, clearly different from A's.
   ⬜ **Zero bleed** — B's greeting never mentions A's companion, choice, or loved thing. (Cross-check: `make memories CHILD=<B-uuid>` shows only B's rows.)

---

## Part G — Safety settings (the core of the product's promise)

Set on Child A (via the parent Settings page): an **avoid-topic** (e.g. *hospitals*), a **low intensity ceiling** (gentlest), a **short session cap**, and a **daily time window**.

⬜ **Avoid-topic never appears** — play several adventures and actively try to steer toward it; the topic never shows in narration, on screen, or in the voice.
⬜ **Ceiling respected** — nothing frightening beyond the gentlest level; worlds/places stay gentle.
⬜ **Session cap → warm close** — at the cap the story *lands* warmly (no cut-off, no "time's up", no two choice cards under a goodbye), and tapping "Again!" does **not** restart the cap.
⬜ **Time window** — starting a new story outside the window is refused (403), but a story already under way is allowed to finish.

---

## Part H — Accessibility

On the child surface, toggle each and confirm it actually changes the experience:

⬜ **Captions / words on screen** appear.
⬜ **Reduced motion** — animations calm down.
⬜ **Dyslexia-friendly font** applies (line spacing opens up).
⬜ **High contrast** — colours shift to the high-contrast palette (day and night).
⬜ **Adjustable audio volume** actually changes loudness.
⬜ **Voice off** — the audio element isn't fetched, and words appear on screen instead (a beat is never both silent *and* wordless).

---

## Part I — Parent dashboard

Open the parent dashboard for Child A:

⬜ **Session report** — reads *true and specific* about what the child actually did (check it against what you remember doing). No internal/machine text leaked in.
⬜ **Weekly report** — sessions, completion, a real highlight; a milestone is only announced when actually newly reached (not re-announced weekly).
⬜ **Insights** — plain-language status, mapped to milestones. **No numbers, no %, no grades, no "behind."** A milestone being practised does not read "Not covered yet".
⬜ **Transparency** — actual stories shown **verbatim** (narration, the child's answers), with safety status visible.
⬜ **Calm theme** across all pages; parent errors are shown (a parent gets a sentence they can act on — the inverse of the child surface).

---

## Part J — Privacy & data rights

⬜ **Export** — download the data bundle. It contains this child's stories verbatim, memories, learning, reports — and **nothing from any other family**.
⬜ **Delete child** — after deleting, confirm in `/admin/` the child's rows are gone, **and** that their companion pictures + narration audio are gone from storage (not just orphaned). *(Local: check `backend/media/`. Prod: check the R2 bucket.)*
⬜ **Delete account** — requires the password; removes every child's data and media.
⬜ **Consent-lapse behaviour** — a lapsed-consent parent can still *read* their own child's dashboard/transparency and export/delete (consent gates *processing*, never *access*), but cannot start a new story.

---

## Part K — Resilience / degradation

⬜ **Provider hiccup** — simulate a failure (e.g. temporarily use a bad image or TTS key) → the child gets **warm fallback** content, never a raw error. Restore the key after.
⬜ **Render error** — the child surface shows a **resting companion**, never a stack trace or "something went wrong".
⬜ **Missing voice** — a beat with no synthesized audio still shows words rather than a silent dead screen.

---

## Part L — Cross-account security spot-check

1. Create a **second parent account** (different family).
2. With the second parent's session, try to open Child A's ids directly (child, adventure, session, transcript, export URLs).

⬜ Every attempt is **refused** (401/403/404) — a second family can never read or modify Child A's data.

> This is a spot-check; the full multi-tenant sweep is in the Day-1–15 audit (step 2). But do it here too.

---

## Part M — Production verification (run AFTER deploy, on production)

You do **not** need to repeat all of A–L on prod. Run this infra-focused pass plus one full happy-path play. Anything that breaks here (having passed locally) is almost certainly infra/config.

**Infra checks:**

⬜ Site loads over **HTTPS**; certificate valid.
⬜ **Same-site**: parent login persists and `/api/v1/auth/refresh` works (the refresh cookie is `SameSite=Lax` — if the PWA and API aren't same-site, sessions silently break; see `DEPLOY.md`).
⬜ **Media serves from R2/CDN** — companion art and audio load from the bucket URL, not local disk.
⬜ **Managed Postgres + pgvector** — migrations applied; a memory recall returns results (proves the vector extension is live).
⬜ **Worker running** — beats have pictures and voice (if art/voice is missing, the Celery worker isn't up).
⬜ **`make launch-check --strict` exits 0** on the server (budget set, encryption key set, media on s3, safety on).
⬜ **Latency** with real providers is acceptable on a real device/tablet.
⬜ **Budget breaker** configured (`INFERENCE_BUDGET_USD_PER_WINDOW` is a real number) and a `budget.tripped` alert exists.

**One full play, on a tablet, as a stranger:**

⬜ Sign up → consent → child → companion in **under 4 minutes**.
⬜ A child plays one adventure end to end — illustrated, voiced, branching, finishes warm.
⬜ **Come back tomorrow** (or simulate next session) → the day-two greeting works **in production**. *(This is the one thing a launch must observe at least once — see `Launch.md §4`.)*
⬜ Parent report reads true and specific.

Then run `make metrics` (or `python manage.py metrics --days 7`) and read **cost/session** — that number sizes your budget breaker.

---

## Results capture

| Area | Local pass? | Prod pass? | Notes / exact quote |
|---|---|---|---|
| Onboarding < 4 min | | | |
| Companion: art + voice | | | |
| Full adventure, no spinner/error/score | | | |
| Struggle → gentle scaffold | | | |
| Ease → stretch | | | |
| Learning beats: no lesson words | | | |
| Mastery moved (no score to child) | | | |
| **Day-two greeting A: by name + real memory** | | | |
| Greeting felt like being *seen* | | | |
| Child B: different world + zero bleed | | | |
| Recurring character/place persists | | | |
| Avoid-topic never appears | | | |
| Ceiling gentle / warm cap / time window | | | |
| Accessibility toggles all work | | | |
| Dashboard: true, specific, no numbers | | | |
| Export / delete child (rows + media) / delete account | | | |
| Degradation: warm fallback, no stack trace | | | |
| Cross-account: all refused | | | |
| **Prod: HTTPS + same-site + R2 + worker + launch-check** | | | |
| **Prod: day-two greeting observed live** | | | |

**Sign-off:** the product is ready to show a family only when every row above is a pass, the day-two greeting has been observed **in production**, and the Day-1–15 audit (step 2) has no open P0/P1. See `docs/Launch/Pre-Family-and-YC.md`.
