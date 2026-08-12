"""The consent gate (PRODUCT.md §13.7, §12.8).

Everything that collects or processes child data must pass through here first.
It lives in one module — rather than as a check inlined at each call site — so
that "which operations require consent?" is answerable by grepping for one
name, and so the rule can never be half-enforced.

**What consent gates, and what it must not (Day 13's decision).**

§13.7 states two things about a parent, and they pull in opposite directions if
one function is used for both:

> *Verifiable parental consent before any child data is collected/processed…*
> *The parent can read everything (transparency log), export everything, and
> delete everything.*

Through Day 12 a single `require_consent` guarded both, which meant a parent whose
consent had lapsed could not open their own child's transparency log — could not
read what had already been generated, could not export it, and reached a 403 on
the very screens §12.8 says are where privacy rights are exercised. That is
backwards in the way that matters: **withdrawing consent has to stop us using a
child's data, not stop a parent seeing it.** A product that answered "you may no
longer look at your own child's records" to a withdrawal would be turning a
privacy control into a hostage situation, and would fail the GDPR/CCPA access and
erasure rights §13.7 designs to on the same day it satisfied COPPA.

So the gate is split by *what the operation does to the child's data*:

`require_consent` — **processing.** Anything that creates, generates, sends to a
provider, or otherwise makes new child data: creating a child, creating a
companion, generating a greeting, starting or advancing an adventure. No consent,
no processing. This is the COPPA floor and it has not moved.

`require_ownership_only` — nothing, and it is documented here rather than
implemented as a function, because the honest implementation of "consent is not
required" is *not calling this module*. The parent-owned reads (`apps.analytics`
views), the settings write, the export and the deletes are guarded by
`IsParentOwner` alone. Each of those either shows a parent data they already own,
lets them *restrict* what happens to it, or deletes it. None of them creates any.

The one thing this split must never become is a loophole. Two invariants hold it:

* **Ownership is still checked, always, first.** Nothing became public; the
  scoping tests in `tests/test_parent_dashboard.py` assert a second family gets
  403 on every endpoint with and without consent.
* **Nothing on the un-gated side writes child data.** The settings write is the
  edge case and it survives inspection: every field it can change is a
  *restriction* (a lower ceiling, a shorter cap, a narrower window, another
  avoid-topic) or a presentation flag, and none of it produces a new row about
  the child.
"""

from django.conf import settings
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import ConsentRecord, User


def active_consent(parent: User, consent_type: str | None = None) -> ConsentRecord | None:
    """The parent's current, un-revoked grant of `consent_type`, if any."""
    consent_type = consent_type or settings.REQUIRED_CONSENT_TYPE
    return (
        ConsentRecord.objects.filter(
            parent=parent,
            consent_type=consent_type,
            revoked_at__isnull=True,
        )
        .order_by("-granted_at")
        .first()
    )


def has_consent(parent: User, consent_type: str | None = None) -> bool:
    return active_consent(parent, consent_type) is not None


def require_consent(parent: User, consent_type: str | None = None) -> ConsentRecord:
    """Return the active grant, or refuse the operation.

    Call this from anything that **processes** child data — see the module
    docstring for what that means and for what deliberately does not call it.

    Raises `PermissionDenied` (403) rather than a validation error because this
    is not a malformed request — it is a well-formed request the parent is not
    yet permitted to make.
    """
    consent_type = consent_type or settings.REQUIRED_CONSENT_TYPE
    record = active_consent(parent, consent_type)
    if record is None:
        raise PermissionDenied(
            detail=(
                f"Parental consent '{consent_type}' is required before a child "
                "profile can be created or a story generated. "
                "Record it via POST /api/v1/consent. "
                "Reading, exporting and deleting your own data never require it."
            ),
            code="consent_required",
        )
    return record
