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


class DeletionReceipt(models.Model):
    """Proof that an account was erased, holding nothing that could re-identify it.

    **Why this table exists.** Deletion is the one irreversible thing the product
    does, and it is the one event we may later need to *prove* happened — to a
    parent who asks, to a regulator who asks, to ourselves during an incident.
    Every row that could answer the question is, by construction, gone.

    **Why it is not a log line.** It was one, and the Day 1-15 audit's F-7 is what
    that cost: `privacy.account_deleted parent=… email=family@example.com` wrote a
    parent's address into a stream with no retention, no rotation, and no delete
    path — the exact store the disclosure says holds nothing for long. The
    accountability need was real and argued in the code; the retained address was
    neither.

    **What is kept, precisely.** A keyed hash of the email, the parent's id, three
    counts and a timestamp. That is enough to answer *"was this family deleted, and
    when?"* — given the address, recompute the hash and look — and not enough to
    answer *"who has deleted their account?"*, which is not a question anybody
    here should be able to ask a table.

    **The honest cost of the key.** `email_fingerprint` is an HMAC salted with
    `SECRET_KEY` rather than a bare SHA-256, because an unkeyed hash of an email
    address is an email address to anybody with a word list. The consequence is
    stated rather than hidden: **rotating `SECRET_KEY` makes every earlier receipt
    unmatchable to an address.** The rows survive, the counts and the timestamps
    survive, and the ability to prove that one *particular* address was deleted
    does not. That is the right way round — a receipt whose privacy depended on
    nobody rotating a key would be the wrong trade — and it is why `Memory` uses a
    key with its own lifecycle and this does not: a memory must survive rotation,
    an audit trail's linkability need not.

    **Append-only.** Nothing writes to a row after `record()` creates it, there is
    no update path, and the admin registration is read-only. An editable audit
    trail is not an audit trail.
    """

    #: `AutoField`, not `UUIDModel`. Every other table in this product is keyed by
    #: an opaque id because its rows are about a family; these rows are about an
    #: *absence*, they are written in strict order, and a monotonic id is what
    #: makes "was anything removed from this table" a question somebody can ask.
    id = models.BigAutoField(primary_key=True)

    parent_id_at_deletion = models.UUIDField(
        help_text="The deleted parent's id. Points at nothing — that is the point."
    )
    email_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        help_text=(
            "salted_hmac of the lowercased email. Not reversible; "
            "matchable if you have the address."
        ),
    )
    children = models.PositiveIntegerField(default=0)
    media_rows = models.PositiveIntegerField(default=0)
    media_objects = models.PositiveIntegerField(default=0)
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-deleted_at"]
        verbose_name = "deletion receipt"

    def __str__(self) -> str:
        return f"deleted {self.parent_id_at_deletion} at {self.deleted_at:%Y-%m-%d %H:%M}"

    @staticmethod
    def fingerprint(email: str) -> str:
        """The stored form of an address. Also how you look one up afterwards."""
        from django.utils.crypto import salted_hmac

        return salted_hmac(
            "primer.accounts.deletion_receipt",
            (email or "").strip().lower(),
            algorithm="sha256",
        ).hexdigest()

    @classmethod
    def record(
        cls, *, parent_id: Any, email: str, children: int, media_rows: int, media_objects: int
    ) -> "DeletionReceipt":
        """Write one receipt. Called after the cascade has actually succeeded."""
        return cls.objects.create(
            parent_id_at_deletion=parent_id,
            email_fingerprint=cls.fingerprint(email),
            children=children,
            media_rows=media_rows,
            media_objects=media_objects,
        )
