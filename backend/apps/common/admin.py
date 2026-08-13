"""The ops console for feature flags (PRODUCT.md §16.10).

The admin is the on-call surface: a flag flipped here takes effect within
`flags.CACHE_TTL_S` seconds, in every process, with no deploy. Which is exactly
why the form validates before it saves — the `ai.beat_model` lever exists to
*fix* a bad model change, and a lever that will happily accept an unreachable
model id is one that breaks the product at the moment it is being used.

`value` is a free-text field rather than a checkbox because half the flags are
not booleans, and a widget that had to switch on the flag's type would be a
second copy of the registry.
"""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.common.flags import REGISTRY
from apps.common.models import FeatureFlag


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ["name", "value", "declared_default", "note", "set_by", "updated_at"]
    search_fields = ["name", "note"]
    readonly_fields = ["declared_default", "description"]

    @admin.display(description="Ships as")
    def declared_default(self, obj: FeatureFlag) -> str:
        flag = REGISTRY.get(obj.name)
        return (flag.default or "(empty)") if flag else "— not a declared flag —"

    @admin.display(description="What it does")
    def description(self, obj: FeatureFlag) -> str:
        flag = REGISTRY.get(obj.name)
        return flag.description if flag else ""

    def save_model(self, request: HttpRequest, obj: FeatureFlag, form: Any, change: bool) -> None:
        """Stamp who did it, and validate before storing.

        `full_clean` rather than trusting the form: `ModelAdmin` calls
        `Model.clean` for fields the form declares, and the point of the check in
        `FeatureFlag.clean` is that it must hold for every write.
        """
        if not obj.set_by:
            obj.set_by = getattr(request.user, "email", "") or str(request.user)
        obj.full_clean()
        super().save_model(request, obj, form, change)
