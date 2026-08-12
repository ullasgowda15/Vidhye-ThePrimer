"""Parent dashboard routes (PRODUCT.md §12, §18.3). Mounted under `/api/v1/`.

No trailing slash, matching every other product route, so a request is never
redirected.

`reports/session/<uuid>` sits above `reports/sessions` in nothing — the two cannot
collide, because the first has a UUID segment after it and the second does not. The
order below is the order §12.10 reads in, not a resolution order.
"""

from django.urls import path

from apps.analytics.views import (
    InsightsView,
    MemoryListView,
    ProgressView,
    SessionReportListView,
    SessionReportView,
    TranscriptListView,
    TranscriptView,
    WeeklyReportView,
)

urlpatterns = [
    path(
        "children/<uuid:child_id>/reports/sessions",
        SessionReportListView.as_view(),
        name="reports-sessions",
    ),
    path(
        "children/<uuid:child_id>/reports/session/<uuid:session_id>",
        SessionReportView.as_view(),
        name="reports-session",
    ),
    path(
        "children/<uuid:child_id>/reports/weekly",
        WeeklyReportView.as_view(),
        name="reports-weekly",
    ),
    path(
        "children/<uuid:child_id>/progress",
        ProgressView.as_view(),
        name="child-progress",
    ),
    path(
        "children/<uuid:child_id>/insights",
        InsightsView.as_view(),
        name="child-insights",
    ),
    path(
        "children/<uuid:child_id>/memories",
        MemoryListView.as_view(),
        name="child-memories",
    ),
    path(
        "children/<uuid:child_id>/transcripts",
        TranscriptListView.as_view(),
        name="child-transcripts",
    ),
    path(
        "adventures/<uuid:adventure_id>/transcript",
        TranscriptView.as_view(),
        name="adventure-transcript",
    ),
]
