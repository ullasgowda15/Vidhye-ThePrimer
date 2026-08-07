# Review — Day 3: Companion Creation & Identity + Media Pipeline

> **Session type:** REVIEW. Independent, skeptical auditor. Gather real evidence, then write `docs/Review/reports/Day3-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day3.md` + PRODUCT.md §9/§8.2/§15.9–15.10/§16.6/§13.8. Verdicts ✅/🟡/❌/🔵/⚪ with evidence.

## 1. Method
Create a companion via API/UI; inspect stored `Media` rows + object storage; **prove the cache** by generating the same scene twice and confirming the provider was called once; eyeball the portrait, the 5 expressions, and 3 sample scene renders for consistency; play the TTS hello.

```bash
# First fetch the valid catalogue keys — do NOT guess palette/preset; unknown keys 400 by design.
curl -s :8000/api/v1/companion-catalog -H "Authorization: Bearer $ACCESS"
# Then create using real keys, e.g. palette ∈ {ember,moss,tide,dusk,blossom},
# personality_preset ∈ {warm_guide,brave_spark,calm_anchor,curious_helper}:
curl -sX POST :8000/api/v1/children/<id>/companion -H "Authorization: Bearer $ACCESS" \
     -d '{"archetype":"dragon","name":"Pip","palette":"ember","personality_preset":"brave_spark"}'
cd backend && python manage.py shell -c "from apps.media.models import Media; print(Media.objects.count())"
python manage.py render_companion <child_id> --scenes 3      # eyeball consistency
# cache proof: call get_or_generate_image twice with identical hashes; assert provider hit-count == 1
pytest apps/companion apps/media -q
```

## 2. Checklist (1:1 with Day 3 acceptance criteria)
1. Companion creates + persists **1:1** (archetype/name/palette/personality); GET returns it.
2. Canonical `visual_descriptor` generated + stored.
3. Portrait + **5 canonical expressions** stored in object storage, served via **CDN URLs**; `reference_asset_ids` references them.
4. **Cache works** — identical `(style,character,scene)` hashes do NOT re‑call the image provider (paste the hit‑count evidence).
5. Image safety scan runs before store; **every** `Media` row has a `safety_status`.
6. TTS hello synthesized in the assigned `voice_id` + plays in the greeting.
7. Consistency — 3 sample renders are recognizably the same character (attach images/paths).
8. Companion picker + greeting scene work in UI; lint/test/CI green.

## 3. Cross-cutting checks
- **Consistency is engineered, not luck:** confirm `build_illustration_prompt` injects the descriptor + style tokens + reference; consistency should collapse if references were removed.
- **Cost/cache (§22.4):** a broken cache silently blows up cost from Day 4 on — treat a non‑working cache as P0.
- **Adapter seams (§16.6):** image + TTS adapters are provider‑swappable (interface + one real impl + typed stubs). The safety‑scan **seam exists** even if stubbed (flag 🔵 for Day 7 hardening).
- **Storage hygiene:** media served via CDN, not the app; no blobs in Postgres.
- **Scope discipline:** no story loop, memory, or evolution logic front‑run (stage 0 only).

## 4. Report to write → `docs/Review/reports/Day3-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 3 Review Report — Companion Creation & Identity + Media Pipeline". Attach or reference the 3 consistency renders as evidence for criterion 7 and the provider hit‑count for criterion 4.

## 5. Verdict rules
**NOT‑READY** if any P0: companion won't persist, **cache doesn't work**, media not stored/served via CDN, missing `safety_status`, TTS/portrait missing, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 8 ✅.

## 6. After writing
Print a 5‑line summary (verdict, counts, P0/P1 titles). Do not fix; do not proceed to Day 4.
