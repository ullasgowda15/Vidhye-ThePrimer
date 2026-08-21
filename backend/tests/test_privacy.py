"""The privacy centre (PRODUCT.md §12.8, §13.7, §13.8).

Day 13's criteria 5 and 6:

> *`export` returns portable parent-owned data; `delete` (account + child)
> cascades **completely** (no orphans).*
>
> *The privacy disclosure is plain-language and accurate.*

"No orphans" is asserted structurally rather than by listing tables: the
declaration test walks every model in the project and fails on any FK to a child
or a parent that would survive the delete. That is the version of the check that
still works after somebody adds a model in six months without reading this file.

It also holds the Day-12 carry-over: consent gates **processing**, never a
parent's access to their own child's records. `apps.accounts.consent` argues it;
`TestConsentGatesProcessingNotAccess` is what stops it drifting back.
"""

import datetime
import io
import json
import zipfile
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection

from apps.accounts.models import ConsentRecord
from apps.companion.models import Companion
from apps.media.models import Media
from apps.media.service import get_or_generate_image
from apps.media.storage import key_for_url
from apps.memory.models import Memory, MemoryType
from apps.profiles.models import Child
from apps.story.models import Adventure, Beat, Choice, PlaySession
from tests.conftest import CHILDREN_URL, LOGIN_URL, PASSWORD

pytestmark = pytest.mark.django_db

User = get_user_model()

PRIVACY_URL = "/api/v1/privacy"
EXPORT_URL = "/api/v1/account/export"
ACCOUNT_URL = "/api/v1/account"


@pytest.fixture
def child(parent, consent) -> Child:
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs"],
        avoid_topics=["hospitals"],
        intensity_ceiling=1,
    )


@pytest.fixture
def played(child) -> Adventure:
    """One child with a real history: a companion, a sitting, a story, a memory."""
    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    session = PlaySession.objects.create(child=child, device="tablet")
    adventure = Adventure.objects.create(
        child=child,
        session=session,
        title="The Frog Chorus",
        plan={"skeleton": ["a"], "world_name": "the Whispering Woods", "place": "the Ribbon River"},
    )
    beat = Beat.objects.create(
        adventure=adventure,
        seq=1,
        narration="Pip peeks over the log. Three little frogs sit in a row!",
        scene={"setting": "a mossy riverbank"},
        interaction={
            "type": "choose",
            "prompt_audio": "Which way?",
            "choices": [{"key": "left", "label": "Go left", "icon_prompt": "a path"}],
        },
        safety_status=Beat.SafetyStatus.PASSED,
        model="test:scripted",
    )
    Choice.objects.create(adventure=adventure, beat=beat, chosen_key="left", value=3)
    Memory.objects.create(
        child=child,
        type=MemoryType.EVENT,
        content="Aarav named a frog Sparkle.",
        salience=0.9,
    )
    return adventure


PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"


class _StubImageProvider:
    """Returns the same bytes for any prompt, so the object is content-addressed
    to one key and two children asking for the same picture really do share it."""

    name = "stub"
    supports_image_reference = False
    supports_negative_prompt = False
    supports_seed = False

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, *, negative_prompt: str = "", seed: int | None = None) -> bytes:
        return PNG


def _picture(child: Child, scene: str = "scene-1") -> Media:
    """One real trip through the media pipeline: bytes scanned, stored, row written."""
    return get_or_generate_image(
        child=child,
        style_hash="style-1",
        character_hash="character-1",
        scene_hash=scene,
        prompt="a mossy riverbank",
        seed=None,
        provider=_StubImageProvider(),
    )


def _bundle(response: Any) -> dict[str, Any]:
    """The zip a response carries, as `{filename: parsed json}`."""
    archive = (
        zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        if getattr(response, "streaming", False)
        else zipfile.ZipFile(io.BytesIO(response.content))
    )
    return {name: json.loads(archive.read(name)) for name in archive.namelist()}


# ---------------------------------------------------------------------------
# Criterion 6: the disclosure (§12.8, §13.8)
# ---------------------------------------------------------------------------
class TestTheDisclosure:
    def test_it_is_readable_without_an_account(self, api) -> None:
        """A parent deciding whether to sign up has to be able to read what we do
        with their child's data *before* they have an account."""
        response = api.get(PRIVACY_URL)

        assert response.status_code == 200, response.data
        assert response.data["sections"]

    def test_it_is_versioned_so_a_parent_can_tell_what_changed(self, api) -> None:
        assert api.get(PRIVACY_URL).data["version"]

    def test_it_says_the_same_thing_to_everyone(self, api, auth, parent) -> None:
        """The security property behind making this page public.

        A public endpoint that varies by who is asking is one refactor away from
        being a leak — somebody adds "your export is ready" or a child's name to
        the disclosure and it is served to anonymous callers too. So the contract
        is that identity cannot reach this response at all: signed in and signed
        out get the same bytes, and the parent's own address appears in neither.

        Asserted rather than assumed because `PrivacyDisclosureView` sets
        `authentication_classes = []`, which is what makes it true today and is
        exactly the line a future "just add the user for convenience" would delete.
        """
        anonymous = api.get(PRIVACY_URL)
        signed_in = auth.get(PRIVACY_URL)

        assert anonymous.status_code == signed_in.status_code == 200
        assert anonymous.data == signed_in.data

        body = json.dumps(signed_in.data).lower()
        assert parent.email.lower() not in body
        assert str(parent.pk) not in body

    def test_reading_it_cannot_spend_the_budget_signup_needs(self) -> None:
        """The statement is linked from the landing footer, so visitors hit it.

        On the shared `anon` scope — 100/day keyed by IP — reading the policy would
        compete with signing up from the same address, and one school or NAT'd
        household could be transparent-with'd right out of an account. Its own
        scope, like `session_refresh` and `companion_read` before it.
        """
        from rest_framework.throttling import ScopedRateThrottle

        from apps.accounts.views import PrivacyDisclosureView

        assert PrivacyDisclosureView.throttle_classes == [ScopedRateThrottle]
        assert PrivacyDisclosureView.throttle_scope == "privacy"

        # Resolved through the throttle rather than read out of settings: that is
        # the path DRF actually takes, and a scope with no configured rate fails
        # there rather than in the dictionary.
        throttle = ScopedRateThrottle()
        throttle.scope = PrivacyDisclosureView.throttle_scope
        assert throttle.get_rate()

    def test_it_sets_no_cookie_and_needs_no_credential(self, api) -> None:
        """Nothing about reading a privacy statement should start a session.

        A `Set-Cookie` here would mean a visitor who only read the policy left
        with a session artefact, which is both a tracking surface and a claim this
        very page is making promises about.
        """
        response = api.get(PRIVACY_URL)

        assert response.status_code == 200
        assert not response.cookies

    def test_it_makes_the_three_commitments_13_8_requires(self, api) -> None:
        text = " ".join(
            section["body"] for section in api.get(PRIVACY_URL).data["sections"]
        ).lower()

        assert "never sell" in text
        assert "train" in text and "models" in text
        # §13.6's promise belongs in the privacy statement too, because it is the
        # one a parent cannot verify from the outside.
        assert "notification" in text and "streak" in text

    def test_it_states_the_rights_13_7_grants(self, api) -> None:
        text = " ".join(
            section["body"] for section in api.get(PRIVACY_URL).data["sections"]
        ).lower()

        for right in ("read everything", "export everything", "delete everything"):
            assert right in text, right

    def test_it_says_withdrawing_consent_never_blocks_access(self, api) -> None:
        # The behaviour this file's last class enforces, promised to the parent in
        # the place they would look for it. The two have to agree.
        text = " ".join(section["body"] for section in api.get(PRIVACY_URL).data["sections"])

        assert "withdrawing consent" in text.lower()

    def test_every_sentence_about_backups_is_true_of_the_code(self, api) -> None:
        """The audit's F-5, and the third disclosure over-claim found in this repo.

        The old text said *"We do not keep backups yet, so today there is no copy
        anywhere for a deletion to miss"* while a 205 KB dump of every family sat
        in the working tree, and `Docs/Runbooks/Launch.md` made the dump cron a
        launch checklist item — so the sentence was already false and was about to
        become structurally false.

        What replaces it is checkable, which is the point: the retention number in
        the prose is `settings.BACKUP_RETENTION_DAYS`, the same number
        `infra/backup/backup.sh` prunes to and `launch_check` reads.
        """
        sections = {s["id"]: s["body"] for s in api.get(PRIVACY_URL).data["sections"]}
        retention = sections["retention"]

        assert "do not keep backups" not in retention.lower()
        assert f"after {django_settings.BACKUP_RETENTION_DAYS} days" in retention
        # And the two things a parent actually needs to know about a copy.
        assert "never to restore one family" in retention
        # The commitment that is *not* yet true is still marked as a commitment
        # rather than stated as a fact.
        assert "before we take on families beyond our pilot" in retention

    def test_the_log_retention_sentence_promises_only_what_is_implemented(self, api) -> None:
        """The audit's F-6. Nothing in this product discards a log line.

        `prod.py` sends everything at INFO to stdout with no rotation, no
        retention window and no scrubbing filter, so *"raw activity logs are kept
        briefly and then discarded"* was a claim with no implementation — the exact
        sibling of the one the Day-13 review removed.

        The replacement is the honest shape: retention is the platform's setting
        and is not claimed, and what *is* claimed is the thing F-7 made true —
        there is no child content in the log to retain.
        """
        retention = {s["id"]: s["body"] for s in api.get(PRIVACY_URL).data["sections"]}["retention"]

        assert "kept briefly and then discarded" not in retention.lower()
        assert "no child's name" in retention

    def test_it_says_what_actually_leaves_the_building(self, api) -> None:
        """The audit's F-12.

        *"nothing identifying beyond that first name"* was defensible on its
        literal terms and misleading in context: every generation also carries the
        avoid list, the fears list and the retrieved memories. Naming them is a
        stronger sentence for the product, not a weaker one — it is §12.6's thesis
        applied to the disclosure itself.
        """
        who = {s["id"]: s["body"] for s in api.get(PRIVACY_URL).data["sections"]}["who_sees_it"]

        assert "asked us to avoid" in who
        assert "frightened of" in who
        assert "companion remembers" in who
        # F-3's other half: the training promise now has something enforcing it,
        # and the disclosure says so in the present tense rather than the future.
        assert "before launch we will only use providers" not in who.lower()
        assert "refuses to start" in who

    def test_it_is_written_in_sentences_rather_than_clauses(self, api) -> None:
        """ "Plain-language" is the criterion, so the cheap mechanical proxy is
        asserted: no section is one unbroken wall, and none uses the vocabulary a
        privacy policy uses when it is hiding something."""
        for section in api.get(PRIVACY_URL).data["sections"]:
            body = section["body"]
            assert len(body.split()) > 20, section["id"]
            for weasel in ("pursuant", "hereunder", "aforementioned", "third parties may"):
                assert weasel not in body.lower(), f"{section['id']}: {weasel}"


# ---------------------------------------------------------------------------
# Criterion 5a: export (§12.8)
# ---------------------------------------------------------------------------
class TestTheExport:
    def test_it_needs_an_account(self, api) -> None:
        assert api.post(EXPORT_URL).status_code == 401

    def test_it_returns_a_named_zip_attachment(self, auth, played) -> None:
        response = auth.post(EXPORT_URL)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment; filename=" in response["Content-Disposition"]
        assert ".zip" in response["Content-Disposition"]

    def test_it_serves_the_accept_header_the_real_client_sends(self, auth, played) -> None:
        """The regression, and the reason every other test here missed it.

        `client.ts` asks for `application/zip`; these tests asked for nothing, so
        DRF fell through to `JSONRenderer` and they all passed against an endpoint
        that answered **406 "Could not satisfy the request Accept header."** to the
        only caller in existence. Content negotiation happens in `APIView.initial()`
        — before the handler — so the view never ran and logged nothing.

        A parent could not download their own child's data, which is the one thing
        §12.8 exists to guarantee.
        """
        response = auth.post(EXPORT_URL, headers={"Accept": "application/zip"})

        assert response.status_code == 200, response.content[:200]
        assert response["Content-Type"] == "application/zip"
        assert zipfile.is_zipfile(io.BytesIO(response.content))

    @pytest.mark.parametrize(
        "name",
        ["../../escaped", "a/b", "a" + chr(92) + "b", "Jo:Jo", "what?", "star*", "pipe|", ".."],
    )
    def test_a_childs_name_cannot_shape_the_path_inside_the_zip(self, auth, parent, name) -> None:
        """`first_name` is free text, and it was interpolated into the zip entry
        unchanged.

        The dangerous half is `../../x`, whose entry a naive extractor writes
        *outside* the directory it was pointed at. The half that will actually
        happen is `Jo:Jo` — an ordinary nickname that produces a bundle Windows
        cannot extract at all, in the one feature that has to work on the day a
        family stops trusting us.

        All that stands between a name and this file is `validate_first_name`,
        and all it does is strip whitespace.
        """
        Child.objects.create(parent=parent, first_name=name, birthdate=datetime.date(2020, 1, 1))

        names = zipfile.ZipFile(io.BytesIO(auth.post(EXPORT_URL).content)).namelist()

        assert any(entry.startswith("children/") for entry in names)
        for entry in names:
            segments = entry.split("/")
            # The archive is exactly two levels deep — `children/<folder>/<file>`
            # — and a name carrying a separator would quietly add a third. That
            # is not dangerous the way `..` is, but it means the bundle no longer
            # has the shape the manifest describes.
            if entry.startswith("children/"):
                assert len(segments) == 3, entry
            assert ".." not in segments, entry
            assert not entry.startswith("/"), entry
            assert chr(92) not in entry, entry
            for segment in segments:
                assert not set(segment) & set(':*?"<>|'), entry
                assert segment == segment.strip(" ."), entry

    def test_two_names_that_sanitise_to_nothing_still_get_separate_folders(
        self, auth, parent
    ) -> None:
        """The id suffix is appended *after* sanitising precisely so that two
        names which reduce to the same string cannot collide and overwrite each
        other inside the archive."""
        for unusable in ("..", "."):
            Child.objects.create(
                parent=parent, first_name=unusable, birthdate=datetime.date(2020, 1, 1)
            )

        names = zipfile.ZipFile(io.BytesIO(auth.post(EXPORT_URL).content)).namelist()
        folders = {e.split("/")[1] for e in names if e.startswith("children/")}

        assert len(folders) == 2, folders
        assert all(f.startswith("child-") for f in folders), folders

    def test_an_error_under_that_header_is_still_readable_json(self, api) -> None:
        """Negotiating a zip must not make the *failures* unreadable.

        By the time a 401 is built, negotiation has already chosen the zip
        renderer for it. Without the view's `finalize_response`, the body would be
        JSON while the header claimed `application/zip` — which our own client
        survives and nothing else should have to.
        """
        response = api.post(EXPORT_URL, headers={"Accept": "application/zip"})

        assert response.status_code == 401
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["detail"]

    def test_it_carries_the_account_and_a_folder_per_child(self, auth, played, parent) -> None:
        files = _bundle(auth.post(EXPORT_URL))

        assert files["account.json"]["email"] == parent.email
        assert files["manifest.json"]["children"] == ["Aarav"]
        assert any(name.endswith("/profile.json") for name in files)

    def test_the_settings_exported_are_the_ones_being_enforced(self, auth, played) -> None:
        files = _bundle(auth.post(EXPORT_URL))
        profile = next(value for name, value in files.items() if name.endswith("/profile.json"))

        assert profile["settings"]["avoid_topics"] == ["hospitals"]
        assert profile["settings"]["intensity_ceiling"] == 1
        assert profile["settings"]["time_window"] == "any time"

    def test_the_stories_are_verbatim_not_summarised(self, auth, played) -> None:
        """§12.6's whole argument, applied to the export. A bundle carrying titles
        and word counts would be a receipt, not the data."""
        files = _bundle(auth.post(EXPORT_URL))
        adventures = next(
            value for name, value in files.items() if name.endswith("/adventures.json")
        )

        (story,) = adventures
        assert story["title"] == "The Frog Chorus"
        assert story["beats"][0]["narration"] == (
            "Pip peeks over the log. Three little frogs sit in a row!"
        )
        assert story["beats"][0]["child_answered"]["chose"] == "left"
        assert story["beats"][0]["child_answered"]["value"] == 3

    def test_it_includes_what_the_companion_remembers(self, auth, played) -> None:
        files = _bundle(auth.post(EXPORT_URL))
        memories = next(value for name, value in files.items() if name.endswith("/memories.json"))

        assert memories[0]["content"] == "Aarav named a frog Sparkle."

    def test_it_carries_no_embeddings(self, auth, played) -> None:
        # A 1536-float array per memory is a hundred megabytes that tells a parent
        # nothing. The sentence is the meaningful part and it is all here.
        files = _bundle(auth.post(EXPORT_URL))
        memories = next(value for name, value in files.items() if name.endswith("/memories.json"))

        assert "embedding" not in json.dumps(memories)

    def test_it_includes_our_own_safety_refusals(self, auth, played, child) -> None:
        from apps.safety.models import Action, SafetyIncident, Severity

        SafetyIncident.objects.create(
            child=child,
            category="avoid_topic",
            action_taken=Action.BLOCKED,
            severity=Severity.HIGH,
            detail={"markers": ["topic:hospitals"]},
        )
        files = _bundle(auth.post(EXPORT_URL))
        incidents = next(
            value for name, value in files.items() if name.endswith("/safety-incidents.json")
        )

        assert incidents[0]["category"] == "avoid_topic"
        assert incidents[0]["action_taken"] == Action.BLOCKED

    def test_the_manifest_names_any_file_it_had_to_truncate(
        self, auth, played, monkeypatch
    ) -> None:
        """An export that quietly dropped rows would be the one dishonest thing in
        a bundle built to prove honesty."""
        from apps.accounts import export as export_module

        monkeypatch.setattr(export_module, "EXPORT_ROW_CAP", 0)
        files = _bundle(auth.post(EXPORT_URL))

        assert any("adventures.json" in name for name in files["manifest.json"]["truncated"])

    def test_one_family_s_export_never_contains_another_s(
        self, api, auth, other_parent, played
    ) -> None:
        other_child = Child.objects.create(
            parent=other_parent, first_name="Mira", birthdate=datetime.date(2020, 1, 1)
        )
        Memory.objects.create(child=other_child, type=MemoryType.EVENT, content="Mira's secret.")

        blob = json.dumps(_bundle(auth.post(EXPORT_URL)))

        assert "Mira" not in blob
        assert "secret" not in blob


# ---------------------------------------------------------------------------
# Criterion 5b: delete cascades completely (§12.8, §13.7, §17.5)
# ---------------------------------------------------------------------------
def _tables_referencing(table: str) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = %s
            """,
            [table],
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


class TestDeletingAChild:
    def test_it_leaves_no_row_referencing_the_child(self, auth, played, child) -> None:
        references = _tables_referencing(Child._meta.db_table)
        assert references, "expected at least one FK to profiles_child"

        assert auth.delete(f"{CHILDREN_URL}/{child.id}").status_code == 204

        with connection.cursor() as cursor:
            for table, column in references:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', [child.id])
                assert cursor.fetchone()[0] == 0, f"{table}.{column} still references the child"

        assert not Child.objects.filter(pk=child.id).exists()

    def test_the_stories_and_beats_go_with_them(self, auth, played, child) -> None:
        auth.delete(f"{CHILDREN_URL}/{child.id}")

        assert not Adventure.objects.filter(child=child).exists()
        assert not Beat.objects.exists(), "beats hang off the adventure, which hangs off the child"
        assert not Choice.objects.exists()
        assert not Memory.objects.filter(child=child).exists()

    def test_a_lapsed_consent_does_not_block_the_delete(self, auth, played, child, consent) -> None:
        """§13.7: the parent can delete everything, always. A product that answered
        "you may not erase this" to a withdrawal would have it exactly backwards."""
        from django.utils import timezone

        consent.revoked_at = timezone.now()
        consent.save(update_fields=["revoked_at"])

        assert auth.delete(f"{CHILDREN_URL}/{child.id}").status_code == 204


class TestDeletingTheAccount:
    def test_it_needs_the_password_not_just_a_token(self, auth, played, parent) -> None:
        """An access token is what a tablet on a kitchen table has. It must not
        also be enough to erase two years of a child's stories."""
        assert auth.delete(ACCOUNT_URL, {"password": "wrong-one"}, format="json").status_code == 400
        assert User.objects.filter(pk=parent.pk).exists()

    def test_it_refuses_without_a_password_at_all(self, auth, parent) -> None:
        assert auth.delete(ACCOUNT_URL, {}, format="json").status_code == 400
        assert User.objects.filter(pk=parent.pk).exists()

    def test_it_needs_an_account(self, api) -> None:
        assert api.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json").status_code == 401

    def test_it_removes_the_account_and_every_child(self, auth, played, parent, child) -> None:
        response = auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        assert response.status_code == 204
        assert not User.objects.filter(pk=parent.pk).exists()
        assert not Child.objects.filter(pk=child.pk).exists()
        assert not Adventure.objects.exists()
        assert not Beat.objects.exists()
        assert not Memory.objects.exists()
        assert not PlaySession.objects.exists()
        assert not Companion.objects.exists()
        assert not ConsentRecord.objects.filter(parent_id=parent.pk).exists()

    def test_it_leaves_no_row_referencing_the_parent(self, auth, played, parent) -> None:
        references = _tables_referencing(User._meta.db_table)
        assert references

        auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        with connection.cursor() as cursor:
            for table, column in references:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', [parent.pk])
                assert cursor.fetchone()[0] == 0, f"{table}.{column} still references the parent"

    def test_it_clears_the_refresh_cookie(self, auth, parent) -> None:
        # Otherwise the cookie sits on the device pointing at an account that no
        # longer exists, and the client spends a page load discovering it via a 401.
        response = auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        assert response.cookies[django_settings.REFRESH_COOKIE_NAME].value == ""

    def test_another_account_is_untouched(self, auth, played, other_parent) -> None:
        survivor = Child.objects.create(
            parent=other_parent, first_name="Mira", birthdate=datetime.date(2020, 1, 1)
        )
        auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        assert User.objects.filter(pk=other_parent.pk).exists()
        assert Child.objects.filter(pk=survivor.pk).exists()

    def test_every_fk_to_a_child_or_a_parent_cascades_by_declaration(self) -> None:
        """The check that still works after somebody adds a model in six months.

        The runtime tests above only cover rows this file remembered to create;
        this one covers the *declaration*, so a future `on_delete=PROTECT` fails
        here even with no fixture written for it (§17.5).

        `SET_NULL` is allowed only where the row genuinely outlives its owner and
        carries no family data once the FK is nulled. Two qualify:

        * `SafetyIncident.reviewed_by` — a staff reviewer. The incident must
          survive that person leaving the company.
        * `OutstandingToken.user` — SimpleJWT's blacklist. The row is a token
          hash, and a token that was blacklisted must *stay* blacklisted after the
          account goes; nulling the FK leaves nothing identifying behind, which
          `test_it_leaves_no_row_referencing_the_parent` proves separately.
        """
        allowed_set_null = {"SafetyIncident.reviewed_by", "OutstandingToken.user"}
        offenders = []

        for model in django_apps.get_models():
            for field in model._meta.fields:
                if field.related_model not in (Child, User) or field.remote_field is None:
                    continue
                name = f"{model.__name__}.{field.name}"
                if field.remote_field.on_delete.__name__ != "CASCADE" and name not in (
                    allowed_set_null
                ):
                    offenders.append(name)

        assert offenders == []


class TestWhatTheLogIsAllowedToKnow:
    """§13.8's data minimisation, applied to the one store no delete reaches.

    The audit's F-7. The database honoured deletion perfectly — a full-schema
    survivor walk found zero rows — and the application log did not: it kept a
    child-uuid-to-interests mapping and a parent's email address indefinitely, in
    a stream with no retention, no rotation and no delete path. A disclosure that
    says identifying activity is discarded, over a log that retains it forever.
    """

    def test_starting_an_adventure_never_writes_what_a_child_loves(
        self, child, caplog, settings
    ) -> None:
        """The two lines that used to read `interests=zzquuxmarkerbinterest`.

        A distinctive marker rather than "frogs", for the reason the audit used
        one: an ordinary word would appear in the log for a dozen innocent
        reasons and the assertion would be measuring nothing.
        """
        import logging

        from apps.story import orchestrator

        child.interests = ["zzquuxmarkerinterest", "zzquuxmarkerbinterest"]
        child.save(update_fields=["interests"])

        with caplog.at_level(logging.DEBUG):
            orchestrator.personalize(child)

        assert "zzquuxmarker" not in caplog.text, (
            "a child's interests reached the application log; the log is the one "
            "store no deletion reaches (13.8)"
        )
        # And the operational signal is still there — this is not a fix that made
        # the log useless, which is the way this regresses.
        assert "story.personalized" in caplog.text
        assert "interests=2" in caplog.text

    def test_deleting_the_account_writes_a_receipt_and_not_an_address(
        self, auth, played, parent, caplog
    ) -> None:
        """The accountability need, kept; the retained address, gone.

        The old log line carried `email=family-b@example.com` and was argued for
        in a comment — a deletion is the one event we may need to prove happened,
        and every row that could prove it is gone by definition. That argument is
        right, which is why this is a receipt rather than a deletion.
        """
        import logging

        from apps.accounts.models import DeletionReceipt

        email = parent.email

        with caplog.at_level(logging.DEBUG):
            response = auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        assert response.status_code == 204
        assert not User.objects.filter(pk=parent.pk).exists()

        assert email not in caplog.text, "the parent's address is still in the log stream"
        assert "privacy.account_deleted" in caplog.text

        receipt = DeletionReceipt.objects.get()
        assert receipt.email_fingerprint == DeletionReceipt.fingerprint(email)
        assert email not in receipt.email_fingerprint
        assert receipt.children == 1

    def test_a_receipt_survives_the_cascade_it_records(self, auth, played, parent) -> None:
        """Otherwise it would be deleted by the thing it exists to record.

        `test_every_fk_to_a_child_or_a_parent_cascades_by_declaration` requires
        every FK to a parent to cascade; the receipt holds a plain `UUIDField` and
        no FK precisely so that rule and this one do not collide. That is a
        deliberate exception to "nothing referencing the parent survives", and it
        is asserted here so a future audit reads it as intent rather than as a
        regression: what survives is an opaque id pointing at nothing, a
        non-reversible hash and three counts.
        """
        from apps.accounts.models import DeletionReceipt

        auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json")

        receipt = DeletionReceipt.objects.get()
        assert receipt.parent_id_at_deletion == parent.pk
        assert not User.objects.filter(pk=receipt.parent_id_at_deletion).exists()
        # Nothing on the row is a name, an address, or anything a child said.
        stored = {
            field.name: getattr(receipt, field.name) for field in DeletionReceipt._meta.fields
        }
        assert set(stored) == {
            "id",
            "parent_id_at_deletion",
            "email_fingerprint",
            "children",
            "media_rows",
            "media_objects",
            "deleted_at",
        }


class TestDeletingLeavesNothingInStorage:
    """Criterion 5's other half: a child's data is not only rows (§13.8, §17.5).

    The structural test above walks `information_schema` and proves no row still
    references the child. That is necessary and it is not sufficient — a picture is
    an object in a bucket, and a bucket has no foreign keys. Day 13 shipped with
    both deletes cascading the `Media` rows and removing nothing from storage, and
    every test passed, because none of them looked in the bucket.

    Worse than a leak that could be swept later: `service.purge_objects` decides
    what is safe to remove by asking which URLs no surviving row wants, so once the
    rows cascade there is nothing left that knows the keys exist. The objects
    become unreachable rather than merely orphaned. Hence storage first, then the
    cascade — and hence a test that reads the filesystem.
    """

    def test_deleting_a_child_removes_the_objects_behind_their_pictures(
        self, auth, child, offline_media
    ) -> None:
        media = _picture(child)
        stored = offline_media / key_for_url(media.url)
        assert stored.exists(), "setup: the object should be in storage"

        assert auth.delete(f"{CHILDREN_URL}/{child.id}").status_code == 204

        assert not Media.objects.filter(child=child).exists()
        assert not stored.exists(), "the picture is still in the bucket"

    def test_deleting_the_account_removes_them_for_every_child(
        self, auth, child, offline_media
    ) -> None:
        sibling = Child.objects.create(
            parent=child.parent, first_name="Mira", birthdate=child.birthdate
        )
        mine = _picture(child, scene="scene-1")
        theirs = _picture(sibling, scene="scene-2")

        assert auth.delete(ACCOUNT_URL, {"password": PASSWORD}, format="json").status_code == 204

        assert not Media.objects.exists()
        assert not (offline_media / key_for_url(mine.url)).exists()
        assert not (offline_media / key_for_url(theirs.url)).exists()

    def test_a_sibling_keeps_the_picture_they_share(self, auth, child, offline_media) -> None:
        """The property `delete_child_media` already had, which must not regress.

        Objects are content-addressed and child-agnostic, so two children can
        legitimately point at one file. Deleting one child must take their *row*
        and leave the object, or the delete would blank the sibling's storybook —
        which is why this is a per-row purge and not a per-prefix one.
        """
        sibling = Child.objects.create(
            parent=child.parent, first_name="Mira", birthdate=child.birthdate
        )
        mine = _picture(child)
        theirs = _picture(sibling)
        assert mine.url == theirs.url, "setup: one object, two rows"

        assert auth.delete(f"{CHILDREN_URL}/{child.id}").status_code == 204

        assert Media.objects.filter(pk=theirs.pk).exists()
        # The URL the sibling's device would fetch still resolves to real bytes.
        assert (offline_media / key_for_url(theirs.url)).exists()


# ---------------------------------------------------------------------------
# The Day-12 carry-over: consent gates processing, never access (§13.7)
# ---------------------------------------------------------------------------
class TestConsentGatesProcessingNotAccess:
    """Day 12 flagged that a lapsed-consent parent could not reach their own
    child's transparency log. §13.7 grants the right to read, export and delete at
    all times; consent gates what we may *do* with a child's data.

    Both halves are asserted, because the fix is only correct if the gate is still
    closed on the processing side.
    """

    @pytest.fixture
    def lapsed(self, consent) -> ConsentRecord:
        from django.utils import timezone

        consent.revoked_at = timezone.now()
        consent.save(update_fields=["revoked_at"])
        return consent

    def test_a_lapsed_parent_can_still_read_the_transparency_log(
        self, auth, played, child, lapsed
    ) -> None:
        assert auth.get(f"/api/v1/adventures/{played.id}/transcript").status_code == 200
        assert auth.get(f"/api/v1/children/{child.id}/transcripts").status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "reports/sessions",
            "reports/weekly",
            "progress",
            "insights",
            "memories",
            "transcripts",
        ],
    )
    def test_every_dashboard_read_survives_a_lapsed_consent(
        self, auth, played, child, lapsed, path
    ) -> None:
        assert auth.get(f"/api/v1/children/{child.id}/{path}").status_code == 200

    def test_a_lapsed_parent_can_still_export(self, auth, played, lapsed) -> None:
        assert auth.post(EXPORT_URL).status_code == 200

    def test_a_lapsed_parent_can_still_read_and_tighten_settings(self, auth, child, lapsed) -> None:
        # Every field on this endpoint restricts. A parent reaching a screen where
        # the only thing they can do is protect their child further is exactly the
        # right outcome for a withdrawn consent.
        assert auth.get(f"/api/v1/children/{child.id}/settings").status_code == 200
        response = auth.patch(
            f"/api/v1/children/{child.id}/settings", {"intensity_ceiling": 0}, format="json"
        )
        assert response.status_code == 200

    def test_but_no_new_child_may_be_created(self, auth, lapsed, child_payload) -> None:
        assert auth.post(CHILDREN_URL, child_payload, format="json").status_code == 403

    def test_and_no_story_may_be_generated(self, auth, child, lapsed) -> None:
        Companion.objects.create(child=child, archetype="dragon", name="Pip")
        response = auth.post(f"/api/v1/children/{child.id}/adventures", {}, format="json")

        assert response.status_code == 403
        assert response.data["detail"]

    def test_the_scoping_is_unchanged_by_any_of_this(
        self, api, other_parent, played, child
    ) -> None:
        """The one thing that must not have been relaxed along with the gate."""
        token = api.post(
            LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        assert api.get(f"/api/v1/children/{child.id}/transcripts").status_code == 403
        assert api.get(f"/api/v1/adventures/{played.id}/transcript").status_code == 403
        assert api.get(f"/api/v1/children/{child.id}/memories").status_code == 403
