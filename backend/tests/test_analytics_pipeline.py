"""§5.6's hard boundary, and the funnel that crosses the onboarding endpoints.

Criterion 3 is the one this file exists for: *product analytics and the
parent-facing curated subset are separated, and raw child behaviour never appears
in the parent view.* A boundary that is only a docstring is not a boundary, so it
is asserted three ways:

1. **Statically** — no module reachable from a parent-facing view imports the
   product-analytics modules. This is the one that catches the accident: somebody
   adds `from apps.analytics import metrics` to a serializer because a number was
   handy, and every other test still passes.
2. **On the wire** — the parent dashboard's own responses are scanned for the
   fields only telemetry has.
3. **By access** — `/admin/metrics` is staff-only, and a signed-in parent with a
   perfectly valid token cannot read it.

Plus the onboarding funnel, driven through the real endpoints, because those four
steps are a *flow* and what is worth asserting is that a parent walking it
produces a funnel.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.analytics.models import Event, GenerationCost
from tests.conftest import CHILDREN_URL, CONSENT_URL, PASSWORD, SIGNUP_URL

pytestmark = pytest.mark.django_db

BACKEND = Path(__file__).resolve().parents[1]

#: The product/ops half of §5.6. Nothing a parent's request can reach may import
#: any of these.
PRODUCT_ANALYTICS = {
    "apps.analytics.metrics",
    "apps.analytics.internal",
    "apps.analytics.cost",
    "apps.analytics.pricing",
}

#: The modules that serve `/api/v1/children/{id}/...` — everything a parent reads.
PARENT_FACING = [
    "apps/analytics/views.py",
    "apps/analytics/serializers.py",
    "apps/analytics/reports.py",
    "apps/analytics/skills.py",
    "apps/analytics/transparency.py",
    "apps/analytics/urls.py",
]


def _imports(path: Path) -> set[str]:
    """Every module name imported by one file, as written.

    An AST walk rather than a regex, so a name inside a docstring or a comment
    about the boundary does not read as a breach of it — this test's own subject
    matter is discussed at length in those files.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


class TestTheSeparationIsStructural:
    """§5.6, asserted as a fact about the import graph."""

    def test_no_parent_facing_module_imports_product_analytics(self) -> None:
        breaches: list[str] = []
        for relative in PARENT_FACING:
            imported = _imports(BACKEND / relative)
            for module in PRODUCT_ANALYTICS & imported:
                breaches.append(f"{relative} imports {module}")

        assert not breaches, (
            "§5.6 keeps product analytics and the parent-facing subset strictly "
            "apart. These parent-facing modules reached across:\n  " + "\n  ".join(breaches)
        )

    def test_the_parent_facing_modules_under_test_actually_exist(self) -> None:
        """The guard on the guard.

        A list of paths is exactly the kind of thing that rots silently: rename
        `reports.py` and the test above starts passing vacuously for it. It has
        happened to better lists.
        """
        missing = [relative for relative in PARENT_FACING if not (BACKEND / relative).exists()]
        assert not missing, f"parent-facing modules moved: {missing}"

    def test_the_telemetry_models_are_not_in_the_parent_api_schema(self) -> None:
        """Nothing on the parent API serialises an `Event` or a `GenerationCost`.

        Read off the generated OpenAPI schema rather than off the source, because
        the schema is what the TS client is built from — it is the surface a
        frontend can actually call.
        """
        import json
        from io import StringIO

        from django.core.management import call_command

        buffer = StringIO()
        call_command("spectacular", "--format", "openapi-json", stdout=buffer)
        schema = json.loads(buffer.getvalue())

        components = set(schema.get("components", {}).get("schemas", {}))
        assert not {name for name in components if name in {"Event", "GenerationCost"}}

        # And the dashboard has no path in the versioned API at all.
        assert not [path for path in schema.get("paths", {}) if "metrics" in path]


class TestRawChildBehaviourNeverReachesTheParentView:
    """Criterion 3, on the wire."""

    def test_the_dashboard_responses_carry_no_telemetry_fields(
        self, auth: Any, parent: Any, consent: Any, child_payload: dict[str, Any], fake_ai: Any
    ) -> None:
        """A parent's own pages, played for real and then read.

        The parent surface is *supposed* to be full of facts about their child —
        that is what §12 is. What it must never carry is the ops telemetry: event
        names, per-generation cost, token counts, provider labels. Those are the
        vocabulary of the other half of §5.6.
        """
        from apps.analytics.tests.conftest import finish, play
        from apps.profiles.models import Child

        created = auth.post(CHILDREN_URL, child_payload, format="json")
        assert created.status_code == 201
        child = Child.objects.get(pk=created.data["id"])

        from apps.companion.service import create_companion

        create_companion(
            child=child,
            archetype="dragon",
            name="Pip",
            palette="ember",
            personality_preset="brave_spark",
        )
        finish(play(child))

        forbidden = {
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "priced",
            "provider",
            "props",
            "event",
            "events",
            "adventures_per_week",
            "activation",
            "dwa",
        }

        for url in (
            f"/api/v1/children/{child.pk}/reports/sessions",
            f"/api/v1/children/{child.pk}/reports/weekly",
            f"/api/v1/children/{child.pk}/insights",
            f"/api/v1/children/{child.pk}/transcripts",
            f"/api/v1/children/{child.pk}/progress",
        ):
            response = auth.get(url)
            assert response.status_code == 200, (url, response.data)
            assert not (forbidden & _keys(response.data)), url

        # **The separation is not "we do not collect it".** We do — that sitting
        # produced both — and the boundary is about where it may be *read*. Without
        # these two lines the assertions above would pass just as happily on a
        # build where the pipeline had stopped writing anything at all, which is
        # the wrong thing to be reassured by.
        assert Event.objects.filter(child=child).exists()
        assert GenerationCost.objects.filter(child=child).exists()


def _keys(payload: Any) -> set[str]:
    """Every key anywhere in a nested response body."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _keys(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys(item)
    return found


class TestTheInternalDashboardIsStaffOnly:
    """Criterion 2's other half: the numbers are *visible*, and only to us."""

    def test_a_parent_cannot_read_it(self, auth: Any) -> None:
        """A valid parent token is not a staff session.

        `/admin/metrics` is session-authenticated like the rest of the admin, so a
        bearer token buys nothing here — the parent is redirected to the admin
        login rather than shown aggregate telemetry about other families.
        """
        response = auth.get("/admin/metrics", follow=False)
        assert response.status_code in (302, 403)
        if response.status_code == 302:
            assert "/admin/login" in response["Location"]

    def test_an_anonymous_visitor_cannot_read_it(self, api: Any) -> None:
        response = api.get("/admin/metrics", follow=False)
        assert response.status_code in (302, 403)

    def test_a_staff_member_sees_the_numbers_and_the_cost_per_session(
        self, client: Any, fake_ai: Any
    ) -> None:
        """Criterion 1 and 2, rendered.

        Asserting on the rendered page rather than on `metrics.snapshot` — which
        `test_metrics.py` already covers — because the criterion is that the
        numbers are *on an internal dashboard*, and a template that silently
        dropped a section would pass every other test in the suite.
        """
        from apps.analytics.tests.conftest import finish, play
        from apps.companion.service import create_companion
        from apps.profiles.models import Child

        staff = get_user_model().objects.create_user(
            email="ops@example.com", password=PASSWORD, is_staff=True
        )
        child = Child.objects.create(
            parent=staff,
            first_name="Ivy",
            birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
            interests=["frogs"],
        )
        companion = create_companion(
            child=child,
            archetype="dragon",
            name="Pip",
            palette="ember",
            personality_preset="brave_spark",
        )
        companion.visual_descriptor = "a small round friendly dragon"
        companion.save(update_fields=["visual_descriptor"])
        finish(play(child))

        client.force_login(staff)
        response = client.get("/admin/metrics")
        assert response.status_code == 200

        body = response.content.decode()
        for heading in (
            "Activation",
            "Day-2 return",
            "W1 retention",
            "Completion rate",
            "Adventures per child per week",
            "Cost per session",
            "Unattributed spend",
            "Unpriced generations",
            # The four diagnostics: which step lost them, how many are still
            # here, whether retention is improving, and what they picked. Listed
            # here because these sections are the only place in the product where
            # a template typo would render as a silently missing metric rather
            # than as an error.
            "Onboarding funnel",
            "Children still playing",
            "Retention by weekly cohort",
            "Worlds children picked",
        ):
            assert heading in body, heading

        # The North Star's own vocabulary, and the §22.3 targets beside the
        # numbers they judge.
        assert "DWA" in body
        assert "≥70" in body
        # And nothing per-child. A staff page that could be pointed at one named
        # four-year-old is a raw behavioural record with a nicer font (§13.8).
        assert "Ivy" not in body

    def test_the_terminal_report_is_the_same_numbers(self, fake_ai: Any) -> None:
        """`manage.py metrics` shares `snapshot` and `cost_per_session` with the
        HTML view. A terminal report with its own arithmetic would be a second
        dashboard that eventually disagrees with the first."""
        from io import StringIO

        from django.core.management import call_command

        buffer = StringIO()
        call_command("metrics", days=7, stdout=buffer)
        report = buffer.getvalue()

        assert "Activation" in report
        assert "cost/session" in report
        assert "DWA" in report
        # The four diagnostics render in the terminal too. `as_text` does its own
        # string formatting over the same snapshot, so a shape it cannot format
        # (a `None` cell, an empty funnel) raises here and nowhere else.
        assert "Onboarding funnel" in report
        assert "stickiness" in report
        assert "Retention by weekly cohort" in report
        assert "Worlds children picked" in report


class TestTheOnboardingFunnel:
    """§4.2 walked through the real endpoints, producing §22.3's funnel."""

    def test_every_step_emits(self, api: Any) -> None:
        signup = api.post(
            SIGNUP_URL, {"email": "funnel@example.com", "password": PASSWORD}, format="json"
        )
        assert signup.status_code == 201, signup.data
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {signup.data['access']}")

        assert (
            api.post(
                CONSENT_URL,
                {"consent_type": "data_collection_v1", "method": "checkbox_attestation"},
                format="json",
            ).status_code
            == 201
        )
        created = api.post(
            CHILDREN_URL,
            {"first_name": "Ivy", "birthdate": "2021-05-04"},
            format="json",
        )
        assert created.status_code == 201, created.data

        companion = api.post(
            f"/api/v1/children/{created.data['id']}/companion",
            {
                "archetype": "dragon",
                "name": "Pip",
                "palette": "ember",
                "personality_preset": "brave_spark",
            },
            format="json",
        )
        assert companion.status_code == 201, companion.data

        emitted = set(Event.objects.values_list("name", flat=True))
        assert {
            Event.PARENT_SIGNED_UP,
            Event.PARENT_CONSENT_RECORDED,
            Event.CHILD_CREATED,
            Event.COMPANION_CREATED,
            Event.ONBOARDING_COMPLETED,
        } <= emitted

    def test_onboarding_completes_once_per_parent(self, api: Any) -> None:
        """A second child a month later is not a second onboarding.

        Activation's first half asks whether the *parent* finished §4.2, so an
        account with three children must not read as three activations.
        """
        signup = api.post(
            SIGNUP_URL, {"email": "two-children@example.com", "password": PASSWORD}, format="json"
        )
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {signup.data['access']}")
        api.post(
            CONSENT_URL,
            {"consent_type": "data_collection_v1", "method": "checkbox_attestation"},
            format="json",
        )

        for name in ("Ivy", "Sam"):
            child = api.post(
                CHILDREN_URL, {"first_name": name, "birthdate": "2021-05-04"}, format="json"
            )
            api.post(
                f"/api/v1/children/{child.data['id']}/companion",
                {
                    "archetype": "dragon",
                    "name": "Pip",
                    "palette": "ember",
                    "personality_preset": "brave_spark",
                },
                format="json",
            )

        assert Event.objects.filter(name=Event.COMPANION_CREATED).count() == 2
        assert Event.objects.filter(name=Event.ONBOARDING_COMPLETED).count() == 1

    def test_a_child_created_event_carries_no_profile_content(self, api: Any) -> None:
        """§13.8 on the funnel's most tempting event.

        A child's interests, fears and avoid-topics are exactly the profile §13.8
        minimises. "Did the parent fill this in" is answerable with a count.
        """
        signup = api.post(
            SIGNUP_URL, {"email": "minimal@example.com", "password": PASSWORD}, format="json"
        )
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {signup.data['access']}")
        api.post(
            CONSENT_URL,
            {"consent_type": "data_collection_v1", "method": "checkbox_attestation"},
            format="json",
        )
        api.post(
            CHILDREN_URL,
            {
                "first_name": "Ivy",
                "birthdate": "2021-05-04",
                "interests": ["dinosaurs"],
                "fears": ["the dark"],
                "avoid_topics": ["hospitals"],
            },
            format="json",
        )

        event = Event.objects.get(name=Event.CHILD_CREATED)
        blob = " ".join(str(value) for value in event.props.values()).lower()
        for secret in ("ivy", "dinosaurs", "the dark", "hospitals", "2021"):
            assert secret not in blob
        # The shape survives, which is what the funnel actually needs.
        assert event.props["interests"] == 1
        assert event.props["fears"] == 1
