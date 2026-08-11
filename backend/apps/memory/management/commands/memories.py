"""Look at what a child's companion remembers (PRODUCT.md §11, §12.6).

    python manage.py memories --child <uuid>
    python manage.py memories --child <uuid> --stm             # the live session log
    python manage.py memories --child <uuid> --like "dragon"   # nearest neighbours
    python manage.py memories --child <uuid> --recall "a beach" # the whole bundle
    python manage.py memories --session <uuid> --consolidate   # re-run one sitting

The admin gives the same rows in a browser; this exists for the three things a
browser is bad at.

The first is the **nearest-neighbour check**. §11.9's retrieval is vector search
plus rules, and the only way to know whether the vectors are any good is to type a
query and look at what comes back ranked. That is one command here and a
hand-written SQL query anywhere else, so without it the ANN index is a thing we
believe in rather than a thing we have seen work.

The second is **the bundle a generation would actually get** (`--recall`). `--like`
shows the vector half alone; this shows what the prompt would carry — the rules
pinned on top, the ranked half under them, and what the token budget cut. It is the
only way to answer "why did the companion mention *that*", which is the question
every tuning conversation about §11.4's threshold and §19.4's budget starts with.

The third is **the STM log while a child is still playing**. It lives in Redis for
a few hours and then it is gone, so there is no page to visit later — `--stm` is
the only way to watch working memory fill up during a session.

Read-only — `--recall` bumps nothing, deliberately, because an inspection tool that
changed recall strength would make the thing it is measuring drift every time you
looked at it. It makes **no provider calls** except the one embedding needed by
`--like` and `--recall`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pgvector.django import CosineDistance

from apps.media.adapters import embedding as embedding_adapter
from apps.media.exceptions import MediaError
from apps.memory import consolidation, retrieval, stm
from apps.memory.models import Memory
from apps.profiles.models import Child
from apps.story.models import PlaySession


class Command(BaseCommand):
    help = "Inspect a child's long-term memories, their live STM log, or re-consolidate a sitting."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--child", help="Child UUID.")
        parser.add_argument("--session", help="PlaySession UUID (for --stm / --consolidate).")
        parser.add_argument(
            "--like",
            default="",
            help="Rank this child's memories by similarity to this text (§11.9).",
        )
        parser.add_argument(
            "--recall",
            default="",
            help="Show the whole bundle a generation would get for this context (§19.4).",
        )
        parser.add_argument("--stm", action="store_true", help="Show the Redis short-term log.")
        parser.add_argument(
            "--consolidate",
            action="store_true",
            help="Re-run consolidation for --session. Writes rows; does nothing if already done.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["consolidate"]:
            self._consolidate(options["session"])
            return
        if options["stm"]:
            self._show_stm(options["session"], options["child"])
            return

        child = self._child(options["child"])
        if options["recall"]:
            self._recall(child, options["recall"])
        elif options["like"]:
            self._nearest(child, options["like"])
        else:
            self._list(child)

    # --- retrieval (§11.9, §19.4) -----------------------------------------
    def _recall(self, child: Child, context: str) -> None:
        """The bundle a beat would be generated from, with the ranking shown.

        Every column here is a decision the ranker made: `rule` versus `vec` says
        which half put it there, and the three scores say why it sits where it sits.
        A bundle that looks wrong is almost always one of those three being wrong,
        and reading them next to each other is how you tell which.
        """
        bundle = retrieval.retrieve(child, context)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'Bundle for {child.first_name} given "{context[:60]}" - '
                f"{len(bundle)} of {bundle.considered} considered, "
                f"{bundle.tokens}/{retrieval.token_budget()} tokens"
                + (self.style.WARNING("  [vector half unavailable]") if bundle.degraded else "")
            )
        )
        for item in bundle:
            half = "rule" if item.pinned else "vec "
            self.stdout.write(
                f"  {half} {item.score:.2f}  "
                f"(sim {item.similarity:.2f} sal {item.salience:.2f} rec {item.recency:.2f})  "
                f"{item.type:<17} {item.content}"
            )
        if not bundle:
            self.stdout.write("  (nothing - this child has no companion, no seeds and no memories)")
        # Said out loud, because "few, high-value" is the claim and a reader should
        # be able to see the cost of it rather than take it on trust.
        self.stdout.write("")
        self.stdout.write(
            f"  recall bookkeeping was NOT written (inspection only); "
            f"{len(bundle.remembered)} of these are stored memories"
        )

    # --- long-term memory --------------------------------------------------
    def _list(self, child: Child) -> None:
        memories = Memory.objects.filter(child=child, deleted_at__isnull=True)
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"{memories.count()} memories for {child.first_name}")
        )
        for memory in memories:
            self.stdout.write(self._row(memory))
        if not memories:
            self.stdout.write(
                "  (none yet - play a session and end it, and make sure a Celery worker is up)"
            )

    def _nearest(self, child: Child, query: str) -> None:
        try:
            vector = embedding_adapter.embed(query)
        except MediaError as exc:
            raise CommandError(
                f"Could not embed the query: {exc}\n"
                "Set EMBEDDING_PROVIDER=local for deterministic offline vectors."
            ) from exc

        ranked = (
            Memory.objects.filter(child=child, deleted_at__isnull=True, embedding__isnull=False)
            .annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance")[:10]
        )
        provider = embedding_adapter.provider_label(embedding_adapter.select_embedding_provider())
        self.stdout.write(
            self.style.MIGRATE_HEADING(f'Nearest to "{query}" for {child.first_name} [{provider}]')
        )
        # Ordering, not the absolute numbers, is what to read here: the local
        # provider is lexical, so its distances are not comparable to a real model's.
        for memory in ranked:
            self.stdout.write(f"  {memory.distance:.3f}  {self._row(memory)}")
        if not ranked:
            self.stdout.write("  (no embedded memories - see the `vector` column in /admin/)")

    def _row(self, memory: Memory) -> str:
        vector = "vec" if memory.embedding is not None else self.style.WARNING("no-vec")
        entities = ",".join(memory.entities[:4])
        return (
            f"[{memory.salience:.2f}] {memory.type:<17} {memory.content}  "
            f"({vector}, valence {memory.valence:+.1f}"
            + (f", {entities}" if entities else "")
            + ")"
        )

    # --- short-term memory -------------------------------------------------
    def _show_stm(self, session_id: str | None, child_id: str | None) -> None:
        session = self._session(session_id) if session_id else self._latest_session(child_id)
        events = stm.events(session.pk)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"STM for session {session.pk} ({session.child.first_name}) - "
                f"key {stm.key_for(session.pk)}"
            )
        )
        for event in events:
            name = event.pop("event", "?")
            fields = " ".join(f"{key}={value}" for key, value in event.items())
            self.stdout.write(f"  {name:<12} {fields}")
        if not events:
            self.stdout.write(
                "  (empty - the sitting has been consolidated, the TTL has passed, "
                "or Redis is unreachable)"
            )

        self.stdout.write("")
        self.stdout.write(f"  session-row mirror: {session.stm_digest or '{}'}")
        self.stdout.write(f"  consolidated_at:    {session.consolidated_at or '(not yet)'}")

    # --- re-running one sitting -------------------------------------------
    def _consolidate(self, session_id: str | None) -> None:
        session = self._session(session_id)
        outcome = consolidation.consolidate_session(session)

        self.stdout.write(self.style.SUCCESS(f"  {outcome}"))
        if outcome.status == "already":
            self.stdout.write(
                "  Clear `consolidated_at` on the session to force a re-run - "
                "the near-duplicate filter will still stop it writing the same rows twice."
            )

    # --- lookups -----------------------------------------------------------
    def _child(self, child_id: str | None) -> Child:
        if not child_id:
            raise CommandError("Pass --child <uuid>. `manage.py shell` -> Child.objects.all().")
        child = Child.objects.filter(pk=child_id).first()
        if child is None:
            raise CommandError(f"No child with id {child_id}.")
        return child

    def _session(self, session_id: str | None) -> PlaySession:
        if not session_id:
            raise CommandError("Pass --session <uuid>.")
        session = PlaySession.objects.select_related("child").filter(pk=session_id).first()
        if session is None:
            raise CommandError(f"No session with id {session_id}.")
        return session

    def _latest_session(self, child_id: str | None) -> PlaySession:
        child = self._child(child_id)
        session = PlaySession.objects.select_related("child").filter(child=child).first()
        if session is None:
            raise CommandError(f"{child.first_name} has never played a session.")
        return session
