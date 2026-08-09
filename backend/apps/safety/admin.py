"""The safety ops console (PRODUCT.md §13.11, §7.11, §19.2).

Two queues, and they are read at different tempos.

**`PromptVersion`** — governance. Visible and rollback-able, but not editable: a
shipped prompt version is a record of what a child was actually told. Changing
one is a new row and a new migration, reviewed like code.

**`SafetyIncident`** — the queue a human works. §13.11 asks for a safety-incident
dashboard, and the honest MVP of a dashboard is a well-filtered list with the
severest thing at the top and one button that says "I have looked at this".

Everything except the review fields is read-only. An incident is a record of what
happened, and a record someone can edit is not one — the only thing a reviewer may
change is whether it has been reviewed.
"""

from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.safety.incidents import mark_reviewed
from apps.safety.models import PromptVersion, SafetyIncident, Severity


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ["name", "version", "active", "created_by", "created_at"]
    list_filter = ["name", "active"]
    search_fields = ["name", "version", "template"]
    # `active` stays writable so a bad prompt can be rolled back without a deploy.
    readonly_fields = ["name", "version", "template", "model_config", "created_by"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


#: Severity, as something the eye finds before the text does. A queue that has to
#: be read word by word is a queue whose critical row is missed on a busy morning.
_SEVERITY_COLOURS: dict[str, str] = {
    Severity.LOW: "#6b7280",
    Severity.MEDIUM: "#b45309",
    Severity.HIGH: "#b91c1c",
    Severity.CRITICAL: "#7f1d1d",
}


class ReviewedFilter(admin.SimpleListFilter):
    """Open vs. closed. The default view is *open*, because that is the work."""

    title = "review state"
    parameter_name = "reviewed"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("open", "Not yet reviewed"), ("done", "Reviewed")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "open":
            return queryset.filter(reviewed_at__isnull=True)
        if self.value() == "done":
            return queryset.filter(reviewed_at__isnull=False)
        return queryset


@admin.register(SafetyIncident)
class SafetyIncidentAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "severity_badge",
        "category",
        "action_taken",
        "escalated",
        "child_name",
        "reviewed_at",
    ]
    # Severity first: §13.11's queue is worked worst-first, and the filter list is
    # read in the order it is declared.
    list_filter = ["severity", ReviewedFilter, "escalated", "action_taken", "category"]
    search_fields = ["category", "child__first_name"]
    date_hierarchy = "created_at"
    list_select_related = ["child", "reviewed_by"]
    actions = ["mark_reviewed_action"]

    readonly_fields = [
        "child",
        "adventure",
        "beat",
        "severity",
        "category",
        "detail",
        "action_taken",
        "escalated",
        "created_at",
        "updated_at",
        "reviewed_by",
        "reviewed_at",
    ]

    @admin.display(description="severity", ordering="severity")
    def severity_badge(self, obj: SafetyIncident) -> Any:
        return format_html(
            '<b style="color:{}">{}</b>',
            _SEVERITY_COLOURS.get(obj.severity, "#6b7280"),
            obj.severity.upper(),
        )

    @admin.display(description="child", ordering="child__first_name")
    def child_name(self, obj: SafetyIncident) -> str:
        # The FK is nullable and `SET_NULL`: an incident outlives the family that
        # caused it, by design (§17.5). Say so rather than rendering "None".
        return obj.child.first_name if obj.child is not None else "(deleted)"

    @admin.action(description="Mark selected incidents reviewed")
    def mark_reviewed_action(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        closed = sum(
            1 for incident in queryset if mark_reviewed(incident, request.user).reviewed_by
        )
        self.message_user(request, f"{closed} incident(s) marked reviewed.", messages.SUCCESS)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Incidents are written by the pipeline, never by hand."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """An incident that can be deleted is an incident that can be hidden."""
        return False
