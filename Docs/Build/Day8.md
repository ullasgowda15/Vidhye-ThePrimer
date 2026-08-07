# Build — Day 8: Memory Write & Consolidation

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 9.
>
> **Source of truth:** `docs/PRODUCT.md` — §11 (Memory, esp. §11.1 architecture, §11.3 LTM, §11.4 salience/consolidation, §11.5 favorite/relationship memories), §5.4 (memory pipeline), §13.8/§13.10 (privacy), §17.3 (`memory` schema).
>
> **If handed `docs/Review/reports/Day8-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Defense‑in‑depth safety wraps every generation (input guard, independent output classifier, image scan, warm fallbacks, incident logging + escalation seam); red‑team suite passes; admin incident queue exists; Week‑1 debt cleared. Full personalized, consistent, safe story loop ships. **Week 1 complete.** No memory or learning engine.

## 2. Today's goal (one sentence)
Give the companion a memory: **capture in‑session events (short‑term), then consolidate the salient ones into durable, typed, embedded long‑term memories** after each session — selectively, like a friend, not a transcript.

## 3. Why this scope
Memory is the differentiator and the moat (§11). It must be **selective** (salience‑scored) so it stays cheap and feels human (§11.4), **durable** (years, §11.10), and **private** (§13.8). Today is the *write* side; retrieval + the day‑two payoff is Day 9.

## 4. Tasks by workstream

### 4.1 Backend — short‑term memory (STM)
- During play, capture beats, choices, reactions, retries, dwell into **Redis** keyed by session (+ mirror salient bits on the `PlaySession`) (§11.2). TTL a few hours; STM is working memory, discarded after consolidation.

### 4.2 Backend — `memory` model (§17.3)
```python
class Memory(UUIDModel):
    child = models.ForeignKey("profiles.Child", on_delete=models.CASCADE, related_name="memories")
    type = models.CharField(max_length=32)   # event|entity|preference|fear|skill_moment|emotional_moment
    content = models.TextField()             # short natural-language summary
    embedding = VectorField(dimensions=1536) # pgvector
    salience = models.FloatField(default=0.0)
    valence = models.FloatField(default=0.0) # -1..1 emotional tone
    entities = models.JSONField(default=list)
    source_adventure = models.ForeignKey("story.Adventure", null=True, on_delete=models.SET_NULL)
    last_recalled_at = models.DateTimeField(null=True)
    recall_count = models.IntegerField(default=0)
    decay = models.FloatField(default=1.0)
    class Meta:
        indexes = [models.Index(fields=["child","type","salience"]),
                   models.Index(fields=["child","last_recalled_at"])]
```
- Add the pgvector **ANN index** (hnsw or ivfflat) on `embedding` via a migration.

### 4.3 Backend — consolidation job (Celery) (§5.4, §11.4)
```python
# apps/memory/tasks.py
@shared_task
def consolidate(session_id): ...   # enqueued on adventure.end
```
- **Salience score** each candidate (0–1) from: emotional intensity, novelty, **child‑initiated** (things the child chose/said/named weigh heavily), skill milestones, repetition (§11.4). Threshold (config) decides LTM‑write vs. summarize/drop.
- Write high‑salience candidates as **typed** `Memory` rows with **embeddings** (embedding adapter, §4.4). Capture first‑class **favorite‑object / character / relationship** memories (§11.5) as `entity`/`preference` types.

### 4.4 AI — consolidation prompt + embeddings
- A prompt that summarizes the session's salient events into typed memory candidates each with a short `content` string, `entities`, and `valence`.
- `apps/media/adapters/embedding.py` (mirrors the image/TTS adapter pattern): `embed(text) -> list[float]`; one real provider, others typed stubs.

### 4.5 Privacy (§13.8, §11.10)
- Child memory encrypted at rest; access‑controlled; deletable via the `child` cascade; **never** sent to third‑party training; never logged in plaintext. Add a read‑only `Memory` admin view (for the Day‑12 parent transparency).

## 5. Key stubs
- Enqueue: on `POST /adventures/{id}/end`, `consolidate.delay(session_id)` — **async**, must not delay the child.
- `salience(candidate) -> float`; `SALIENCE_THRESHOLD = 0.5` (config).

## 6. Do NOT do today
- ❌ No retrieval into prompts / day‑two greeting (Day 9). ❌ No learning engine (Day 10). ❌ Do NOT store raw transcripts as "memory" — salience‑filter aggressively. ❌ Do NOT block session‑end on consolidation.

## 7. Acceptance criteria (done when ALL true)
1. During play, STM in Redis captures choices/reactions/retries/dwell (inspect keys).
2. On `end`, `consolidate` runs **async** (session‑end returns immediately; task runs on a Celery worker).
3. Salient memories are written as **typed** `Memory` rows with **embeddings**; mundane events are NOT (evidence: one played session yields a *few* meaningful memories, not dozens).
4. Child‑initiated moments (named the dragon, chose to help) are captured as high‑salience `entity`/`event` memories (§11.5).
5. The pgvector ANN index exists and a nearest‑neighbor query returns sensible neighbors.
6. Memory is deletable via `child` cascade; encrypted at rest; not plaintext‑logged.
7. `make lint && make test` + **CI green**, incl. a test asserting salience filtering (N raw events → k ≪ N memories).

## 8. Founder manual test
Play a session where you name the companion and make a distinctive choice. After it ends, check `/admin/` (memory view): a *small* set of meaningful memories appears (including the name/choice), not a full transcript. Confirm session‑end felt instant (consolidation ran in the background).

## 9. End state to hand to Day 9
STM capture + an async consolidation job that salience‑scores and writes typed, embedded LTM (incl. first‑class favorite/character/relationship memories), pgvector‑indexed and privacy‑safe. Memory is being **written** selectively. Not yet **retrieved** into stories — no day‑two greeting yet.
