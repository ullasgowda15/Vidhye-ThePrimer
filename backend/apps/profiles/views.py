"""Child profile CRUD and the parent's controls (PRODUCT.md §18.3, §13.7, §12.7).

Two rules hold these endpoints up, and both are enforced server-side because the
client is not trusted with either:

1. **Consent precedes the child.** No child row is created before an active
   consent record exists for the parent (§13.7).
2. **A child belongs to exactly one parent.** The queryset is scoped, and the
   object permission re-checks ownership on detail routes.

Note which of those applies to the settings route and which does not, because it
is a decision rather than an omission. Settings are owner-scoped and are **not**
behind the consent gate: §13.7 gives the parent the right to control, read,
export and delete at all times, and a parent whose consent has lapsed reaching a
screen where the only thing they can do is *restrict* their child further — or a
screen showing them what is currently set — is exactly the right outcome.
`apps.accounts.consent` carries the full argument.
"""

from typing import cast

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.serializers import BaseSerializer

from apps.accounts.consent import require_consent
from apps.accounts.models import User
from apps.common.permissions import IsParentOwner, ParentScopedQuerysetMixin
from apps.profiles.models import Child
from apps.profiles.serializers import ChildSerializer, ChildSettingsSerializer


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


@extend_schema_view(
    get=extend_schema(
        operation_id="child_settings_read",
        summary="This child's settings and controls (§12.7)",
        description=(
            "The session cap, daily time window, intensity ceiling, avoid-topics, "
            "fears, voice/text switches and accessibility flags.\n\n"
            "Returned **normalised**: what comes back is what the safety layer will "
            "actually enforce, not the raw stored column."
        ),
    ),
    put=extend_schema(
        operation_id="child_settings_replace", summary="Replace this child's settings"
    ),
    patch=extend_schema(
        operation_id="child_settings_update",
        summary="Change this child's settings",
        description=(
            "Takes effect on the **next generation** — there is no cache to wait "
            "for. The prompt assembler, the independent classifier and the "
            "deterministic policy all read these values at the moment a beat is "
            "written or graded (§12.7, §13.4), and the wellbeing rules are read on "
            "every stream open (§13.6).\n\n"
            "`accessibility` is merged rather than replaced, so sending one flag "
            "changes one flag."
        ),
    ),
)
class ChildSettingsView(RetrieveUpdateAPIView):
    """`GET`/`PATCH /children/{id}/settings` — the parent's controls.

    Deliberately **not** consent-gated, and deliberately still owner-gated. See the
    module docstring; `IsParentOwner` runs against the child before the row is
    serialised, so a request from the wrong family cannot learn that this child
    exists, let alone what their parent has forbidden.
    """

    serializer_class = ChildSettingsSerializer
    permission_classes = [IsAuthenticated, IsParentOwner]
    # Unscoped on purpose: `get_object` resolves against this and then runs
    # `check_object_permissions`, so another family's child id is a 403 rather than
    # a 404. That is the API's documented posture everywhere else
    # (`common.permissions.ParentScopedQuerysetMixin` explains why).
    queryset = Child.objects.all()
    lookup_url_kwarg = "child_id"
