"""Email verification — stubbed until a real provider lands.

Day 2 deliberately keeps the *token* real and only the *delivery* fake: the
link is generated with Django's signed token generator and logged, so when an
email provider is wired in, the only thing that changes is the transport. A
stub that skipped token generation would be a stub that hid the hard part.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import User

logger = logging.getLogger(__name__)


def verification_link(user: User) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_BASE_URL}/parent/verify-email?uid={uid}&token={token}"


def send_verification_email(user: User) -> str:
    """Log the verification link and return it.

    Returns the link so tests can assert on it without parsing log output.
    """
    link = verification_link(user)
    logger.info("Email verification link for %s: %s", user.email, link)
    return link
