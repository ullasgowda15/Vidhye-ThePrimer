"""Rolling a prompt version back (PRODUCT.md §19.2, §16.10, §19.7, Day 15).

`PromptVersion` has held every system prompt we have ever shipped since Day 1,
with a partial unique index guaranteeing exactly one live row per layer. What Day
15 adds is the *going back*, and the reason it needed a command rather than two
admin clicks is that a rollback is two writes which must not be separately
visible: between them, `active_template` raises and a child's beat would be
assembled with no identity layer at all.

So what is asserted here is not "the row flipped". It is that the **assembled
prompt** a child's story is generated from changed, that it changed back, and
that at no point was there a window with no live version.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.safety.models import SYSTEM_IDENTITY, PromptVersion
from apps.safety.prompting import assembler

pytestmark = pytest.mark.django_db


@pytest.fixture
def shipped() -> PromptVersion:
    """The version the seed migration installs — what production is running."""
    return PromptVersion.objects.get(name=SYSTEM_IDENTITY, active=True)


@pytest.fixture
def a_new_version(tmp_path: Any, shipped: PromptVersion) -> str:
    """A change to the identity layer, published and live. Stands in for the kind
    of prompt edit §19.7's gate is about."""
    path = tmp_path / "identity.txt"
    path.write_text(
        shipped.template + "\n\nAlways mention the weather in the first sentence.",
        encoding="utf-8",
    )
    call_command("prompts", "publish", SYSTEM_IDENTITY, "9.9.9", "--file", str(path), verbosity=0)
    return "9.9.9"


class TestARollback:
    def test_the_assembled_prompt_changes_and_changes_back(
        self, shipped: PromptVersion, a_new_version: str
    ) -> None:
        """The property that matters: what reaches the model, not what is in a row."""
        marker = "Always mention the weather"

        assert marker in assembler.system_identity_layer()

        call_command("prompts", "rollback", SYSTEM_IDENTITY, shipped.version, verbosity=0)

        live = assembler.system_identity_layer()
        assert marker not in live
        assert live == shipped.template

    def test_there_is_never_a_moment_with_no_live_version(
        self, shipped: PromptVersion, a_new_version: str
    ) -> None:
        """A generation with no identity layer is a generation with no rules
        attached to it, which is the thing `PromptVersionUnavailable` exists to
        prevent — so the two writes are one transaction, and the database's own
        partial unique index means there is never more than one either."""
        call_command("prompts", "rollback", SYSTEM_IDENTITY, shipped.version, verbosity=0)

        live = PromptVersion.objects.filter(name=SYSTEM_IDENTITY, active=True)
        assert live.count() == 1
        assert live.get().version == shipped.version

    def test_the_rolled_back_version_is_still_there_to_roll_forward_to(
        self, shipped: PromptVersion, a_new_version: str
    ) -> None:
        """A rollback selects; it never deletes. Both versions are records of what
        a child was actually told."""
        call_command("prompts", "rollback", SYSTEM_IDENTITY, shipped.version, verbosity=0)
        call_command("prompts", "rollback", SYSTEM_IDENTITY, a_new_version, verbosity=0)

        assert "Always mention the weather" in assembler.system_identity_layer()

    def test_a_rollback_cannot_invent_a_version(self, shipped: PromptVersion) -> None:
        with pytest.raises(CommandError, match="No system_identity v0.0.1"):
            call_command("prompts", "rollback", SYSTEM_IDENTITY, "0.0.1", verbosity=0)


class TestPublishing:
    def test_a_shipped_version_can_never_be_edited(
        self, shipped: PromptVersion, tmp_path: Any
    ) -> None:
        """A published version is a record of what a child was actually told.

        Without this, a "rollback to 1.0.0" could quietly land on text that is not
        the 1.0.0 anybody reviewed — which is worse than no rollback at all,
        because it looks like one.
        """
        path = tmp_path / "identity.txt"
        path.write_text("something else entirely", encoding="utf-8")

        with pytest.raises(CommandError, match="already exists"):
            call_command(
                "prompts",
                "publish",
                SYSTEM_IDENTITY,
                shipped.version,
                "--file",
                str(path),
                verbosity=0,
            )
        shipped.refresh_from_db()
        assert "something else entirely" not in shipped.template

    def test_an_empty_template_is_refused(self, tmp_path: Any) -> None:
        path = tmp_path / "empty.txt"
        path.write_text("   \n", encoding="utf-8")
        with pytest.raises(CommandError, match="is not a layer"):
            call_command(
                "prompts", "publish", SYSTEM_IDENTITY, "2.0.0", "--file", str(path), verbosity=0
            )

    def test_stage_publishes_without_going_live(
        self, shipped: PromptVersion, tmp_path: Any
    ) -> None:
        """§19.7's "ship behind a flag": a new prompt can exist, be reviewed and be
        graded by the eval suite before any child meets it."""
        path = tmp_path / "identity.txt"
        path.write_text("A staged identity layer, not yet live.", encoding="utf-8")
        call_command(
            "prompts",
            "publish",
            SYSTEM_IDENTITY,
            "3.0.0",
            "--file",
            str(path),
            "--stage",
            verbosity=0,
        )

        assert PromptVersion.objects.filter(name=SYSTEM_IDENTITY, version="3.0.0").exists()
        assert assembler.system_identity_layer() == shipped.template
