"""Ownership permissions.

Every row in this product belongs to exactly one parent, reached either
directly (`obj.parent_id`) or through the child (`obj.child.parent_id`). One
permission class expressing that walk means a new child-scoped model gets the
right rule by declaring the right FK, not by remembering to write a check.

Object permissions are only half the job: DRF never calls them for list views,
so every child-scoped queryset must *also* filter by the requesting parent.
`ParentScopedQuerysetMixin` is that half.
"""

from typing import Any, cast

from django.db.models import QuerySet
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class IsParentOwner(BasePermission):
    """Allow only the parent who owns the object (directly or via its child)."""

    message = "This resource belongs to another account."

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        parent_id = getattr(obj, "parent_id", None) or getattr(
            getattr(obj, "child", None), "parent_id", None
        )
        user = request.user
        return bool(parent_id is not None and user.is_authenticated and parent_id == user.id)


class ParentScopedQuerysetMixin:
    """Narrow a viewset's collection routes to the requesting parent.

    `parent_lookup` is the ORM path from the model to the owning parent —
    `"parent"` for `Child`, `"child__parent"` for anything hanging off a child.

    Detail routes deliberately resolve against the *unscoped* queryset so that
    `IsParentOwner` is the thing that rejects them, with a 403. Filtering them
    too would give 404 and hide the existence of the row — marginally better
    against enumeration, but it would also make an ownership bug indistinguish-
    able from a deleted row in the logs, and it is not what the API promises.
    Object IDs are UUIDs, so there is nothing here to enumerate.
    """

    parent_lookup = "parent"

    def get_queryset(self) -> QuerySet[Any]:
        # `self` is always a viewset here; casting rather than re-declaring
        # `request`/`queryset` on the mixin keeps it from colliding with the
        # same names on Django's own View base classes.
        view = cast(GenericAPIView, self)
        user = view.request.user
        queryset = cast(QuerySet[Any], view.queryset)

        if not user.is_authenticated:
            # Belt and braces: the permission classes already rejected this
            # request, but an empty queryset means a misconfigured view leaks
            # nothing rather than everything.
            return queryset.none()
        if getattr(self, "action", None) in {"list", None}:
            return queryset.filter(**{self.parent_lookup: user})
        return queryset
