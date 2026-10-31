"""Fix my agent over HTTP: the user's fixes, the engine's calls from its sandbox, and
the run lists that leave a fix's check runs out."""

import json
import subprocess
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4, uuid5

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organization, Workspace
from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.models import TestExecution as Execution
from simulate.pydantic_schemas.agent_fix import Surface
from simulate.services.agent_fix_agents import VersionedSourceAgent
from simulate.services.agent_fix_runs import close_fix_run
from simulate.services.harness_environment import (
    annotate_for_list,
    environment_row,
    run_summaries,
)
from simulate.services.hosted_harness import hash_secret
from simulate.services.preview_pagination import _read_revision_state
from simulate.services.run_results_v3_queries import (
    build_run_comparison,
    run_calls_queryset,
)
from simulate.tests.test_agent_fix_services import (
    KEYS,
    PATCH,
    _candidate,
    _check,
    _fix_run,
    _run,
    _tree,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_agent_versions import _passing
from simulate.tests.test_harness_environment_regrade import _mapped_eval
from simulate.tests.test_harness_run_compare import _done
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.views.run_results_v3 import _execution_payload
from tfc.ee_gating import EEFeature, FeatureUnavailable

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/agent-fix-runs"
ENGINE = "/simulate/api/agent-fix-engine"
TOKEN = "token-1"
ENGINE_MODEL = "deepseek/deepseek-v4.1-flash"
PLATFORM_KEY = "platform-harness-key"


@pytest.fixture(autouse=True)
def entitled():
    with patch("simulate.views.agent_fix.check_ee_feature") as gate:
        yield gate


def _armed(fix_run, *, expires_in=timedelta(hours=1)):
    """Give the fix the credentials its sandbox holds."""
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        token_hash=hash_secret(TOKEN),
        token_expires_at=timezone.now() + expires_in,
    )
    return fix_run


def _engine(client, method, fix_run, path, *, token=TOKEN, **kwargs):
    headers = {}
    if token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return getattr(client, method)(f"{ENGINE}/{fix_run.id}/{path}", **headers, **kwargs)


def _billed(usd):
    """A model call's cost as the organization is billed for it: at the AI markup, to the
    spend column's four places."""
    from ee.usage.services.config import BillingConfig

    return (usd * BillingConfig.get().get_ai_cost_markup()).quantize(Decimal("0.0001"))


def _out_of_credits():
    """The usage check's refusal when the organization's AI credits are paused."""
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult

    return UsageLimitExceeded(
        CheckResult(
            allowed=False,
            error_code="BUDGET_PAUSED",
            dimension="ai_credits",
            reason="AI credits are paused by your budget",
            current_usage=12,
            limit=12,
        )
    )


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _launch_body(source_run, **overrides):
    return {
        "source_run_id": str(source_run.test_execution_id),
        "target_scenario_keys": [KEYS[0]],
        "surfaces": ["prompt"],
        "budget_credits": 500,
        "max_minutes": 30,
        **overrides,
    }


class TestEngineAuthentication:
    def test_the_sandbox_reads_its_brief(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(api_client, "get", fix_run, "brief/")

        assert response.status_code == 200, response.content
        assert response.json()["fix_run_id"] == str(fix_run.id)

    @pytest.mark.parametrize(
        ("sent", "code"),
        [
            ({"token": "token-2"}, "authentication_invalid"),
            ({"token": None}, "authentication_required"),
        ],
        ids=["wrong-token", "no-bearer"],
    )
    def test_wrong_credentials_are_refused(self, api_client, source_run, sent, code):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(api_client, "get", fix_run, "brief/", **sent)

        assert (response.status_code, response.json()["error"]) == (401, code)

    def test_an_expired_bearer_is_refused(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run), expires_in=timedelta(seconds=-1))

        response = _engine(api_client, "get", fix_run, "brief/")

        assert (response.status_code, response.json()["error"]) == (
            401,
            "authentication_expired",
        )

    def test_a_closed_fix_refuses_its_sandbox(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run))
        close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        response = _engine(api_client, "get", fix_run, "brief/")

        assert (response.status_code, response.json()["error"]) == (
            401,
            "authentication_invalid",
        )


class TestEngineCalls:
    def test_a_fix_from_first_candidate_to_ending(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run))

        with ExitStack() as stack:
            for one in _passing():
                stack.enter_context(one)
            created = _engine(
                api_client,
                "post",
                fix_run,
                "candidates/",
                data={
                    "attempt": "1",
                    "summary": "refund first",
                    "tree": SimpleUploadedFile("tree.tar.gz", _tree()),
                    "patch": SimpleUploadedFile("candidate.patch", PATCH),
                },
                format="multipart",
            )
        assert created.status_code == 201, created.content
        candidate = created.json()
        assert candidate["preflight_status"] == "passed"

        started = _engine(
            api_client,
            "post",
            fix_run,
            "check-runs/",
            data={
                "label": "candidate-1",
                "agent_version_id": candidate["agent_version_id"],
                "scenario_keys": [KEYS[0]],
                "trials": 1,
            },
            format="json",
        )
        assert started.status_code == 202, started.content
        run_id = started.json()["run_id"]
        assert _engine(api_client, "get", fix_run, f"check-runs/{run_id}/").json() == {
            "run_id": run_id,
            "state": "queued",
            "finished": False,
            "total": 1,
            "failure": None,
            "records": [],
        }

        ended = _engine(
            api_client,
            "post",
            fix_run,
            "report/",
            data={
                "stage": "done",
                "ending": "ready",
                "winner_candidate_id": candidate["candidate_id"],
            },
            format="json",
        )
        assert ended.json() == {"state": "ready"}
        assert AgentFixCandidate.no_workspace_objects.get(
            id=candidate["candidate_id"]
        ).outcome == ("winner")
        assert _engine(api_client, "get", fix_run, "brief/").status_code == 401

    @pytest.mark.parametrize(
        ("path", "body", "field"),
        [
            ("candidates/", {"attempt": "1"}, "tree"),
            (
                "check-runs/",
                {"label": "x", "agent_version_id": "a"},
                "agent_version_id",
            ),
            ("report/", {"stage": "x" * 33}, "stage"),
        ],
        ids=[
            "candidate-without-files",
            "check-run-incomplete",
            "report-stage-too-long",
        ],
    )
    def test_malformed_calls_are_refused(
        self, api_client, source_run, path, body, field
    ):
        fix_run = _armed(_fix_run(source_run))
        fmt = "multipart" if path == "candidates/" else "json"

        response = _engine(api_client, "post", fix_run, path, data=body, format=fmt)

        # The platform's request-validation envelope names the field it refused.
        assert response.status_code == 400
        assert field in response.json()["detail"]
        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.stage) == (AgentFixRun.State.RUNNING, "")

    @pytest.mark.parametrize(
        ("path", "body", "fmt"),
        [
            (
                "report/",
                {"stage": "x", "winner_candidate_id": "not-a-uuid"},
                "json",
            ),
            (
                "candidates/",
                {
                    "attempt": "one",
                    "tree": SimpleUploadedFile("tree.tar.gz", b"t"),
                    "patch": SimpleUploadedFile("candidate.patch", b"p"),
                },
                "multipart",
            ),
        ],
        ids=["report-candidate-id", "candidate-attempt"],
    )
    def test_hostile_ids_are_refused(self, api_client, source_run, path, body, fmt):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(api_client, "post", fix_run, path, data=body, format=fmt)

        assert response.status_code == 400

    def test_a_newer_engine_s_unknown_report_field_is_ignored(
        self, api_client, source_run
    ):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(
            api_client,
            "post",
            fix_run,
            "report/",
            data={"stage": "Diagnose", "insights": {"held_back": 2}},
            format="json",
        )

        assert response.status_code == 200, response.content
        fix_run.refresh_from_db()
        assert fix_run.stage == "Diagnose"

    def test_a_progress_report_s_step_plan_diagnosis_and_tiers_reach_the_read(
        self, api_client, auth_client, source_run
    ):
        fix_run = _armed(_fix_run(source_run))
        candidate = _candidate(fix_run)
        tiers = [
            {"name": "static", "status": "passed", "detail": "1 file changed"},
            {"name": "target", "status": "failed", "detail": "0 of 1 passed"},
        ]

        response = _engine(
            api_client,
            "post",
            fix_run,
            "report/",
            data={
                "stage": "Screen · attempt 1",
                "step": "search",
                "plan": {
                    "target": 1,
                    "held_back": 0,
                    "must_pass": 1,
                    "max_candidates": 4,
                    "repeats": 3,
                    "baseline_spread_pts": 17,
                },
                "diagnosis": {"causes": ["It never refunds before closing"]},
                "candidates": [
                    {
                        "candidate_id": str(candidate.id),
                        "outcome": "rejected",
                        "verdict": {
                            "accepted": False,
                            "reason": "target: 0 of 1 passed",
                            "must_pass_regressions": [],
                            "tiers": tiers,
                        },
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        read = auth_client.get(f"{BASE}/{fix_run.id}/").json()
        assert (
            read["step"],
            read["plan"]["baseline_spread_pts"],
            read["plan"]["protected"],
        ) == (
            "search",
            17,
            0,
        )
        assert read["diagnosis"] == {"causes": ["It never refunds before closing"]}
        assert read["candidates"][0]["verdict"]["tiers"] == tiers

    def test_a_check_run_past_the_organizations_limit_is_refused_with_its_reason(
        self, api_client, source_run, temporal
    ):
        temporal.usage.side_effect = _out_of_credits()
        fix_run = _armed(_fix_run(source_run))

        response = _engine(
            api_client,
            "post",
            fix_run,
            "check-runs/",
            data={
                "label": "base",
                "agent_version_id": str(fix_run.base_agent_version_id),
                "scenario_keys": [KEYS[0]],
                "trials": 1,
            },
            format="json",
        )

        assert response.status_code == 402, response.content
        # In the engine's error shape, named apart from the fix's own budget.
        assert response.json() == {
            "error": "usage_limit",
            "message": "AI credits are paused by your budget",
            "retryable": False,
        }
        assert not HostedHarnessJob.no_workspace_objects.filter(
            fix_run=fix_run
        ).exists()

    def test_the_engine_reads_only_its_own_check_runs(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(api_client, "get", fix_run, f"check-runs/{source_run.id}/")

        assert (response.status_code, response.json()["error"]) == (404, "not_found")


class TestEngineModelCalls:
    """The engine's model calls go through its fix: the platform's key, the fix's budget."""

    @pytest.fixture(autouse=True)
    def gateway(self, settings, monkeypatch):
        settings.AGENT_FIX_ENGINE_MODELS = [ENGINE_MODEL]
        monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
        monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", PLATFORM_KEY)
        monkeypatch.setenv(
            "AGENTCC_GATEWAY_INTERNAL_URL", "http://gateway.internal:8080/"
        )
        reply = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "diagnosis"}}]},
            headers={"x-agentcc-cost": "0.0123", "x-agentcc-request-id": "req-1"},
        )
        with patch(
            "simulate.services.agent_fix_models.httpx.post", return_value=reply
        ) as post:
            yield post

    @pytest.fixture(autouse=True)
    def billed(self, monkeypatch):
        """The usage events the calls emit, as on the cloud."""
        from ee.usage.services import emitter

        events = []
        monkeypatch.setattr("simulate.services.agent_fix_models.is_oss", lambda: False)
        monkeypatch.setattr(emitter, "emit", events.append)
        return events

    def _complete(self, client, fix_run, *, HTTP_ACCEPT="*/*", **body):
        return _engine(
            client,
            "post",
            fix_run,
            "v1/chat/completions",
            data=json.dumps({"model": ENGINE_MODEL, "messages": [], **body}),
            content_type="application/json",
            HTTP_ACCEPT=HTTP_ACCEPT,
        )

    def test_a_call_goes_out_on_the_platform_key_and_is_metered(
        self, api_client, source_run, gateway
    ):
        fix_run = _armed(_fix_run(source_run))

        response = self._complete(api_client, fix_run)

        assert response.status_code == 200, response.content
        assert response.json() == {"choices": [{"message": {"content": "diagnosis"}}]}
        [call] = gateway.call_args_list
        assert call.args == ("http://gateway.internal:8080/v1/chat/completions",)
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {PLATFORM_KEY}"
        assert json.loads(call.kwargs["content"])["model"] == ENGINE_MODEL
        fix_run.refresh_from_db()
        assert fix_run.spent_usd == _billed(Decimal("0.0123"))

    def test_the_fix_spends_what_the_call_bills(
        self, api_client, source_run, monkeypatch
    ):
        from ee.usage.services.config import BillingConfig

        monkeypatch.setattr(
            BillingConfig, "get_ai_cost_markup", lambda self: Decimal("1.2")
        )
        fix_run = _armed(_fix_run(source_run))

        assert self._complete(api_client, fix_run).status_code == 200

        fix_run.refresh_from_db()
        # $0.0123 at the gateway, $0.01476 billed.
        assert fix_run.spent_usd == Decimal("0.0148")

    def test_a_metered_call_is_billed_to_the_organization_as_ai_credits(
        self, api_client, source_run, billed
    ):
        from ee.usage.services.config import BillingConfig

        fix_run = _armed(_fix_run(source_run))

        assert self._complete(api_client, fix_run).status_code == 200

        [event] = billed
        assert (event.org_id, event.event_type) == (
            str(fix_run.organization_id),
            "agent_fix",
        )
        assert event.amount == BillingConfig.get().calculate_ai_credits(
            Decimal("0.0123")
        )
        assert event.properties["fix_run_id"] == str(fix_run.id)
        assert event.properties["raw_cost_usd"] == "0.0123"
        assert event.properties["gateway_request_id"] == "req-1"
        assert event.event_id == str(uuid5(fix_run.id, "req-1"))

    def test_a_billing_failure_leaves_the_call_answered_and_metered(
        self, api_client, source_run, monkeypatch
    ):
        from ee.usage.services import emitter

        def down(event):
            raise ConnectionError("redis down")

        monkeypatch.setattr(emitter, "emit", down)
        fix_run = _armed(_fix_run(source_run))

        response = self._complete(api_client, fix_run)

        assert response.status_code == 200, response.content
        fix_run.refresh_from_db()
        assert fix_run.spent_usd == _billed(Decimal("0.0123"))

    def test_the_gateway_s_refusal_reaches_the_engine_and_costs_nothing(
        self, api_client, source_run, gateway, billed
    ):
        fix_run = _armed(_fix_run(source_run))
        gateway.return_value = httpx.Response(429, json={"error": "rate limited"})

        response = self._complete(api_client, fix_run)

        assert (response.status_code, response.json()) == (
            429,
            {"error": "rate limited"},
        )
        fix_run.refresh_from_db()
        assert fix_run.spent_usd == 0
        assert billed == []

    @pytest.mark.parametrize(
        ("fix", "body", "refusal"),
        [
            ({}, {"model": "openai/gpt-5"}, (403, "fix_model_not_allowed")),
            ({}, {"stream": True}, (400, "fix_model_stream_unsupported")),
            (
                {"state": AgentFixRun.State.STOPPING},
                {},
                (409, "fix_run_not_running"),
            ),
            ({"spent_usd": Decimal("5")}, {}, (402, "fix_budget_exhausted")),
        ],
        ids=["other-model", "streamed", "stopping", "budget-spent"],
    )
    def test_refused_calls_never_reach_the_gateway(
        self, api_client, source_run, gateway, fix, body, refusal
    ):
        fix_run = _armed(_fix_run(source_run, **fix))

        response = self._complete(api_client, fix_run, **body)

        assert (response.status_code, response.json()["error"]) == refusal
        gateway.assert_not_called()

    def test_a_client_asking_for_a_stream_is_told_why(
        self, api_client, source_run, gateway
    ):
        fix_run = _armed(_fix_run(source_run))

        response = self._complete(
            api_client, fix_run, stream=True, HTTP_ACCEPT="text/event-stream"
        )

        assert (response.status_code, response.json()["error"]) == (
            400,
            "fix_model_stream_unsupported",
        )
        gateway.assert_not_called()

    def test_an_unreachable_gateway_is_retryable(self, api_client, source_run, gateway):
        fix_run = _armed(_fix_run(source_run))
        gateway.side_effect = httpx.ConnectError("refused")

        response = self._complete(api_client, fix_run)

        assert response.status_code == 502
        assert response.json()["error"] == "fix_model_gateway_unreachable"
        assert response.json()["retryable"] is True

    def test_a_replaced_sandbox_s_token_is_refused(
        self, api_client, source_run, gateway
    ):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(
            api_client,
            "post",
            fix_run,
            "v1/chat/completions",
            token="token-0",
            data=json.dumps({"model": ENGINE_MODEL}),
            content_type="application/json",
        )

        assert (response.status_code, response.json()["error"]) == (
            401,
            "authentication_invalid",
        )
        gateway.assert_not_called()


class TestUserApi:
    def test_without_the_entitlement_nothing_starts(
        self, auth_client, source_run, entitled
    ):
        entitled.side_effect = FeatureUnavailable(EEFeature.FIX_MY_AGENT)

        response = auth_client.post(f"{BASE}/", _launch_body(source_run), format="json")

        assert response.status_code == 402
        assert entitled.call_args.args == (EEFeature.FIX_MY_AGENT,)
        assert not AgentFixRun.no_workspace_objects.exists()

    def test_out_of_ai_credits_nothing_starts(self, auth_client, source_run, temporal):
        decision = _out_of_credits().check_result.model_dump(mode="json")
        with patch(
            "simulate.services.harness_usage.check_harness_action",
            return_value=decision,
        ) as usage:
            response = auth_client.post(
                f"{BASE}/", _launch_body(source_run), format="json"
            )

        assert response.status_code == 402, response.content
        assert response.json()["error_code"] == "BUDGET_PAUSED"
        assert usage.call_args.args == (str(source_run.organization_id), "agent_fix")
        assert not AgentFixRun.no_workspace_objects.exists()
        temporal.start.assert_not_called()

    def test_starts_a_fix_from_a_finished_run(
        self, auth_client, source_run, temporal, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                f"{BASE}/", _launch_body(source_run), format="json"
            )

        assert response.status_code == 201, response.content
        body = response.json()
        assert (body["state"], body["source_run_id"]) == (
            "queued",
            str(source_run.test_execution_id),
        )
        assert body["request"]["failing_sub_goals"] == ["refunded"]
        temporal.start.assert_called_once_with(body["id"])

    @pytest.mark.parametrize(
        "overrides",
        [
            {"budget_credits": 0},
            {"surfaces": ["everything"]},
            {"target_scenario_keys": []},
            {"unexpected": True},
        ],
        ids=["no-budget", "unknown-surface", "no-targets", "unknown-field"],
    )
    def test_a_malformed_request_starts_nothing(
        self, auth_client, source_run, overrides
    ):
        response = auth_client.post(
            f"{BASE}/", _launch_body(source_run, **overrides), format="json"
        )

        assert response.status_code == 400
        assert not AgentFixRun.no_workspace_objects.exists()

    def test_launch_options_prefill_the_form_from_the_run(
        self, auth_client, environment, source_run
    ):
        response = auth_client.get(
            f"{BASE}/launch-options/",
            {"source_run_id": str(source_run.test_execution_id)},
        )

        assert response.status_code == 200, response.content
        options = response.json()
        assert [
            (one["key"], one["passed"], one["failing_sub_goals"])
            for one in options["scenarios"]
        ] == [
            (KEYS[0], False, ["refunded"]),
            (KEYS[1], True, []),
        ]
        assert options["suggested"] == {
            "target_scenario_keys": [KEYS[0]],
            "protected_scenario_keys": [KEYS[1]],
        }
        assert options["source_run"]["id"] == str(source_run.test_execution_id)
        assert options["source_run"]["number"] == 1
        assert options["environment"]["id"] == str(environment.id)
        assert options["agent"]["kind"] == "github"
        assert options["agent"]["version_id"] == str(source_run.agent_version_id)
        assert [(one["id"], one["allowed"]) for one in options["surfaces"]] == [
            ("prompt", True),
            ("tools", True),
            ("settings", True),
            ("code", True),
        ]
        assert options["defaults"] == {
            "surfaces": ["prompt"],
            "budget_credits": 1500,
            "max_minutes": 150,
        }
        assert options["limits"] == {
            "budget_credits_max": 50000,
            "max_minutes_max": 1440,
            "scenarios_max": 200,
        }
        assert options["blocked"] is None

    def test_launch_options_say_what_would_refuse_the_launch(
        self, auth_client, environment, source_run
    ):
        _fix_run(source_run)

        options = auth_client.get(
            f"{BASE}/launch-options/",
            {"source_run_id": str(source_run.test_execution_id)},
        ).json()
        launched = auth_client.post(f"{BASE}/", _launch_body(source_run), format="json")

        assert options["blocked"]["code"] == "fix_run_active"
        assert (launched.status_code, launched.json()["error"]) == (
            409,
            "fix_run_active",
        )

    def test_launch_refuses_a_part_the_agent_does_not_offer(
        self, auth_client, source_run, monkeypatch
    ):
        monkeypatch.setattr(
            VersionedSourceAgent, "surfaces", frozenset({Surface.PROMPT})
        )

        response = auth_client.post(
            f"{BASE}/",
            _launch_body(source_run, surfaces=["prompt", "code"]),
            format="json",
        )

        assert (response.status_code, response.json()["error"]) == (
            422,
            "fix_surface_not_allowed",
        )

    def test_the_read_names_the_fix_its_run_and_its_agent(
        self, auth_client, environment, source_run
    ):
        first = _fix_run(source_run, state=AgentFixRun.State.READY)
        second = _fix_run(source_run, step="search")

        older = auth_client.get(f"{BASE}/{first.id}/").json()
        newer = auth_client.get(f"{BASE}/{second.id}/").json()

        assert (older["number"], newer["number"]) == (1, 2)
        assert (older["step"], newer["step"]) == ("done", "search")
        assert newer["source_run_number"] == 1
        assert newer["agent"]["kind"] == "github"
        assert newer["agent"]["version_id"] == str(source_run.agent_version_id)
        assert newer["environment"]["default_agent_version_id"] == str(
            environment.agent_version_id
        )
        assert newer["environment"]["name"]

    def test_lists_an_environments_fixes_newest_first(
        self, auth_client, environment, source_run
    ):
        older = _fix_run(source_run, state=AgentFixRun.State.READY)
        newer = _fix_run(source_run)

        listed = auth_client.get(f"{BASE}/", {"environment_id": str(environment.id)})
        by_run = auth_client.get(f"{BASE}/", {"environment_id": str(source_run.id)})

        assert listed.status_code == 200, listed.content
        assert [one["id"] for one in listed.json()["results"]] == [
            str(newer.id),
            str(older.id),
        ]
        assert by_run.status_code == 404

    def test_fixes_list_in_pages_with_their_total(
        self, auth_client, environment, source_run
    ):
        fixes = [_fix_run(source_run, state=AgentFixRun.State.READY) for _ in range(3)]
        newest_first = [str(one.id) for one in reversed(fixes)]

        def page(**query):
            response = auth_client.get(
                f"{BASE}/", {"environment_id": str(environment.id), **query}
            )
            assert response.status_code == 200, response.content
            body = response.json()
            return body["count"], [one["id"] for one in body["results"]]

        assert page() == (3, newest_first)
        assert page(limit=2) == (3, newest_first[:2])
        assert page(limit=2, page=2) == (3, newest_first[2:])

    def test_a_fix_shows_its_limit_and_spend_in_credits(
        self, auth_client, environment, source_run, settings
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.5"
        fix_run = _fix_run(source_run, spent_usd=Decimal("0.25"))
        check = _check(fix_run, trials=2)

        def shown():
            listed = auth_client.get(
                f"{BASE}/", {"environment_id": str(environment.id)}
            ).json()
            read = auth_client.get(f"{BASE}/{fix_run.id}/").json()
            assert listed["results"] == [read]
            return read["spent_credits"], read["committed_credits"]

        # The engine's $0.25, and two unsealed check conversations held at $0.50 each.
        assert shown() == (25.0, 125.0)
        # Sealed, the check run counts at what it billed and its hold goes.
        HostedHarnessJob.no_workspace_objects.filter(id=check.id).update(
            fix_cost_usd=Decimal("0.094")
        )
        assert shown() == (34.4, 34.4)
        read = auth_client.get(f"{BASE}/{fix_run.id}/").json()
        assert read["request"]["budget_credits"] == 500.0

    def test_listing_more_fixes_and_candidates_costs_no_more_queries(
        self, auth_client, environment, source_run
    ):
        def add(candidates):
            fix_run = _fix_run(source_run, state=AgentFixRun.State.READY)
            for attempt in range(1, candidates + 1):
                AgentFixCandidate.no_workspace_objects.create(
                    fix_run=fix_run,
                    attempt=attempt,
                    agent_version_id=fix_run.base_agent_version_id,
                    patch_object_key="k",
                )

        def queries():
            with CaptureQueriesContext(connection) as captured:
                response = auth_client.get(
                    f"{BASE}/", {"environment_id": str(environment.id)}
                )
            assert response.status_code == 200, response.content
            return len(captured.captured_queries)

        add(1)
        one = queries()
        for _ in range(3):
            add(3)

        assert queries() == one

    @pytest.mark.parametrize("moved_to", ["organization", "workspace"])
    def test_another_tenants_fix_is_not_found(
        self, auth_client, user, environment, source_run, temporal, moved_to
    ):
        fix_run = _fix_run(source_run)
        if moved_to == "organization":
            elsewhere = {"organization": Organization.objects.create(name="Other org")}
        else:
            elsewhere = {
                "workspace": Workspace.objects.create(
                    name="Other", organization=user.organization, created_by=user
                )
            }
        HostedHarnessJob.no_workspace_objects.filter(
            id__in=[environment.id, source_run.id]
        ).update(**elsewhere)

        responses = [
            auth_client.get(f"{BASE}/{fix_run.id}/"),
            auth_client.get(f"{BASE}/", {"environment_id": str(environment.id)}),
            auth_client.post(f"{BASE}/{fix_run.id}/stop/", {}, format="json"),
            auth_client.post(f"{BASE}/", _launch_body(source_run), format="json"),
        ]

        assert [one.status_code for one in responses] == [404] * 4
        fix_run.refresh_from_db()
        assert fix_run.state == AgentFixRun.State.RUNNING
        temporal.cancel.assert_not_called()

    def test_stop(
        self, auth_client, source_run, temporal, django_capture_on_commit_callbacks
    ):
        fix_run = _fix_run(source_run)

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(f"{BASE}/{fix_run.id}/stop/", {}, format="json")

        assert (response.status_code, response.json()["state"]) == (200, "stopping")
        temporal.cancel.assert_called_once_with(str(fix_run.id))

    def test_retry(
        self, auth_client, source_run, temporal, django_capture_on_commit_callbacks
    ):
        failed = _fix_run(
            source_run,
            state=AgentFixRun.State.FAILED,
            failure={"code": "fix_engine_failed", "message": "m"},
        )
        assert auth_client.get(f"{BASE}/{failed.id}/").json()["retryable"] is True

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(f"{BASE}/{failed.id}/retry/", {}, format="json")

        assert response.status_code == 201, response.content
        retried = response.json()
        assert (retried["state"], retried["retried_from_id"], retried["retryable"]) == (
            "queued",
            str(failed.id),
            False,
        )
        temporal.start.assert_called_once_with(retried["id"])
        assert auth_client.get(f"{BASE}/{failed.id}/").json()["retryable"] is False

    def test_a_candidates_patch_downloads(self, auth_client, source_run, storage):
        fix_run = _fix_run(source_run)
        candidate = AgentFixCandidate.no_workspace_objects.create(
            fix_run=fix_run,
            attempt=3,
            agent_version_id=fix_run.base_agent_version_id,
            patch_object_key="agent-fix/candidate-3.patch",
        )
        storage.objects[candidate.patch_object_key] = PATCH

        response = auth_client.get(
            f"{BASE}/{fix_run.id}/candidates/{candidate.id}/patch/"
        )

        assert response.status_code == 200
        assert response.content == PATCH
        assert response["Content-Disposition"] == (
            f'attachment; filename="fix-{fix_run.id}-attempt-3.patch"'
        )

    def test_a_fix_shows_its_check_runs_and_each_candidates(
        self, auth_client, source_run
    ):
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        baseline = _check(fix_run, label="baseline")
        screen = _check(
            fix_run, label="c1-target", version_id=candidate.agent_version_id
        )

        body = auth_client.get(f"{BASE}/{fix_run.id}/").json()

        assert body["check_runs"] == [
            {
                "run_id": str(baseline.id),
                "test_execution_id": str(baseline.test_execution_id),
                "label": "baseline",
                "state": baseline.state,
            }
        ]
        [shown] = body["candidates"]
        assert [(run["run_id"], run["label"]) for run in shown["check_runs"]] == [
            (str(screen.id), "c1-target")
        ]

    def test_a_retry_shows_the_check_runs_it_reused(self, auth_client, source_run):
        failed = _fix_run(source_run)
        baseline = _check(failed, label="baseline")
        _check(failed, label="c1-target")  # Cut short, so a retry runs it again.
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(
            state="completed"
        )
        AgentFixRun.no_workspace_objects.filter(id=failed.id).update(
            state=AgentFixRun.State.FAILED
        )
        retried = _fix_run(source_run, retried_from=failed)

        body = auth_client.get(f"{BASE}/{retried.id}/").json()

        assert [(run["run_id"], run["label"]) for run in body["check_runs"]] == [
            (str(baseline.id), "baseline")
        ]

    def test_http_patch_is_not_an_action_of_a_fix(self, auth_client, source_run):
        fix_run = _fix_run(source_run)

        response = auth_client.patch(f"{BASE}/{fix_run.id}/", {}, format="json")

        assert response.status_code == 405

    def _proven(self, source_run, kind):
        """A finished fix with a proven candidate, on an agent of this source kind."""
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind=kind)
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome=AgentFixCandidate.Outcome.WINNER
        )
        close_fix_run(fix_run.id, AgentFixRun.State.READY)
        return fix_run, candidate

    def test_a_proven_candidate_is_made_the_default(
        self, auth_client, environment, source_run
    ):
        fix_run, candidate = self._proven(source_run, "archive")

        response = auth_client.post(
            f"{BASE}/{fix_run.id}/candidates/{candidate.id}/make-default/",
            {},
            format="json",
        )

        assert (response.status_code, response.json()) == (
            200,
            {"outcome": "activated"},
        )
        environment.refresh_from_db()
        assert environment.agent_version_id == candidate.agent_version_id

    def test_a_github_agent_is_not_moved_off_its_repository(
        self, auth_client, environment, source_run
    ):
        fix_run, candidate = self._proven(source_run, "github")
        before = environment.agent_version_id

        response = auth_client.post(
            f"{BASE}/{fix_run.id}/candidates/{candidate.id}/make-default/",
            {},
            format="json",
        )

        assert (response.status_code, response.json()["error"]) == (
            422,
            "fix_github_use_pull_request",
        )
        environment.refresh_from_db()
        assert environment.agent_version_id == before

    PULL = "https://github.com/acme/agent/pull/7"

    @pytest.fixture
    def github(self, tmp_path, monkeypatch, source_run):
        """The agent's repository, connected through the App, as a local bare repository
        holding the commit the run checked out."""
        work = tmp_path / "work"
        work.mkdir()
        _git(work, "init", "-q", "-b", "main")
        (work / "agent.py").write_text("PROMPT = ''\n")
        _git(work, "add", "-A")
        _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        commit = _git(work, "rev-parse", "HEAD")
        bare = tmp_path / "acme" / "agent.git"
        _git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
        _git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
        monkeypatch.setattr(
            "simulate.services.hosted_harness_gateway.GITHUB_GIT_URL",
            f"file://{tmp_path}",
        )
        monkeypatch.setattr(
            "simulate.services.agent_fix_github.GitHubAppTokenProvider.from_settings",
            lambda: SimpleNamespace(credential=lambda _: nullcontext("app-token")),
        )
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=source_run.agent_version_id
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            source_kind="github",
            source={"repository": "acme/agent", "ref": "refs/heads/main"},
            agent_payload={
                **(runtime.agent_payload or {}),
                "source_access": {"visibility": "private", "installation_id": "42"},
            },
        )
        payload = {
            **source_run.payload,
            "source": {
                "kind": "github",
                "repository": "acme/agent",
                "commit_sha": commit,
            },
        }
        HostedHarnessJob.no_workspace_objects.filter(id=source_run.id).update(
            payload=payload
        )
        return SimpleNamespace(bare=bare, commit=commit)

    def _ship(self, auth_client, fix_run, candidate, replies):
        """POST pull-request with GitHub's API answering ``replies`` in turn."""
        calls = []

        def answer(method, url, **kwargs):
            calls.append((method, url, kwargs))
            status, body = replies.pop(0)
            return SimpleNamespace(
                status_code=status, json=lambda: body, text=json.dumps(body)
            )

        with patch(
            "simulate.services.agent_fix_github.requests.request", side_effect=answer
        ):
            response = auth_client.post(
                f"{BASE}/{fix_run.id}/candidates/{candidate.id}/pull-request/",
                {},
                format="json",
            )
        return response, calls

    def test_a_github_winner_ships_as_one_pull_request(
        self, auth_client, source_run, github
    ):
        fix_run, candidate = self._proven(source_run, "github")
        branch = f"futureagi/fix-{str(fix_run.id)[:8]}-1"

        first, calls = self._ship(
            auth_client, fix_run, candidate, [(201, {"html_url": self.PULL})]
        )
        again, more = self._ship(auth_client, fix_run, candidate, [])

        assert (first.status_code, first.json()) == (
            200,
            {"pull_request_url": self.PULL},
        )
        assert (again.json(), more) == (first.json(), [])
        assert (
            _git(github.bare, "show", f"{branch}:agent.py") == "PROMPT = 'refund first'"
        )
        assert _git(github.bare, "rev-parse", f"{branch}~1") == github.commit
        [(method, url, sent)] = calls
        assert (method, url) == (
            "POST",
            "https://api.github.com/repos/acme/agent/pulls",
        )
        assert (sent["json"]["head"], sent["json"]["base"]) == (branch, "main")
        assert sent["headers"]["Authorization"] == "Bearer app-token"
        [shown] = auth_client.get(f"{BASE}/{fix_run.id}/").json()["candidates"]
        assert shown["pull_request_url"] == self.PULL

    def test_a_pull_request_github_already_has_is_found_not_duplicated(
        self, auth_client, source_run, github
    ):
        fix_run, candidate = self._proven(source_run, "github")
        self._ship(auth_client, fix_run, candidate, [(201, {"html_url": self.PULL})])
        branch = _git(
            github.bare, "rev-parse", f"futureagi/fix-{str(fix_run.id)[:8]}-1"
        )
        # The URL was not kept: the same commit is pushed again and GitHub's PR is found.
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            pull_request_url=""
        )

        response, calls = self._ship(
            auth_client,
            fix_run,
            candidate,
            [
                (422, {"message": "A pull request already exists for acme:branch."}),
                (200, [{"html_url": self.PULL}]),
            ],
        )

        assert (response.status_code, response.json()) == (
            200,
            {"pull_request_url": self.PULL},
        )
        assert [method for method, _, _ in calls] == ["POST", "GET"]
        assert (
            calls[1][2]["params"]["head"]
            == f"acme:futureagi/fix-{str(fix_run.id)[:8]}-1"
        )
        assert (
            _git(github.bare, "rev-parse", f"futureagi/fix-{str(fix_run.id)[:8]}-1")
            == branch
        )

    def test_a_branch_someone_changed_is_not_overwritten(
        self, auth_client, source_run, github, tmp_path
    ):
        fix_run, candidate = self._proven(source_run, "github")
        branch = f"futureagi/fix-{str(fix_run.id)[:8]}-1"
        other = tmp_path / "other"
        _git(tmp_path, "clone", "-q", str(github.bare), str(other))
        (other / "agent.py").write_text("PROMPT = 'mine'\n")
        _git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x")
        _git(other, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

        response, calls = self._ship(auth_client, fix_run, candidate, [])

        assert (response.status_code, response.json()["error"], calls) == (
            409,
            "fix_github_branch_changed",
            [],
        )
        assert _git(github.bare, "show", f"{branch}:agent.py") == "PROMPT = 'mine'"

    def test_a_repository_name_that_could_leave_its_path_is_refused(
        self, auth_client, source_run, github
    ):
        fix_run, candidate = self._proven(source_run, "github")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source={"repository": "acme/../agent", "ref": "refs/heads/main"})

        response, calls = self._ship(auth_client, fix_run, candidate, [])

        assert (response.status_code, response.json()["error"], calls) == (
            422,
            "github_repository_invalid",
            [],
        )

    @pytest.mark.parametrize("unconnected", ["archive", "no_installation"])
    def test_a_pull_request_needs_a_connected_repository(
        self, auth_client, source_run, github, unconnected
    ):
        fix_run, candidate = self._proven(
            source_run, "archive" if unconnected == "archive" else "github"
        )
        if unconnected == "no_installation":
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=source_run.agent_version_id
            ).update(agent_payload={"source_access": {"visibility": "public"}})

        response, calls = self._ship(auth_client, fix_run, candidate, [])

        assert (response.status_code, response.json()["error"], calls) == (
            422,
            "fix_github_not_connected",
            [],
        )


class TestCheckRunsStayOutOfTheUsersRuns:
    @pytest.fixture
    def runs(self, environment, source_run):
        """The user's run, then a fix's check run, then the user's next run."""
        check = _run(environment, [KEYS[0]], key="check", fix_run=_fix_run(source_run))
        latest = _run(environment, [KEYS[0]], key="latest")
        for run, minutes in ((source_run, 2), (check, 1), (latest, 0)):
            _done(run, ago=timedelta(minutes=minutes))
        return SimpleNamespace(first=source_run, check=check, latest=latest)

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("delete", "/simulate/test-executions/{id}/delete/", {}),
            ("post", "/simulate/test-executions/{id}/cancel/", {}),
            (
                "post",
                "/simulate/test-executions/{id}/rerun-calls/",
                {"rerun_type": "eval_only", "call_execution_ids": [str(uuid4())]},
            ),
        ],
        ids=["delete", "cancel", "rerun-calls"],
    )
    def test_a_check_run_cannot_be_acted_on_by_id(
        self, auth_client, runs, method, path, body
    ):
        execution_id = runs.check.test_execution_id

        response = getattr(auth_client, method)(
            path.format(id=execution_id), body, format="json"
        )

        assert response.status_code == 404, response.content
        assert Execution.objects.filter(id=execution_id, deleted=False).exists()

    def test_the_environments_run_count(self, environment, runs):
        annotated = annotate_for_list(
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id)
        ).get()

        assert annotated.simulation_run_count == 2
        assert environment_row(environment)["runs_count"] == 2

    def test_the_run_history(self, auth_client, environment, runs):
        response = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        )

        assert response.status_code == 200, response.content
        listed = {row["id"] for row in response.json()["results"]}
        assert {
            str(runs.first.test_execution_id),
            str(runs.latest.test_execution_id),
        } <= listed
        assert str(runs.check.test_execution_id) not in listed

    def test_the_harness_job_list(self, auth_client, runs):
        response = auth_client.get("/simulate/api/harness-jobs/")

        assert response.status_code == 200, response.content
        listed = {row["job"]["job_id"] for row in response.json()}
        assert {str(runs.first.id), str(runs.latest.id)} <= listed
        assert str(runs.check.id) not in listed

    def test_the_run_compared_against_by_default(self, runs):
        execution = Execution.objects.get(id=runs.latest.test_execution_id)

        comparison = build_run_comparison(execution, run_calls_queryset(execution))

        assert comparison["previous_execution_id"] == str(runs.first.test_execution_id)

    def test_the_runs_number(self, runs):
        latest = Execution.objects.get(id=runs.latest.test_execution_id)

        assert _execution_payload(latest, {})["ordinal"] == 2
        assert run_summaries([latest])[latest.id]["run_number"] == 2

    def test_the_run_preview(self, auth_client, environment, runs):
        with (
            patch(
                "simulate.views.preview_pagination._repeatable_read_snapshot",
                _snapshot,
            ),
            patch(
                "simulate.services.preview_pagination._read_revision_state",
                _revision_state,
            ),
        ):
            response = auth_client.get(
                f"/simulate/run-tests/{environment.run_test_id}/preview-executions/",
                {"page_size": 10},
            )

        assert response.status_code == 200, response.content
        listed = {row["id"] for row in response.json()["results"]}
        assert listed == _ids(runs.first, runs.latest)

    def test_rerunning_every_run(self, auth_client, environment, runs):
        with patch(
            "simulate.views.run_test._dispatch_repository_harness_rerun",
            return_value={"status": {}},
        ):
            response = auth_client.post(
                f"/simulate/run-tests/{environment.run_test_id}/rerun-test-executions/",
                {"rerun_type": "call_and_eval", "select_all": True},
                format="json",
            )

        assert response.status_code == 200, response.content
        rerun = set(response.json()["results"][0]["successful_reruns"])
        assert rerun == _ids(runs.first, runs.latest)

    def test_deleting_runs(self, auth_client, environment, runs):
        url = f"/simulate/run-tests/{environment.run_test_id}/delete-test-executions/"

        by_id = auth_client.post(
            url,
            {"test_execution_ids": [str(runs.check.test_execution_id)]},
            format="json",
        )
        every = auth_client.post(url, {"select_all": True}, format="json")

        assert by_id.status_code == 400, by_id.content
        assert every.status_code == 200, every.content
        assert set(every.json()["deleted_ids"]) == _ids(runs.first, runs.latest)
        assert Execution.objects.filter(id=runs.check.test_execution_id).exists()

    def test_new_evals_on_every_run(self, auth_client, environment, runs):
        evaluation = _mapped_eval(environment)

        with patch(
            "simulate.views.run_test.regrade_run_evals", return_value=1
        ) as grade:
            response = auth_client.post(
                f"/simulate/run-tests/{environment.run_test_id}/run-new-evals/",
                {"select_all": True, "eval_config_ids": [str(evaluation.id)]},
                format="json",
            )

        assert response.status_code == 200, response.content
        assert set(grade.call_args.args[1]) == _ids(runs.first, runs.latest)


def _ids(*runs):
    return {str(run.test_execution_id) for run in runs}


@contextmanager
def _snapshot(deadline):
    """The preview's snapshot without its isolation switch, which PostgreSQL refuses
    inside the transaction a test runs in."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT txid_current_snapshot()::text, CURRENT_TIMESTAMP")
        row = cursor.fetchone()
    yield row


def _revision_state(**kwargs):
    """The preview's revision with its rows counted but always visible: the test's own
    uncommitted rows can sit past the snapshot's xmax, so their visibility means nothing."""
    _parent_visible, physical_count, _all_visible = _read_revision_state(**kwargs)
    return True, physical_count, True
