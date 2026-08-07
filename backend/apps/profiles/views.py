"""Child profile CRUD (PRODUCT.md §18.3, §13.7).

Two rules hold this endpoint up, and both are enforced server-side because the
client is not trusted with either:

1. **Consent precedes the child.** No child row is created before an active
   consent record exists for the parent (§13.7).
2. **A child belongs to exactly one parent.** The queryset is scoped, and the
   object permission re-checks ownership on detail routes.
"""

from typing import cast

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.serializers import BaseSerializer

from apps.accounts.consent import require_consent
from apps.accounts.models import User
from apps.common.permissions import IsParentOwner, ParentScopedQuerysetMixin
from apps.profiles.models import Child
from apps.profiles.serializers import ChildSerializer


@extend_schema_view(
    list=extend_schema(operation_id="children_list", summary="List this parent's children"),
    create=extend_schema(
        operation_id="children_create",
        summary="Create a child profile",
        description="Requires an active consent record; returns 403 without one.",
    ),
    retrieve=extend_schema(operation_id="children_retrieve", summary="Fetch a child profile"),
    update=extend_schema(operation_id="children_update", summary="Replace a child profile"),
    partial_update=extend_schema(
        operation_id="children_partial_update", summary="Update a child profile"
    ),
    destroy=extend_schema(
        operation_id="children_destroy",
        summary="Delete a child and every row linked to them",
        description=(
            "Every table referencing a child cascades from this row (§17.5), so one "
            "delete satisfies 'delete my child's data'."
        ),
    ),
)
class ChildViewSet(ParentScopedQuerysetMixin, viewsets.ModelViewSet):
    serializer_class = ChildSerializer
    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Child.objects.all()
    parent_lookup = "parent"

    def perform_create(self, serializer: BaseSerializer) -> None:
        # `IsAuthenticated` has already run, so this is a real parent.
        parent = cast(User, self.request.user)
        require_consent(parent)
        serializer.save(parent=parent)
