"""Shared model bases, and the one table that belongs to no product area.

Every child-linked table uses a UUID primary key (PRODUCT.md §17): IDs are
handed to clients and appear in URLs, so they must not leak row counts, and
they let us generate IDs client-side later without a round trip.
"""

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FeatureFlag(UUIDModel):
    """One overridden lever (§16.10). The registry is `apps.common.flags`.

    A row exists only when someone has *changed* something — an unset flag has no
    row and resolves to the settings value or the declared default. That is why
    the table is usually empty, and why a row in it is worth reading during an
    incident: every one of them is a deliberate act.

    `name` is not a choices field on purpose. A migration per new flag would mean
    the thing designed to move without a deploy needs one, and the registry
    already refuses unknown names on both read (`flags.value`) and write
    (`clean`) — which is enforcement in the two places that matter, without a
    schema change standing between an on-call engineer and a lever.
    """

    name = models.CharField(max_length=64, unique=True)
    value = models.CharField(max_length=200, blank=True)
    #: Why. Free text, and the admin asks for it — a flag flipped six weeks ago
    #: with no note is a flag nobody dares turn back.
    note = models.CharField(max_length=200, blank=True)
    set_by = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}={self.value or '(empty)'}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        self._forget()

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        result = super().delete(*args, **kwargs)
        self._forget()
        return result

    def clean(self) -> None:
        from apps.common.flags import REGISTRY

        flag = REGISTRY.get(self.name)
        if flag is None:
            raise ValidationError(
                {
                    "name": (
                        f"'{self.name}' is not a declared flag. Known flags: "
                        f"{', '.join(sorted(REGISTRY))}."
                    )
                }
            )
        if flag.validate and (problem := flag.validate(self.value)):
            raise ValidationError({"value": problem})

    def _forget(self) -> None:
        """Drop this flag's cached value so *this* process sees the change now.

        Other processes wait out `flags.CACHE_TTL_S`. Stated rather than hidden:
        that TTL is the real write-to-effect bound, and pretending a save is
        instant everywhere is how somebody concludes a lever is broken and pulls
        a second one.
        """
        from apps.common.flags import invalidate

        invalidate(self.name)
