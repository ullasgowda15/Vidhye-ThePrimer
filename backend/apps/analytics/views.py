"""Parent dashboard endpoints (PRODUCT.md §12, §18.3, §18.7).

    GET /api/v1/children/{id}/reports/sessions            the reports index
    GET /api/v1/children/{id}/reports/session/{sessionId}  one sitting (§12.1)
    GET /api/v1/children/{id}/reports/weekly               the week (§12.2, §18.7)
    GET /api/v1/children/{id}/progress                     per-objective (§12.3)
    GET /api/v1/children/{id}/insights                     milestones + interests
    GET /api/v1/children/{id}/memories                     what it remembers (§12.6)
    GET /api/v1/children/{id}/transcripts                  the log's index (§12.6)
    GET /api/v1/adventures/{id}/transcript                 one story, verbatim

Three of these are additions to the day's brief, and each is the index its page
needs: §12.10's IA has a *Session Reports* page and a *Transparency Log* page, and
a page cannot list what no endpoint enumerates. `insights` is the milestone rollup
and the interest trend in one read, because `/progress` is specified as a flat list
of objectives and §12.3 asks for them grouped under milestones.

**Every one of them is owner-scoped, and the check is always against the child.**
`IsParentOwner` runs before any report, memory, beat or progress row is read, so a
request from the wrong parent cannot learn that a sitting exists, let alone what
happened in it. That ordering is the same one the greeting endpoint uses and it is
load-bearing for the same reason (§11.10): the sensitive thing here is not the
payload, it is the fact of it.

**Ownership, and deliberately not consent.** Through Day 12 these reads also
required an active consent record, which meant a parent whose consent had lapsed
could not open their own child's transparency log — the screen §12.8 says is where
data rights are exercised. §13.7 grants the parent the right to read, export and
delete *at all times*; consent gates what we may **do** with a child's data, not
what a parent may **see** of it. The gate therefore stays on the processing paths
(`profiles.views` on create, `story.views` on generation) and is off here. The
full argument is in `apps.accounts.consent`.

**They are all reads.** The one exception is invisible and deliberate: asking for a
report that was never generated generates it (`reports.report_for_session`), so a
broker outage on the evening a child played does not leave a permanent hole in the
dashboard. It is idempotent, it is on a request a parent made, and it can only ever
produce the report that sitting should already have had.

Nothing here is reachable from the child surface, and nothing here is written for a
child to read (§14.2).
"""

from __future__ import annotations

import datetime
from typing import Any

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.analytics import reports, skills, transparency
from apps.analytics.models import SessionReport
from apps.analytics.serializers import (
    InsightsSerializer,
    MemorySerializer,
    ProgressSerializer,
    SessionReportSerializer,
    SessionReportSummarySerializer,
    TranscriptSerializer,
    TranscriptSummarySerializer,
    WeeklyReportSerializer,
)
from apps.common.permissions import IsParentOwner
from apps.memory.models import Memory, MemoryType
from apps.profiles.models import Child
from apps.story.models import Adventure

#: How many stories the transparency index lists. A year of daily play, which is
#: more than a parent will scroll and is still one bounded query.
TRANSCRIPT_PAGE = 200

#: How many memories the transparency view returns. The most salient first, which
#: is the order retrieval would reach for them in — so a parent reading this list
#: is reading roughly what the companion is most likely to bring up.
MEMORY_PAGE = 200


class ParentChildView(GenericAPIView):
    """Shared plumbing for the child-scoped dashboard reads.

    One `child_for` rather than a `get_object_or_404` per view, so the ownership
    check cannot be forgotten on the one endpoint somebody adds in a hurry — and so
    it is always in the same place in the request: before anything is read.
    """

    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Child.objects.none()

    def child_for(self, request: Request, child_id: Any) -> Child:
        child = get_object_or_404(Child, pk=child_id)
        self.check_object_permissions(request, child)
        return child


@extend_schema_view(
    get=extend_schema(
        operation_id="reports_sessions",
        summary="This child's session reports, newest first",
        responses=SessionReportSummarySerializer(many=True),
    )
)
class SessionReportListView(ParentChildView):
    serializer_class = SessionReportSummarySerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        rows = SessionReport.objects.filter(child=child).order_by("-occurred_at")[
            : reports.REPORT_PAGE
        ]
        return Response(SessionReportSummarySerializer(rows, many=True).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="reports_session",
        summary="The report for one sitting (§12.1)",
        description=(
            "Where the child went, the choice that mattered, the objective that was "
            "exercised and how it went, and one specific real moment.\n\n"
            "Generated after the sitting ends. Asking for a report that was never "
            "generated generates it, so an outage on a worker cannot leave a hole in "
            "the dashboard. A sitting still in progress has no report and returns 404 "
            "— there is nothing to recap yet."
        ),
        responses=SessionReportSerializer,
    )
)
class SessionReportView(ParentChildView):
    serializer_class = SessionReportSerializer

    def get(self, request: Request, child_id: Any, session_id: Any) -> Response:
        child = self.child_for(request, child_id)
        report = reports.report_for_session(child, session_id)
        if report is None:
            # 404 for "no such sitting for this child" *and* for "this sitting has
            # not finished". A parent-owned 404 leaks nothing: the ownership check
            # above already ran against the child, so this cannot be used to probe
            # another family's session ids.
            raise NotFound("No finished session with that id for this child.")
        return Response(SessionReportSerializer(report).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="reports_weekly",
        summary="The weekly digest (§12.2, §18.7)",
        description=(
            "Monday to Sunday. `week` is any date inside the week wanted "
            "(`YYYY-MM-DD`); it defaults to this week.\n\n"
            "Computed on read from the session reports written at the time plus the "
            "live skill model and interest graph — so it is never stale, and its "
            "movement claims are measured against snapshots rather than recomputed."
        ),
        parameters=[
            OpenApiParameter(
                name="week",
                type=str,
                description="Any date inside the week wanted, as YYYY-MM-DD.",
            )
        ],
        responses=WeeklyReportSerializer,
    )
)
class WeeklyReportView(ParentChildView):
    serializer_class = WeeklyReportSerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        report = reports.generate_weekly_report(child, _week_param(request))
        return Response(WeeklyReportSerializer(report).data)


def _week_param(request: Request) -> datetime.date | None:
    """`?week=YYYY-MM-DD`, or None for this week.

    Rejected rather than ignored when it is unparseable: a dashboard silently
    showing this week when the parent asked for last week is a report about the
    wrong seven days, and there is no way for them to tell.
    """
    raw = str(request.query_params.get("week") or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError({"week": "Expected a date as YYYY-MM-DD."}) from exc


@extend_schema_view(
    get=extend_schema(
        operation_id="child_progress",
        summary="Per-objective progress in plain language (§12.3)",
        description=(
            "Every objective in this child's curriculum with where they are on it: "
            "`not_started`, `emerging`, `practising` or `mastered`, and the same "
            "thing as a sentence.\n\n"
            "**Never a grade or a percentile** (§12.3). `mastery` and `reach` are "
            "carried for completeness and are not meant to be rendered: mastery is "
            "on the difficulty scale, and its ceiling differs per objective, so the "
            "number alone says something untrue about a child who has finished "
            "counting to three."
        ),
        responses=ProgressSerializer(many=True),
    )
)
class ProgressView(ParentChildView):
    serializer_class = ProgressSerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        return Response(ProgressSerializer(skills.skill_views(child), many=True).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="child_insights",
        summary="Milestones and the interest trend (§12.3, §12.5, §11.7)",
        responses=InsightsSerializer,
    )
)
class InsightsView(ParentChildView):
    serializer_class = InsightsSerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        return Response(
            InsightsSerializer(
                {
                    "milestones": skills.milestone_rollup(child),
                    "interests": reports.interest_trend(child),
                }
            ).data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="child_memories",
        summary="What the companion remembers about this child (§12.6, §11.10)",
        description=(
            "The parent-facing half of the memory graph: what was kept, in the "
            "companion's own words, most salient first. Deleted memories are not "
            "returned.\n\n"
            "No ranking numbers are included — see the schema. This is a record of "
            "moments, not a score sheet."
        ),
        responses=MemorySerializer(many=True),
    )
)
class MemoryListView(ParentChildView):
    serializer_class = MemorySerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        labels = dict(MemoryType.choices)
        rows = Memory.objects.filter(child=child, deleted_at__isnull=True).order_by(
            "-salience", "-created_at"
        )[:MEMORY_PAGE]
        return Response(
            MemorySerializer(
                [
                    {
                        "id": memory.pk,
                        "type": memory.type,
                        "type_label": labels.get(memory.type, memory.type),
                        "content": memory.content,
                        "created_at": memory.created_at,
                        "adventure_id": memory.source_adventure_id,
                    }
                    for memory in rows
                ],
                many=True,
            ).data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="child_transcripts",
        summary="The transparency log's index — every story this child has played",
        responses=TranscriptSummarySerializer(many=True),
    )
)
class TranscriptListView(ParentChildView):
    serializer_class = TranscriptSummarySerializer

    def get(self, request: Request, child_id: Any) -> Response:
        child = self.child_for(request, child_id)
        adventures = transparency.adventures_for(child, limit=TRANSCRIPT_PAGE)
        return Response(
            TranscriptSummarySerializer(
                [
                    {
                        "id": adventure.pk,
                        "title": adventure.title or "An untitled adventure",
                        "status": adventure.status,
                        "started_at": adventure.started_at,
                        "beats": adventure.beats.count(),
                    }
                    for adventure in adventures
                ],
                many=True,
            ).data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="adventure_transcript",
        summary="One story, verbatim (§12.6)",
        description=(
            "Exactly what the child saw and heard, beat by beat, with what they did "
            "under each one. Nothing is summarised or omitted — that is the feature "
            "(§12.6).\n\n"
            "Owner-scoped against the child the adventure belongs to."
        ),
        responses=TranscriptSerializer,
    )
)
class TranscriptView(GenericAPIView):
    """One adventure's transcript, owner-scoped through its child."""

    permission_classes = [IsAuthenticated, IsParentOwner]
    serializer_class = TranscriptSerializer
    queryset = Adventure.objects.none()

    def get(self, request: Request, adventure_id: Any) -> Response:
        adventure = get_object_or_404(Adventure.objects.select_related("child"), pk=adventure_id)
        # Against the adventure, whose `child.parent_id` is what `IsParentOwner`
        # walks to. Checked before a single beat is read — the transcript is the
        # most sensitive read in the product after retrieval itself.
        #
        # Ownership and nothing else, for the reason `accounts.consent` sets out:
        # this is the §12.6 transparency log, and a parent who has withdrawn
        # consent has *more* claim to read what was already generated about their
        # child, not less.
        self.check_object_permissions(request, adventure)
        return Response(TranscriptSerializer(transparency.transcript_for(adventure)).data)
