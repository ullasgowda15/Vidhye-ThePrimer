# Build — Day 10: Learning Engine v0 (Invisible Curriculum)

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 11.
>
> **Source of truth:** `docs/PRODUCT.md` — §10 (Learning Engine, esp. §10.2 skill model, §10.3 objective selection, §10.4 difficulty, §10.5 evidence/mastery, §10.6 milestones), §8.9–8.10 (difficulty / learning goals), §5.5 (learning pipeline), §17.3 (`objective`, `milestone`, `learning_progress`).
>
> **If handed `docs/Review/reports/Day10-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Hybrid memory retrieval feeds story start/branches; the companion greets returning children by name and references specific memories; injection‑through‑memory neutralized; no cross‑child leak. **The differentiator works.** No learning engine; personalization not yet interest‑graph‑driven.

## 2. Today's goal (one sentence)
Embed **invisible education**: a per‑child skill model, a ZPD objective selector with spaced retrieval, a difficulty parameter the story engine turns into concrete demands, and mastery updates from **in‑story behavioral evidence** — all without the child ever perceiving a lesson.

## 3. Why this scope
Learning under story is the promise (§10). It must be **invisible** (Principle #1) and assessed via play behavior, never a quiz (§10.5). Start with **one domain done well (numeracy: counting)** + opportunistic vocabulary (§10.1).

## 4. Tasks by workstream

### 4.1 Backend — catalog + progress models (§17.3)
```python
class Milestone(UUIDModel):
    domain = models.CharField(max_length=32); name = models.CharField(max_length=120)
    description = models.TextField(); standard_ref = models.JSONField(default=dict)

class Objective(UUIDModel):
    domain = models.CharField(max_length=32)
    code = models.CharField(max_length=64, unique=True)     # e.g. "count_to_5"
    description = models.TextField()
    min_age = models.IntegerField(); max_age = models.IntegerField()
    milestone = models.ForeignKey(Milestone, on_delete=models.PROTECT)
    prerequisites = models.JSONField(default=list)          # [objective codes]

class LearningProgress(UUIDModel):
    child = models.ForeignKey("profiles.Child", on_delete=models.CASCADE, related_name="progress")
    objective = models.ForeignKey(Objective, on_delete=models.CASCADE)
    mastery = models.FloatField(default=0.0)      # 0..1
    uncertainty = models.FloatField(default=1.0)
    last_practiced_at = models.DateTimeField(null=True)
    due_at = models.DateTimeField(null=True)      # spaced retrieval
    evidence_count = models.IntegerField(default=0)
    class Meta: constraints=[models.UniqueConstraint(fields=["child","objective"], name="uniq_child_obj")]
```
- **Seed** (data migration): numeracy (`count_to_3/5/10`, `more_fewer`) + a few vocabulary objectives, each mapped to a `Milestone` (§10.6) with `standard_ref` for later standards mapping.

### 4.2 Backend — selector + mastery update
```python
# apps/learning/engine.py
def select_objective(child) -> tuple[Objective, float]:
    # balance: due-for-review (spacing) + edge-of-ability (mastery 0.4-0.7 ZPD) + interest fit;
    # avoid over-repeating; interleave domains across sessions. Returns (objective, difficulty 0..1).
def update_from_session(session):   # Celery task, runs alongside consolidation
    # from in-story evidence (correct/assisted/incorrect, retries, dwell -> captured Day 5)
    # update mastery (simple, interpretable Bayesian-flavored estimate) + advance due_at (spacing).
```

### 4.3 AI — difficulty → demands + invisible embedding (§8.9–8.10)
- Translate the difficulty float into concrete story asks (count to 3 vs 10; 2 vs 3 choices; familiar vs novel word). Adapt **within** a session: struggle → scaffold (companion counts along, narrows choices, `assisted=True`); ease → stretch.
- The objective is woven into plot ("five stepping stones across the river"), **never named** as a lesson. Tag each learning beat with `learning={objective_id, difficulty, skill_evidence_key}` for later verification (§8.10).

### 4.4 Frontend
- Invisible to the child. Ensure evidence (latency/retries/assisted) is captured on learning interactions (built Day 5) and flows to `update_from_session`.

## 5. Key stubs
- `select_objective(child)` is called at plan time (Day 4 orchestrator) — replace the Day‑4 placeholder objective with this.
- `update_from_session.delay(session_id)` enqueued on `end`, alongside `consolidate`.

## 6. Do NOT do today
- ❌ No quiz/test UI, no scores/grades shown to the child (violates Principle #1). ❌ No parent‑facing insights yet (Day 12). ❌ Do NOT add domains beyond numeracy + vocab. ❌ Do NOT over‑engineer the mastery model — keep it interpretable (parents see it Day 12).

## 7. Acceptance criteria (done when ALL true)
1. `Objective`/`Milestone` catalog seeded (numeracy + vocab), mapped to milestones with `standard_ref`.
2. `select_objective` picks an objective at the child's ZPD, respecting spacing + interleaving (evidence across ≥2 sessions).
3. The selected objective is **embedded invisibly** — a reviewer reading the beat sees a story, not a lesson; beats carry the `learning` tag.
4. Within‑session difficulty adapts: a deliberate struggle triggers scaffolding (`assisted=True`); ease triggers a stretch (demonstrate both).
5. `mastery` updates from behavioral evidence; `due_at` advances per spacing; **no quiz anywhere**.
6. Two different children at different levels get different `difficulty` for the same objective.
7. `make lint && make test` + **CI green**, incl. a test asserting mastery moves on evidence and the objective is actually exercised in the beat.

## 8. Founder manual test
Play with a child profile and count things in‑story a couple of times; the difficulty should feel right, and if you deliberately "struggle," the companion gently helps rather than failing you. Check `/admin/` that the child's counting `mastery` moved — with no test or grade ever shown to the child.

## 9. End state to hand to Day 11
A per‑child skill model with ZPD selection, spaced retrieval, invisible in‑story embedding (tagged beats), within‑session difficulty adaptation, and behavioral mastery updates — for numeracy + vocabulary. Education happens under the story, measurably, invisibly. Personalization still seed/memory‑driven, not yet interest‑graph‑driven; no parent insights yet.
