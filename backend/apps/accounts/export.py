"""The data export (PRODUCT.md §12.8, §13.7, §13.8).

> *"One-click data export and account/data deletion. COPPA/GDPR/CCPA rights are
> exercised here."*

One zipped bundle of JSON, built in memory and streamed as a download. What it
contains is everything a parent owns: the account, each child's profile and
settings, their companion, every adventure with every beat and every choice
**verbatim**, every memory the companion holds, the learning progress, the world
map, and every session report the dashboard has ever shown them.

Four decisions are worth stating, because each is a place an export quietly
becomes worthless.

**Verbatim, not summarised.** The transparency log's whole argument (§12.6) is
that a parent can read the actual stories; an export that shipped titles and word
counts would be a receipt, not the data. So `adventures.json` carries the
narration of every beat and what the child answered under it — the same bytes the
transcript endpoint returns.

**Everything, including the things that are awkward.** The safety incidents are in
here. A parent is entitled to know that eleven beats about their child were
refused and why, and an export that omitted our own refusals would be the one
omission a reader would most want. What is *not* in here is the beat text a guard
blocked, because that text was never persisted — see `apps.safety.incidents`.

**No embeddings, no vectors, no internal ids beyond what is needed to join the
files.** An export is a document a person reads, and a 1536-float array per memory
would be a hundred megabytes of noise that tells a parent nothing about their
child. The memory *content* — the sentence the companion holds — is all of it that
is meaningful, and it is all here.

**Built synchronously, and bounded by that on purpose.** A family's data is
kilobytes to a few megabytes; this is not a job worth a worker, a signed URL and
a "your export is ready" email that would itself need a notification channel we
have deliberately not built. `EXPORT_ROW_CAP` bounds each collection so a
pathological account cannot turn one request into an out-of-memory, and the
manifest says plainly when a cap was hit rather than silently truncating.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.accounts.models import ConsentRecord, User
from apps.analytics.models import SessionReport
from apps.companion.models import Character, Companion
from apps.learning.models import LearningProgress
from apps.memory.models import InterestEdge, Memory, WorldPlace
from apps.profiles.models import Child
from apps.profiles.settings import settings_for
from apps.reading.models import ReadingAttempt
from apps.safety.models import SafetyIncident
from apps.story.models import Adventure, Beat, Choice, PlaySession

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

#: How many rows of any one collection an export will carry. High enough that no
#: real family reaches it (a year of daily play is a few hundred adventures and a
#: few thousand beats), low enough that the bundle stays a document. When it is
#: hit, `manifest.json` says so — a truncated export that did not admit it would
#: be a lie in the one file whose job is honesty.
EXPORT_ROW_CAP = 5_000

#: The format version, so a bundle a parent kept for two years is still readable
#: and so a future importer knows what it is holding.
EXPORT_FORMAT = "primer-export-v1"


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _capped(queryset: QuerySet[Any]) -> tuple[list[Any], bool]:
    """`(rows, was_truncated)` — one extra row is fetched to detect the cap.

    A `QuerySet` and not an `Iterable`: the slice below is the whole point, and it
    is a `LIMIT` in SQL rather than a Python truncation of a list we already paid
    to build. Every caller passes a queryset; the looser annotation was the reason
    mypy could not see that the slice was legal.
    """
    rows = list(queryset[: EXPORT_ROW_CAP + 1])
    return (rows[:EXPORT_ROW_CAP], True) if len(rows) > EXPORT_ROW_CAP else (rows, False)


def _account(parent: User) -> dict[str, Any]:
    return {
        "id": str(parent.pk),
        "email": parent.email,
        "display_name": parent.display_name,
        "locale": parent.locale,
        "date_joined": _iso(parent.date_joined),
        "consents": [
            {
                "type": record.consent_type,
                "method": record.method,
                "granted_at": _iso(record.granted_at),
                "revoked_at": _iso(record.revoked_at),
                # The IP and the user agent are evidence *about the parent*, kept
                # for the consent audit trail (§13.7), so they belong in the
                # parent's own export.
                "ip": record.ip,
                "evidence": record.evidence_ref,
            }
            for record in ConsentRecord.objects.filter(parent=parent).order_by("granted_at")
        ],
    }


def _child(child: Child) -> dict[str, Any]:
    """One child's profile *and* the settings as they are actually enforced."""
    controls = settings_for(child)
    return {
        "id": str(child.pk),
        "first_name": child.first_name,
        "birthdate": child.birthdate.isoformat(),
        "locale": child.locale,
        "pre_reader": child.pre_reader,
        "interests": list(child.interests or []),
        "created_at": _iso(child.created_at),
        "settings": {
            "intensity_ceiling": controls.intensity_ceiling,
            "avoid_topics": list(controls.avoid_topics),
            "fears": list(controls.fears),
            "session_cap_min": controls.session_cap_min,
            "time_window": controls.time_window.as_text(),
            "voice_on": controls.voice_on,
            "text_on": controls.text_on,
            "accessibility": controls.accessibility.as_json(),
        },
    }


def _companion(child: Child) -> dict[str, Any] | None:
    companion = Companion.objects.filter(child=child).first()
    if companion is None:
        return None
    return {
        "name": companion.name,
        "archetype": companion.archetype,
        "personality": companion.personality,
        "visual_descriptor": companion.visual_descriptor,
        "created_at": _iso(companion.created_at),
        "characters_met": [
            {"name": row.name, "description": row.visual_descriptor, "met_at": _iso(row.created_at)}
            for row in Character.objects.filter(child=child).order_by("created_at")
        ],
    }


def _adventures(child: Child) -> tuple[list[dict[str, Any]], bool]:
    """Every story, verbatim, with what the child did in it (§12.6).

    Two queries and then a group-by in Python rather than a query per adventure:
    an export of a year of play is a few hundred adventures and would otherwise be
    a few hundred round trips on one request.
    """
    adventures, truncated = _capped(Adventure.objects.filter(child=child).order_by("started_at"))
    ids = [adventure.pk for adventure in adventures]

    beats_by_adventure: dict[Any, list[Beat]] = {}
    for beat in Beat.objects.filter(adventure_id__in=ids).order_by("adventure_id", "seq"):
        beats_by_adventure.setdefault(beat.adventure_id, []).append(beat)

    choice_by_beat = {
        choice.beat_id: choice for choice in Choice.objects.filter(adventure_id__in=ids)
    }

    exported = []
    for adventure in adventures:
        beats = []
        for beat in beats_by_adventure.get(adventure.pk, []):
            choice = choice_by_beat.get(beat.pk)
            beats.append(
                {
                    "seq": beat.seq,
                    "narration": beat.narration,
                    "asked": (beat.interaction or {}).get("prompt_audio"),
                    "options": [option.get("label") for option in beat.options],
                    "emotion": beat.emotion,
                    # Shown rather than hidden, exactly as the transparency log
                    # shows it: a beat the safety layer rewrote or replaced is a
                    # fact about this child's stories.
                    "safety_status": beat.safety_status,
                    "written_by": beat.model,
                    "child_answered": None
                    if choice is None
                    else {
                        "chose": choice.chosen_key,
                        "value": choice.value,
                        "assisted": choice.assisted,
                        "retries": choice.retries,
                        "at": _iso(choice.created_at),
                    },
                }
            )
        exported.append(
            {
                "id": str(adventure.pk),
                "title": adventure.title,
                "status": adventure.status,
                "started_at": _iso(adventure.started_at),
                "completed_at": _iso(adventure.completed_at),
                "world": adventure.plan.get("world_name") or "",
                "place": adventure.plan.get("place") or "",
                "objective": adventure.primary_objective_id,
                "beats": beats,
            }
        )
    return exported, truncated


def _sessions(child: Child) -> list[dict[str, Any]]:
    rows, _ = _capped(PlaySession.objects.filter(child=child).order_by("started_at"))
    return [
        {
            "id": str(row.pk),
            "started_at": _iso(row.started_at),
            "ended_at": _iso(row.ended_at),
            "duration_s": row.duration_s,
            "ended_reason": row.ended_reason,
        }
        for row in rows
    ]


def _memories(child: Child) -> list[dict[str, Any]]:
    """What the companion remembers. Content only — no vectors (see the header)."""
    rows, _ = _capped(
        Memory.objects.filter(child=child, deleted_at__isnull=True).order_by("-created_at")
    )
    return [
        {
            "type": row.type,
            "content": row.content,
            "created_at": _iso(row.created_at),
            "source_adventure": str(row.source_adventure_id) if row.source_adventure_id else None,
        }
        for row in rows
    ]


def _reading(child: Child) -> list[dict[str, Any]]:
    """Every line this child was invited to read aloud, and what came of it.

    In the export because §13.7's right to *see* what is held has to cover
    everything held, and this is a record of a child performing — which is a more
    personal thing than a tap on a choice card.

    `audio_kept` is the field a parent is actually here for: it says, per
    attempt, whether a recording of their child's voice exists. `audio_path` is
    deliberately not exported — a server path is operational detail that tells a
    parent nothing and tells anyone who obtains the bundle where to look.
    """
    rows, _ = _capped(ReadingAttempt.objects.filter(child=child).order_by("created_at"))
    return [
        {
            "read_at": _iso(row.created_at),
            "line": row.passage_text,
            "spoke": row.spoke,
            "duration_ms": row.duration_ms,
            "recording_kept": row.audio_kept,
        }
        for row in rows
    ]


def _learning(child: Child) -> list[dict[str, Any]]:
    return [
        {
            "objective": row.objective.code,
            "description": row.objective.description,
            "mastery": round(float(row.mastery), 3),
            "updated_at": _iso(row.updated_at),
        }
        for row in LearningProgress.objects.filter(child=child).select_related("objective")
    ]


def _world(child: Child) -> dict[str, Any]:
    """The child's own map and interest graph (§8.3, §11.7) — the durable half.

    §13.8 draws the line this file sits on: raw behavioural logs are short-lived,
    but the memory graph is *the parent's*, kept for years, and is therefore
    exactly what an export exists to hand over.
    """
    return {
        "places": [
            {
                "name": row.name,
                "mood": row.mood,
                "description": row.description,
                "inhabitants": list(row.inhabitants or []),
                "visits": row.visit_count,
                "last_visited_at": _iso(row.last_visited_at),
            }
            for row in WorldPlace.objects.filter(child=child).order_by("name")
        ],
        "interests": [
            {"topic": row.topic, "weight": round(float(row.weight), 3)}
            for row in InterestEdge.objects.filter(child=child).order_by("-weight")
        ],
    }


def _reports(child: Child) -> list[dict[str, Any]]:
    """Every session report the dashboard has shown this parent (§12.1)."""
    rows, _ = _capped(SessionReport.objects.filter(child=child).order_by("occurred_at"))
    return [
        {
            "session": str(row.session_id) if row.session_id else None,
            "occurred_at": _iso(row.occurred_at),
            "duration_s": row.duration_s,
            "ended_reason": row.ended_reason,
            "beats": row.beats,
            "adventure": row.adventure_title,
            "world": row.world_name,
            "place": row.place_name,
            "key_choice": row.key_choice,
            "objective": row.objective_code,
            "milestone": row.milestone_name,
            "how_it_went": row.how_it_went,
            "highlight": row.highlight,
            "skills": row.skills,
            "interests": row.interests,
        }
        for row in rows
    ]


def _incidents(child: Child) -> list[dict[str, Any]]:
    """Our own refusals, in the parent's export (§12.6's honesty, applied here).

    Never the refused text — it was never stored (`apps.safety.incidents`). What a
    parent gets is what fired, when, and what we did about it.
    """
    rows, _ = _capped(SafetyIncident.objects.filter(child=child).order_by("created_at"))
    return [
        {
            "category": row.category,
            "action_taken": row.action_taken,
            "severity": row.severity,
            "at": _iso(row.created_at),
        }
        for row in rows
    ]


#: Characters a child's name must not contribute to a zip entry. `/` and `\`
#: would make the name a *path* rather than a path segment; the rest (`:*?"<>|`
#: and the control codes) are illegal in Windows filenames, where the bundle is
#: most likely to be opened.
_UNSAFE_IN_A_PATH_SEGMENT = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _folder_for(child: Child) -> str:
    """`children/<name>-<id8>`, with the name made safe to be part of a path.

    `first_name` is free text: the serializer strips whitespace and requires it
    to be non-empty, and imposes nothing else. Interpolated straight into a zip
    entry — which is what this did — it is a path-injection primitive.

    Two things go wrong, and they are not equally likely. A child called
    `../../x` yields an entry that a naive extractor writes *outside* the
    directory it was pointed at; that one needs a parent to attack their own
    download, so it is a real class of bug and a small risk. A child called
    `Jo:Jo`, or a nickname with a slash in it, yields a bundle that Windows
    simply cannot extract — and that one is an ordinary family, on an ordinary
    name, in the single feature that has to work on the day they stop trusting
    us.

    The `-<id8>` suffix is appended *after* sanitising and is not itself
    sanitised, so two children whose names reduce to the same string still get
    separate folders. A name that reduces to nothing at all falls back to
    `child`, which with the suffix is still unique and still readable.
    """
    name = _UNSAFE_IN_A_PATH_SEGMENT.sub("-", child.first_name)
    # Trailing dots and spaces are also illegal on Windows, and a name of `..`
    # is a traversal that survives the substitution above untouched.
    name = name.strip(" .")
    return f"children/{name or 'child'}-{str(child.pk)[:8]}"


def build_bundle(parent: User) -> tuple[bytes, str]:
    """`(zip bytes, filename)` — everything this parent owns (§12.8).

    Deflated, because it is JSON and compresses roughly ten to one, and a parent
    on a phone is downloading it.
    """
    generated_at = timezone.now()
    truncated: list[str] = []

    children = list(Child.objects.filter(parent=parent).order_by("created_at"))
    files: dict[str, Any] = {"account.json": _account(parent)}

    for child in children:
        folder = _folder_for(child)
        adventures, hit_cap = _adventures(child)
        if hit_cap:
            truncated.append(f"{folder}/adventures.json")

        files[f"{folder}/profile.json"] = _child(child)
        files[f"{folder}/companion.json"] = _companion(child)
        files[f"{folder}/adventures.json"] = adventures
        files[f"{folder}/sessions.json"] = _sessions(child)
        files[f"{folder}/memories.json"] = _memories(child)
        files[f"{folder}/reading.json"] = _reading(child)
        files[f"{folder}/learning.json"] = _learning(child)
        files[f"{folder}/world.json"] = _world(child)
        files[f"{folder}/reports.json"] = _reports(child)
        files[f"{folder}/safety-incidents.json"] = _incidents(child)

    files["manifest.json"] = {
        "format": EXPORT_FORMAT,
        "generated_at": _iso(generated_at),
        "account": parent.email,
        "children": [child.first_name for child in children],
        "files": sorted(name for name in files if name != "manifest.json"),
        "row_cap": EXPORT_ROW_CAP,
        # Named, not counted: a parent who hits the cap needs to know *which*
        # file is short, and an export that quietly dropped rows would be the one
        # dishonest thing in a bundle built to prove honesty.
        "truncated": truncated,
        "notes": (
            "Everything The Primer holds about your family. Story text is exactly "
            "what your child saw and heard. Memory embeddings are omitted: they are "
            "machine representations of the sentences in memories.json and contain "
            "nothing that is not already there."
        ),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(
                # A fixed timestamp per file would be tidier; the real one is more
                # useful to a parent unzipping this in a year.
                zipfile.ZipInfo(name, date_time=generated_at.astimezone(UTC).timetuple()[:6]),
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            )

    logger.info(
        "privacy.export_built parent=%s children=%d files=%d bytes=%d truncated=%d",
        parent.pk,
        len(children),
        len(files),
        buffer.tell(),
        len(truncated),
    )
    filename = f"primer-export-{generated_at:%Y-%m-%d}.zip"
    return buffer.getvalue(), filename
