"""Child memory is encrypted at rest (PRODUCT.md §11.10, §13.8).

The claim being tested is narrow and worth stating precisely: **the bytes in the
`memory.content` column are ciphertext.** Not "the volume is encrypted", which is
the deployment's business and is transparently undone by a restored snapshot or a
`pg_dump`, but the column itself — so a leaked database says how many memories a
child has and not what they were.

The load-bearing test is `test_the_column_holds_ciphertext`, which reads the column
with raw SQL and therefore bypasses the field that would decrypt it. Every other
test in this file is about not breaking the product while doing that.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.db import connection

from apps.common import crypto
from apps.memory.models import Memory, MemoryType

pytestmark = pytest.mark.django_db

SECRET = "Aarav named the little dragon Sparkle."


def write(child, content: str = SECRET) -> Memory:
    return Memory.objects.create(child=child, type=MemoryType.ENTITY, content=content, salience=0.8)


def raw_content(memory: Memory) -> str:
    """The column as Postgres holds it, with the ORM's decryption bypassed."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT content FROM memory_memory WHERE id = %s", [str(memory.pk)])
        return cursor.fetchone()[0]


def test_the_column_holds_ciphertext(aarav) -> None:
    memory = write(aarav)
    stored = raw_content(memory)

    assert SECRET not in stored
    assert "Sparkle" not in stored
    assert "Aarav" not in stored
    # A Fernet token, so it is authenticated rather than merely scrambled.
    assert stored.startswith("gAAAAA")


def test_the_orm_round_trips_it_transparently(aarav) -> None:
    """Everything above the field — retrieval, the admin, the parent view — sees text."""
    memory = write(aarav)

    assert Memory.objects.get(pk=memory.pk).content == SECRET
    assert list(Memory.objects.values_list("content", flat=True)) == [SECRET]


def test_two_identical_memories_encrypt_differently(aarav) -> None:
    """A random IV per row, which is why the column cannot be searched by value."""
    assert raw_content(write(aarav)) != raw_content(write(aarav))


def test_an_empty_memory_is_not_a_hundred_byte_token(aarav) -> None:
    memory = Memory.objects.create(child=aarav, type=MemoryType.EVENT, content="")

    assert raw_content(memory) == ""
    assert Memory.objects.get(pk=memory.pk).content == ""


def test_a_row_written_under_another_key_reads_as_unreadable(aarav, settings) -> None:
    """A rotated key must not 500 the page a parent is looking at.

    `UNREADABLE` rather than an exception, and rather than an empty string: "we hold
    this and cannot read it" is a diagnosable state, and it is a different fact from
    "this memory is blank".
    """
    memory = write(aarav)
    settings.MEMORY_ENCRYPTION_KEY = Fernet.generate_key().decode()

    assert Memory.objects.get(pk=memory.pk).content == crypto.UNREADABLE


def test_a_hand_inserted_plaintext_row_still_reads(aarav) -> None:
    """The obvious way to try something out in psql must not break the admin."""
    memory = write(aarav)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memory SET content = %s WHERE id = %s",
            ["typed in by hand", str(memory.pk)],
        )

    assert Memory.objects.get(pk=memory.pk).content == "typed in by hand"


def test_the_key_is_derived_from_the_secret_when_none_is_configured(settings) -> None:
    """Zero-setup development; `prod.py` refuses to boot on this path."""
    settings.MEMORY_ENCRYPTION_KEY = ""
    settings.SECRET_KEY = "a-development-secret"

    token = crypto.encrypt(SECRET)
    assert crypto.decrypt(token) == SECRET

    # And it is genuinely derived from the secret, not a constant: a different
    # SECRET_KEY cannot read it. This is precisely why production must set its own
    # key — rotating SECRET_KEY would otherwise orphan every memory.
    settings.SECRET_KEY = "a-different-development-secret"
    assert crypto.decrypt(token) == crypto.UNREADABLE


def test_a_malformed_configured_key_is_a_loud_error(settings) -> None:
    settings.MEMORY_ENCRYPTION_KEY = "not-a-fernet-key"

    with pytest.raises(crypto.EncryptionKeyMissing, match="Fernet"):
        crypto.encrypt(SECRET)


def test_nothing_logs_the_memory_itself(aarav, caplog, settings) -> None:
    """§13.8: never plaintext-logged. `__str__` is the leak that would be invisible."""
    memory = write(aarav)

    assert SECRET not in str(memory)
    assert SECRET not in repr(memory)

    settings.MEMORY_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with caplog.at_level("ERROR"):
        unreadable = Memory.objects.get(pk=memory.pk).content

    assert unreadable == crypto.UNREADABLE
    assert caplog.records, "an undecryptable row should be reported"
    assert all(SECRET not in record.getMessage() for record in caplog.records)
