"""Instant eval and Insights are Enterprise-only, like the error feed; Jev is
Community.

Every endpoint of both features is refused without the entitlement (an OSS
install, a license that doesn't list it, a Cloud plan without it) with the
error feed's 402, and served with it. Insights also needs Turing, but only
while it is configured to call Turing models. The Temporal side refuses an
unlicensed run on its own, and Jev runs on any self-hosted install.
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from rest_framework import status
from temporalio.exceptions import ApplicationError

from tfc import ee_gates
from tfc.capabilities import service
from tfc.capabilities.errors import CapabilityDenied
from tfc.licensing.types import (
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
    LicenseType,
)
from tracer import urls as tracer_urls
from tracer.models.eval_task import EvalTask
from tracer.models.insights import InsightConfig, InsightRun
from tracer.models.instant_eval import InstantEvalRun
from tracer.services.instant_eval_rows import RUN_MARKER
from tracer.tests.test_insights_api import CONFIGS, RUNS, new_run_body
from tracer.views import insights as insight_views
from tracer.views._license import InsightsLicenseRequired, InstantEvalLicenseRequired

INSTANT_RUNS = "/tracer/instant-eval/runs/"
INSTANT_START = "/tracer/instant-eval/start/"
FEED_ISSUES = "/tracer/feed/issues/"

LOCKED = {"instant_eval": "instant-eval/", "insights": "insights/"}


class _License:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_snapshot(self):
        return self._snapshot


class _Plan:
    """A Cloud plan resolver: the features each org's plan has."""

    def __init__(self, features):
        self.features = set(features)
        self.asked = []

    def has_feature(self, org_id, feature_id):
        self.asked.append((org_id, feature_id))
        return feature_id in self.features

    def get_upgrade_cta(self, org_id, feature_id):
        return None


def _license(state=LicenseState.ACTIVE, features=()):
    return LicenseSnapshot(
        state=state,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_test",
        band="enterprise",
        features=frozenset(features),
        validated_at=datetime(2026, 10, 1, tzinfo=UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )


def oss():
    service.configure(
        flavor=DeploymentFlavor.OSS, location=DeploymentLocation.SELF_HOSTED
    )


def enterprise(*features, state=LicenseState.ACTIVE):
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=_License(_license(state, features)),
    )


def cloud(*features):
    plan = _Plan(features)
    service.configure(
        flavor=DeploymentFlavor.CLOUD,
        location=DeploymentLocation.CLOUD,
        cloud_plan_resolver=plan,
    )
    return plan


@pytest.fixture(autouse=True)
def _keep_service_state(monkeypatch):
    # Each test configures the service; put back whatever it was after.
    for name in (
        "_license_resolver",
        "_cloud_plan_resolver",
        "_deployment_flavor",
        "_deployment_location",
        "_configured",
    ):
        monkeypatch.setattr(service, name, getattr(service, name))


@pytest.fixture
def no_start(monkeypatch):
    monkeypatch.setattr(insight_views, "_start", lambda run: None)
    monkeypatch.setattr(insight_views, "_spend", lambda *a: True)


@pytest.fixture
def turing_defaults(settings, monkeypatch):
    """Insights on its default models, which are Turing's."""
    for name in ("INSIGHTS_FAST_MODEL", "INSIGHTS_THINKING_MODEL"):
        setattr(settings, name, None)
        monkeypatch.delenv(name, raising=False)


def endpoints(prefix):
    """Every (method, path, view) the tracer routes serve under ``prefix``."""
    found = []
    for pattern in tracer_urls.urlpatterns:
        route = str(pattern.pattern)
        if not route.startswith(prefix):
            continue
        view = pattern.callback.view_class
        path = "/tracer/" + re.sub(r"<uuid:\w+>", str(uuid.uuid4()), route)
        for method in ("get", "post", "put", "patch", "delete"):
            if hasattr(view, method):
                found.append((method, path, view))
    return found


def call(client, method, path, project):
    body = {"project_id": str(project.id)}
    if method == "get":
        return client.get(path, body)
    return getattr(client, method)(path, body, format="json")


NAMES = {"instant_eval": "Instant eval", "insights": "Insights"}
# A denial names the feature and what unlocks it on this deployment: never the
# internal id, and never "upgrade your plan" on a self-hosted install.
MESSAGES = {
    "LICENSE_MISSING": "{} needs an Enterprise license that includes it.",
    "LICENSE_FEATURE_MISSING": "{} isn't included in this Enterprise license.",
    "LICENSE_EXPIRED": "{} needs an Enterprise license; this one has expired.",
    "EE_CODE_UNAVAILABLE": "{} is not available.",
    "PLAN_FEATURE_MISSING": "{} isn't included in your plan.",
}


def assert_refused(response, feature, code):
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED, response.content
    body = response.json()
    assert body["code"] == code
    assert body["message"] == MESSAGES[code].format(NAMES[feature])
    assert body["upgrade_required"] is True
    assert body["error"]["code"] == code
    assert body["error"]["detail"]["feature"] == feature


# ─── Every endpoint ──────────────────────────────────────────────────────────
def test_the_routes_cover_every_view_and_method():
    instant = endpoints("instant-eval/")
    insights = endpoints("insights/")
    # Estimate, start, summary, rows (GET/POST), export (GET/POST), control, runs.
    assert len(instant) == 9
    # Config list, config get/patch/delete, draft, preview, estimate, run
    # create, run detail, run stop, run units, a unit's cited image, ask.
    assert len(insights) == 13
    for _, _, view in instant:
        assert issubclass(view, InstantEvalLicenseRequired), view.__name__
    for _, _, view in insights:
        assert issubclass(view, InsightsLicenseRequired), view.__name__


@pytest.mark.django_db
@pytest.mark.parametrize("feature", sorted(LOCKED))
def test_oss_refuses_every_endpoint_like_the_error_feed(
    auth_client, observe_project, feature
):
    oss()
    for method, path, _ in endpoints(LOCKED[feature]):
        response = call(auth_client, method, path, observe_project)
        assert_refused(response, feature, "EE_CODE_UNAVAILABLE")
    feed = auth_client.get(FEED_ISSUES)
    assert feed.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert feed.json()["code"] == "EE_CODE_UNAVAILABLE"
    assert set(feed.json()) == set(response.json())


@pytest.mark.parametrize("feature", sorted(LOCKED))
def test_the_denial_comes_before_any_database_temporal_or_agent_call(feature):
    """The license is checked right after authentication, before a handler
    reads anything: with the database blocked (no django_db here) and no
    Temporal or agent reachable, every view still answers the 402."""
    from types import SimpleNamespace

    from rest_framework.test import APIRequestFactory, force_authenticate

    oss()
    user = SimpleNamespace(
        is_authenticated=True, organization=SimpleNamespace(id=uuid.uuid4())
    )
    factory = APIRequestFactory()
    for pattern in tracer_urls.urlpatterns:
        route = str(pattern.pattern)
        if not route.startswith(LOCKED[feature]):
            continue
        view = pattern.callback.view_class
        kwargs = {name: uuid.uuid4() for name in re.findall(r"<uuid:(\w+)>", route)}
        for method in ("get", "post", "put", "patch", "delete"):
            if not hasattr(view, method):
                continue
            request = getattr(factory, method)(f"/tracer/{route}")
            force_authenticate(request, user=user)
            response = view.as_view()(request, **kwargs)
            assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
            assert response.data["code"] == "EE_CODE_UNAVAILABLE"
            assert response.data["error"]["detail"]["feature"] == feature


@pytest.mark.django_db
@pytest.mark.parametrize("feature", sorted(LOCKED))
def test_a_license_without_the_feature_refuses_every_endpoint(
    auth_client, observe_project, feature
):
    enterprise("error_feed", *(set(LOCKED) - {feature}))
    for method, path, _ in endpoints(LOCKED[feature]):
        response = call(auth_client, method, path, observe_project)
        assert_refused(response, feature, "LICENSE_FEATURE_MISSING")


@pytest.mark.django_db
def test_an_expired_license_refuses(auth_client, observe_project):
    enterprise("instant_eval", "insights", state=LicenseState.EXPIRED)
    assert_refused(
        auth_client.get(INSTANT_RUNS, {"project_id": str(observe_project.id)}),
        "instant_eval",
        "LICENSE_EXPIRED",
    )
    assert_refused(
        auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}),
        "insights",
        "LICENSE_EXPIRED",
    )


@pytest.mark.django_db
def test_a_license_listing_them_serves_both(auth_client, observe_project):
    enterprise("instant_eval", "insights")
    runs = auth_client.get(INSTANT_RUNS, {"project_id": str(observe_project.id)})
    assert runs.status_code == status.HTTP_200_OK, runs.content
    configs = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)})
    assert configs.status_code == status.HTTP_200_OK, configs.content
    assert configs.json()["result"]["configs"] == []


@pytest.mark.django_db
def test_cloud_follows_the_plan(auth_client, observe_project, organization):
    plan = cloud("instant_eval", "insights")
    project = {"project_id": str(observe_project.id)}
    assert auth_client.get(INSTANT_RUNS, project).status_code == status.HTTP_200_OK
    assert auth_client.get(CONFIGS, project).status_code == status.HTTP_200_OK
    assert (str(organization.id), "instant_eval") in plan.asked
    assert (str(organization.id), "insights") in plan.asked

    cloud()
    assert_refused(
        auth_client.get(INSTANT_RUNS, project), "instant_eval", "PLAN_FEATURE_MISSING"
    )
    assert_refused(
        auth_client.get(CONFIGS, project), "insights", "PLAN_FEATURE_MISSING"
    )


@pytest.mark.requires_ee
def test_cloud_plans_name_them_has_instant_eval_and_has_insights():
    from ee.usage.services.entitlements import get_cloud_plan_resolver

    with patch(
        "ee.usage.services.entitlements.Entitlements.has_feature",
        side_effect=lambda org_id, key: key == "has_instant_eval",
    ) as has_feature:
        resolver = get_cloud_plan_resolver()
        assert resolver.has_feature("org_1", "instant_eval") is True
        assert resolver.has_feature("org_1", "insights") is False
    has_feature.assert_any_call("org_1", "has_insights")


@pytest.mark.django_db
def test_capabilities_list_both_and_jev_on_oss(auth_client):
    oss()
    features = auth_client.get("/api/capabilities/").json()["features"]
    assert features["instant_eval"]["display_name"] == "Instant eval"
    assert features["insights"]["display_name"] == "Insights"
    for feature in ("instant_eval", "insights"):
        assert features[feature]["allowed"] is False
        assert features[feature]["reason_code"] == "EE_CODE_UNAVAILABLE"
    assert features["jev_models"]["allowed"] is True


@pytest.mark.django_db
def test_instant_eval_is_checked_before_the_handler(auth_client, observe_project):
    """The license comes first: an unlicensed start never reaches the
    handler, so neither its questions nor Jev's own gate in it are read."""
    from tracer.views.instant_eval_scale import InstantEvalStartView

    oss()
    with patch.object(InstantEvalStartView, "post") as handler:
        response = auth_client.post(
            INSTANT_START, {"project_id": str(observe_project.id)}, format="json"
        )
    assert_refused(response, "instant_eval", "EE_CODE_UNAVAILABLE")
    handler.assert_not_called()


# ─── Insights and Turing ─────────────────────────────────────────────────────
@pytest.mark.requires_ee
@pytest.mark.django_db
def test_insights_on_turing_models_needs_turing(
    auth_client, observe_project, no_start, turing_defaults
):
    enterprise("insights")
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.json()["code"] == "CAPABILITY_DENIED"
    # Self-hosted: the license lacks Turing, and its own models are the way out.
    assert response.json()["message"] == ee_gates.INSIGHTS_TURING_UNLICENSED_MESSAGE
    assert "plan" not in response.json()["message"]
    assert not InsightConfig.objects.exists()

    enterprise("insights", "turing_models")
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_200_OK, response.content

    cloud("insights")
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.json()["message"] == ee_gates.INSIGHTS_TURING_DENIED_MESSAGE


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_insights_on_the_deployments_own_models_doesnt_need_turing(
    auth_client, observe_project, no_start, settings
):
    enterprise("insights")
    settings.INSIGHTS_FAST_MODEL = "gpt-4.1-mini"
    settings.INSIGHTS_THINKING_MODEL = "anthropic/claude-sonnet-4"
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_200_OK, response.content

    # One Turing tier is enough to need Turing.
    settings.INSIGHTS_THINKING_MODEL = "turing_large"
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.json()["message"] == ee_gates.INSIGHTS_TURING_UNLICENSED_MESSAGE


@pytest.mark.requires_ee
def test_insights_turing_models_reads_both_tiers(settings, turing_defaults):
    assert ee_gates.insights_turing_models() == ["turing_flash", "turing_large"]
    settings.INSIGHTS_FAST_MODEL = "gpt-4.1-mini"
    settings.INSIGHTS_THINKING_MODEL = "gpt-4.1"
    assert ee_gates.insights_turing_models() == []
    settings.INSIGHTS_FAST_MODEL = "turing/turing_flash"
    assert ee_gates.insights_turing_models() == ["turing/turing_flash"]


# ─── Temporal ────────────────────────────────────────────────────────────────
@pytest.fixture
def insight_run(auth_client, observe_project, no_start, turing_defaults):
    enterprise("insights", "turing_models")
    result = auth_client.post(
        RUNS, new_run_body(observe_project), format="json"
    ).json()["result"]
    return InsightRun.objects.get(id=result["run_id"])


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_an_insights_stage_refuses_an_unlicensed_run(insight_run):
    from tfc.temporal.insights import activities

    ran = []
    stage = activities._licensed(lambda run_id: ran.append(run_id) or "ran")
    assert stage(str(insight_run.id)) == "ran"

    oss()
    with pytest.raises(ApplicationError) as refused:
        stage(str(insight_run.id))
    assert refused.value.non_retryable
    assert refused.value.type == "CapabilityDenied"
    assert refused.value.message == "Insights is not available."
    assert ran == [str(insight_run.id)]

    # A lapsed license says so, as the views' 402 does, not "plan".
    enterprise("insights", "turing_models", state=LicenseState.EXPIRED)
    with pytest.raises(ApplicationError) as refused:
        stage(str(insight_run.id))
    assert refused.value.message == (
        "Insights needs an Enterprise license; this one has expired."
    )

    enterprise("insights")
    with pytest.raises(ApplicationError) as refused:
        activities.require_license(str(insight_run.id))
    assert refused.value.message == ee_gates.INSIGHTS_TURING_UNLICENSED_MESSAGE


@pytest.mark.requires_ee
def test_an_insights_stage_keeps_the_refusal_and_still_records_a_failure(
    monkeypatch,
):
    """A refusal reaches the workflow as is (non-retryable, with its own
    message), not as the generic retryable error; recording the failure
    skips the check."""
    from tfc.temporal.insights import activities

    def to_async(fn, **_):
        async def run(*args):
            return fn(*args)

        return run

    def refuse(run_id):
        raise ApplicationError(
            ee_gates.INSIGHTS_DENIED_MESSAGE,
            type="CapabilityDenied",
            non_retryable=True,
        )

    monkeypatch.setattr("channels.db.database_sync_to_async", to_async)
    monkeypatch.setattr(activities, "require_license", refuse)
    with pytest.raises(ApplicationError) as refused:
        asyncio.run(activities._run("prepare", lambda run_id: "ran", "run-1"))
    assert refused.value.non_retryable
    assert refused.value.message == ee_gates.INSIGHTS_DENIED_MESSAGE

    def fail(run_id, message):
        return "recorded"

    assert (
        asyncio.run(activities._run("fail", fail, "run-1", "why", licensed=False))
        == "recorded"
    )


def _instant_task(eval_task, organization):
    eval_task.filters = {**(eval_task.filters or {}), RUN_MARKER: True}
    eval_task.save(update_fields=["filters"])
    InstantEvalRun.objects.create(
        eval_task=eval_task,
        project=eval_task.project,
        organization=organization,
        unit="sessions",
        row_type="sessions",
    )
    return eval_task


@pytest.mark.django_db
def test_an_instant_eval_task_fails_closed_without_the_license(
    eval_task, organization, monkeypatch
):
    from tfc.temporal.eval_tasks import activities

    task = _instant_task(eval_task, organization)
    enterprise("instant_eval")
    activities._require_instant_eval_license(task)

    oss()
    with pytest.raises(ApplicationError) as refused:
        activities._require_instant_eval_license(task)
    assert refused.value.non_retryable
    assert refused.value.type == "CapabilityDenied"
    assert refused.value.message == "Instant eval is not available."

    enterprise("insights")
    with pytest.raises(ApplicationError) as refused:
        activities._require_instant_eval_license(task)
    assert refused.value.message == (
        "Instant eval isn't included in this Enterprise license."
    )

    # Reconcile and every claim check it before touching the task's rows.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    with (
        patch("tracer.services.eval_tasks.reconciler.reconcile") as reconcile,
        patch("tracer.services.eval_tasks.entries.claim_pending_batch") as claim,
    ):
        with pytest.raises(ApplicationError):
            activities._reconcile_sync(str(task.id))
        with pytest.raises(ApplicationError):
            activities._claim_batch_sync(str(task.id), 5)
    reconcile.assert_not_called()
    claim.assert_not_called()


@pytest.mark.django_db
def test_an_instant_eval_task_without_its_run_row_is_refused(eval_task):
    from tfc.temporal.eval_tasks import activities

    eval_task.filters = {**(eval_task.filters or {}), RUN_MARKER: True}
    eval_task.save(update_fields=["filters"])
    enterprise("instant_eval")
    with pytest.raises(ApplicationError) as refused:
        activities._require_instant_eval_license(eval_task)
    assert refused.value.non_retryable
    assert refused.value.type == "CapabilityDenied"


@pytest.mark.django_db
def test_an_unanswerable_entitlement_check_is_retried_not_a_denial(
    eval_task, organization
):
    from tfc.capabilities.errors import CapabilityDenied
    from tfc.temporal.eval_tasks import activities

    task = _instant_task(eval_task, organization)
    with patch(
        "tfc.ee_gating.check_ee_feature",
        side_effect=CapabilityDenied(
            feature_id="instant_eval", reason_code="RESOLVER_UNAVAILABLE"
        ),
    ):
        with pytest.raises(ApplicationError) as unavailable:
            activities._require_instant_eval_license(task)
    assert not unavailable.value.non_retryable
    assert unavailable.value.type == "CapabilityUnavailable"
    with patch(
        "tfc.ee_gating.check_ee_feature",
        side_effect=CapabilityDenied(
            feature_id="instant_eval", reason_code="PLAN_FEATURE_MISSING"
        ),
    ):
        with pytest.raises(ApplicationError) as refused:
            activities._require_instant_eval_license(task)
    assert refused.value.non_retryable


@pytest.mark.django_db
def test_other_eval_tasks_dont_need_the_instant_eval_license(eval_task):
    from tfc.temporal.eval_tasks import activities

    oss()
    activities._require_instant_eval_license(EvalTask.objects.get(id=eval_task.id))


# ─── Jev is Community ────────────────────────────────────────────────────────
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "configure",
    [oss, lambda: enterprise(), lambda: enterprise(state=LicenseState.EXPIRED)],
    ids=["oss", "ee-unlicensed", "ee-expired"],
)
def test_jev_runs_on_any_self_hosted_install(configure):
    configure()
    assert service.check("jev_models", org_id="org_1").allowed is True
    service.check_or_raise("jev_models", org_id="org_1")
    assert ee_gates._jev_denied_off_cloud() is False
    assert ee_gates.ee_model_gate_for_template("jev-latest") is None


def test_jev_on_cloud_follows_the_plan():
    cloud()
    with pytest.raises(CapabilityDenied):
        service.check_or_raise("jev_models", org_id="org_1")
    cloud("jev_models")
    service.check_or_raise("jev_models", org_id="org_1")


def test_a_build_without_jev_refuses_it_clearly(monkeypatch):
    from evaluations.engine.instance import (
        reject_jev_without_code,
        reject_unknown_jev_model,
    )

    oss()
    monkeypatch.setattr(
        "tfc.ee_loader.has_ee",
        lambda module: not module.startswith(("ee.jev", "ee.evals")),
    )
    monkeypatch.setitem(sys.modules, "ee.jev.mapping", None)
    assert ee_gates.jev_available() is False

    response = ee_gates.ee_model_gate_for_template("jev-latest")
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert response.data["message"] == ee_gates.JEV_UNAVAILABLE_MESSAGE
    assert response.data["feature"] == "jev"

    with pytest.raises(CapabilityDenied) as refused:
        reject_jev_without_code()
    assert str(refused.value.detail) == ee_gates.JEV_UNAVAILABLE_MESSAGE
    assert refused.value.reason_code == "EE_CODE_UNAVAILABLE"
    # An unknown Jev ID can't reach the stripped mapping module either.
    with pytest.raises(CapabilityDenied):
        reject_unknown_jev_model("jev-9.9.9")


def test_a_build_without_jev_refuses_it_before_building_the_evaluator():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from evaluations.engine.instance import create_eval_instance

    template = SimpleNamespace(
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": "jev-latest",
            "rule_prompt": "Judge {{input}}",
            "output": "Pass/Fail",
            "required_keys": ["input"],
        },
        organization=SimpleNamespace(id="org_1"),
        workspace=None,
        choice_scores=None,
        choices=[],
        pass_threshold=0.5,
        multi_choice=False,
    )
    with (
        patch("tfc.ee_gates.jev_available", return_value=False),
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch("tfc.capabilities.service.check_or_raise") as capability,
    ):
        with pytest.raises(CapabilityDenied) as refused:
            create_eval_instance(Mock(), template)
    assert str(refused.value.detail) == ee_gates.JEV_UNAVAILABLE_MESSAGE
    capability.assert_not_called()


def test_an_insights_stage_without_ee_fails_for_good_not_on_import(monkeypatch):
    """The open tree has no Insights pipeline: a stage that somehow runs fails
    once with a clear, non-retryable error instead of an ImportError retry."""
    import builtins

    from tfc.temporal.insights import activities

    real_import = builtins.__import__

    def no_ee(name, *args, **kwargs):
        if name.startswith("ee.agenthub.insights"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_ee)
    with pytest.raises(ApplicationError) as refused:
        activities._pipeline("prepare")
    assert refused.value.non_retryable
    assert refused.value.message == activities.NOT_IN_BUILD
    assert activities._model_error_class() is None


def test_a_failed_report_shows_only_text_written_for_people():
    """A timeout's "activity StartToClose timeout" never reaches run.error."""
    from temporalio.exceptions import ActivityError, TimeoutError, TimeoutType

    from tfc.temporal.insights import workflows

    recorded = []
    current = {}

    async def execute_activity(name, arg, **_):
        if name == "insight_fail":
            recorded.append(arg.message)
            return None
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="w",
            activity_type=name,
            activity_id="1",
            retry_state=None,
        ) from current["cause"]

    for cause, expected in (
        (
            TimeoutError(
                "activity StartToClose timeout",
                type=TimeoutType.START_TO_CLOSE,
                last_heartbeat_details=[],
            ),
            workflows.FAILED_MESSAGE,
        ),
        (
            ApplicationError("Your credits ran out.", type="InsightModelError"),
            "Your credits ran out.",
        ),
        # An untyped error is not text written for people.
        (ApplicationError("Activity failed: KeyError"), workflows.FAILED_MESSAGE),
    ):
        recorded.clear()
        current["cause"] = cause
        with patch.object(workflows.workflow, "execute_activity", execute_activity):
            with pytest.raises(ActivityError):
                asyncio.run(
                    workflows.InsightReportWorkflow().run(
                        workflows.InsightRunInput(run_id="run-1")
                    )
                )
        assert recorded == [expected]
