"""Shared model bases.

Every child-linked table uses a UUID primary key (PRODUCT.md §17): IDs are
handed to clients and appear in URLs, so they must not leak row counts, and
they let us generate IDs client-side later without a round trip.
"""

import uuid

from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
