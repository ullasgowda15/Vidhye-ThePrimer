"""The parent account (PRODUCT.md §17.2 `parent`).

The buyer and the trust-gate is the parent, not the child — children never have
accounts of their own. Email is the login identifier because there is no
username anywhere in the product surface, and asking a parent to invent one is
friction with no payoff.
"""

from typing import Any, ClassVar

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
