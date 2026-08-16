"""Time-based one-time passwords, RFC 6238, in the standard library.

The second factor `/admax` asks for. Thirty lines of `hmac` rather than a
dependency, for three reasons that all point the same way:

* it is a **closed, frozen spec** — RFC 6238 has not changed since 2011, so the
  usual argument for a library (they track the upstream) buys nothing here;
* it is on the **login path of the most sensitive page in the product**, and a
  transitive dependency on that path is a supply-chain question we would have to
  keep answering;
* the image is built from `requirements.txt` with no compiler, and this needs no
  wheel, no C extension and no `collectstatic`.

Interoperable with Google Authenticator, 1Password, Authy and every other app
that reads an `otpauth://` URI: SHA-1, six digits, a thirty-second step. Those
three are the defaults precisely because they are what authenticator apps
assume, and a "stronger" choice here would mean a code that no app can generate.

Nothing in this module touches a database or a request. It is pure arithmetic on
a secret and a clock, which is what makes it testable against the RFC's own
vectors (`tests/test_admax.py::TestTheRFCVectors`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

#: Seconds per step. Thirty is the RFC default and what every authenticator app
#: assumes; it is not configurable for that reason.
STEP_SECONDS = 30

#: Digits in a code. Six, for the same reason.
DIGITS = 6

#: How many steps either side of "now" are accepted. One, i.e. ±30 seconds.
#:
#: The trade is explicit: zero drift rejects anyone whose phone clock is a few
#: seconds slow, which is common enough to be a support burden; two would widen
#: the window a stolen code stays live in to two and a half minutes. One is the
#: usual answer, and the replay guard on `AdmaxCredential.last_step` is what stops
#: the widened window from being a reuse window.
DRIFT_STEPS = 1

#: Bytes of entropy in a new seed. Twenty is the RFC 4226 recommendation and the
#: SHA-1 block-adjacent size every app expects; it base32-encodes to 32
#: characters with no padding.
SECRET_BYTES = 20


def new_secret() -> str:
    """A fresh base32 seed, from `secrets` rather than `random`.

    Unpadded, because the `=` a padded base32 string ends with is exactly the
    character that gets lost when a seed is copied through a terminal, a chat
    window and a phone keyboard.
    """
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    """Decode a base32 seed, tolerating how humans transcribe it.

    Uppercased, spaces stripped, padding restored. Authenticator apps display
    seeds in space-separated groups of four and users paste them back that way;
    rejecting that is a support ticket, not a security control.
    """
    cleaned = secret.strip().replace(" ", "").replace("-", "").upper()
    # b32decode demands the string be a multiple of eight characters.
    padding = (-len(cleaned)) % 8
    return base64.b32decode(cleaned + "=" * padding, casefold=True)


def step_at(moment: float | None = None) -> int:
    """The TOTP step number a moment falls in."""
    return int((moment if moment is not None else time.time()) // STEP_SECONDS)


def code_for(secret: str, step: int) -> str:
    """The six-digit code for one step. HOTP (RFC 4226) over the step counter."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    # Dynamic truncation, RFC 4226 §5.3: the low nibble of the last byte picks
    # where the four-byte window starts.
    offset = digest[-1] & 0x0F
    (truncated,) = struct.unpack(">I", digest[offset : offset + 4])
    return str((truncated & 0x7FFFFFFF) % (10**DIGITS)).zfill(DIGITS)


def verify(
    secret: str, code: str, *, moment: float | None = None, after: int | None = None
) -> int | None:
    """Check a code. Returns the step it matched, or `None`.

    **The step is returned rather than a bool** so the caller can write it down:
    a code that verified is valid for its whole 30-second window and for the
    drift either side of it, so "was this correct" is not the same question as
    "may this be used", and only the caller knows the answer to the second.
    `after` is that answer — a step at or below it is a replay and is refused
    even though the arithmetic checks out.

    Comparison is `compare_digest`, not `==`. The timing signal from comparing
    six ASCII digits is small, and it is not zero, and this is a login.
    """
    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return None

    now = step_at(moment)
    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        step = now + offset
        if after is not None and step <= after:
            continue
        if hmac.compare_digest(code_for(secret, step), cleaned):
            return step
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "The Primer") -> str:
    """The `otpauth://` URI an authenticator app scans or accepts as text.

    Printed by `manage.py admax_enroll`. The parameters are stated explicitly
    rather than left to default, because an app that assumes SHA-256 when we mean
    SHA-1 produces codes that never match and gives no hint why.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
