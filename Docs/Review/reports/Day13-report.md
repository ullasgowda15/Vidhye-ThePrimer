# Day 13 Review Report — Settings, Controls & Wellbeing
**Reviewed:** 2026-08-12 · **Commit:** `42e1be7` ("Day 13: settings, controls and wellbeing") · **Reviewer:** Claude Code (combined review-then-fix session, both phases authorised by the founder)

> **On the session brief's open question.** Day 13 is committed **and pushed** — `origin/main` is at `d5a85a7` (the docs commit on top of the build commit `42e1be7`), and CI run **`31596188690` is green** on the build commit. No push was needed to bring main up to date before this review.

## Overall verdict

**NOT-READY as found — one P0.** (Day 13's verdict rule: *"NOT-READY if any P0: … delete leaves orphans …"*.)

Six of the seven criteria are met, and the two that carry the most weight are met unusually well. `profiles/settings.py` is the right piece of architecture: the prompt assembler, the independent classifier, the deterministic policy, `story.worlds` and `personalize` now build their rules from **one** normalised object, so §13.4's two halves — the rule stated to the model and the rule enforced on its output — can no longer disagree. The day's own tests found two real bugs while it was being written, and one of them (`policy._word_pattern`'s case-sensitive plural strip) had been live and invisible since Day 7. §13.6 is genuinely enforced rather than displayed: the cap *lands* the story instead of cutting it, the module that implements it contains no way to stop a stream, and criterion 3's "zero child-directed pull mechanics" is provable rather than asserted — the `notifications` app is an empty shell with no delivery path in it at all.

**What fails is deletion, and it fails in the place the brief singles out.** `DELETE /children/{id}` and `DELETE /account` remove every Postgres row — that part is watertight and structurally asserted. Neither removes the **objects in storage** behind the child's pictures and their narration audio. `apps.media.service.delete_child_media` exists, handles the shared-object case correctly, and is called by nothing but a smoke-test command and its own unit tests. Because the `Media` rows cascade away first, nothing is left that could ever identify those objects again: they are not merely orphaned, they are unreachable. A parent who exercises their COPPA/GDPR erasure right gets their child's companion portrait and recorded narration left behind in the bucket, permanently, while `privacy.py` tells them "the data goes at once".

The day's own "no orphans" test is a Postgres foreign-key walk, which is why nothing caught it.

The second finding is the same class as Day 12's fixed P1s and was found the same way — by reading the sentences a parent reads. The privacy disclosure, in the one document whose own docstring says *"the ones that are not yet true are not made"*, describes a backup regime that does not exist and an access-audit control that has not been built.

Everything else I tried to break held. I could not get an avoid-topic past the guard, including with a model actively trying; I could not buy permissiveness by corrupting the ceiling in the database; I could not restart the session cap by asking for another story; and I could not reach another family's settings, transcripts, memories or export. The Day-12 consent question is not merely resolved but resolved with the argument written down: consent gates **processing**, never a parent's **access** to their own child's records.

**All P0/P1 items were fixed in this session** (see *Fixes applied*), re-verified, and pushed.

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | Each setting honored immediately in **both** generation **and** the safety guard (avoid-topic absent; ceiling respected — prove by trying) | ✅ | Both halves now read `profiles.settings.settings_for(child)`: `assembler.safety_policy_layer` (generation) and `policy.check_text` (guard), plus `classifier.audience_block` (the independent grader), `story.worlds.select_world/select_place/_elsewhere` and `orchestrator.personalize`. I grepped for surviving raw reads of the control columns — `child.intensity_ceiling\|avoid_topics\|fears\|session_cap_min\|accessibility\|voice_on\|text_on` — and outside `settings.py` itself the only production hits are `smoke_story.py` (a dev command that prints them) and `memory/retrieval.py:358` (see **P2-1**). **Proved by trying**, not by inspection: `TestAnAvoidTopicNeverReachesTheChild` queues a *defiant* provider whose beat says "Pip and you walked into the hospital…" in the narration, the scene, the prompt and both choice labels, then asserts `"hospital" not in` the concatenation of everything the child sees or hears — and that the result is still an illustrated, answerable beat (`safety_status=BLOCKED`, narration non-empty, `illustration_prompt` present), so the child cannot tell. Ceiling: `test_frightening_language_above_the_ceiling_is_refused` (ceiling 1, "the monster loomed out of the pitch black" → `FRIGHTENING`/`REWRITE`), and the same sentence passes at ceiling 2. "Immediately" is asserted rather than assumed — `test_a_setting_changed_now_is_in_the_very_next_prompt` writes the column and re-reads the prompt with no cache in between. |
| 2 | Session cap → **warm** close; time window enforced | ✅ | The cap never cuts: `wellbeing.py` contains no call that ends a stream (asserted against the source by `test_the_wellbeing_module_cannot_stop_a_stream`). At the cap `is_final_seq` starts returning true and the beat is built under `FINAL_BEAT_RULE` **plus** `CAP_CLOSE_RULE` — both asserted present, and `CAP_CLOSE_RULE` asserted *absent* on a natural ending. The produced close has `options == []` and `interaction.type == "none"`, so no child is handed two choice cards under a goodbye. The cap is measured across a **sitting** (`sitting_started_at` joins sessions less than `SITTING_GAP_MIN`=15 apart), which is the part that would otherwise have silently never fired, since `create_adventure` opens a session per adventure and the warm close offers "Again!". `winding_down` is memoised per request but re-decided per beat, so raising the cap mid-sitting takes effect at once. Window: `gate_for` is checked in `AdventureStartView` **only when a new story would start**, so a story under way always reaches its ending; `GET /children/{id}/wellbeing` is advisory and `POST /adventures` refuses regardless (403 `outside_time_window`). Wrapping windows (19:00→07:00) handled and tested. |
| 3 | **Zero** child-directed notifications/streaks/FOMO (inspection evidence) | ✅ | The brief's grep, run verbatim, and then narrowed to the source (excluding tests and generated schema): <br>`grep -rEni "streak\|fomo\|comeback" frontend/src/routes backend/apps/story/wellbeing.py backend/apps/story/serializers.py backend/apps/accounts/privacy.py` → **6 hits, every one a negation or a stated commitment**: `StoryPage.tsx:669` "no time played, no streak"; `Settings.tsx:231` "never sends your child a notification, a reminder or a streak"; `wellbeing.py:5,51` §13.6 quoted and "There is no streak, no 'you have N minutes left', no comeback nudge"; `serializers.py:161` "no next-open time and no streak"; `privacy.py:74` the promise to the parent. No mechanic. Structurally: `backend/apps/notifications/` contains **`models.py` with three lines of comment and no model** — there is no delivery path to a child to turn on. `WellbeingSerializer` carries three fields and no counter, and `test_the_wellbeing_payload_carries_no_counters` pins that. The resting scene is asserted to contain **no digit at all** (`expect(scene.textContent).not.toMatch(/\d/)`). The day's own scan test is honest about its limit (it skips negated lines, so it catches the earnest addition and not an adversary) and carries a canary that proves it still bites. |
| 4 | Accessibility toggles (captions, reduced-motion, dyslexia font, high-contrast, adjustable audio) work in the child UI | ✅ | Three are `data-a11y-motion\|font\|contrast` attributes on the document root, applied by `useAccessibility` and consumed only in `tokens.css` — asserted end-to-end in `settings.test.tsx` by rendering the **story page** with the flags on and reading `document.documentElement`, and asserted clean when they are off. The CSS is real, not decorative: the contrast block re-points `--bg/--surface/--text/--border` (with a separate `night` variant) and is declared last so it beats a world palette; the dyslexia block sets `--line-height-base: 1.8`, which `index.css:20` actually consumes on `body`. Volume is set through a **ref callback** rather than a JSX attribute — correct, because `volume` is a DOM property React will not apply from JSX, and written as `volume={…}` this would have been a control that silently did nothing. `safeVolume` clamps (an out-of-range assignment throws `IndexSizeError` between a child and their story). Captions: `wordsOnScreen` is implemented twice on purpose (server `ChildSettings.words_on_screen`, client `design/accessibility.wordsOnScreen`) with the same five cases tested on both sides — accessibility outranks preference, and a silent voice outranks both. With `voice_on` off the `<audio>` element is not rendered at all rather than muted, so the clip is not fetched. |
| 5 | Export returns portable data; delete (account + child) cascades **completely** (no orphans) | ❌ → ✅ *(after fix)* | **Export ✅** (with **P2-2** recorded — it carries no pictures). **Export scoping ✅.** Owner-scoping is structural — every collection filters on a `child` drawn from `Child.objects.filter(parent=parent)`, and `_adventures` scopes `Beat`/`Choice` by `adventure_id__in` of that child's adventures. `test_one_family_s_export_never_contains_another_s` creates a second family with a memory reading "Mira's secret." and asserts neither `"Mira"` nor `"secret"` appears anywhere in the serialised bundle. Content is genuinely portable: stories **verbatim** (narration, the child's answer, the retries, the `safety_status`), memories, learning, world map, reports, and **our own safety incidents**; no embeddings; `EXPORT_ROW_CAP` bounds it and `manifest.json` **names** any file it truncated rather than silently dropping rows. **Delete ❌ — this is P0-1.** Every Postgres row goes, and that half is asserted well (a live `information_schema` FK walk after a real `DELETE`, plus a declaration-level test that fails on any future non-CASCADE FK to `Child`/`User`). But **no object in storage is removed**: neither delete path calls `media.service.delete_child_media`, so a deleted child's pictures and narration audio stay in the bucket — and, because the rows cascade first, unreachable. Proven below. |
| 6 | Privacy disclosure plain-language + accurate | 🟡 → ✅ *(after fix)* | Plain-language ✅ and better than the criterion asks: it is served from the backend (`accounts/privacy.py`) rather than written in the React page, because every sentence is a claim about what the server does; it is versioned; it is rendered as text, never HTML; and the page splits on the server's own blank lines so paragraphs stay paragraphs. The mechanical checks are real (no section under 20 words, a weasel-word list, the three §13.8 commitments, the three §13.7 rights). The **accurate** half is where it slips: two sentences describe things that are not built — see **P1-1**. Two claims I checked and found **true**: "the sensitive fields encrypted in the database" (`Memory.content` really is an `EncryptedTextField`, Fernet, key required outside dev) and "everything travels over encrypted connections" (`SECURE_SSL_REDIRECT` + HSTS preload in `config/settings/prod.py`). The provider paragraph is phrased as a **future** commitment ("Before launch we will only use providers whose terms forbid training… and we will name them here") — which is exactly the right treatment, and exactly what the three inaccurate sentences do not get. |
| 7 | lint/test/CI green incl. an avoid-topic test and a warm-cap test | ✅ | `make lint` → ruff check clean, `ruff format --check` clean (202 files), eslint clean, prettier clean. `make test` → **exit 0**. Backend `pytest -q` → **1146 passed**, exit 0 (local, against the Docker `db`/`redis`). Frontend `vitest run` → **142 passed (11 files)**. The two tests the criterion names both exist and both do the hard version of the job: `TestAnAvoidTopicNeverReachesTheChild::test_the_child_gets_a_safe_fallback_instead_of_the_forbidden_word` (defiant provider, asserts on what reaches the database) and `TestTheSessionCap::test_the_close_is_warm_and_asks_the_child_for_nothing`. **CI green on `42e1be7`** — run **`31596188690`**. |

Counts (as found, before fixes): ✅ 5 · 🟡 1 · ❌ 1

## Tried to violate the settings → outcome

Set against a child with `avoid_topics=["hospitals"]`, `intensity_ceiling=1`, `session_cap_min` at the floor, exercised through the real code paths.

| What I tried | Outcome |
|---|---|
| Get the forbidden word past the guard with a **compliant** model | Never reaches it — the topic is in the prompt as a rule (`Never depict, name, or allude to any of these topics:`) |
| Get it past with a **defiant** model that puts "hospital" in the narration, the scene, the spoken prompt *and* both choice labels | **Blocked.** `"hospital"` absent from the concatenation of narration + spoken line + scene + interaction. The child still gets an illustrated, answerable beat — no error, no gap |
| Type the topic in **capitals** (`"  HOSPITALS  "`), written straight to the column bypassing the serializer | **Caught.** This is the Day-13 bug fix: `removesuffix("s")` compares exactly, so before today this produced a rule matching the plural and not the singular, invisibly, because the match itself is `IGNORECASE` |
| Type it in the **singular** and use the plural in the beat (and vice versa), across four capitalisations | **Caught** in all eight combinations |
| Corrupt the ceiling to `9` in the database to buy permissiveness | **Tightens.** `clamped_ceiling` resolves an unreadable value to the *gentlest* rung, not `min(MAX, value)` — so a stored `9` makes the guard stricter. "The monster waved a friendly claw" is refused at `9` and allowed at a genuine `2` |
| Exceed the ceiling in the beat text at ceiling 1 | **Refused** (`FRIGHTENING` / `REWRITE`) |
| Restart the cap by tapping "Again!" for another story | **Does not restart.** The cap is measured across the sitting, not the `PlaySession` row |
| Start a new story outside the daily window | **403** `outside_time_window`, even if the client never asked `/wellbeing` |
| Finish a story that was under way when the window shut | **Allowed to finish** — deliberate, at most one adventure, and stated in `wellbeing.py` rather than discovered |
| Read/patch another family's child settings | **403** (`IsParentOwner` runs before the row is serialised) |
| Find another family's data in my export | **Not present** |
| **Delete a child and find their pictures still in storage** | **Still there.** See P0-1 |

## The P0, proven

`apps.media.service.delete_child_media` is called from exactly two places in the repo:

```
$ grep -rn "delete_child_media" .
backend/apps/media/service.py:19          (docstring)
backend/apps/media/service.py:475         (the definition)
backend/apps/media/management/commands/smoke_media.py:397
backend/apps/media/tests/test_service.py:16,319,336
```

`smoke_media.py` is a dev command and `test_service.py` is its own unit test. **Neither `DELETE /children/{id}` nor `DELETE /account` is in that list.** `ChildViewSet` inherits DRF's default `perform_destroy` (`instance.delete()`), and `AccountDeleteView.delete` calls `parent.delete()`. There are no Django signals anywhere in the codebase (`grep -rn "post_delete\|pre_delete"` → no handlers), so nothing else picks it up.

The `Media` row cascades (`Media.child` is `on_delete=CASCADE`), which is why the day's structural "no orphans" test passes: it walks `information_schema` for rows still referencing the child, and there are none. The object in R2 is not a row.

It is worse than a leak that could be swept later. `purge_objects` decides what is safe to delete by asking *which URLs no surviving `Media` row points at* — so once the rows are gone, there is no longer anything in the system that knows those keys exist. They cannot be enumerated back from the database at all.

**Probe** (temporary test, run against the real API with the local filesystem storage backend, then removed):

```
$ pytest tests/test_privacy_media_probe.py -q -s

PROBE child-delete:   row gone=True  object_still_on_disk=True  key=assets/44cb979d5808496ae86eaa54f97c33b0.png
PROBE account-delete: rows gone=True object_still_on_disk=True

FAILED test_probe_deleting_a_child_leaves_the_object_in_storage
       AssertionError: the object behind the picture is still in storage
FAILED test_probe_deleting_the_account_leaves_the_object_in_storage
       AssertionError: the object behind the picture is still in storage
```

The probe generates one picture for a child, calls the real `DELETE /api/v1/children/{id}` (and, in the second case, `DELETE /api/v1/account` with the password) through the API, and then looks on disk. Both times the `Media` row is gone and the file is not. The log line from the second run is the whole story in two entries: `media.stored key=assets/44cb979d…png`, then `privacy.account_deleted parent=… children=1` — and nothing in between.

What the product tells a parent while this is true:

- `accounts/privacy.py` — *"When you delete a child or your account, the data goes at once."*
- `accounts/privacy.py` — *"deleting a child removes their profile, their companion, every story, every memory and every report"*
- `Privacy.tsx` — *"Their profile, companion, every story, every memory and every report. This cannot be undone."*
- `AccountDeleteView` docstring — *"No soft delete and no grace period… a hidden row that still exists is not deleted"*
- `media/service.py` docstring — *"The object goes when the last row pointing at it does."*

The last one is the tell: the mechanism was designed correctly and simply never wired to the two endpoints that need it.

## Punch-list

### P0 — blockers

- **P0-1 — Deleting a child or an account leaves every generated image and audio clip in object storage, permanently.** `ChildViewSet.perform_destroy` and `AccountDeleteView.delete` cascade the `Media` rows and never call `media.service.delete_child_media`, which exists and already handles the shared-object case correctly. Because the rows go first, the keys become unrecoverable. Fails criterion 5 ("cascades **completely** (no orphans)") and contradicts five parent-facing or self-documenting claims listed above. Fix: call `delete_child_media` on the child (and on each child of the account) *before* the cascade, and test it against storage rather than against `information_schema`.

### P1

- **P1-1 — The privacy disclosure states three things that are not true yet**, in the document whose own module docstring says *"Every claim here is checked against the code, and the ones that are not yet true are not made."*
  - *"Encrypted backups roll off on their own schedule and are never used to restore a deleted family."* There are no backups. `Docs/Hosting.md` item 10 lists nightly `pg_dump` to R2 as a **to-do** — *"Do this before onboarding real families — it's the one irreplaceable thing"* — and nothing in the repo implements, encrypts or expires one.
  - *"Access to production data is restricted and audited."* §13.8 asks for audit logging on child data as a design goal; nothing implements it, and there is no production deployment yet.
  - The same paragraph's *"and that check is tested from the outside, with two real accounts, on every endpoint"* is **true** and should stay — it is the model for how the other two should be phrased. The file already knows how to write an unbuilt commitment honestly (the provider paragraph does exactly that); these three sentences just did not get the same treatment.
- **P1-2 — `useChildSettings` documents caching behaviour it does not have.** Its docstring says *"`staleTime: 0` and a refetch on focus, deliberately against the pattern the dashboard reads use… this is the one document in the product where showing a stale value is showing a *wrong safety setting*."* The hook sets neither option, so it inherits `App.tsx`'s defaults — `staleTime: 30_000` and `refetchOnWindowFocus: **false**` — i.e. the exact opposite of both claims. Real effect is modest but real: the child's story page holds the accessibility flags for up to 30s and never re-reads them on focus, and the parent's settings form can open on a stale copy. Two lines to fix, and the comment is currently a false statement about a safety surface.

### P2 — improvements (recorded, not fixed in this session)

- **P2-1 — `settings_for` is not quite "the only way anything reads a control".** `profiles/settings.py`'s docstring opens with that claim; `memory/retrieval.py:358` reads `child.fears` raw into `must_include`, bypassing `_clean_terms`. Retrieval is not the safety guard, so the impact is cosmetic (a blank or duplicated fear could reach a bundle), but the claim is load-bearing enough to be worth making true or narrowing.
- **P2-2 — The export contains no pictures and no audio, and no reference to them.** `adventures.json` carries narration verbatim but not the illustration or the clip, while `Privacy.tsx` offers "every story word for word". §12.8 names profiles/memories/adventures/reports, so this is defensible — but a storybook is half pictures, and a parent taking their data out gets the words only. At minimum the manifest should say so.
- **P2-3 — Parent-typed text goes into a zip entry path unescaped.** `export.build_bundle` builds `children/{child.first_name}-{pk[:8]}/…`, and `validate_first_name` only strips whitespace. A child named `../x` produces an archive entry that escapes its directory on a naive extractor. The attacker and the victim are the same account, so severity is low, but a slug helper here costs one line.
- **P2-4 — At the cap the model receives two rules that pull against each other.** `FINAL_BEAT_RULE` mandates a tomorrow hook and calls it *"the reason the child asks to come back"*; `CAP_CLOSE_RULE`, appended right after it, says *"do not suggest hurrying back"*. Both are defensible in isolation and the ordering is deliberate, but a model has to reconcile them. Worth watching in the manual test against a real provider — the scripted provider cannot show this.
- **P2-5 — Day-12's P2-2 is confirmed still open, and correctly out of Day-13 scope.** The weekly report counts stored `SessionReport` rows rather than sittings, so a sitting whose report never generated is invisible to the week. Day 13 touched `analytics/views.py` only to lift the consent gate. `PROGRESS.md` records it deliberately, and today's export makes it *visible* to a parent for the first time by shipping `sessions.json` and `reports.json` as separate files that can now be seen to disagree. It belongs with whoever revisits the weekly digest.
- **P2-6 — Working-tree hygiene, same pattern as Days 11 and 12.** `Docs/Hosting.md` (founder-authored, 146 lines) sits untracked. Recorded rather than acted on — it is not Day-13 output and not mine to commit.

## Things I checked that are fine

- **Consent gating (the Day-12 carry-over) is resolved deliberately, not accidentally.** `accounts/consent.py` now carries the full argument in its module docstring: consent gates **processing** (child creation, every generation path) and never **access** (dashboard reads, the settings write, the export, the deletes). Both halves are tested — a lapsed-consent parent gets 200 on six dashboard reads, the transcript, the export and a settings PATCH, and 403 on creating a child or starting a story — and `test_the_scoping_is_unchanged_by_any_of_this` proves the second family still gets 403 on the same routes. The settings write surviving the gate is the one that needed justifying, and it survives inspection: every field it can change is a restriction or a presentation flag, and none of it creates a row about the child.
- **Cross-account scoping.** No leak found on any Day-13 route. The settings view deliberately uses an unscoped queryset so `IsParentOwner` answers 403 rather than 404 — consistent with the API's documented posture — and the permission walks `obj.parent_id` or `obj.child.parent_id`, which `Child` satisfies directly.
- **Memory vectors.** `Memory.embedding` is a column on the row, so it cascades with everything else — the "vectors" half of the deletion question is genuinely covered. Redis STM holds no narration, is keyed per session, and expires on its own TTL, which matches §13.8's "short retention on raw behavioural events" and the disclosure's "raw activity logs are kept briefly".
- **Account deletion needs the password**, not just a bearer token — the right call for the one irreversible action, and tested in both directions.
- **Scope discipline.** No billing, no analytics/eval hardening, no new story features. The `accessibility` serializer rejecting an undeclared key with a 400 rather than dropping it silently is the single best small decision in the day: it is the only place "a toggle that does nothing" could have hidden.

## Fixes applied

Phase 2 of this session. Only the items the audit named above were touched; nothing marked ✅ was changed except where a docstring or a parent-facing sentence had to catch up with the fix.

### P0-1 — deletion now clears object storage ✅ **FIXED**

- **`profiles/views.py`** — `ChildViewSet` gains `perform_destroy`, which calls `media_service.delete_child_media(instance)` **before** `instance.delete()`. The ordering is the fix and is argued in the method's docstring: the `Media` rows are the only record of where the objects live, so a cascade that ran first would leave the objects behind *and* destroy the means of ever finding them.
- **`accounts/views.py`** — `AccountDeleteView.delete` purges each child's media before the single `parent.delete()`, and the `privacy.account_deleted` log line now carries `media_rows` and `media_objects` so the deletion is observable.
- Not implemented as a `post_delete` signal, deliberately: "what happens when a child is deleted" should be answerable by reading the two endpoints that delete a child.
- **Sharing is preserved.** `delete_child_media` purges per row and `purge_objects` only removes what no surviving row wants, so a sibling — or another family who happened to need the same picture — keeps theirs. That property is now pinned by a test rather than assumed.
- **Three tests, in the place the old ones did not look** (`TestDeletingLeavesNothingInStorage` in `tests/test_privacy.py`): the object is gone after a child delete; gone for every child after an account delete; and **still there for a sibling who shares it**.
- **The new tests were verified to bite.** With the two view fixes stashed and everything else in place, both delete tests fail on the assertion they exist for:
  ```
  FAILED TestDeletingLeavesNothingInStorage::test_deleting_a_child_removes_the_objects_behind_their_pictures
         AssertionError: the picture is still in the bucket
  FAILED TestDeletingLeavesNothingInStorage::test_deleting_the_account_removes_them_for_every_child
  ```
  Restored, all three pass. `tests/test_privacy.py` → **42 passed**, exit 0.
- `AccountDeleteView`'s docstring and `profiles/views.py`'s module docstring now say deletion is two deletions, and say why the previous "no orphans" test could pass while it was not.
- **Parent-facing copy caught up with the behaviour**, in both places that enumerate what a delete removes: the disclosure's *"What you can do"* section and `Privacy.tsx`'s per-child card now end "…every report, and every picture and recording we made for them." Those sentences were previously accurate about rows and silent about the rest.

### P1-1 — the disclosure now states only what is true ✅ **FIXED**

Two sentences in `accounts/privacy.py` rewritten, using the treatment the file already applies to the provider paragraph — say what is true today, and phrase what is coming as a commitment with a trigger.

- *Was:* "Access to production data is restricted and audited."
  *Now:* the encryption sentence gains "with a key the database itself does not hold" (true — `EncryptedTextField`, and `MEMORY_ENCRYPTION_KEY` is required outside dev), followed by: "We are a very small team and nobody outside it can reach this data. Before we take on families beyond our pilot we will add audited access logging on child data and say so here; today the honest statement is that the controls are the ones a small team can have, and we would rather tell you that than imply a process we have not built."
- *Was:* "Encrypted backups roll off on their own schedule and are never used to restore a deleted family."
  *Now:* "We do not keep backups yet, so today there is no copy anywhere for a deletion to miss. When we start taking them — before we take on families beyond our pilot, because losing your child's memories would be unforgivable — they will be encrypted, they will expire on a schedule we will state here, and a deleted family will never be restored from one."
  This is the stronger claim, not the weaker one: with no backups, "the data goes at once" is now unconditionally true.
- `DISCLOSURE_VERSION` stays `2026-08-12` — the disclosure changed on the day it was written, so the version is still correct rather than in need of a bump.
- Checked that none of the six disclosure tests depended on the removed wording, and that no fixture or snapshot elsewhere quoted it (`grep -rn "restricted and audited\|backups roll off"` → nothing outside the file).

### P1-2 — `useChildSettings` now behaves as documented ✅ **FIXED**

`staleTime: 0` and `refetchOnWindowFocus: true` set **explicitly** on the hook, which is what the docstring already claimed. The comment now also records *why* they are stated rather than left to the defaults: `App.tsx`'s app-wide defaults are `staleTime: 30_000` / `refetchOnWindowFocus: false`, so a hook that only described this behaviour inherited the reverse of it — which is exactly what had happened.

### Not fixed (P2s), by decision

All six P2s are left open and recorded above. None is a correctness or privacy failure, and three of them (P2-2 export media, P2-4 the two close rules, P2-5 the Day-12 weekly count) want a real-provider playtest or belong to another day's surface rather than a code change made blind.

## Re-verification (re-measured, after the fixes)

| Check | Result |
|---|---|
| `make lint` | ruff check clean · `ruff format --check` clean (202 files) · eslint clean · prettier clean |
| Backend `pytest` | **1149 passed** in 483s, exit 0 — 1146 before, +3 new storage tests |
| Frontend `vitest run` | **142 passed** (11 files) |
| `npm run typecheck` | `tsc --noEmit` clean |
| `makemigrations --check --dry-run` | "No changes detected" — this fix touches no model |
| Schema / client regenerated | `frontend/openapi.json` + `schema.ts` — one operation changed (`children_destroy` summary + description), regenerated and committed so the CI staleness check stays green |
| **CI** | PENDING_CI |

Final scorecard: ✅ **7 / 7**. The verdict after this session is **SHIP**, with the six P2s carried forward.

**Still owed:** the founder's manual test (§8 of the brief) — set an avoid-topic, a low ceiling and a short cap, play against a **real** provider, and confirm the topic never shows, the intensity stays gentle, the session ends warmly at the cap, each accessibility toggle takes effect, the export downloads, and a deleted child is gone. Everything verified in this report was verified against the scripted provider or by direct code exercise; P2-4 in particular (the two close rules the model has to reconcile at the cap) cannot be judged without a real model.
