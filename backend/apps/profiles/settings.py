"""The parent's settings, as one object (PRODUCT.md §7.13, §12.7, §13.4, §13.6).

`settings_for(child)` is the **only** way anything reads a control. That is the
whole point of the module and it is worth stating plainly, because before Day 13
the controls were read straight off the model in three places — the prompt
assembler, the deterministic policy, and the story orchestrator — and three
readers of a raw column is three chances for one of them to interpret it
differently. §12.7's promise is that a setting is *honored immediately*, and a
promise with three implementations is a promise about whichever one you happened
to test.

So the rules live here, once:

**Normalisation happens on read, not on write.** A ceiling of `9` in the database
— from a fixture, a shell, an old row, a future migration — clamps to the
gentlest rung rather than licensing everything, and an avoid-topic that is blank
or 400 characters long is dropped rather than rendered into a prompt as an empty
bullet. The serializer validates too (`profiles.serializers`), but validation at
the edge protects the rows written *through* the edge, and the safety layer has
to be right about the rows that were not.

**Nothing here is a preference.** `avoid_topics` and `intensity_ceiling` are
inputs to §13.4's enforcement; `session_cap_min` and the time window are §13.6's
wellbeing rules. The presentation half (`voice_on`, `text_on`, `accessibility`)
is genuinely a preference, and is kept in the same object precisely so that "the
parent set this" has one answer rather than two.

**What is deliberately *not* here: anything that loosens a rule.** A parent can
lower the ceiling; nothing in the product raises the absolutes in
`apps.safety.policy`, and `clamped_ceiling` cannot return a value the policy
treats as "anything goes".
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from apps.profiles.models import Child

#: The rungs `intensity_ceiling` may take (§13.4). Anything else resolves to the
#: **gentlest** rung rather than being clamped toward the top, so a corrupt value
#: is never a licence — see `clamped_ceiling`, which is where that is argued and
#: where getting it wrong is a real bug rather than a style point.
MIN_CEILING = 0
MAX_CEILING = 3
#: What a child gets when nobody has chosen — the same value `Child` defaults the
#: column to. Restated rather than imported so the two are visibly the same
#: decision; `tests/test_settings_controls.py` asserts they have not drifted.
DEFAULT_CEILING = 2

#: What a session cap may be set to, in minutes (§13.6). The floor is not zero: a
#: cap of zero is a product that ends the story before it starts, which is not a
#: wellbeing control but a broken one.
MIN_SESSION_CAP_MIN = 5
MAX_SESSION_CAP_MIN = 30

#: How long one parent-typed topic or fear may be. Matches the serializer's own
#: bound; restated here because this function has to be right about rows that
#: never went through the serializer.
MAX_TOPIC_CHARS = 60
MAX_TOPIC_COUNT = 10

#: Volume as a fraction. Clamped rather than trusted because it is handed to an
#: `<audio>` element, where an out-of-range value throws.
MIN_VOLUME = 0.0
MAX_VOLUME = 1.0
DEFAULT_VOLUME = 1.0


def _clean_terms(values: Any) -> tuple[str, ...]:
    """Parent-typed topics or fears, as something a rule can be built from.

    Whitespace-collapsed, trimmed to `MAX_TOPIC_CHARS`, blanks dropped, order
    preserved, deduplicated case-insensitively, and bounded in count. Every one of
    those is load-bearing somewhere downstream: an empty string becomes a bullet
    with nothing after it in the safety-policy layer, and a duplicate becomes two
    identical findings on one beat and therefore two rows in the incident table
    about one refusal.
    """
    if not isinstance(values, Iterable) or isinstance(values, str | bytes):
        return ()

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = " ".join(str(value).split())[:MAX_TOPIC_CHARS]
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            cleaned.append(term)
    return tuple(cleaned[:MAX_TOPIC_COUNT])


def clamped_ceiling(value: Any) -> int:
    """`intensity_ceiling`, or the **gentlest** rung if it is not one of them.

    Note what this deliberately does *not* do: `min(MAX_CEILING, value)`. Clamping
    an out-of-range ceiling toward the top would turn a `9` — a fixture, a shell
    session, a bad migration — into a `3`, which is the *most permissive* setting
    in the product. A value we do not recognise is not a parent asking for more; it
    is a value we cannot read, and the only safe reading of an unreadable ceiling
    is the gentlest one (§13.4). The cost of being wrong in this direction is a
    quieter story; the cost of being wrong in the other is a child shown something
    their parent refused.

    `apps.safety.prompting.assembler` has made the same call since Day 2 — its
    `INTENSITY_GUIDANCE.get(ceiling, INTENSITY_GUIDANCE[0])` falls to rung 0 — and
    the test that asserts it is what caught this function getting it backwards.
    """
    try:
        rung = int(value)
    except (TypeError, ValueError):
        return MIN_CEILING
    return rung if MIN_CEILING <= rung <= MAX_CEILING else MIN_CEILING


def clamped_cap(value: Any) -> int:
    """`session_cap_min`, forced into range. An unreadable value takes the floor.

    A genuine clamp here, unlike `clamped_ceiling`, and the asymmetry is the point:
    for a cap, *both* ends of the clamp are the conservative direction. A stored
    `999` becoming 30 minutes is a shorter sitting, and a stored `-1` becoming 5 is
    a shorter sitting. There is no value of this field where clamping toward the
    range hands a child more than their parent asked for.
    """
    try:
        return max(MIN_SESSION_CAP_MIN, min(MAX_SESSION_CAP_MIN, int(value)))
    except (TypeError, ValueError):
        return MIN_SESSION_CAP_MIN


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """The daily hours a child may play in (§13.6), or the open window.

    `start is None or end is None` means always open, which is the default and the
    state most families will stay in.

    **Wrapping windows are real and are handled.** A parent who allows play
    between 16:00 and 18:00 writes `start < end`; one who allows it between 19:00
    and 07:00 — the bedtime-story shape, and the reason this is not a simple
    comparison — writes `start > end`, and the window is then the union of
    "after start" and "before end". A window whose ends are equal is read as open
    rather than as a zero-length window nobody could ever play in.
    """

    start: datetime.time | None = None
    end: datetime.time | None = None

    @property
    def always_open(self) -> bool:
        return self.start is None or self.end is None or self.start == self.end

    def contains(self, moment: datetime.time) -> bool:
        start, end = self.start, self.end
        if start is None or end is None or start == end:
            return True
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end

    def is_open(self, now: datetime.datetime | None = None) -> bool:
        """Is the window open at `now`, in the server's local time?

        Local time rather than UTC, because a bedtime is a wall-clock fact about
        the room the child is in. Per-family timezones are a real gap and are
        recorded as one: `Child.locale` carries a language, not a zone, so until
        there is a zone to read this is the honest approximation rather than a
        silent one.
        """
        moment = timezone.localtime(now) if now is not None else timezone.localtime()
        return self.contains(moment.time())

    def as_text(self) -> str:
        """The window as a parent would say it, for a report or a log line."""
        start, end = self.start, self.end
        if start is None or end is None or start == end:
            return "any time"
        return f"{start:%H:%M} to {end:%H:%M}"


@dataclass(frozen=True, slots=True)
class Accessibility:
    """§14.6's flags, with defaults and a shape.

    Defaults are the *accessible* choice where there is one: captions on, full
    volume. `reduced_motion`, `high_contrast` and `dyslexia_font` default off
    because each changes the experience for everyone who has not asked for it —
    and because the child surface already honours the OS-level
    `prefers-reduced-motion` on its own (`motion-safe:` throughout the child
    tree), so the toggle is the parent's override rather than the only defence.
    """

    captions: bool = True
    reduced_motion: bool = False
    dyslexia_font: bool = False
    high_contrast: bool = False
    audio_volume: float = DEFAULT_VOLUME

    @classmethod
    def from_json(cls, blob: Any) -> Accessibility:
        """Read the stored blob, ignoring anything unrecognised.

        Tolerant on purpose. This is read on the child's story path, and a key
        added by a newer deploy — or a hand-edited row — must produce a slightly
        stale experience rather than a stack trace between a child and their
        story (§13).
        """
        data = blob if isinstance(blob, dict) else {}

        def flag(name: str, default: bool) -> bool:
            value = data.get(name, default)
            return bool(value) if isinstance(value, bool | int) else default

        try:
            volume = float(data.get("audio_volume", DEFAULT_VOLUME))
        except (TypeError, ValueError):
            volume = DEFAULT_VOLUME

        return cls(
            captions=flag("captions", True),
            reduced_motion=flag("reduced_motion", False),
            dyslexia_font=flag("dyslexia_font", False),
            high_contrast=flag("high_contrast", False),
            audio_volume=max(MIN_VOLUME, min(MAX_VOLUME, volume)),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "captions": self.captions,
            "reduced_motion": self.reduced_motion,
            "dyslexia_font": self.dyslexia_font,
            "high_contrast": self.high_contrast,
            "audio_volume": self.audio_volume,
        }


@dataclass(frozen=True, slots=True)
class ChildSettings:
    """Everything a parent has decided about one child, normalised.

    Frozen, because a caller that could edit this would be editing a copy and
    would believe it had changed a setting. The way to change one is `PATCH
    /children/{id}/settings`.
    """

    #: Safety (§13.4). Read by the prompt assembler *and* the output guard.
    intensity_ceiling: int = DEFAULT_CEILING
    avoid_topics: tuple[str, ...] = ()
    fears: tuple[str, ...] = ()

    #: Wellbeing (§13.6).
    session_cap_min: int = MIN_SESSION_CAP_MIN
    time_window: TimeWindow = field(default_factory=TimeWindow)

    #: Presentation (§7.13, §14.6). Never read by generation or the guard.
    voice_on: bool = True
    text_on: bool = True
    accessibility: Accessibility = field(default_factory=Accessibility)

    @property
    def words_on_screen(self) -> bool:
        """Will the child's screen carry the spoken words in text?

        Accessibility outranks preference: `text_on` is a parent's taste about the
        story paragraph, `captions` is §14.6's guarantee that everything spoken has
        an on-screen equivalent, and when the two disagree the guarantee wins. The
        child surface additionally forces this true when the voice is off, because
        a beat delivered as neither sound nor text is not a beat.
        """
        return self.text_on or self.accessibility.captions or not self.voice_on


def settings_for(child: Child) -> ChildSettings:
    """This child's controls, normalised — the one read every caller uses.

    Pure and cheap: it touches only fields already loaded on `child`, so it is
    safe on the story-start path, inside the prompt assembler (which must stay
    free of I/O — see that module's docstring), and inside an async generator.
    """
    return ChildSettings(
        intensity_ceiling=clamped_ceiling(child.intensity_ceiling),
        avoid_topics=_clean_terms(child.avoid_topics),
        fears=_clean_terms(child.fears),
        session_cap_min=clamped_cap(child.session_cap_min),
        time_window=TimeWindow(start=child.time_window_start, end=child.time_window_end),
        voice_on=bool(child.voice_on),
        text_on=bool(child.text_on),
        accessibility=Accessibility.from_json(child.accessibility),
    )
