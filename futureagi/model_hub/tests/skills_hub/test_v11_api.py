"""Real HTTP/authentication coverage, with only outbound adapters mocked.

This local URLConf exercises the lane without modifying integration-owned mounts.
All examples are synthetic and no provider/GitHub request is allowed in tests.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey
from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub_v11 import (
    HubSkillAssistProposal,
    HubSkillGitHubPreview,
    HubSkillJob,
    HubSkillModelCall,
    HubSkillTestAttempt,
    HubSkillTestRun,
)
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.views import skills_hub_v11 as views
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub_v11"))]
BASE = "/model-hub/skills/"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def local_routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture(params=["jwt", "user", "mcp"])
def client(request, user, organization, workspace):
    client = APIClient()
    if request.param == "jwt":
        token = AuthToken.objects.create(
            user=user, auth_type="access", last_used_at=timezone.now()
        )
        value = generate_encrypted_message(
            {"user_id": str(user.pk), "id": str(token.pk)}
        )
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {value}", HTTP_X_WORKSPACE_ID=str(workspace.pk)
        )
    else:
        key = OrgApiKey.objects.create(
            name="Synthetic Skills reader",
            api_key=f"test-{uuid4()}",
            secret_key=f"test-{uuid4()}",
            type=request.param,
            user=user,
            organization=organization,
            workspace=workspace,
        )
        client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def result(response, status=200):
    assert response.status_code == status, getattr(response, "data", response.content)
    assert response.data["status"] is True
    return response.data["result"]


def error(response, status, code):
    assert response.status_code == status, getattr(response, "data", response.content)
    assert response.data["status"] is False
    assert response.data["code"] == code
    return response.data


def case():
    return {
        "id": str(uuid4()),
        "title": "Synthetic amount",
        "source_group_id": "manual-example",
        "prompt": "Return the supplied amount.",
        "fixtures": [],
        "expected_trigger": True,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "json_equals",
                "critical": True,
                "config": {"pointer": "/amount", "value": 3},
            }
        ],
        "reviewed": True,
        "reviewed_by": None,
        "reviewed_at": None,
        "origin": "manual",
        "exposed_to_assist": False,
    }


@pytest.fixture
def make_job(organization, workspace, user):
    def make(*, kind="test", skill=None, owner=None, status="queued", **kwargs):
        return HubSkillJob.objects.create(
            organization=organization,
            workspace=workspace,
            skill=skill,
            owner_user=owner or user,
            principal_kind="human",
            kind=kind,
            idempotency_key=uuid4(),
            request_digest=DIGEST,
            workflow_id=f"skills-job-{uuid4()}",
            deadline_at=timezone.now() + timedelta(minutes=5),
            status=status,
            **kwargs,
        )

    return make


@pytest.fixture
def make_run(make_job, hub_skill, make_skill_files):
    def make(*, owner=None, visibility="private", status="queued"):
        files = [
            {
                **item,
                "sha256": file_sha256(item["content"]),
                "size_bytes": len(item["content"].encode()),
            }
            for item in make_skill_files()
        ]
        digest = manifest_digest(files)
        bundle = {
            "skill_id": str(hub_skill.pk),
            "version_id": None,
            "version": None,
            "draft_revision": 1,
            "digest": digest,
            "execution_manifest_digest": digest,
            "name": hub_skill.name,
            "description": hub_skill.description,
            "files": files,
        }
        identity = {
            key: bundle[key]
            for key in (
                "skill_id",
                "version_id",
                "version",
                "draft_revision",
                "digest",
                "execution_manifest_digest",
            )
        }
        execution = {
            "model": {
                "model_id": "historical-fixture-model",
                "temperature": 0,
                "max_output_tokens": 100,
            },
            "judge": None,
            "harness_version": "frozen-harness",
            "adapter_version": "frozen-adapter",
            "evaluator_version": "frozen-evaluator",
        }
        example = case()
        run = HubSkillTestRun.objects.create(
            job=make_job(skill=hub_skill, owner=owner, status=status),
            profile="quick",
            visibility=visibility,
            repetitions=1,
            harness_version="frozen-harness",
            adapter_version="frozen-adapter",
            evaluator_version="frozen-evaluator",
            execution_configuration=execution,
            plan_fingerprint=DIGEST,
            execution_fingerprint=DIGEST,
            candidate_digest=digest,
            candidate=identity,
            selected_case_ids=[example["id"]],
            validity="reviewed",
            payload_expires_at=timezone.now() + timedelta(days=30),
            snapshot={
                "plan_fingerprint": DIGEST,
                "cases": [example],
                "candidate": bundle,
                "production": None,
                "competitors": [],
                "configuration": {"baselines": ["without_target"]},
            },
        )
        for index, variant in enumerate(["candidate", "without_target"]):
            HubSkillTestAttempt.objects.create(
                run=run,
                case_id=example["id"],
                variant=variant,
                repeat_index=0,
                schedule_index=index,
            )
        return run

    return make


def test_suite_read_save_conflict_and_unknown_fields(client, hub_skill, user):
    url = f"{BASE}{hub_skill.pk}/tests/"
    assert result(client.get(url)) == {
        "revision": 0,
        "digest": None,
        "cases": [],
        "updated_at": None,
        "updated_by": None,
        "visibility": "workspace",
    }
    example = {
        key: value
        for key, value in case().items()
        if key not in {"reviewed_by", "reviewed_at", "origin", "exposed_to_assist"}
    }
    # A competing later save must not replace this operation's acknowledgment.
    with patch.object(
        views.suites,
        "get_suite",
        side_effect=AssertionError("Do not re-read after saving"),
    ):
        saved = result(
            client.put(url, {"revision": 0, "cases": [example]}, format="json")
        )
    assert saved["revision"] == 1
    assert saved["cases"][0]["reviewed_by"]["id"] == str(user.pk)
    error(
        client.put(url, {"revision": 0, "cases": []}, format="json"),
        409,
        "test_suite_conflict",
    )
    error(
        client.put(
            url,
            {
                "revision": 1,
                "cases": [{**example, "reviewed_by": {"id": str(uuid4())}}],
            },
            format="json",
        ),
        400,
        "invalid_test_config",
    )


def test_compact_run_list_counts_only_visible_and_stores_historical_configuration(
    client, make_run, hub_other_user
):
    own = make_run()
    make_run(owner=hub_other_user)
    shared = make_run(owner=hub_other_user, visibility="workspace")
    page = result(client.get(f"{BASE}{own.job.skill_id}/test-runs/?page_size=1"))
    assert page["count"] == 2 and page["total_pages"] == 2 and len(page["results"]) == 1
    assert page["results"][0]["id"] == str(shared.pk)
    value = result(client.get(f"{BASE}{own.job.skill_id}/test-runs/{own.pk}/"))
    assert (
        value["execution_configuration"]["model"]["model_id"]
        == "historical-fixture-model"
    )
    assert value["counts"]["planned"] == 2
    assert "snapshot" not in value and "answer" not in value
    assert value["decision"]["state"] == "inconclusive"


def test_attempt_read_file_membership_and_expiry(client, make_run):
    run = make_run()
    attempt = run.hub_skill_attempts.first()
    call = HubSkillModelCall.objects.create(
        job=run.job,
        attempt=attempt,
        kind="task",
        sequence=1,
        claim_generation=0,
        state="completed",
        request_digest=DIGEST,
        requested_model="requested-fixture",
        returned_model="returned-revision",
        reserved_tokens=10,
        reserved_estimate_usd="0.01",
        usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        estimated_cost_usd="0.001",
        assistant_message={"content": "Synthetic answer"},
    )
    base = f"{BASE}{run.job.skill_id}/test-runs/{run.pk}/"
    value = result(client.get(base + f"attempts/{attempt.pk}/"))
    assert value["calls"][0]["id"] == str(call.pk)
    assert value["calls"][0]["requested_model"] == "requested-fixture"
    assert value["calls"][0]["returned_model"] == "returned-revision"
    assert value["metrics"]["task_usage"]["known_total_tokens"] == 5
    query = {
        "snapshot_skill_id": str(run.job.skill_id),
        "variant": "candidate",
        "path": "SKILL.md",
        "max_bytes": 8,
    }
    chunk = result(client.get(base + "files/", query))
    assert chunk["skill_id"] == str(run.job.skill_id) and chunk["next_offset"] == 8
    assert chunk["text"] == run.snapshot["candidate"]["files"][0]["content"][:8]
    error(
        client.get(base + "files/", {**query, "variant": "without_target"}),
        404,
        "resource_not_found",
    )
    error(
        client.get(base + "files/", {**query, "snapshot_skill_id": str(uuid4())}),
        404,
        "resource_not_found",
    )
    error(
        client.get(base + "files/", {**query, "offset": "no"}),
        400,
        "invalid_test_config",
    )
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    assert result(client.get(base))["payload_expired"] is True
    error(client.get(base + f"attempts/{attempt.pk}/"), 410, "evidence_expired")
    error(client.get(base + "files/", query), 410, "evidence_expired")


def test_parent_mismatch_and_foreign_private_ids_are_not_found(
    client, make_run, hub_other_user
):
    private = make_run(owner=hub_other_user)
    error(
        client.get(f"{BASE}{private.job.skill_id}/test-runs/{private.pk}/"),
        404,
        "resource_not_found",
    )
    own = make_run()
    error(client.get(f"{BASE}{uuid4()}/test-runs/{own.pk}/"), 404, "resource_not_found")
    error(
        client.post(
            f"{BASE}{uuid4()}/test-runs/{own.pk}/share/",
            {"acknowledge_snapshot_sharing": True},
            format="json",
        ),
        404,
        "resource_not_found",
    )
    error(
        client.get(f"{BASE}{own.job.skill_id}/test-runs/{own.pk}/attempts/{uuid4()}/"),
        404,
        "resource_not_found",
    )


def test_sharing_requires_explicit_acknowledgment_and_owner(
    client, make_run, hub_other_user
):
    own = make_run()
    base = f"{BASE}{own.job.skill_id}/test-runs/"
    error(
        client.post(base + f"{own.pk}/share/", {}, format="json"),
        400,
        "invalid_test_config",
    )
    value = result(
        client.post(
            base + f"{own.pk}/share/",
            {"acknowledge_snapshot_sharing": True},
            format="json",
        )
    )
    assert value["visibility"] == "workspace" and value["shared_at"]
    other = make_run(owner=hub_other_user, visibility="workspace")
    error(
        client.post(
            base + f"{other.pk}/share/",
            {"acknowledge_snapshot_sharing": True},
            format="json",
        ),
        404,
        "resource_not_found",
    )


def test_admin_cancel_returns_no_private_payload(client, make_run, hub_other_user):
    other = make_run(owner=hub_other_user)
    url = f"{BASE}{other.job.skill_id}/test-runs/{other.pk}/cancel/"
    value = result(client.post(url, {}, format="json"), 202)
    assert value == {"id": str(other.pk), "status": "cancelling"}
    other.job.status = "cancelled"
    other.job.save(update_fields=["status"])
    assert result(client.post(url, {}, format="json")) == {
        "id": str(other.pk),
        "status": "cancelled",
    }


def test_options_are_local_and_return_effective_limits(client, settings):
    settings.SKILLS_TEST_LIMITS = {
        "max_estimated_cost_usd": "2.50",
        "profiles": {"quick": {"max_cases": 2}},
    }
    with patch.object(views.model_calls, "list_test_models", return_value=[]):
        value = result(client.get(BASE + "testing-options/"))
    assert value["limits"]["max_estimated_cost_usd"] == "2.50000000"
    assert value["limits"]["profiles"]["quick"]["max_cases"] == 2
    assert value["limits"]["profiles"]["repeated"]["max_cases"] == 20


# Exercise permissions before body validation and resource lookup for every route.
ALL_REQUESTS = [
    ("get", "testing-options/"),
    ("post", "github/previews/"),
    ("get", "github/previews/{child}/"),
    ("post", "github/import/"),
    ("get", "{skill}/tests/"),
    ("put", "{skill}/tests/"),
    ("post", "{skill}/test-runs/preview/"),
    ("get", "{skill}/test-runs/"),
    ("post", "{skill}/test-runs/"),
    ("get", "{skill}/test-runs/{child}/"),
    ("get", "{skill}/test-runs/{child}/results/"),
    ("get", "{skill}/test-runs/{child}/attempts/{other}/"),
    ("get", "{skill}/test-runs/{child}/files/"),
    ("post", "{skill}/test-runs/{child}/cancel/"),
    ("post", "{skill}/test-runs/{child}/share/"),
    ("get", "{skill}/test-evidence/"),
    ("post", "{skill}/assist/preview/"),
    ("post", "{skill}/assist/"),
    ("get", "{skill}/assist/{child}/"),
    ("post", "{skill}/assist/{child}/apply/"),
    ("post", "{skill}/assist/{child}/cancel/"),
    ("get", "{skill}/source/"),
    ("post", "{skill}/source/check-updates/"),
    ("post", "{skill}/source/stage-update/"),
    ("get", "{skill}/label-protection/"),
    ("put", "{skill}/label-protection/"),
    ("get", "{skill}/label-protection/history/"),
    ("get", "{skill}/adoption/"),
]


@pytest.mark.parametrize("method,route", ALL_REQUESTS)
def test_real_system_key_rejected_before_private_lookup(
    method, route, user, organization, workspace
):
    key = OrgApiKey.objects.create(
        name="Synthetic system key",
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        type="system",
        user=user,
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = getattr(client, method)(
        BASE + route.format(skill=uuid4(), child=uuid4(), other=uuid4()),
        {},
        format="json",
    )
    error(response, 403, "principal_not_supported")


@pytest.mark.parametrize("method,route", ALL_REQUESTS)
def test_missing_scope_is_forbidden_for_every_route(method, route, user):
    from django.urls import resolve

    url = BASE + route.format(skill=uuid4(), child=uuid4(), other=uuid4())
    match = resolve(url)
    request = getattr(APIRequestFactory(), method)(url, {}, format="json")
    force_authenticate(request, user=user)
    error(match.func(request, **match.kwargs), 403, "workspace_required")


def test_suite_in_other_workspace_and_archived_parent_are_hidden(
    client, hub_skill, hub_other_workspace
):
    hub_skill.workspace = hub_other_workspace
    hub_skill.save(update_fields=["workspace"])
    response = client.get(f"{BASE}{hub_skill.pk}/tests/")
    assert response.status_code == 404
    assert response.data["status"] is False


def test_run_pagination_and_results_query_are_validated(client, make_run):
    run = make_run()
    base = f"{BASE}{run.job.skill_id}/test-runs/"
    for query in ("page=zero", "page_size=101", "page=0", "unrecognized=forged"):
        error(client.get(base + "?" + query), 400, "invalid_test_config")
    error(
        client.get(base + f"{run.pk}/results/?filter=unknown"),
        400,
        "invalid_test_config",
    )
    rows = result(client.get(base + f"{run.pk}/results/"))
    assert rows["count"] == 1 and rows["results"][0]["change"] == "unmatched"


def test_job_start_requires_header_but_preview_does_not(client, hub_skill):
    body = {"url": "https://github.com/example/synthetic", "path": None, "ref": None}
    with patch.object(views.github, "create_github_preview") as create:
        error(
            client.post(BASE + "github/previews/", body, format="json"),
            400,
            "invalid_test_config",
        )
        error(
            client.post(
                BASE + "github/previews/",
                body,
                format="json",
                HTTP_IDEMPOTENCY_KEY="bad",
            ),
            400,
            "invalid_test_config",
        )
        create.assert_not_called()


def test_assist_preview_route_accepts_nonpaid_union_and_preserves_review_contract(
    client, hub_skill
):
    body = {
        "kind": "draft",
        "revision": 1,
        "expected_digest": DIGEST,
        "instruction": "Draft a synthetic policy",
        "context_text": "Approved context",
        "model": {"model_id": "fixture", "temperature": 0, "max_output_tokens": 100},
        "max_estimated_cost_usd": "1.00",
    }
    preview = {
        "input_fingerprint": DIGEST,
        "provider_disclosure": {"provider": "fixture"},
        "allowance": {
            "input_bytes": 100,
            "max_input_bytes": 65536,
            "max_total_tokens": 300,
            "max_output_tokens": 100,
            "max_output_bytes": 65536,
            "max_files": 3,
            "deadline_seconds": 60,
        },
        "estimate": {"upper_bound_usd": "0.1", "pricing_source": "fixture"},
        "outbound_findings": [],
    }
    with patch.object(
        views.assist, "preview_assist_proposal", return_value=preview
    ) as service:
        value = result(
            client.post(f"{BASE}{hub_skill.pk}/assist/preview/", body, format="json")
        )
        assert (
            value["input_fingerprint"] == DIGEST
            and value["estimate"]["upper_bound_usd"] == "0.10000000"
        )
        assert service.call_args.kwargs["skill_id"] == hub_skill.pk
    error(
        client.post(
            f"{BASE}{hub_skill.pk}/assist/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        ),
        400,
        "invalid_test_config",
    )
    error(
        client.post(
            f"{BASE}{hub_skill.pk}/assist/preview/",
            {**body, "expected_draft_revision": 1},
            format="json",
        ),
        400,
        "invalid_test_config",
    )


def test_github_continuation_is_not_a_new_branch_resolution(client, make_job):
    job = make_job(kind="github")
    parent = uuid4()
    with patch.object(
        views.github, "create_github_preview", return_value=SimpleNamespace(job=job)
    ) as create:
        value = result(
            client.post(
                BASE + "github/previews/",
                {"parent_preview_id": str(parent), "path": "skills/example"},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            ),
            202,
        )
        assert value["id"] == str(job.pk)
        assert create.call_args.kwargs["request"] == {
            "parent_preview_id": parent,
            "path": "skills/example",
        }
    error(
        client.post(
            BASE + "github/previews/",
            {"parent_preview_id": str(parent), "path": "skills/example", "ref": "main"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        ),
        400,
        "invalid_test_config",
    )


def test_published_target_cannot_return_as_competitor(client, hub_skill, publish_skill):
    version = publish_skill(hub_skill).version
    example = {
        key: value
        for key, value in case().items()
        if key not in {"reviewed_by", "reviewed_at", "origin", "exposed_to_assist"}
    }
    configuration = {
        "candidate": {
            "kind": "version",
            "version": version.number,
            "digest": version.digest,
        },
        "cases": {"kind": "ad_hoc", "cases": [example]},
        "baselines": ["without_target"],
        "competitors": [{"skill_id": str(hub_skill.pk), "version": version.number}],
        "model": {
            "model_id": "not-contacted",
            "temperature": 0,
            "max_output_tokens": 100,
        },
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 10000, "max_estimated_cost_usd": "1.00"},
        "purpose": "regression",
        "parent_run_id": None,
    }
    base = f"{BASE}{hub_skill.pk}/test-runs/"
    error(
        client.post(base + "preview/", configuration, format="json"),
        400,
        "invalid_test_config",
    )
    body = {
        "configuration": configuration,
        "expected_fingerprint": DIGEST,
        "acknowledge_provider_processing": True,
        "inventory_review": {
            "fingerprint": DIGEST,
            "reviewed_all_files": True,
            "no_known_answer_keys": True,
            "classifications": [],
            "false_positive_secret_findings": [],
        },
    }
    error(
        client.post(base, body, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())),
        400,
        "invalid_test_config",
    )
    assert not HubSkillJob.objects.exists()


def test_private_assist_and_preview_reads_parent_matching_and_expiry(
    client, make_job, hub_skill, hub_other_user
):
    own = HubSkillAssistProposal.objects.create(
        job=make_job(kind="assist", skill=hub_skill),
        skill=hub_skill,
        kind="draft",
        base_revision=1,
        base_digest=DIGEST,
        input_fingerprint=DIGEST,
        prompt_version="fixture",
        proposal={"files": [], "description": None, "summary": None},
    )
    url = f"{BASE}{hub_skill.pk}/assist/{own.pk}/"
    assert result(client.get(url))["base_revision"] == 1
    error(client.get(f"{BASE}{uuid4()}/assist/{own.pk}/"), 404, "resource_not_found")
    own.payload_expires_at = timezone.now() - timedelta(seconds=1)
    own.save(update_fields=["payload_expires_at"])
    assert result(client.get(url))["payload_expired"] is True
    assert result(client.get(url))["proposal"] is None
    foreign = HubSkillAssistProposal.objects.create(
        job=make_job(kind="assist", skill=hub_skill, owner=hub_other_user),
        skill=hub_skill,
        kind="summary",
        base_digest=DIGEST,
        input_fingerprint=DIGEST,
        prompt_version="fixture",
    )
    error(
        client.get(f"{BASE}{hub_skill.pk}/assist/{foreign.pk}/"),
        404,
        "resource_not_found",
    )
    preview = HubSkillGitHubPreview.objects.create(
        job=make_job(kind="github"),
        operation="import",
        repo="example/synthetic",
        path="",
        ref="main",
        ref_kind="branch",
    )
    value = result(client.get(f"{BASE}github/previews/{preview.pk}/"))
    assert (
        value["source"]["ref"] == "main"
        and value["bundle"] is None
        and value["can_stage"] is False
    )
    preview.job.owner_user = hub_other_user
    preview.job.save(update_fields=["owner_user"])
    error(client.get(f"{BASE}github/previews/{preview.pk}/"), 404, "resource_not_found")


def test_initial_import_route_never_accepts_update_preview(client, make_job, hub_skill):
    preview = HubSkillGitHubPreview.objects.create(
        job=make_job(kind="github", skill=hub_skill), operation="update"
    )
    with patch.object(views.github, "stage_github_preview") as stage:
        error(
            client.post(
                BASE + "github/import/",
                {
                    "preview_id": str(preview.pk),
                    "rights": {"confirmed": True, "review_note": "Synthetic"},
                },
                format="json",
            ),
            404,
            "resource_not_found",
        )
        stage.assert_not_called()


def test_viewer_can_read_shared_but_cannot_write_or_cancel(
    client, make_run, organization, workspace, user
):
    run = make_run(visibility="workspace")
    OrganizationMembership.no_workspace_objects.filter(
        organization=organization, user=user
    ).update(level=Level.WORKSPACE_VIEWER)
    WorkspaceMembership.no_workspace_objects.filter(
        workspace=workspace, user=user
    ).update(level=Level.WORKSPACE_VIEWER)
    value = result(client.get(f"{BASE}{run.job.skill_id}/test-runs/{run.pk}/"))
    assert value["visibility"] == "workspace"
    response = client.post(
        f"{BASE}{run.job.skill_id}/test-runs/{run.pk}/cancel/", {}, format="json"
    )
    assert response.status_code == 403
    run.job.refresh_from_db()
    assert run.job.status == "queued"


def test_domain_conflict_preserves_structured_detail(client, hub_skill):
    from model_hub.services.skills_hub.errors import SkillsHubError

    with patch.object(
        views.suites,
        "save_suite",
        side_effect=SkillsHubError(
            "test_suite_conflict",
            "Changed.",
            status=409,
            detail={"revision": 7, "updated_at": None},
        ),
    ):
        value = error(
            client.put(
                f"{BASE}{hub_skill.pk}/tests/",
                {"revision": 1, "cases": []},
                format="json",
            ),
            409,
            "test_suite_conflict",
        )
    assert value["detail"] == {"revision": 7, "updated_at": None}
    assert all(key in value for key in ("type", "code", "message", "error", "result"))


def test_openapi_exposes_all_new_routes_and_preflight_without_paid_header():
    from drf_yasg import openapi
    from drf_yasg.generators import OpenAPISchemaGenerator

    schema = OpenAPISchemaGenerator(
        info=openapi.Info(title="Skills contracts", default_version="v1.1"),
        patterns=urlpatterns,
    ).get_schema(request=None, public=True)
    assert len(schema.paths) == 25
    paths = schema.paths
    assist_preview_path = next(
        name for name in paths if name.endswith("/assist/preview/")
    )
    assist_create_path = next(name for name in paths if name.endswith("/assist/"))
    assert not any(
        parameter["name"] == "Idempotency-Key"
        for parameter in paths[assist_preview_path]["post"].parameters
    )
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["required"]
        for parameter in paths[assist_create_path]["post"].parameters
    )
    # The shared Axios contract checker consumes these extensions. A plain
    # JSONField is emitted as object-only and rejects scalar JSON assertions
    # or the standard string authentication detail before the hook sees it.
    detail = schema.definitions["SkillsHubV11Error"]["properties"]["detail"]
    assert detail["x-anyOf"] == [
        {"type": "string"},
        {"$ref": "#/definitions/SkillsHubConflictDetail"},
        {"$ref": "#/definitions/SkillsHubChecksResult"},
    ]
    for definition, field in (
        ("SkillsHubV11CheckConfig", "value"),
        ("SkillsHubV11CheckResult", "expected"),
        ("SkillsHubV11CheckResult", "actual"),
    ):
        value = schema.definitions[definition]["properties"][field]
        assert value.get("x-json-value"), (definition, field, value)
    limits = schema.definitions["SkillsHubV11Limits"]["properties"]
    assert limits["max_estimated_cost_usd"]["type"] == "string"
    assert "profiles" in limits and "max_queue_wait_seconds" in limits
    assert (
        "execution_configuration"
        in schema.definitions["SkillsHubV11RunSummary"]["properties"]
    )
    assert (
        "requested_model" in schema.definitions["SkillsHubV11CallSummary"]["properties"]
    )
    assert (
        "parent_preview_id"
        in schema.definitions["SkillsHubV11GitHubPreviewRequest"]["properties"]
    )


def test_domain_error_contract_accepts_structured_conflict_detail():
    serializer = views.SkillTestSuiteView.put._swagger_auto_schema["responses"][409]
    payload = views.invalid_request({"cases": ["Invalid."]}).data
    payload.update(
        code="test_suite_conflict", detail={"revision": 7, "updated_at": None}
    )
    declared = serializer(data=payload)
    assert declared.is_valid(), declared.errors


def test_every_handler_has_success_errors_and_runtime_validation_annotations():
    from model_hub.urls_skills_hub_v11 import urlpatterns

    assert len(urlpatterns) == 25
    for route in urlpatterns:
        assert hasattr(route.callback, "view_class")
        cls = route.callback.view_class
        for method in ("get", "post", "put"):
            if not hasattr(cls, method):
                continue
            handler = getattr(cls, method)
            annotation = handler._swagger_auto_schema
            if method != "get" or "query_serializer" in annotation:
                assert annotation["runtime_request_validation"]
            assert annotation["runtime_response_validation"]
            assert {403, 404, 409, 410, 422, 428, 429, 503} <= set(
                annotation["responses"]
            )
