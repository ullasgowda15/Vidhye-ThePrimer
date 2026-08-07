"""The parent account and its consent records (PRODUCT.md §17.2).

The buyer and the trust-gate is the parent, not the child — children never have
accounts of their own. Email is the login identifier because there is no
username anywhere in the product surface, and asking a parent to invent one is
friction with no payoff.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel


class UserManager(BaseUserManager["User"]):
    """Manager keyed on email rather than username."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> "User":
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, UUIDModel):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=120, blank=True)
    locale = models.CharField(max_length=16, default="en-US")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = "parent"
        verbose_name_plural = "parents"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email

    def get_short_name(self) -> str:
        return self.display_name or self.email.split("@")[0]

    def get_full_name(self) -> str:
        return self.display_name or self.email


class ConsentRecord(UUIDModel):
    """One verifiable-parental-consent grant (PRODUCT.md §17.2, §13.7).

    A separate, append-only-in-spirit table rather than a boolean on `parent`,
    because what the law asks for is an auditable *history*: what was consented
    to, when, by which method, from which address, and whether it was later
    revoked. A flag can only answer "yes, currently".

    Consent is recorded per parent; `child` is optional because consent is
    granted *before* the first child exists — that ordering is the whole point
    of the gate.
    """

    class ConsentType(models.TextChoices):
        DATA_COLLECTION_V1 = "data_collection_v1", "Data collection v1"

    class Method(models.TextChoices):
        CHECKBOX_ATTESTATION = "checkbox_attestation", "Checkbox attestation"

    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="consents",
    )
    child = models.ForeignKey(
        "profiles.Child",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="consents",
    )
    consent_type = models.CharField(max_length=64, choices=ConsentType.choices)
    method = models.CharField(max_length=64, choices=Method.choices)
    granted_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    evidence_ref = models.CharField(max_length=255, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-granted_at"]
        indexes = [models.Index(fields=["parent", "consent_type"])]

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"{self.parent_id} · {self.consent_type} ({state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
