"""The memory view (PRODUCT.md §11.10, §12.6, §13.8).

§11.10 requires child memory to be "parent-viewable and parent-deletable". The
parent's own surface for that is Day 12; this is the operator's view of the same
rows, and it exists now for two reasons: the founder needs to see what a played
session actually wrote, and a table nobody has ever looked at is a table whose
salience threshold nobody has ever tuned.

**Read-only, and more strictly than the incident queue.** An incident admin keeps
one writable field so a reviewer can close a row. Nothing here is writable at
all: a memory is a record of something that happened to a child, and an edited
memory is a companion confidently remembering something that did not. Salience in
particular is never touched by hand — it is what the scoring pass decided at the
time, and re-scoring it later from a different opinion would rewrite the child's
history.

**Deletion is allowed, on purpose.** It is the one destructive action that is a
*feature* here: §13.8's erasure right has to be honourable today, and until the
parent-facing control lands (Day 12) an operator acting on a request is the path.
`child` cascade covers "delete everything about this child"; this covers "delete
that one".

**`embedding` is deliberately not on this page.** It is 1536 floats, it would
render as a wall of digits that pushes the content off the screen, and it says
nothing a human can read. `has_embedding` answers the only question anyone
actually has about it.
"""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html

from apps.memory.models import Memory


@admin.register(Memory)
class MemoryAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "child_name",
        "type",
        "salience_badge",
        "valence",
        "summary",
        "has_embedding",
        "recall_count",
    ]
    # Type first, then salience: the two questions worth asking of this table are
    # "what kinds of thing do we remember about this child" and "are we keeping
    # the mundane". `list_filter` is read in declaration order.
    # `RelatedOnlyFieldListFilter` on `child`, not the plain FK filter: the plain one
    # renders every child in the database as a `<select>` option, so the page gets
    # slower with every family that signs up whether or not they have any memories.
    list_filter = ["type", ("child", admin.RelatedOnlyFieldListFilter)]
    date_hierarchy = "created_at"
    list_select_related = ["child", "source_adventure"]
    # Not `content`: the column is ciphertext in the database (`common.crypto`), so
    # a `LIKE` over it would silently never match — a search box that is quietly
    # broken is worse than no search box. `entities` is plain JSON and is the
    # useful handle anyway ("everything about character:sparkle").
    search_fields = ["entities", "child__first_name"]

    readonly_fields = [
        "child",
        "type",
        "content",
        "salience",
        "valence",
        "entities",
        "source_adventure",
        "last_recalled_at",
        "recall_count",
        "decay",
        "deleted_at",
        "created_at",
        "updated_at",
        "has_embedding",
    ]
    # `embedding` excluded from the form entirely rather than merely read-only.
    exclude = ["embedding"]

    @admin.display(description="child", ordering="child__first_name")
    def child_name(self, obj: Memory) -> str:
        return obj.child.first_name

    @admin.display(description="memory")
    def summary(self, obj: Memory) -> str:
        """The memory itself, decrypted by the field on the way out of the DB.

        Clipped in the list view because these are one sentence by construction and
        a row that wraps to three lines makes the table unreadable. The detail page
        shows the whole thing.
        """
        text = obj.content or ""
        return text if len(text) <= 90 else f"{text[:89]}…"

    @admin.display(description="salience", ordering="salience")
    def salience_badge(self, obj: Memory) -> Any:
        # Colour so the eye can spot a table drifting toward the mundane without
        # reading every number: anything under the shipping threshold should not be
        # here at all, and a page full of pale rows means the threshold moved.
        colour = (
            "#166534" if obj.salience >= 0.7 else "#b45309" if obj.salience >= 0.5 else "#6b7280"
        )
        # Rounded *before* `format_html`, not inside its format string. `format_html`
        # conditionally escapes each argument into a `SafeString`, and `"{:.2f}"`
        # cannot format one of those — the same line with the spec inline raises
        # `ValueError: Unknown format code 'f'` and 500s the whole list page.
        return format_html('<b style="color:{}">{}</b>', colour, f"{obj.salience:.2f}")

    @admin.display(description="vector", boolean=True)
    def has_embedding(self, obj: Memory) -> bool:
        """Whether this memory is reachable by semantic retrieval (§11.9).

        False is not corruption: the column is nullable so that an embedding
        provider having a bad day costs the vector rather than the memory. A page
        with several of these is a prompt to look at the worker log.
        """
        return obj.embedding is not None

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Memories are consolidated from sessions, never typed in."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """An editable memory is a companion remembering something invented."""
        return False
