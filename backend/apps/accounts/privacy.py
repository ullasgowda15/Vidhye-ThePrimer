"""The privacy disclosure, in plain language (PRODUCT.md §12.8, §13.8).

> *"Plain-language explanation of what we collect, why, how it's protected, and
> how it's used (and not used — e.g., never sold, never used to train third-party
> models)."*

**Why the text lives in the backend rather than in the React page.** Because every
sentence in it is a claim about what the *server* does, and the two have to be
changeable together. A disclosure written in the frontend is a disclosure that
drifts the first time somebody adds a provider, and a privacy page that is out of
date is worse than none — it is a specific false statement to a parent who is
trusting it.

**Every claim here is checked against the code, and the ones that are not yet
true are not made.** That rule is the reason for the awkward sentences. We do not
say "encrypted at rest" as an unqualified boast, because that is a property of the
deployment; we say what is true of the application. We do not promise deletion
from provider logs we do not control; we say which providers see what and what
their retention is governed by. §12.6's whole product thesis is that a parent can
check what we tell them, and a disclosure is the one document where an
over-claim is indistinguishable from a lie.

The `[ASSUMPTION]` posture of §13.7 applies: this is the engineering description,
reviewed by nobody legal yet, and it says so at the bottom rather than pretending
otherwise.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

#: Bumped whenever a claim below changes, so a parent can tell whether what they
#: read last month is what is true now — and so a future consent version can point
#: at the disclosure it was granted against.
DISCLOSURE_VERSION = "2026-08-13"

#: Read from settings rather than written into the prose, because the prose is a
#: promise and the setting is what keeps it: `infra/backup/backup.sh` prunes to
#: this number and `manage.py launch_check` reads the newest dump against it. The
#: audit's F-5 was a retention sentence with nothing behind it; a sentence with a
#: hard-coded number would have been the same bug one refactor later.
_BACKUP_RETENTION_DAYS = settings.BACKUP_RETENTION_DAYS

SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "what_we_collect",
        "title": "What we collect",
        "body": (
            "Your email address, and for each child: the first name or nickname you "
            "choose, their birthdate, the things you tell us they love, the things "
            "they are frightened of, and the topics you ask us never to include.\n\n"
            "Then, as they play: the stories we generate for them, the choices they "
            "make, how long each sitting lasted, and the things the companion "
            "remembers about them. Nothing else. We do not ask for a surname, an "
            "address, a school, a photograph, or a contact detail of any kind, and "
            "the companion is instructed never to ask for one either.\n\n"
            "If your child is old enough to read, the companion sometimes asks them "
            "to read a short line aloud at the end of a story. When they do, we keep "
            "the recording. We are being direct about that because it is the "
            "one thing here you would most want to be told plainly: during this "
            "early period we save the audio of your child reading, so we can check "
            "whether the app actually heard them correctly. It was built and tested "
            "on adults imitating children, and that is not good enough to correct a "
            "real child with.\n\n"
            "What that means in practice. The recording stays on our own machine. "
            "It is never uploaded to storage, never sent to any other company, and "
            "nothing has listened to it — not a person and not a program. It is "
            "never used to decide anything about your child, and the app never tells "
            "them they read something wrong.\n\n"
            "You can turn it off for any child at any time, in their settings. "
            "Turning it off deletes every recording we already have of them, "
            "immediately. Deleting the child or the account does the same."
        ),
    },
    {
        "id": "why",
        "title": "Why we collect it",
        "body": (
            "All of it exists to make the story personal and to keep it safe. The "
            "birthdate sets the age band the safety layer enforces. The fears and "
            "avoid-topics are copied into every generation as rules. What the child "
            "loves is what tomorrow's adventure gets built out of, and what the "
            'companion remembers is why it can say "do you still have that dragon '
            'you named?"\n\n'
            "There is no other purpose. We do not build advertising profiles, we do "
            "not sell or share this with data brokers, and there is no third party "
            "receiving any of it for their own use."
        ),
    },
    {
        "id": "never",
        "title": "What we never do",
        "body": (
            "We never sell your data or your child's data. Not to anyone, not ever.\n\n"
            "We never use your child's data to train anybody's AI models — not "
            "ours, and not our providers'.\n\n"
            "We never send your child a notification, a reminder, a streak, or "
            "anything designed to pull them back to the app. The controls in "
            "Settings only ever make their time here shorter. This is deliberate "
            "and it is not something we will change quietly."
        ),
    },
    {
        "id": "who_sees_it",
        "title": "Who else sees it",
        "body": (
            "Writing a story needs an AI provider, drawing it needs an image "
            "provider, and reading it aloud needs a voice provider. When your child "
            "plays, the words needed for that beat go to those companies and come "
            "back. That includes their first name, because the companion says it.\n\n"
            "It does not include your email or your account, and nothing "
            "identifying beyond that first name. It does include the things you "
            "asked us to avoid, the things your child is frightened of, and what "
            "the companion remembers about them — because those are the rules the "
            "story has to be written against, and a provider that was not told "
            "them could not keep them.\n\n"
            "Every one of those providers is on terms that forbid training on what "
            "we send them, and the software now refuses to start if one of them is "
            "not: a cheaper route that reserves the right to learn from your "
            "child's words cannot be configured by accident. What we cannot promise "
            "is anything about a provider's own internal logs, because that is not "
            "ours to control — and we would rather say so plainly than imply a "
            "guarantee we do not have."
        ),
    },
    {
        "id": "protection",
        "title": "How it is protected",
        "body": (
            "Everything travels over encrypted connections. Sign-in tokens are held "
            "in cookies a script cannot read. Every request for a child's data is "
            "checked against the account that owns it before a single row is "
            "loaded, so one family's stories cannot reach another's — and that "
            "check is tested from the outside, with two real accounts, on every "
            "endpoint.\n\n"
            "What the companion remembers is stored with the sensitive fields "
            "encrypted in the database, with a key the database itself does not "
            "hold.\n\n"
            "We are a very small team and nobody outside it can reach this data. "
            "Before we take on families beyond our pilot we will add audited "
            "access logging on child data and say so here; today the honest "
            "statement is that the controls are the ones a small team can have, "
            "and we would rather tell you that than imply a process we have not "
            "built."
        ),
    },
    {
        "id": "your_rights",
        "title": "What you can do",
        "body": (
            "Read everything: the transparency log shows you every story your child "
            "has been told, word for word, including the ones our safety layer "
            "rewrote or replaced.\n\n"
            "Export everything: one file with all of it, including our own safety "
            "refusals.\n\n"
            "Delete everything: deleting a child removes their profile, their "
            "companion, every story, every memory, every report, every picture and "
            "recording we made for them, and any recording of their own voice you "
            "allowed us to keep. Deleting your account does that for "
            "every child and then removes the account. Both happen immediately, and "
            "neither is reversible.\n\n"
            "None of these three needs our permission and none is affected by "
            "withdrawing consent. Withdrawing consent stops us generating anything "
            "new; it never stops you seeing, exporting or deleting what already "
            "exists."
        ),
    },
    {
        "id": "retention",
        "title": "How long we keep it",
        "body": (
            "What the companion remembers is kept for as long as you keep the "
            "account, because losing it would mean losing the friendship your child "
            "has built.\n\n"
            "The server writes an operational log of what it did — which page was "
            "asked for, which story was generated, how long it took. It carries no "
            "child's name and none of the things they love or fear; it identifies a "
            "child only by the same meaningless id the database uses. How long our "
            "hosting provider keeps that log is their setting rather than ours, so "
            "we will not put a number on it here that we cannot enforce. What we "
            "can say, and have made true, is that there is nothing in it worth "
            "keeping about your child.\n\n"
            "When you delete a child or your account, the data goes at once — the "
            "records, what the companion remembered, and the pictures and recorded "
            "narration too. One thing is deliberately kept: a dated line saying that "
            "a deletion happened, so we can prove it did. It holds a scrambled form "
            "of your email address that cannot be turned back into it, and nothing "
            "else about you.\n\n"
            "We take a daily copy of the whole database, because losing your "
            "child's memories would be unforgivable. Each copy deletes itself "
            f"after {_BACKUP_RETENTION_DAYS} days, so a deletion you make today is "
            "gone from every copy we hold within that window. A copy is only ever "
            "used to bring the whole service back after a disaster, never to "
            "restore one family, and anything deleted before the disaster is "
            "deleted again immediately afterwards — that step is written into the "
            "procedure, not left to somebody's memory. Today those copies sit on "
            "the same machine as the database and are not separately encrypted; "
            "before we take on families beyond our pilot they will be encrypted "
            "and held somewhere else, and this paragraph will say so."
        ),
    },
    {
        "id": "standing",
        "title": "Where this stands",
        "body": (
            "The Primer is built for children, so we design to COPPA and to "
            "GDPR-K from the first line of code rather than retrofitting them. "
            "This page describes what the software actually does today. It is "
            "written by the people who built it and has not yet been through "
            "formal legal review — when it has, this page will say so."
        ),
    },
)


def disclosure() -> dict[str, Any]:
    """The whole disclosure, as the privacy centre renders it (§12.8)."""
    return {
        "version": DISCLOSURE_VERSION,
        "headline": "What we collect, why, and what we will never do with it.",
        "sections": [dict(section) for section in SECTIONS],
    }
