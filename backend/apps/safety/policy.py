"""The content policy, as something a machine can check (PRODUCT.md §13.3, §13.4).

`assembler.safety_policy_layer` writes this policy *for a model to follow*. This
module checks it *after the fact*, without one. The two are deliberately the same
rules expressed twice, because a rule that only exists as prose in a prompt is a
rule with nothing behind it — that is exactly how the Day-5 "tomorrow hook" went
missing five times out of five.

**Why a deterministic layer at all, when §13.3 asks for a model classifier.**
Because the classifier is a network call, and a network call has an outage. A
guard that disappears when a provider has a bad day is not a guard; the honest
posture is a cheap, high-precision check that *always* runs, plus a strong model
check on top of it (`apps.safety.classifier`), with the model's absence recorded
as a degradation rather than passed off as a pass. The two together are §13.2's
defence in depth in one call site.

**Precision over recall, deliberately.** Every pattern here is narrow enough that
a false positive is close to impossible in a warm children's story, because the
cost of a false positive is a child being served a fallback beat instead of the
story they were in. Recall is the classifier's job — it is the layer that can
read "the fox held the rabbit under the water until it stopped moving" and know
what it is, and no keyword list ever will.

Nothing here reads the *prompt*; it reads the generated beat. A policy check that
inspected its own instructions would grade the wrong document.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.profiles.models import Child
from apps.safety.models import Severity

# ---------------------------------------------------------------------------
# What a finding is
# ---------------------------------------------------------------------------
#: The four actions §13.2 defines, and the only values `Decision.action` takes.
PASS = "pass"
REWRITE = "rewrite"
BLOCK = "block"
ESCALATE = "escalate"

#: Ordered worst-last, so combining two verdicts is `max` over this index.
ACTION_ORDER = (PASS, REWRITE, BLOCK, ESCALATE)

SEVERITY_ORDER = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)

# Categories. Named because they are written to `SafetyIncident.category` and
# asserted on by the red-team suite.
VIOLENCE = "violence"
DEATH = "death"
SELF_HARM = "self_harm"
ADULT = "adult_theme"
SUBSTANCES = "substances"
UNSAFE_ADVICE = "unsafe_advice"
GROOMING = "grooming"
PII_SOLICITATION = "pii_solicitation"
FRIGHTENING = "frightening_above_ceiling"
AVOID_TOPIC = "avoid_topic"
FEAR_MISUSE = "fear_misuse"
DISTRESS = "child_distress"
CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
MALFORMED = "malformed_generation"


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule that fired, and what it thinks should happen."""

    category: str
    action: str
    severity: str
    #: The *pattern* that matched, never the text it matched. An incident row
    #: must not become a copy of the content we refused to show a child.
    marker: str


def worst(actions: Iterable[str]) -> str:
    """The most severe action in a set. `PASS` when there is nothing."""
    return max((a for a in actions), key=ACTION_ORDER.index, default=PASS)


def worst_severity(severities: Iterable[str]) -> str:
    return max((s for s in severities), key=SEVERITY_ORDER.index, default=Severity.LOW)


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def _rule(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE)


#: `(category, action, severity, name, pattern)`. Applies to **every** beat,
#: whatever the child's age band or ceiling — these are §13.3's absolutes, and a
#: parent cannot raise a slider far enough to license any of them.
_ABSOLUTE_RULES: tuple[tuple[str, str, str, str, re.Pattern[str]], ...] = (
    (
        VIOLENCE,
        BLOCK,
        Severity.HIGH,
        "harm_to_a_character",
        _rule(
            r"\b(?:kill|kills|killed|killing|murder|murdered|stab|stabbed|stabbing|"
            r"strangle[ds]?|strangling|shoot\s+(?:him|her|them|it)|shot\s+dead|"
            r"beat\s+(?:him|her|them)\s+up|smash(?:ed)?\s+(?:his|her|their)\s+"
            r"(?:head|face)|tortur(?:e|ed|ing))\b"
        ),
    ),
    (
        VIOLENCE,
        BLOCK,
        Severity.HIGH,
        "weapon",
        # `blade` is deliberately absent: a blade of grass is a picture-book noun
        # and this list must not cost a beat for one.
        _rule(r"\b(?:gun|guns|rifle|pistol|shotgun|bullet|bullets|knife|knives|sword|swords)\b"),
    ),
    (
        VIOLENCE,
        BLOCK,
        Severity.HIGH,
        "gore",
        _rule(r"\b(?:blood|bloody|bleeding|gore|wound|wounded|corpse|dead\s+body)\b"),
    ),
    (
        DEATH,
        BLOCK,
        Severity.HIGH,
        "death",
        _rule(r"\b(?:died|dying|death|drowned|drowning|funeral|buried\s+(?:him|her|them))\b"),
    ),
    (
        SELF_HARM,
        ESCALATE,
        Severity.CRITICAL,
        "self_harm",
        _rule(
            r"\b(?:suicide|kill(?:s|ed|ing)?\s+(?:your|my|him|her|them)self|"
            r"hurt(?:s|ing)?\s+(?:your|my|him|her)self|cut\s+(?:your|my)self|"
            r"end\s+(?:your|my)\s+life|want(?:s|ed)?\s+to\s+die)\b"
        ),
    ),
    (
        ADULT,
        BLOCK,
        Severity.HIGH,
        "adult_theme",
        _rule(r"\b(?:sex|sexy|sexual|naked|nude|porn|erotic|kiss(?:ed|ing)?\s+passionately)\b"),
    ),
    (
        SUBSTANCES,
        BLOCK,
        Severity.MEDIUM,
        "substances",
        _rule(
            r"\b(?:beer|wine|whisky|whiskey|vodka|drunk|drunken|cigarette|cigarettes|"
            r"smoking|vape|vaping|cocaine|heroin|weed|marijuana)\b"
        ),
    ),
    (
        UNSAFE_ADVICE,
        BLOCK,
        Severity.HIGH,
        "unsafe_advice",
        _rule(
            r"\b(?:swallow|drink|eat|taste)\s+(?:the\s+|this\s+|some\s+|that\s+)?"
            r"(?:bleach|pills?|medicine|tablets?|berries\s+you\s+find|mushrooms\s+you\s+find)\b"
            r"|\btouch\s+(?:the\s+)?(?:stove|hob|socket|plug|wires?|flames?)\b"
            r"|\bgo\s+(?:outside|out)\s+(?:alone|by\s+yourself)\s+at\s+night\b"
            r"|\bclimb\s+out\s+(?:of\s+)?(?:the\s+)?window\b"
        ),
    ),
    (
        GROOMING,
        ESCALATE,
        Severity.CRITICAL,
        "secrecy",
        _rule(
            r"\b(?:our\s+(?:little\s+)?secret|don'?t\s+tell\s+(?:anyone|any\s?one|"
            r"your\s+(?:mum|mom|mummy|mommy|dad|daddy|parents?|grown-?ups?)|a\s+grown-?up)|"
            r"keep\s+(?:this|it)\s+(?:a\s+)?secret\s+from\s+(?:your|the))\b"
        ),
    ),
    (
        PII_SOLICITATION,
        BLOCK,
        Severity.HIGH,
        "asks_for_identity",
        _rule(
            r"\b(?:what'?s?\s+your\s+(?:last\s+name|surname|full\s+name|address|"
            r"phone(?:\s+number)?|school|password)|where\s+do\s+you\s+live|"
            r"what\s+school\s+do\s+you\s+go\s+to|tell\s+me\s+your\s+address)\b"
        ),
    ),
)

#: Frightening imagery, graded by the ceiling it is allowed *above* (§13.4). A
#: beat carrying one of these is checked against `child.intensity_ceiling`: at or
#: below the listed rung it is a rewrite, above it the parent has licensed it.
#:
#: Rewrite rather than block, because "too intense for this particular child" is
#: the one policy failure a constrained regeneration reliably fixes — the story is
#: fine, the dial is wrong.
#:
#: The rungs are set so the **default** ceiling of 2 ("real stakes, but no one is
#: ever frightened or hurt") licenses a goblin and refuses the word *terrified*.
#: That split is the point: a picture-book villain is ordinary vocabulary and
#: flagging it would spend a second generation on most beats in the product,
#: while language about being frightened contradicts the ceiling in its own words.
_INTENSITY_RULES: tuple[tuple[int, str, re.Pattern[str]], ...] = (
    (
        1,
        "monstrous",
        _rule(r"\b(?:monster|monsters|beast|demon|devil|zombie|vampire|witch|goblin|troll)\b"),
    ),
    (
        2,
        "terror",
        _rule(
            r"\b(?:terrified|terrifying|terror|horror|horrifying|petrified|"
            r"screamed?|screaming|shriek(?:ed|ing)?|nightmare|nightmares)\b"
        ),
    ),
    (
        1,
        "peril",
        # No `chased` and no `fell`: a puppy chases a butterfly and a leaf falls
        # from a tree in stories this product exists to tell.
        _rule(
            r"\b(?:trapped|lost\s+forever|never\s+come\s+back|no\s+way\s+out|"
            r"can'?t\s+get\s+out)\b"
        ),
    ),
    (1, "dark", _rule(r"\b(?:pitch\s+black|complete\s+darkness|alone\s+in\s+the\s+dark)\b")),
)

#: Words that make a child's *own* utterance an escalation rather than a story
#: input (§13.10). Narrow to the point of bluntness: this fires a parent alert,
#: and an alert a family learns to ignore is worse than no alert at all.
_DISTRESS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "harm_disclosure",
        _rule(
            r"\b(?:(?:he|she|they|mum|mummy|mom|mommy|dad|daddy|my\s+\w+)\s+"
            r"(?:hits?|hit|hurts?|hurt|beats?|beat|touched)\s+me"
            r"|someone\s+(?:hurt|hurts|touched)\s+me"
            r"|i'?m\s+(?:being\s+)?(?:hurt|bullied)"
            r"|i\s+(?:am\s+)?(?:being\s+)?(?:hurt|bullied))\b"
        ),
    ),
    (
        "self_harm_disclosure",
        _rule(
            r"\b(?:i\s+want\s+to\s+die|i\s+wish\s+i\s+(?:was|were)\s+dead|"
            r"i\s+hate\s+myself|i\s+want\s+to\s+hurt\s+myself|nobody\s+loves\s+me)\b"
        ),
    ),
    (
        "acute_distress",
        _rule(r"\b(?:help\s+me\s+please|i'?m\s+really\s+scared|i'?m\s+not\s+safe)\b"),
    ),
)


def _word_pattern(term: str) -> re.Pattern[str] | None:
    """A word-boundary matcher for a parent-supplied topic or fear.

    Escaped, because these strings came from a text box: a parent who types
    `spiders (big ones)` must get a working rule and not a regex error, and a
    parent who types `.*` must not get a rule that matches every beat.

    **Number-insensitive on the final word**, which is not a nicety: a parent
    typing "spiders" into the avoid box has said *never spiders*, and a beat that
    said "a friendly spider waved hello" got through the first version of this
    function. Topics are typed in whichever number comes to mind and the rule has
    to mean the same thing either way.
    """
    cleaned = " ".join(term.split())
    if len(cleaned) < 3:
        # Two characters match inside ordinary words and would flag every beat.
        return None

    head, _, tail = cleaned.rpartition(" ")
    stem = (
        tail.removesuffix("es") if tail.endswith(("ses", "xes", "hes")) else tail.removesuffix("s")
    )
    if len(stem) < 3:
        stem = tail
    prefix = f"{re.escape(head)} " if head else ""
    return re.compile(rf"\b{prefix}{re.escape(stem)}(?:e?s)?\b", re.IGNORECASE)


def avoid_topic_findings(text: str, topics: Sequence[str]) -> list[Finding]:
    """Topics the parent named, appearing in the beat (§13.4).

    A block, at the highest deterministic severity below escalation, and with no
    ceiling to weigh it against: an avoid-topic is not a dial, it is the one
    place in the product where a parent gets to say *never*, and honouring it
    approximately would be worse than not offering it.
    """
    findings = []
    for topic in topics:
        pattern = _word_pattern(str(topic))
        if pattern is not None and pattern.search(text):
            findings.append(
                Finding(
                    category=AVOID_TOPIC,
                    action=BLOCK,
                    severity=Severity.HIGH,
                    # The topic *is* the parent's own words, and it is already in
                    # their child's profile — recording it here tells a reviewer
                    # which rule fired without copying any generated content.
                    marker=f"topic:{' '.join(str(topic).split())[:60]}",
                )
            )
    return findings


def fear_findings(text: str, fears: Sequence[str]) -> list[Finding]:
    """A known fear used as a threat rather than as something faced safely.

    §13.4 and the safety-policy layer both allow a fear to appear — with the
    companion present, with the child in control, resolved warmly. So the fear
    alone is not a finding; the fear *next to* frightening language is. That
    keeps "Pip and you say hello to a friendly spider" playable and catches "a
    huge spider chased you into the dark".
    """
    if not fears:
        return []

    frightening = [name for _, name, pattern in _INTENSITY_RULES if pattern.search(text)]
    if not frightening:
        return []

    findings = []
    for fear in fears:
        pattern = _word_pattern(str(fear))
        if pattern is not None and pattern.search(text):
            findings.append(
                Finding(
                    category=FEAR_MISUSE,
                    action=REWRITE,
                    severity=Severity.MEDIUM,
                    marker=f"fear+{frightening[0]}",
                )
            )
    return findings


def check_text(text: str, child: Child) -> list[Finding]:
    """Every deterministic policy finding for one piece of generated text.

    `text` is everything the child would see or hear — the narration, the spoken
    question and every option label — concatenated by the caller. Concatenated
    rather than checked field by field because a rule split across two fields is
    still a rule broken, and because the option labels are read aloud in the same
    breath as the narration (`orchestrator.spoken_line`).
    """
    findings: list[Finding] = []

    for category, action, severity, name, pattern in _ABSOLUTE_RULES:
        if pattern.search(text):
            findings.append(
                Finding(category=category, action=action, severity=severity, marker=name)
            )

    ceiling = child.intensity_ceiling
    for licensed_above, name, pattern in _INTENSITY_RULES:
        if ceiling <= licensed_above and pattern.search(text):
            findings.append(
                Finding(
                    category=FRIGHTENING,
                    action=REWRITE,
                    severity=Severity.MEDIUM,
                    marker=f"{name}@ceiling{ceiling}",
                )
            )

    findings.extend(avoid_topic_findings(text, child.avoid_topics or []))
    findings.extend(fear_findings(text, child.fears or []))
    return findings


def check_child_utterance(text: str) -> list[Finding]:
    """Escalation signals in something the child themselves said (§13.10).

    Separate from `check_text` because the direction matters: the same sentence
    is a policy violation coming *out* of the model and a child asking for help
    coming *in* from a microphone. Only one of those calls a parent.
    """
    findings = []
    for name, pattern in _DISTRESS_RULES:
        if pattern.search(text):
            findings.append(
                Finding(
                    category=DISTRESS,
                    action=ESCALATE,
                    severity=Severity.CRITICAL,
                    marker=name,
                )
            )
    return findings
