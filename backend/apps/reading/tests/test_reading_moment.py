"""Inviting a child to read (Docs/Build/Reading-Integration.md §8).

Phase 1's whole claim is that a child is *asked* and never *judged*, and that
their voice is not kept unless a grown-up said it could be. Both are properties
of the code rather than of the intention, so both are asserted here.

The gates get the most attention, because the way this feature fails badly is not
a crash. It is a four-year-old being asked to read.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pytest
from django.urls import reverse

from apps.profiles.models import Child
from apps.reading import service
from apps.reading.models import ReadingAttempt

pytestmark = pytest.mark.django_db


@pytest.fixture
def reader(child: Any, settings: Any) -> Child:
    """A child the product may invite: old enough, and a parent says they read."""
    settings.READING_ENABLED = True
    child.birthdate = datetime.date(datetime.date.today().year - 6, 5, 4)
    child.pre_reader = False
    child.save()
    return child


def url_for(child: Child) -> str:
    return reverse("child-reading", kwargs={"child_id": child.pk})


def a_line(child: Child) -> Any:
    """A line, asserted present.

    `choose_line` returns None only when the curated bank holds nothing short
    enough, which is a programming error rather than a state a test explores —
    so every call site would otherwise carry the same `assert is not None`
    purely to satisfy the type checker.
    """
    line = service.choose_line(child)
    assert line is not None, "the curated bank should always yield a line"
    return line


# ---------------------------------------------------------------------------
# Who gets asked
# ---------------------------------------------------------------------------
class TestTheGates:
    def test_a_child_who_reads_is_offered_a_line(self, reader: Child) -> None:
        assert service.may_read(reader) is True

    def test_a_pre_reader_is_never_asked(self, reader: Child) -> None:
        """The failure this feature most needs to avoid.

        Being asked to read by a friend who should know you cannot is not a
        small disappointment to a five-year-old. `pre_reader` has been on the
        model since Day 1 and defaults to True, so silence means no.
        """
        reader.pre_reader = True
        reader.save()

        assert service.may_read(reader) is False

    def test_a_four_year_old_is_never_asked_however_configured(self, reader: Child) -> None:
        """Age is a rule about the product, not a setting. `pre_reader=False` on a
        four-year-old is a parent being optimistic, and it must not be enough."""
        reader.birthdate = datetime.date(datetime.date.today().year - 4, 5, 4)
        reader.pre_reader = False
        reader.save()

        assert service.may_read(reader) is False

    def test_nobody_is_asked_while_the_feature_is_off(self, reader: Child, settings: Any) -> None:
        settings.READING_ENABLED = False

        assert service.may_read(reader) is False

    def test_a_child_who_cannot_see_words_is_not_asked_to_read_them(self, reader: Child) -> None:
        """`words_on_screen` is already the product's answer to "is text shown".
        A line nobody can see is not a line anybody can read."""
        reader.text_on = False
        reader.voice_on = True
        reader.accessibility = {"captions": False}
        reader.save()

        assert service.may_read(reader) is False


# ---------------------------------------------------------------------------
# The invitation
# ---------------------------------------------------------------------------
class TestTheInvitation:
    def test_it_offers_a_short_decodable_line(self, auth_client: Any, reader: Child) -> None:
        response = auth_client.get(url_for(reader))

        assert response.status_code == 200
        assert response.data["offered"] is True
        assert response.data["text"]
        assert len(response.data["text"].split()) <= service.MAX_WORDS
        assert response.data["prompt"]

    def test_a_pre_reader_gets_a_complete_answer_rather_than_an_error(
        self, auth_client: Any, reader: Child
    ) -> None:
        """`offered: false` is the correct response for most children, and it has
        to be distinguishable from a failure — a null body cannot do that."""
        reader.pre_reader = True
        reader.save()

        response = auth_client.get(url_for(reader))

        assert response.status_code == 200
        assert response.data["offered"] is False

    def test_it_prefers_a_line_they_have_not_met(self, reader: Child) -> None:
        """A child handed the same sentence every evening learns that nothing they
        do changes anything."""
        first = a_line(reader)
        for _ in range(6):
            service.record(child=reader, passage_id=first["id"], passage_text=first["text"])

        picks = {a_line(reader)["id"] for _ in range(15)}
        assert first["id"] not in picks


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------
class TestTheResponse:
    def test_the_companion_is_warm_and_says_nothing_about_quality(
        self, auth_client: Any, reader: Child, audio: Any
    ) -> None:
        """Nothing scored it, so nothing may sound like it did."""
        line = a_line(reader)

        response = auth_client.post(
            url_for(reader),
            {"passageId": line["id"], "audio": audio, "durationMs": 2400},
            format="multipart",
        )

        assert response.status_code == 200
        assert response.data["say"]
        # The shape is the guarantee: there is nowhere to put a verdict.
        assert "correct" not in response.data
        assert "score" not in response.data

    def test_a_quiet_child_is_recorded_and_not_chased(
        self, auth_client: Any, reader: Child
    ) -> None:
        """Going quiet is an answer. It is also a fact worth having — it is the
        difference between "they will not do this" and "we never asked"."""
        line = a_line(reader)

        response = auth_client.post(url_for(reader), {"passageId": line["id"]}, format="multipart")

        assert response.status_code == 200
        attempt = ReadingAttempt.objects.get(pk=response.data["attemptId"])
        assert attempt.spoke is False
        assert attempt.audio_kept is False

    def test_a_passage_we_never_offered_is_refused(
        self, auth_client: Any, reader: Child, audio: Any
    ) -> None:
        """The corpus records the text the child was looking at, and Phase 2 tunes
        a detector against that field. A client-supplied transcript would make it
        worthless."""
        response = auth_client.post(
            url_for(reader),
            {"passageId": "not-a-passage", "audio": audio},
            format="multipart",
        )

        assert response.status_code == 400
        assert ReadingAttempt.objects.count() == 0

    def test_the_stored_text_is_ours_not_the_clients(
        self, auth_client: Any, reader: Child, audio: Any
    ) -> None:
        line = a_line(reader)

        auth_client.post(
            url_for(reader),
            {"passageId": line["id"], "text": "something else entirely", "audio": audio},
            format="multipart",
        )

        assert ReadingAttempt.objects.get().passage_text == line["text"]


# ---------------------------------------------------------------------------
# The child's voice
# ---------------------------------------------------------------------------
class TestConsent:
    def test_keeping_the_recording_is_the_default(self, reader: Child) -> None:
        """A founder's decision, taken deliberately, and pinned here so it cannot
        drift back by accident in either direction.

        The reason is in `Child.reading_audio_consent`: a detector tuned against
        an adult imitating a five-year-old cannot be fixed without recordings of
        the real thing. What makes it defensible rather than merely convenient is
        that `apps.accounts.privacy` states it in the present tense, the audio
        never leaves the machine, and switching it off deletes what was kept —
        all three of which have their own tests.

        Worth revisiting before the product reaches families the founder has not
        spoken to.
        """
        assert reader.reading_audio_consent is True

    def test_without_consent_no_file_is_ever_written(
        self, auth_client: Any, reader: Child, audio: Any, settings: Any, tmp_path: Path
    ) -> None:
        """Not written and deleted — never written.

        Vidhye wrote the file and removed it after analysis, which was right when
        something had to analyse it. Nothing does yet, so the stronger shape is
        available and this is the test that keeps it.
        """
        settings.READING_CORPUS_DIR = str(tmp_path)
        # Set rather than assumed. This used to lean on the model default, which
        # made it a test of two things at once — and it broke the day the default
        # changed, for a reason that had nothing to do with what it checks.
        reader.reading_audio_consent = False
        reader.save()
        line = a_line(reader)

        auth_client.post(
            url_for(reader),
            {"passageId": line["id"], "audio": audio},
            format="multipart",
        )

        attempt = ReadingAttempt.objects.get()
        assert attempt.spoke is True
        assert attempt.audio_kept is False
        assert attempt.audio_path == ""
        assert list(tmp_path.iterdir()) == []

    def test_with_consent_the_recording_is_kept_outside_the_repo(
        self, auth_client: Any, reader: Child, audio: Any, settings: Any, tmp_path: Path
    ) -> None:
        settings.READING_CORPUS_DIR = str(tmp_path)
        reader.reading_audio_consent = True
        reader.save()
        line = a_line(reader)

        auth_client.post(
            url_for(reader),
            {"passageId": line["id"], "audio": audio},
            format="multipart",
        )

        attempt = ReadingAttempt.objects.get()
        assert attempt.audio_kept is True
        written = list(tmp_path.iterdir())
        assert len(written) == 1
        # The filename carries what a tuning run groups by, and no name.
        assert str(reader.pk) in written[0].name
        assert reader.first_name.lower() not in written[0].name.lower()

    def test_withdrawing_consent_deletes_what_was_kept(
        self, reader: Child, settings: Any, tmp_path: Path, audio: Any
    ) -> None:
        """A switch that only stops future recordings is a pause, not a
        withdrawal — and a pause is not the reading a parent has of it."""
        settings.READING_CORPUS_DIR = str(tmp_path)
        reader.reading_audio_consent = True
        reader.save()
        line = a_line(reader)
        service.record(child=reader, passage_id=line["id"], passage_text=line["text"], audio=audio)
        assert len(list(tmp_path.iterdir())) == 1

        removed = service.delete_corpus_for(reader)

        assert removed == 1
        assert list(tmp_path.iterdir()) == []
        assert ReadingAttempt.objects.get().audio_kept is False

    def test_a_corpus_that_cannot_be_written_does_not_fail_the_child(
        self, reader: Child, settings: Any, audio: Any, tmp_path: Path
    ) -> None:
        """Whose convenience matters. A research corpus failing must not be why a
        child's turn failed (§13).

        The unwritable path is a directory *under a file*, which fails the same
        way on Windows and Linux. A first draft used `/proc/nonexistent/...`,
        which POSIX refuses and Windows cheerfully creates — so the test passed
        for the wrong reason on CI and failed honestly on a laptop.
        """
        blocker = tmp_path / "not-a-directory"
        blocker.write_bytes(b"")
        settings.READING_CORPUS_DIR = str(blocker / "corpus")
        reader.reading_audio_consent = True
        reader.save()
        line = a_line(reader)

        attempt = service.record(
            child=reader, passage_id=line["id"], passage_text=line["text"], audio=audio
        )

        assert attempt.pk is not None
        assert attempt.spoke is True
        assert attempt.audio_kept is False
