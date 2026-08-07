"""The consent gate (PRODUCT.md §13.7).

Everything that collects or processes child data must pass through here first.
It lives in one module — rather than as a check inlined at each call site — so
that "which operations require consent?" is answerable by grepping for one
name, and so the rule can never be half-enforced.
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
                "profile can be created. Record it via POST /api/v1/consent."
            ),
            code="consent_required",
        )
    return record
