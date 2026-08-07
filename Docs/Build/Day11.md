# Build — Day 11: Personalization Depth (Every Child a Different World)

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 12.
>
> **Source of truth:** `docs/PRODUCT.md` — §11.7 (interest graph), §8.3 (world generation), §2.4 (why personalization), §15.1 (per‑world palettes), §11.6 (fear ceiling), §17.2 (`character`, `world_place`, `interest_edge`).
>
> **If handed `docs/Review/reports/Day11-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
A per‑child skill model with ZPD selection, spaced retrieval, invisible embedding, difficulty adaptation, and behavioral mastery updates (numeracy + vocab). Education is under the story, measurably. Personalization still seed/memory‑driven; no parent insights.

## 2. Today's goal (one sentence)
Make **every child's world visibly their own**: an interest graph that updates from behavior and steers world/character selection, persistent recurring characters + a world map, and distinct per‑world palettes — so two profiles produce clearly different adventures.

## 3. Why this scope
Personalization is the central bet and the moat's felt form (§2.4): generic content produces generic attention. Recurring characters + a home world create continuity and comfort (§8.3); interest‑driven selection is what makes it *theirs*.

## 4. Tasks by workstream

### 4.1 Backend — interest graph + world/character persistence (§17.2)
```python
class InterestEdge(UUIDModel):
    child = models.ForeignKey("profiles.Child", on_delete=models.CASCADE, related_name="interests")
    topic = models.CharField(max_length=64)
    weight = models.FloatField(default=0.0)          # time-decaying
    last_reinforced_at = models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["child","topic"], name="uniq_child_topic")]

class WorldPlace(UUIDModel):
    child = models.ForeignKey("profiles.Child", on_delete=models.CASCADE, related_name="places")
    name=models.CharField(max_length=120); mood=models.CharField(max_length=32)
    description=models.TextField(); inhabitants=models.JSONField(default=list)
    first_seen_adventure=models.ForeignKey("story.Adventure", null=True, on_delete=models.SET_NULL)
    last_visited_at=models.DateTimeField(null=True); visit_count=models.IntegerField(default=0)
# `character` model from Day 4 is reused for recurring cast.
```
- `apps/memory/interest.py::update_from_session(session)` (Celery): raise weights for topics the child chose/reacted to; apply time decay to others (§11.7).

### 4.2 AI — personalized planner (§8.3)
- The story planner input now includes: `top_interests` (from InterestEdge), `known_places` + `recurring_characters` (reuse for comfort or discover new for novelty), and the `fear_ceiling`.
- Prefer worlds/characters/objectives matching top interests; introduce/reuse characters **consistently** (ties to Day 6 references); **respect the fear ceiling** always (§11.6).

### 4.3 Frontend / Design — per‑world palettes (§15.1)
- A **theme layer** over base tokens: implement **≥3 distinct worlds** (e.g. Whispering Woods, Star Harbor, Coral Deep) with different palette/mood. Apply via a `data-world` attribute driving CSS‑var overrides. Never harsh/overstimulating; night‑friendly variant respected.

## 5. Key stubs
- Planner signature: `build_plan(child, top_interests, known_places, recurring_characters, fear_ceiling, objective, difficulty)`.
- World palette registry: `{ "whispering_woods": {...tokens}, "star_harbor": {...}, "coral_deep": {...} }`.

## 6. Do NOT do today
- ❌ No parent dashboard (Day 12). ❌ No companion **evolution** mechanics (keep stage 0; §9.4 is later). ❌ Do NOT exceed the fear ceiling chasing engagement. ❌ Do NOT break Day‑6 character consistency or Day‑9 memory.

## 7. Acceptance criteria (done when ALL true)
1. `InterestEdge` weights update from behavior (evidence: choosing frog paths raises "frogs"; unrelated topics decay).
2. The planner demonstrably prefers high‑interest topics and reuses recurring characters/known places.
3. **Two different child profiles produce visibly different worlds/stories** for the same objective (attach both; differences are structural, not a swapped noun).
4. **≥3 distinct world palettes** render, matching world mood; night‑friendly variant works.
5. A recurring character/place persists and reappears **consistently** across two adventures.
6. The fear ceiling is always respected in personalized content.
7. `make lint && make test` + **CI green**; no regression to consistency (Day 6), safety (Day 7), or memory (Day 9).

## 8. Founder manual test
Create two children with different loves (dinosaurs vs. ocean). Play each → the worlds, characters, and palette feel clearly different and personal. Revisit a place/character across two sessions and confirm it persists and looks the same. Confirm nothing exceeds a low fear ceiling.

## 9. End state to hand to Day 12
An interest graph steers personalized world/character selection; recurring characters + a persistent `WorldPlace` map exist; ≥3 distinct world palettes render; fear ceiling respected; consistency + memory intact. **Every child gets a visibly different world.** No parent‑facing surfaces yet.
