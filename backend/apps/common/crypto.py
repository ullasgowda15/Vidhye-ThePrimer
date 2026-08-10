"""Application-level encryption for the most sensitive column we hold
(PRODUCT.md §13.8, §11.10).

§11.10 is unusually blunt about `memory.content`: *"Child memory is the most
sensitive data we hold. It is minimized, access-controlled, encrypted at rest…"*
This module is what makes the last clause true of the bytes rather than only of
the deployment.

**Why application-level and not just disk encryption.** A managed Postgres gives
you encrypted volumes, and that is worth having — it covers a stolen disk. It
does not cover the failure modes that actually happen to a small product: a
snapshot restored into a staging environment, a `pg_dump` in someone's downloads
folder, a read-replica connection string in a leaked `.env`, a support engineer
with `SELECT`. In every one of those the volume encryption is transparently
already undone. `EncryptedTextField` means the row is ciphertext everywhere
except in a process holding the key, so the blast radius of a leaked database is
"we know a child has 14 memories" rather than "here is what they said".

**Fernet, not something bespoke.** AES-128-CBC with an HMAC-SHA256 over the
ciphertext, a version byte and a timestamp, from `cryptography`. Authenticated,
so a tampered row fails to decrypt rather than decrypting to something else. The
alternative — writing a mode ourselves — is the classic way to end up with
unauthenticated encryption and a false sense of security.

**The key.** `MEMORY_ENCRYPTION_KEY` in the environment, or, when that is unset,
one *derived* from `SECRET_KEY`. Derivation is what keeps development
zero-setup — a fresh clone can consolidate a session without generating a key
first — and `prod.py` refuses to boot without a real one, for the same reason it
refuses to boot with the safety classifier off. A derived key is not a weaker
key; it is a key with the wrong *lifecycle*, because rotating `SECRET_KEY` (a
routine thing to do after a leak) would silently orphan every memory ever
written. That is why production must set its own.

**Decryption never raises at a call site.** A row we cannot read — written under
a key that has since been rotated away — must not break a parent's dashboard or
a retrieval bundle mid-story. It reads as `UNREADABLE` and logs an error, which
is a visible, diagnosable state rather than a 500 on a page about a child's
memories.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

#: What a row we hold but cannot decrypt reads as. Deliberately not an empty
#: string: "this memory is unreadable" and "this memory is blank" are different
#: facts, and only one of them means someone should go and look at the key.
UNREADABLE = "[unreadable: encrypted with a different key]"

#: The info string in the key derivation. Present so that a second encrypted
#: field, derived from the same `SECRET_KEY`, gets a *different* key — one
#: compromised context must not decrypt the other.
_DERIVATION_INFO = b"primer.memory.content.v1"


class EncryptionKeyMissing(Exception):
    """No usable key at all. Only reachable with both settings blank."""


@lru_cache(maxsize=8)
def _fernet_for(key_material: str, secret: str) -> Fernet:
    """The cipher for one (configured key, secret) pair.

    Cached because `Fernet(...)` parses and splits the key on every construction
    and this runs once per memory row read. Keyed on the *inputs* rather than
    stored in a module global so a test that overrides either setting gets a
    cipher built from what it set, which a global would not give it.
    """
    if key_material:
        try:
            return Fernet(key_material.encode() if isinstance(key_material, str) else key_material)
        except (ValueError, TypeError) as exc:
            raise EncryptionKeyMissing(
                "MEMORY_ENCRYPTION_KEY is not a valid Fernet key. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc

    if not secret:
        raise EncryptionKeyMissing(
            "Neither MEMORY_ENCRYPTION_KEY nor SECRET_KEY is set, so child memory "
            "cannot be encrypted (§11.10). Set MEMORY_ENCRYPTION_KEY."
        )
    # HKDF would be the textbook answer; a single SHA-256 over a domain-separated
    # secret is equivalent here because the input is already a high-entropy key
    # rather than a password, and it needs no salt management. It is the
    # *development* path in any case — production sets its own key.
    digest = hashlib.sha256(_DERIVATION_INFO + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def cipher() -> Fernet:
    """The configured cipher for child memory content."""
    return _fernet_for(
        str(getattr(settings, "MEMORY_ENCRYPTION_KEY", "") or ""),
        str(getattr(settings, "SECRET_KEY", "") or ""),
    )


def encrypt(plaintext: str) -> str:
    """`plaintext` as a Fernet token, safe to store in a text column."""
    return cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """The plaintext behind a Fernet token, or `UNREADABLE`.

    Tolerant of a value that was never encrypted. Nothing in the shipped schema
    writes plaintext here, but a row inserted by hand in a psql session — the
    obvious way to try something out — would otherwise make the admin page for a
    child's memories fail rather than show what somebody just typed.
    """
    try:
        return cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        if not _looks_like_token(token):
            return token
        # No content in the log line, by design (§13.8): the whole point of this
        # module is that this string does not get copied anywhere.
        logger.error("crypto.undecryptable_value len=%d", len(token))
        return UNREADABLE
    except EncryptionKeyMissing:
        logger.error("crypto.no_key — MEMORY_ENCRYPTION_KEY and SECRET_KEY are both unset")
        return UNREADABLE


def _looks_like_token(value: str) -> bool:
    """Was this ever meant to be ciphertext? Fernet tokens start with `gAAAAA`."""
    return value.startswith("gAAAAA")


class EncryptedTextField(models.TextField):
    """A `TextField` whose value is ciphertext in the database.

    A `TextField` subclass on purpose, so §17.3's `content (text)` is still what
    the schema says it is and the model definition still reads as
    `models.TextField()` does. Everything above the ORM — serializers, the admin,
    the consolidation job, Day 9's retrieval — sees plain `str`.

    What this costs, stated plainly rather than discovered later: **the column is
    not searchable or orderable in SQL.** `filter(content__icontains=…)` cannot
    work, because equal plaintexts encrypt to different tokens (Fernet has a
    random IV). That is fine for this table specifically — memory is retrieved by
    vector similarity over `embedding` plus rules over `salience`/`type`/recency
    (§11.9), never by substring — and it is exactly the property that makes a
    leaked dump useless. It would be the wrong field for anything you look up by
    value.
    """

    def get_prep_value(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        # An empty memory is not worth a 100-byte token, and `""` leaks nothing.
        return encrypt(text) if text else ""

    def from_db_value(self, value: object, expression: object, connection: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        return decrypt(text) if text else ""
