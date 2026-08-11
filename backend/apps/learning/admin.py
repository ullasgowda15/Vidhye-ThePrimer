"""Admin for the invisible curriculum.

Two different jobs, and the read-only settings are the difference between them.
The catalog is editable — it is content, and someone will want to widen an age
band without shipping a migration. `LearningProgress` is **read-only in every
field that matters**: it is a derived estimate, and a number typed into it by hand
would be indistinguishable afterwards from one a child earned. This is also the
page the founder manual test opens to check that a child's counting mastery moved
after a session (Day 10 §8), which only means anything if nothing else could have
moved it.
"""

from typing import Any

from django.contrib import admin

from apps.learning.models import LearningProgress, Milestone, Objective


class ObjectiveInline(admin.TabularInline):
    model = Objective
    extra = 0
    fields = ["code", "description", "min_age", "max_age", "prerequisites"]
    ordering = ["min_age", "code"]


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ["name", "domain", "standard_ref"]
    list_filter = ["domain"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [ObjectiveInline]


@admin.register(Objective)
class ObjectiveAdmin(admin.ModelAdmin):
    list_display = ["code", "domain", "milestone", "min_age", "max_age", "prerequisites"]
    list_filter = ["domain", "milestone"]
    search_fields = ["code", "description"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(LearningProgress)
class LearningProgressAdmin(admin.ModelAdmin):
    """One child's skill model, as the estimate it is.

    `mastery` and `uncertainty` are shown to three decimals rather than rounded,
    because the question this page is opened to answer is "did it move", and a
    first session moves counting by a few hundredths.
    """

    list_display = [
        "child",
        "objective",
        "mastery_display",
        "uncertainty_display",
        "evidence_count",
        "last_practiced_at",
        "due_at",
    ]
    list_filter = ["objective__domain", "objective"]
    search_fields = ["child__first_name", "objective__code"]
    date_hierarchy = "last_practiced_at"
    readonly_fields = [
        "child",
        "objective",
        "mastery",
        "uncertainty",
        "last_practiced_at",
        "due_at",
        "evidence_count",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request: Any) -> bool:
        # A progress row is created by evidence (`engine.apply_evidence`) and by
        # nothing else. One added here would be a child credited with a story they
        # never played.
        return False

    @admin.display(description="mastery", ordering="mastery")
    def mastery_display(self, obj: LearningProgress) -> str:
        return f"{obj.mastery:.3f}"

    @admin.display(description="uncertainty", ordering="uncertainty")
    def uncertainty_display(self, obj: LearningProgress) -> str:
        return f"{obj.uncertainty:.3f}"
