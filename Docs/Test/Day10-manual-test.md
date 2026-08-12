# The Primer — Deep Manual Test (through Day 10)

> **Purpose:** play the product end-to-end with **real providers** and judge the two things no test suite can prove — (1) *being remembered* feels real (the moat, Day 9), and (2) *learning is invisible and humane* (Day 10). Also a gut-check on whether we're building the right thing before Day 11 compounds on it.
>
> **Time:** ~45–60 min. **Prereqs:** see `test-prerequisites.md` (keys must be set first, or you'll be testing lexical matching and 35–90s beats, which won't tell you the truth).
>
> Keep a notepad open. Every ⬜ is a thing to look at and judge, not just click.

---

## Part 0 — Bring the stack up (once)

Do these in order. You'll end with **four terminals** running.

1. **Keys in `.env`** — confirm real keys are set (see `test-prerequisites.md`). Critical: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (embeddings), and one TTS key.
2. **Provider/model match** — if you changed `AI_PROVIDER_ORDER`, `AI_BEAT_MODEL` **and** `AI_SAFETY_MODEL` must both be in that provider's form, or Django refuses to boot. Default (Anthropic) needs no change.
3. `make up` — starts Postgres (pgvector) + Redis.
4. `make migrate` — applies schema.
5. **Terminal A:** `make run` — API under uvicorn (SSE needs ASGI).
6. **Terminal B:** `make worker` — Celery. **Required** — consolidation *and* mastery updates run here. If this isn't up, nothing gets remembered and no mastery moves.
7. **Terminal C:** `make front` — the PWA at `http://localhost:5173`.
8. **Sanity checks before playing:**
   - `make ping` → round-trips a task; proves the worker is live.
   - `make hello-beat` → generates one real validated beat; proves your AI keys work. If this errors, fix keys before going further.

⬜ All four processes up, `make ping` returns, `make hello-beat` produces a beat. **Do not proceed until these pass.**

---

## Part 1 — Session 1, Child A ("Day one": create a memory)

The goal here is to *deposit* something specific for the companion to remember tomorrow.

1. In the browser, **sign up** as a parent → verify (Day 2 logs the link to Terminal A's console — copy it from there) → **give consent**.
2. **Create Child A.** Give them a real **interest** (e.g. *frogs*, or *dinosaurs*) and age **5**. Note the interest.
3. **Meet the companion** (first-meeting flow). **Name it something memorable** — e.g. *Sparkle*. **Write down the name.**
4. **Start an adventure.** Play it fully:
   - At the first branch (two cards), make a **deliberate, memorable choice** — write down which one (e.g. *"followed the sparkling river"*).
   - When the companion asks you to **count** something, count it **correctly**.
5. **End the adventure** (finish it — this is what enqueues `consolidate` + `update_from_session` on the worker). Watch Terminal B: you should see those tasks run.

**Judge as you play:**

⬜ A child **never** saw a spinner or a raw error — a failed anything still rendered the companion and something to tap.
⬜ The companion **spoke** (voice played), and art rendered.
⬜ The counting moment felt like **a story**, not a quiz — no "correct/wrong", no score.

**Get Child A's UUID:** `/admin/` → Children → copy A's id. Keep it handy.

**Verify the deposit landed (server side):**

- `make memories CHILD=<A-uuid>` → you should see written long-term memories: the **named companion**, the **choice**, the **loved thing**.
- `make memories CHILD=<A-uuid> MEM_ARGS="--recall follow the sparkling river"` → shows the exact bundle a generation would receive, ranked with `sim/sal/rec`. With a **real embedding key**, the relevant memory should rank up on *meaning*, not just shared words. (This is the Day-9 semantic claim you're here to verify.)
- `/admin/` → LearningProgress for Child A → the counting objective's `mastery` moved off 0.0 and `evidence_count > 0`.

⬜ Memories written and specific. ⬜ Recall bundle looks relevant. ⬜ Mastery moved — with **no score ever shown to the child**.

---

## Part 2 — Session 2, Child A ("Day two": THE MOAT)

This is the single most important moment in the product.

1. Return to the app as **Child A**. From the child home, tap the **star** → it routes to `/welcome/:childId`.
2. **Watch and listen to the greeting.**

**This is "right" only if ALL of these hold:**

⬜ Greets Child A **by name**.
⬜ References a **specific, real** thing from yesterday — the choice, the companion's name, or the loved thing — **not** a generic "Welcome back!".
⬜ Feels like **reciprocity / being seen** (e.g. *"You named me Sparkle…"*), warm, in the companion's voice.
⬜ **Never** an empty "Welcome back!" with nothing specific under it. (If you see that, it's a defect — note it.)
⬜ Then flows naturally into **today's** new adventure.

> If this moment doesn't land emotionally, stop and tell me — this is the retention thesis, and Day 11 builds on it.

---

## Part 3 — Child B (different history + no cross-child bleed)

1. Create **Child B** — a **different interest**, and set them at a **different level** (younger, or just play differently).
2. Play a **different** session: a **different choice**, a **different count**. Name B's companion something **distinct** (e.g. *Thunderclap*). End it.
3. Return "day two" as **Child B** → `/welcome/:childId`.

⬜ B's greeting references **B's** history (B's choice / B's companion name) — and is **clearly different** from A's.
⬜ B's greeting **never** mentions A's companion, A's choice, or A's loved thing. **Zero bleed.**
⬜ (Optional cross-check) `make memories CHILD=<B-uuid>` shows only B's rows.

---

## Part 4 — Invisible learning + humane difficulty (Day 10 core)

Do this within Child A's or B's play.

1. **Deliberately struggle** on a counting beat — give the wrong number, or retry. The companion should **gently help** (*"Let's count them together — I'll help you"*), mark it **assisted**, and **accept your next tap whatever it is**. No failure, no mark.
   ⬜ Struggle → a **hand**, not a grade. The next ask **scaffolds** (fewer things / narrower choice).
2. On another beat, answer **quickly and correctly**. The next ask should **stretch** — visibly **more** things to count.
   ⬜ Ease → a bigger ask, dressed as story.
3. **Read the learning beats** (beat 2, and the one before the ending). With a **real model**, check the narration:
   ⬜ It never says *count / lesson / practice / quiz / test / score / number / objective* out loud. It's a riverbank with frogs, not a worksheet. *(This is the P1-3 concern — only a real model shows it.)*
4. After the session, `/admin/` → the counting mastery moved again.

⬜ Learning is invisible in the words. ⬜ Struggle is met with warmth. ⬜ Difficulty feels **right** for the child.

---

## Part 5 — New-child fallback (no awkward empty greeting)

1. Create **Child C**. Do **not** play any session.
2. Immediately tap the star → `/welcome/:childId`.

⬜ Child C gets a **first-meeting hello** (*"Hi {name}, I'm {companion}…"*) — **not** an empty "Welcome back!". No promise-of-memory it can't keep.

---

## Part 6 — "Are we in the right direction?" (the strategic gut-check)

After playing, answer these honestly — this is your steering signal before Day 11:

⬜ Did *being remembered* feel like a **relationship**, or like a log line read aloud?
⬜ Would a 3–7 year old feel **seen**?
⬜ Was the learning genuinely **invisible** — would a parent watching think "story" not "app teaching my kid"?
⬜ On struggle, was the companion **kind** — the most emotionally load-bearing moment?
⬜ Any point where a child would see a **spinner, error, or score**? (Should be never.)
⬜ With real providers, is **latency** per beat acceptable for a small child's attention span?
⬜ Anything that felt **off, cold, or wrong** — however small? Write it down; that's the thing to fix before it compounds.

---

## Results capture

| Moment | Pass? | Notes / exact quote |
|---|---|---|
| Stack up, ping + hello-beat | | |
| No spinner/error/score ever shown | | |
| Voice + art present | | |
| Memories written (named companion, choice, loved thing) | | |
| Recall bundle relevant (semantic) | | |
| Day-two greeting: by name + specific real memory | | |
| Greeting felt like being *seen* | | |
| Child B greeting different + zero bleed | | |
| Struggle → gentle scaffold (assisted) | | |
| Ease → visible stretch | | |
| Learning beats say no lesson words (real model) | | |
| Mastery moved, no score shown to child | | |
| New child → first-meeting, not empty "Welcome back" | | |
| **Direction: does the moat land?** | | |

> Fill the quotes in — the exact greeting text for A and B is the best evidence, both that it works and (if it doesn't) of exactly how it missed.
