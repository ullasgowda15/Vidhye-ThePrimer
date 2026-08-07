# Build — Day 4: Story Orchestrator v0 (Plan + First Beat over SSE)

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 5.
>
> **Source of truth:** `docs/PRODUCT.md` — §8 (Story Engine, esp. §8.1 beat contract), §5.3 (story pipeline), §16.4 (streaming under ASGI), §18.4–18.5 (adventure API + SSE), §19.1/§19.6 (prompt layers, structured output + repair).
>
> **If handed `docs/Review/reports/Day4-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Companions have a persistent identity (descriptor + portrait + 5 expressions + voice). A cache‑first media pipeline (image + TTS adapters, style tokens, `build_illustration_prompt`, safety‑scan seam, object storage/CDN, hash cache) is live. A "meet your companion" greeting scene works. **No** story loop, memory, or runtime safety hardening.

## 2. Today's goal (one sentence)
Generate and **stream the first beat of a personalized adventure**: persist an `Adventure` + its `plan`, generate a schema‑valid first `Beat` (narration + illustration + interaction), render its image via the media pipeline and its voice via TTS **in parallel**, and stream it to the child UI over **SSE under ASGI** with no visible spinner.

## 3. Why this scope
This is the product's heart coming online (§8). We generate **beat‑by‑beat, not whole‑story**, because branching + streaming + latency‑hiding demand it (§8.1). Today is a single beat end‑to‑end; the choice→consequence loop is Day 5.

## 4. Tasks by workstream

### 4.1 Backend — `story` models & migrations (§17.2)
```python
class Adventure(UUIDModel):
    child = models.ForeignKey("profiles.Child", on_delete=models.CASCADE, related_name="adventures")
    session = models.ForeignKey("story.PlaySession", null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=200, blank=True)
    plan = models.JSONField(default=dict)              # skeleton + branch themes
    primary_objective_id = models.CharField(max_length=64, null=True)   # nullable today
    secondary_objective_id = models.CharField(max_length=64, null=True)
    difficulty = models.FloatField(default=0.4)
    status = models.CharField(max_length=16, default="in_progress")     # in_progress|completed|abandoned
    running_summary = models.TextField(blank=True)
    emotion_arc = models.JSONField(default=list)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

class Beat(UUIDModel):
    adventure = models.ForeignKey(Adventure, on_delete=models.CASCADE, related_name="beats")
    seq = models.PositiveIntegerField()
    narration = models.TextField()
    scene = models.JSONField(default=dict)            # {setting, illustration_prompt, characters_present}
    interaction = models.JSONField(null=True)         # {type, prompt_audio?, expected?, choices?}
    learning = models.JSONField(null=True)            # {objective_id, difficulty, skill_evidence_key}
    emotion = models.CharField(max_length=32, blank=True)
    media = models.ForeignKey("media.Media", null=True, on_delete=models.SET_NULL, related_name="+")
    audio = models.ForeignKey("media.Media", null=True, on_delete=models.SET_NULL, related_name="+")
    safety_status = models.CharField(max_length=16, default="unchecked")

class Choice(UUIDModel):   # defined now, USED on Day 5
    adventure = models.ForeignKey(Adventure, on_delete=models.CASCADE, related_name="choices")
    beat = models.ForeignKey(Beat, on_delete=models.CASCADE)
    options = models.JSONField(default=list); chosen_key = models.CharField(max_length=64, blank=True)
    consequence_summary = models.TextField(blank=True)
    latency_ms = models.IntegerField(null=True); retries = models.IntegerField(default=0)
    assisted = models.BooleanField(default=False)
```

### 4.2 Backend — orchestrator (`apps/story/orchestrator.py`)
```python
async def begin_adventure(child) -> Adventure:      # generate PLAN via Claude, persist, then beat 1
def build_plan(child) -> dict:                       # skeleton: hook->challenge->climax->resolution + branch themes
async def generate_beat(adventure, *, prev_choice=None) -> Beat:
```
- Plan generation calls the AI router with the assembler layers (System + Safety + companion/world[companion only today] + **learning directive stub** + output contract). Persist the plan to `Adventure.plan`.
- Beat 1 generation: structured output validated against the `Beat` schema (§8.1) with **one repair attempt** on failure (§19.6); personalize from the child's seeds.

### 4.3 Backend — API (§18.4–18.5)
```
POST /api/v1/children/{id}/adventures  -> {adventureId, streamUrl, companion:{name,voiceId}, estimatedBeats}
GET  /api/v1/adventures/{id}/stream    -> text/event-stream   (ASGI async view + StreamingHttpResponse)
```
SSE events:
```
event: beat
data: {"beatId","narration","scene":{"imageUrl","status"},"audioUrl","interaction","emotion"}
event: done
data: {"status":"awaiting_choice"}
```
- The stream view MUST be an **async** view under uvicorn; do not buffer. After generating the beat text, launch illustration + TTS with `asyncio.gather` and emit media URLs as they resolve (§18.5).

### 4.4 AI — prompt assembly + parallel media
- Wire the Day‑2 `assembler.assemble(...)` into generation. Learning directive is a **placeholder objective** today (e.g. `count_to_3`); the real selector is Day 10.
- `await asyncio.gather(illustrate(beat), synthesize(beat))` using the Day‑3 pipeline; attach resulting `media`/`audio` to the `Beat`.

### 4.5 Frontend — StoryPage + SSE client
- `src/routes/child/StoryPage.tsx`: render **narration first**, then swap in illustration + play audio as their URLs arrive (progressive). While the first beat renders, show the **companion "thinking"/idle animation** — never a spinner (§4.9).
- `src/lib/sse.ts`: an `EventSource`/fetch‑stream client that consumes `event: beat`/`done`. Start an adventure from `ChildHome`.

## 5. Key stubs
- `estimatedBeats` from the plan skeleton length (~5–7).
- `running_summary` starts empty (compaction is Day 5).
- Beat schema reuse: import the `Beat` pydantic/serializer from Day 1's `apps/story/ai/schemas.py`; the DB `Beat` mirrors it.

## 6. Do NOT do today
- ❌ No choice handling / consequence beats / multi‑beat loop (Day 5). ❌ No memory retrieval (Days 8–9). ❌ No safety hardening beyond the existing seam (Day 7). ❌ Do NOT pre‑generate the whole story. ❌ No WSGI fallback for the stream — ASGI only.

## 7. Acceptance criteria (done when ALL true)
1. `POST /adventures` creates an `Adventure` with a persisted `plan` and returns a valid `streamUrl`.
2. The SSE endpoint streams a **schema‑valid** beat 1 (validate against §8.1) then `done`; the response is genuinely chunked (`Content-Type: text/event-stream`, not one buffered blob) and served under ASGI.
3. Beat 1 is **personalized** from the child's seeds (a frog‑lover gets a frog‑relevant beat — show evidence).
4. Illustration + TTS are generated **in parallel** (log timings proving overlap) and their URLs attach to the beat.
5. `StoryPage` renders narration immediately, then swaps in image + audio as ready; **no spinner** appears at any point (companion idle animation covers the gap).
6. A forced malformed generation triggers the **repair** path; persistent failure does not crash the stream (it ends gracefully).
7. `make lint && make test` + **CI green**; at least one test consumes the SSE stream and asserts a valid beat.

## 8. Founder manual test
From the child home, start an adventure → after a moment of the companion "thinking," the first illustrated, voiced scene appears, personalized to your test child's loves. Confirm (via devtools/network) the stream arrives incrementally and no spinner ever showed.

## 9. End state to hand to Day 5
`Adventure`/`Beat`/`Choice` models exist; the orchestrator generates + persists a plan and streams a schema‑valid, personalized **first beat** (illustration + voice, generated in parallel) over **SSE under ASGI**, with validate‑repair. `StoryPage` renders progressively with no spinner. Still single‑beat: **no** choices, consequence loop, memory, or safety hardening.
