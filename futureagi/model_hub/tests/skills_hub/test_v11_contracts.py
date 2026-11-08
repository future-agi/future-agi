"""Synthetic contract/data tests; execute only on the labelled cloud test VM."""

from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication, generate_encrypted_message
from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v11 import (
    HubSkillAssistProposal,
    HubSkillGitHubPreview,
    HubSkillJob,
    HubSkillModelCall,
    HubSkillTestAttempt,
    HubSkillTestEvidence,
    HubSkillTestRun,
    HubSkillTestSuite,
)
from model_hub.selectors import skills_hub_v11 as selectors
from model_hub.serializers import skills_hub_v11 as s
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.limits import (
    effective_limits,
    validate_competitors,
)
from model_hub.services.skills_hub.v11.principals import (
    AuthenticatedRequest,
    principal_from_request,
    require_private_principal,
    require_scope_access,
    require_skill_admin,
)
from model_hub.services.skills_hub.v11.suites import (
    get_suite,
    save_suite,
    validate_cases,
    validate_json_schema,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

pytestmark = pytest.mark.django_db
DIGEST = "sha256:" + "a" * 64
FINGERPRINT = "b" * 64


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )


@pytest.fixture
def case():
    return {
        "id": str(uuid4()),
        "title": "Boundary request",
        "source_group_id": "synthetic-policy-1",
        "prompt": "Return the supplied value.",
        "fixtures": [],
        "expected_trigger": True,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "json_equals",
                "critical": True,
                "config": {"pointer": "/eligible", "value": False},
            }
        ],
        "reviewed": True,
    }


@pytest.fixture
def configuration(case):
    return {
        "candidate": {"kind": "draft", "revision": 1, "digest": DIGEST},
        "cases": {"kind": "ad_hoc", "cases": [case]},
        "baselines": ["without_target"],
        "competitors": [],
        "model": {
            "model_id": "synthetic-model",
            "temperature": 0,
            "max_output_tokens": 1024,
        },
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 150000, "max_estimated_cost_usd": "1.00"},
        "purpose": "regression",
        "parent_run_id": None,
    }


@pytest.fixture
def make_job(principal, hub_skill):
    def make(*, kind="test", owner=None, skill=hub_skill, scope=None, **kwargs):
        scope = scope or principal.scope
        ident = uuid4()
        return HubSkillJob.objects.create(
            id=ident,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            skill=skill,
            owner_user_id=owner or principal.user_id,
            kind=kind,
            idempotency_key=uuid4(),
            request_digest=DIGEST,
            workflow_id=f"skills-job-{ident}",
            deadline_at=timezone.now() + timedelta(minutes=5),
            **kwargs,
        )

    return make


@pytest.fixture
def make_run(make_job, configuration):
    def make(*, visibility="private", owner=None, **kwargs):
        job = make_job(owner=owner)
        return HubSkillTestRun.objects.create(
            job=job,
            visibility=visibility,
            profile="quick",
            repetitions=1,
            harness_version="historic-harness",
            adapter_version="historic-adapter",
            evaluator_version="historic-evaluator",
            execution_configuration={
                "model": configuration["model"],
                "judge": None,
                "harness_version": "historic-harness",
                "adapter_version": "historic-adapter",
                "evaluator_version": "historic-evaluator",
            },
            plan_fingerprint=FINGERPRINT,
            execution_fingerprint=DIGEST,
            candidate_digest=DIGEST,
            validity="reviewed",
            selected_case_ids=[configuration["cases"]["cases"][0]["id"]],
            candidate={
                "skill_id": str(job.skill_id),
                "version_id": None,
                "version": None,
                "draft_revision": 1,
                "digest": DIGEST,
                "execution_manifest_digest": DIGEST,
            },
            snapshot={
                "cases": configuration["cases"]["cases"],
                "configuration": configuration,
            },
            **kwargs,
        )

    return make


def test_exact_limits_are_shared_by_options_and_preview():
    value = effective_limits()
    assert set(value) == set(s.Limits().fields)
    assert set(value["profiles"]["quick"]) == set(s.ProfileLimits().fields)
    assert value["profiles"]["quick"]["max_attempts"] == 6
    assert value["profiles"]["repeated"]["max_attempts"] == 180
    assert value["profiles"]["quick"]["max_delivered_bytes_per_attempt"] == 192 * 1024
    assert value["max_estimated_cost_usd"] == "25.00000000"
    assert s.Limits(value).data == value
    value["profiles"]["quick"]["max_cases"] = 99
    assert effective_limits()["profiles"]["quick"]["max_cases"] == 3


def test_limits_effective_deployment_overrides():
    with override_settings(
        SKILLS_TEST_LIMITS={
            "max_estimated_cost_usd": "2.5",
            "profiles": {"quick": {"max_total_tokens": 50000}},
        }
    ):
        assert effective_limits()["max_estimated_cost_usd"] == "2.50000000"
        assert effective_limits()["profiles"]["quick"]["max_total_tokens"] == 50000


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_competitors": 6},
        {"max_competitors": True},
        {"unknown": 1},
        {"profiles": {"repeated": {"repetitions": 1}}},
        {"max_estimated_cost_usd": 1.0},
        {"max_estimated_cost_usd": "NaN"},
        {"max_estimated_cost_usd": "0"},
    ],
)
def test_invalid_limit_overrides_fail_closed(overrides):
    with (
        override_settings(SKILLS_TEST_LIMITS=overrides),
        pytest.raises(ImproperlyConfigured),
    ):
        effective_limits()


def test_run_config_round_trip_preserves_typed_json(configuration):
    serializer = s.RunConfig(data=configuration)
    assert serializer.is_valid(), serializer.errors
    check = serializer.validated_data["cases"]["cases"][0]["checks"][0]
    assert check["config"]["value"] is False
    assert serializer.validated_data["limits"]["max_estimated_cost_usd"] == Decimal("1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_draft_revision", 1),
        ("organization_id", str(uuid4())),
        ("api_key", "not-a-credential"),
    ],
)
def test_unknown_request_fields_rejected(configuration, field, value):
    configuration[field] = value
    serializer = s.RunConfig(data=configuration)
    assert not serializer.is_valid()
    assert field in serializer.errors


def test_unknown_nested_fields_and_review_attribution_rejected(case):
    for field in ("reviewed_by", "origin", "exposed_to_assist"):
        serializer = s.CaseInput(data={**case, field: None})
        assert not serializer.is_valid()
        assert field in serializer.errors
    case["checks"][0]["config"]["provider_secret"] = "not-a-credential"
    serializer = s.CaseInput(data=case)
    assert not serializer.is_valid()


def test_candidate_ref_rejects_both_and_alias():
    for extra in ({"version": 1}, {"expected_draft_revision": 1}):
        serializer = s.CandidateRef(
            data={"kind": "draft", "revision": 1, "digest": DIGEST, **extra}
        )
        assert not serializer.is_valid()


@pytest.mark.parametrize(
    "path", ["/absolute", "a/../b", "a//b", "a\\b", "a.", "a/", "a/\x00"]
)
def test_delivery_checks_reject_unsafe_paths(case, path):
    case["checks"][0] = {
        "id": str(uuid4()),
        "kind": "file_delivered",
        "critical": True,
        "config": {"path": path, "complete": True},
    }
    with pytest.raises(SkillsHubError) as caught:
        validate_cases([case])
    assert caught.value.code == "invalid_test_config"


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema"},
        {"$dynamicRef": "file:///schema"},
        {"$id": "https://example.invalid/base", "$ref": "#"},
        {"type": "invented"},
    ],
)
def test_schema_external_references_and_invalid_types_rejected(schema):
    with pytest.raises(SkillsHubError):
        validate_json_schema(schema)


def test_schema_local_reference_and_depth_bounds():
    validate_json_schema(
        {"$defs": {"flag": {"type": "boolean"}}, "$ref": "#/$defs/flag"}
    )
    nested: dict = {}
    for _ in range(21):
        nested = {"properties": {"x": nested}}
    with pytest.raises(SkillsHubError):
        validate_json_schema(nested)


def test_prompt_limit_is_utf8_bytes(case):
    case["prompt"] = "é" * 8192
    validate_cases([case])
    case["prompt"] += "é"
    with pytest.raises(SkillsHubError):
        validate_cases([case])


def test_fixture_bytes_are_summed_not_per_fixture(case):
    case["fixtures"] = [
        {"id": str(uuid4()), "name": "input", "text": "a" * (24 * 1024)}
        for _ in range(2)
    ]
    validate_cases([case])
    case["fixtures"][1]["text"] += "a"
    with pytest.raises(SkillsHubError):
        validate_cases([case])


def test_suite_cas_review_identity_and_empty_revision(principal, hub_skill, case, user):
    assert get_suite(scope=principal.scope, skill_id=hub_skill.id) == {
        "revision": 0,
        "digest": None,
        "cases": [],
        "updated_at": None,
        "updated_by": None,
        "visibility": "workspace",
    }
    suite = save_suite(
        principal=principal, skill_id=hub_skill.id, revision=0, cases=[case]
    )
    original_review = suite.cases[0]["reviewed_at"]
    assert suite.cases[0]["reviewed_by"]["id"] == str(user.pk)
    assert suite.cases[0]["origin"] == "manual"
    unchanged = save_suite(
        principal=principal, skill_id=hub_skill.id, revision=1, cases=[case]
    )
    assert unchanged.cases[0]["reviewed_at"] == original_review
    case["prompt"] = "A changed request"
    case["reviewed"] = False
    edited = save_suite(
        principal=principal, skill_id=hub_skill.id, revision=2, cases=[case]
    )
    assert edited.cases[0]["reviewed_by"] is None
    assert edited.cases[0]["reviewed_at"] is None
    with pytest.raises(SkillsHubError) as caught:
        save_suite(principal=principal, skill_id=hub_skill.id, revision=2, cases=[])
    assert caught.value.code == "test_suite_conflict"
    assert caught.value.detail["revision"] == 3
    assert HubSkillTestSuite.objects.get(pk=suite.pk).cases == edited.cases
    assert (
        save_suite(
            principal=principal, skill_id=hub_skill.id, revision=3, cases=[]
        ).revision
        == 4
    )


def test_suite_reconfirmed_changes_get_new_review(principal, hub_skill, case):
    first = save_suite(
        principal=principal, skill_id=hub_skill.id, revision=0, cases=[case]
    )
    case["prompt"] += " Reconfirmed."
    second = save_suite(
        principal=principal, skill_id=hub_skill.id, revision=1, cases=[case]
    )
    assert second.cases[0]["reviewed_at"] != first.cases[0]["reviewed_at"]
    assert second.digest != first.digest


def test_suite_foreign_scope_and_archived_parent(
    principal, hub_skill, hub_other_workspace, case
):
    wrong = SkillScope(principal.scope.organization_id, hub_other_workspace.id)
    with pytest.raises(SkillsHubError) as caught:
        get_suite(scope=wrong, skill_id=hub_skill.pk)
    assert caught.value.status == 404
    hub_skill.deleted = True
    hub_skill.save(update_fields=["deleted"])
    with pytest.raises(SkillsHubError) as caught:
        save_suite(principal=principal, skill_id=hub_skill.pk, revision=0, cases=[case])
    assert caught.value.code == "resource_not_found"


def test_target_published_version_cannot_reenter_control(
    configuration, hub_skill, publish_skill
):
    version = publish_skill(hub_skill).version
    configuration["competitors"] = [
        {"skill_id": str(hub_skill.pk), "version": version.number}
    ]
    with pytest.raises(SkillsHubError) as caught:
        s.RunConfig(data=configuration, context={"skill_id": hub_skill.pk}).is_valid(
            raise_exception=True
        )
    assert caught.value.code == "invalid_test_config"
    with pytest.raises(SkillsHubError) as caught:
        validate_competitors(
            skill_id=hub_skill.pk, competitors=configuration["competitors"]
        )
    assert caught.value.status == 400


def test_start_review_is_separate_from_plan_fingerprint(configuration):
    body = {
        "configuration": configuration,
        "expected_fingerprint": FINGERPRINT,
        "acknowledge_provider_processing": True,
        "inventory_review": {
            "fingerprint": FINGERPRINT,
            "reviewed_all_files": True,
            "no_known_answer_keys": True,
            "classifications": [],
            "false_positive_secret_findings": [],
        },
    }
    serializer = s.CreateTestRunRequest(data=body)
    assert serializer.is_valid(), serializer.errors
    body["inventory_review"]["fingerprint"] = "c" * 64
    with pytest.raises(SkillsHubError) as caught:
        s.CreateTestRunRequest(data=body).is_valid(raise_exception=True)
    assert caught.value.code == "invalid_inventory_review"


def test_assist_preview_and_reviewed_creation_are_distinct(configuration):
    body = {
        "kind": "draft",
        "revision": 1,
        "expected_digest": DIGEST,
        "instruction": "Use the supplied policy.",
        "context_text": "Synthetic policy",
        "model": configuration["model"],
        "max_estimated_cost_usd": "1.00",
    }
    assert s.AssistInput(data=body).is_valid()
    assert not s.CreateAssistRequest(data=body).is_valid()
    body.update(
        expected_input_fingerprint=FINGERPRINT,
        acknowledge_provider_processing=True,
        outbound_review={
            "fingerprint": FINGERPRINT,
            "false_positive_secret_findings": [],
        },
    )
    assert s.CreateAssistRequest(data=body).is_valid()
    assert not s.AssistInput(data=body).is_valid()


def test_outbound_findings_have_auditable_locations():
    for location in (
        {
            "kind": "bundle_file",
            "skill_id": str(uuid4()),
            "digest": DIGEST,
            "path": "policy.md",
            "line": 1,
        },
        {
            "kind": "fixture",
            "case_id": str(uuid4()),
            "fixture_id": str(uuid4()),
            "line": 1,
        },
        {"kind": "assist_input", "pointer": "/context_text", "line": 1},
    ):
        serializer = s.OutboundFinding(
            data={
                "finding_id": FINGERPRINT,
                "code": "secret_suspected",
                "location": location,
                "explanation": "Value redacted.",
            }
        )
        assert serializer.is_valid(), serializer.errors


def test_github_continuation_is_exclusive():
    parent = str(uuid4())
    assert s.GitHubPreviewRequest(
        data={"parent_preview_id": parent, "path": "skills/a"}
    ).is_valid()
    assert s.GitHubPreviewRequest(
        data={"url": "https://github.com/example/repo"}
    ).is_valid()
    for extra in ({"url": "https://github.com/example/repo"}, {"ref": "main"}):
        assert not s.GitHubPreviewRequest(
            data={"parent_preview_id": parent, "path": "skills/a", **extra}
        ).is_valid()
    assert not s.GitHubPreviewRequest(
        data={"parent_preview_id": parent, "path": None}
    ).is_valid()


@pytest.mark.parametrize("kind", ["jwt", "user", "mcp", "system"])
def test_real_authentication_classifies_keys_before_private_lookup(
    kind, user, organization, workspace
):
    headers: dict[str, Any]
    if kind == "jwt":
        token = AuthToken.objects.create(
            user=user, auth_type="access", last_used_at=timezone.now()
        )
        encoded = generate_encrypted_message(
            {"user_id": str(user.pk), "id": str(token.pk)}
        )
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {encoded}",
            "HTTP_X_WORKSPACE_ID": str(workspace.pk),
        }
        key = None
    else:
        key = OrgApiKey.no_workspace_objects.create(
            name="Synthetic contract test",
            organization=organization,
            workspace=workspace,
            user=user if kind != "system" else None,
            type=kind,
        )
        headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
    request = Request(APIRequestFactory().get("/model-hub/skills/", **headers))
    request.user, request.auth = APIKeyAuthentication().authenticate(request)
    resolved = principal_from_request(cast(AuthenticatedRequest, request))
    assert resolved.kind == ("human" if kind == "jwt" else f"{kind}_key")
    assert resolved.api_key_id == (key.id if key else None)
    if kind == "system":
        assert request.user.id == user.id
        assert resolved.user_id is None
        with pytest.raises(SkillsHubError) as caught:
            require_private_principal(principal=resolved)
        assert caught.value.code == "principal_not_supported"
    else:
        assert resolved.user_id == user.pk
        require_private_principal(principal=resolved)
        require_scope_access(principal=resolved, write=True)


def test_revoked_key_and_missing_membership_stop_background_access(
    principal, user, organization, workspace
):
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic delegated key",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    delegated = SkillPrincipal(principal.scope, user.id, key.id, "user_key")
    require_scope_access(principal=delegated, write=True)
    key.enabled = False
    key.save(update_fields=["enabled"])
    with pytest.raises(SkillsHubError):
        require_scope_access(principal=delegated, write=False)
    key.enabled = True
    key.save(update_fields=["enabled"])
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(is_active=False)
    with pytest.raises(SkillsHubError):
        require_scope_access(principal=delegated, write=False)


def test_system_key_guard_precedes_even_invalid_owner_lookup(
    principal, hub_skill, django_assert_num_queries
):
    system = SkillPrincipal(principal.scope, None, uuid4(), "system_key")
    with django_assert_num_queries(0), pytest.raises(SkillsHubError) as caught:
        selectors.get_visible_run(
            principal=system, skill_id=hub_skill.id, run_id=uuid4()
        )
    assert caught.value.code == "principal_not_supported"


def test_wrong_workspace_admin_and_inactive_user_fail(
    principal, user, organization, workspace, hub_skill, hub_other_workspace
):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=1)
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=hub_other_workspace,
        role="workspace_admin",
        level=8,
        is_active=True,
    )
    with pytest.raises(SkillsHubError) as caught:
        require_skill_admin(principal=principal, skill=hub_skill)
    assert caught.value.code == "protected_label_forbidden"
    require_scope_access(principal=principal, write=False)
    with pytest.raises(SkillsHubError):
        require_scope_access(principal=principal, write=True)
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(SkillsHubError):
        require_scope_access(principal=principal, write=False)


def test_visible_runs_exclude_other_private_even_for_admin(
    principal, hub_skill, hub_other_user, make_run
):
    own = make_run()
    shared = make_run(owner=hub_other_user.id, visibility="workspace")
    private = make_run(owner=hub_other_user.id)
    visible = selectors.list_visible_runs(principal=principal, skill_id=hub_skill.pk)
    # Pagination is exercised by the selector, not by post-filtering a page.
    assert visible["count"] == 2
    assert {str(row["id"]) for row in visible["results"]} == {
        str(own.pk),
        str(shared.pk),
    }
    with pytest.raises(SkillsHubError) as caught:
        selectors.get_visible_run(
            principal=principal, skill_id=hub_skill.pk, run_id=private.pk
        )
    assert caught.value.status == 404
    with pytest.raises(SkillsHubError):
        selectors.get_visible_run(principal=principal, skill_id=uuid4(), run_id=own.pk)


def test_expiry_retains_configuration_but_refuses_payload(
    principal, hub_skill, make_run
):
    run = make_run(payload_expires_at=timezone.now() - timedelta(seconds=1))
    run.snapshot = None
    run.payload_purged_at = timezone.now()
    run.save(update_fields=["snapshot", "payload_purged_at"])
    result = selectors.get_visible_run(
        principal=principal, skill_id=hub_skill.id, run_id=run.pk
    )
    assert result.execution_configuration["harness_version"] == "historic-harness"
    with pytest.raises(SkillsHubError) as caught:
        selectors.require_run_payload(result)
    assert caught.value.code == "evidence_expired"
    assert "execution_configuration" in s.RunSummary().fields
    assert {"requested_model", "returned_model"} <= set(s.CallSummary().fields)


def test_jobs_attempts_and_calls_enforce_uniqueness(make_job, make_run, case):
    job = make_job()
    with transaction.atomic(), pytest.raises(IntegrityError):
        HubSkillJob.objects.create(
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            skill_id=job.skill_id,
            owner_user_id=job.owner_user_id,
            kind=job.kind,
            idempotency_key=job.idempotency_key,
            request_digest=DIGEST,
            workflow_id=f"skills-job-{uuid4()}",
            deadline_at=job.deadline_at,
        )
    run = make_run()
    values = {
        "run": run,
        "case_id": case["id"],
        "variant": "candidate",
        "repeat_index": 0,
        "schedule_index": 0,
    }
    HubSkillTestAttempt.objects.create(**values)
    with transaction.atomic(), pytest.raises(IntegrityError):
        HubSkillTestAttempt.objects.create(**values)
    HubSkillModelCall.objects.create(
        job=job, kind="assist", sequence=1, request_digest=DIGEST
    )
    with transaction.atomic(), pytest.raises(IntegrityError):
        HubSkillModelCall.objects.create(
            job=job, kind="assist", sequence=1, request_digest=DIGEST
        )


def test_evidence_conditional_uniqueness(principal, hub_skill, publish_skill, make_run):
    version = publish_skill(hub_skill).version
    run = make_run()
    values = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "skill": hub_skill,
        "version": version,
        "run": run,
        "candidate_digest": DIGEST,
        "execution_fingerprint": FINGERPRINT,
    }
    HubSkillTestEvidence.objects.create(**values)
    with transaction.atomic(), pytest.raises(IntegrityError):
        HubSkillTestEvidence.objects.create(**values)
    from model_hub.models.skills_hub import HubSkillLabelEvent

    first = HubSkillLabelEvent.objects.create(
        skill=hub_skill, label="staging", from_version=None, to_version=version.number
    )
    second = HubSkillLabelEvent.objects.create(
        skill=hub_skill,
        label="staging",
        from_version=version.number,
        to_version=version.number,
    )
    HubSkillTestEvidence.objects.create(**values, label_event=first)
    HubSkillTestEvidence.objects.create(**values, label_event=second)
    with transaction.atomic(), pytest.raises(IntegrityError):
        HubSkillTestEvidence.objects.create(**values, label_event=first)


def test_integrated_migration_has_real_parent_and_only_additive_v1_fields():
    migration = import_module("model_hub.migrations.0124_skills_hub_v11").Migration
    assert ("model_hub", "0123_skills_hub") in migration.dependencies
    assert not any(app.startswith("ee") for app, _ in migration.dependencies)
    additions = {
        ("hubskill", "protected_label_names"),
        ("hubskill", "release_revision"),
        ("hubskilldraft", "source"),
        ("hubskilllabelevent", "release_revision"),
    }
    observed = set()
    for operation in migration.operations:
        assert operation.__class__.__name__ not in {"RunPython", "RunSQL"}
        model_name = getattr(operation, "model_name", "").lower()
        if model_name in {
            "hubskill",
            "hubskilldraft",
            "hubskillversion",
            "hubskilllabelevent",
        }:
            assert operation.__class__.__name__ in {"AddField", "AlterField"}
            assert (model_name, operation.name) in additions
            observed.add((model_name, operation.name))
    assert observed == additions
    new_models = {
        op.name for op in migration.operations if op.__class__.__name__ == "CreateModel"
    }
    assert len(new_models) == 11


def test_migration_state_matches_registered_new_models():
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state([("model_hub", "0124_skills_hub_v11")])
    for model in (
        HubSkillJob,
        HubSkillTestRun,
        HubSkillModelCall,
        HubSkillTestEvidence,
        HubSkillAssistProposal,
        HubSkillGitHubPreview,
    ):
        historical = state.apps.get_model("model_hub", model.__name__)
        assert {f.name for f in model._meta.local_fields} == {
            f.name for f in historical._meta.local_fields
        }
    assert state.apps.get_model("model_hub", "HubSkillTestRun")._meta.pk.name == "job"


@pytest.mark.django_db(transaction=True)
def test_migration_reverses_and_preserves_legacy_policy(organization, workspace):
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("model_hub", "0123_skills_hub")])
        tables = connection.introspection.table_names()
        assert "model_hub_hubskilljob" not in tables
        assert "model_hub_hubskill" in tables
        old = executor.loader.project_state(
            [("model_hub", "0123_skills_hub")]
        ).apps.get_model("model_hub", "HubSkill")
        legacy = old.objects.create(
            name="legacy-migration",
            description="Legacy policy is retained.",
            organization_id=organization.pk,
            workspace_id=workspace.pk,
        )
    finally:
        MigrationExecutor(connection).migrate([("model_hub", "0124_skills_hub_v11")])
    assert "model_hub_hubskilljob" in connection.introspection.table_names()
    assert HubSkill.no_workspace_objects.get(pk=legacy.pk).protected_label_names == []
    fresh = HubSkill.no_workspace_objects.create(
        name="fresh-migration",
        description="New policy opts into protection.",
        organization_id=organization.pk,
        workspace_id=workspace.pk,
    )
    assert fresh.protected_label_names == ["production"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_suite_creation_has_one_revision_winner(principal, hub_skill, case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    barrier = Barrier(2)

    def save():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                saved = save_suite(
                    principal=principal, skill_id=hub_skill.pk, revision=0, cases=[case]
                )
                return saved.revision
            except SkillsHubError as error:
                return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert set(results) == {1, "test_suite_conflict"}
    assert HubSkillTestSuite.objects.filter(skill=hub_skill).count() == 1


def test_other_tenant_and_forged_attempts_do_not_disclose(
    principal,
    hub_skill,
    make_run,
    make_job,
    hub_other_org_workspace,
    hub_other_organization,
    case,
):
    run = make_run()
    wrong_job = make_job(
        scope=SkillScope(hub_other_organization.id, hub_other_org_workspace.id)
    )
    foreign = HubSkillTestRun.objects.create(
        job=wrong_job,
        profile="quick",
        candidate_digest=DIGEST,
        validity="reviewed",
        snapshot={},
    )
    with pytest.raises(SkillsHubError) as caught:
        selectors.get_visible_run(
            principal=principal, run_id=foreign.pk, skill_id=hub_skill.id
        )
    assert caught.value.code == "resource_not_found"
    other_run = make_run()
    attempt = HubSkillTestAttempt.objects.create(
        run=other_run,
        case_id=case["id"],
        variant="candidate",
        repeat_index=0,
        schedule_index=0,
    )
    with pytest.raises(SkillsHubError) as caught:
        selectors.get_attempt_detail(
            principal=principal,
            run_id=run.pk,
            skill_id=hub_skill.id,
            attempt_id=attempt.id,
        )
    assert caught.value.status == 404


def test_owned_proposals_and_previews_never_grant_admin_payload_access(
    principal, hub_skill, hub_other_user, make_job
):
    assist = HubSkillAssistProposal.objects.create(
        job=make_job(kind="assist", owner=hub_other_user.pk),
        skill=hub_skill,
        kind="draft",
        base_digest=DIGEST,
        input_fingerprint=FINGERPRINT,
    )
    preview = HubSkillGitHubPreview.objects.create(
        job=make_job(kind="github", owner=hub_other_user.pk, skill=None),
        operation="import",
    )
    for selector, kwargs in (
        (selectors.get_owned_proposal, {"proposal_id": assist.pk}),
        (selectors.get_owned_preview, {"preview_id": preview.pk}),
    ):
        with pytest.raises(SkillsHubError) as caught:
            selector(principal=principal, **kwargs)
        assert caught.value.status == 404


def test_summary_query_count_is_bounded_and_snapshot_not_selected(
    principal, hub_skill, make_run
):
    from django.test.utils import CaptureQueriesContext

    run = make_run()
    HubSkillModelCall.objects.create(
        job=run.job,
        kind="task",
        sequence=1,
        request_digest=DIGEST,
        requested_model="historic-request",
        returned_model="historic-return",
        state="completed",
        usage={"input_tokens": 11, "output_tokens": 5},
        estimated_cost_usd=Decimal("0.01"),
        reserved_estimate_usd=Decimal("0.02"),
        latency_ms=200,
        pricing_source="historic-price",
    )
    HubSkillModelCall.objects.create(
        job=run.job,
        kind="judge",
        sequence=2,
        request_digest=DIGEST,
        requested_model="historic-judge",
        state="unknown",
        reserved_estimate_usd=Decimal("0.03"),
    )
    with CaptureQueriesContext(connection) as first:
        page = selectors.list_visible_runs(principal=principal, skill_id=hub_skill.pk)
    metrics = page["results"][0]["metrics"]
    assert metrics["task_usage"]["known_total_tokens"] == 16
    assert metrics["judge_usage"]["complete"] is False
    assert Decimal(metrics["judge_cost"]["reserved_estimate_usd"]) == Decimal("0.03")
    for _ in range(8):
        make_run()
    with CaptureQueriesContext(connection) as many:
        page = selectors.list_visible_runs(principal=principal, skill_id=hub_skill.pk)
    assert len(first) == len(many)
    assert page["count"] == 9
    assert len(s.RunPage(page).data["results"]) == 9
    # IS NOT NULL is intentional; the large JSON column is never projected.
    assert all(
        '"model_hub_hubskilltestrun"."snapshot",' not in item["sql"] for item in many
    )


def test_mixed_baseline_result_filter_and_historical_model_ids(
    principal, hub_skill, make_run, case
):
    run = make_run()
    run.repetitions = 3
    run.snapshot["configuration"]["baselines"] = ["production", "without_target"]
    run.save(update_fields=["snapshot", "repetitions"])
    for variant, passes in (("candidate", 2), ("production", 3), ("without_target", 0)):
        for index in range(3):
            HubSkillTestAttempt.objects.create(
                run=run,
                case_id=case["id"],
                variant=variant,
                repeat_index=index,
                schedule_index=index,
                status="completed",
                answer="{}",
                answer_verdict="pass" if index < passes else "fail",
                routing_verdict="pass",
                delivery_verdict="not_applicable",
            )
    results = selectors.list_case_results(
        principal=principal, skill_id=hub_skill.pk, run_id=run.pk, filter="regressions"
    )
    assert results["count"] == 1
    assert results["results"][0]["change"] == "mixed"
    comparisons = results["results"][0]["answer_comparisons"]
    assert comparisons[0]["baseline"] == "production"
    assert comparisons[0]["delta_percentage_points"] < 0
    assert comparisons[1]["baseline"] == "without_target"
    assert comparisons[1]["delta_percentage_points"] > 0


def test_run_summary_uses_stored_execution_configuration_after_expiry(
    principal, hub_skill, make_run
):
    run = make_run(payload_expires_at=timezone.now() - timedelta(seconds=1))
    run.snapshot = None
    run.save(update_fields=["snapshot"])
    loaded = selectors.get_visible_run(
        principal=principal, skill_id=hub_skill.pk, run_id=run.pk
    )
    summary = selectors.run_summary(principal=principal, run=loaded)
    wire = s.RunSummary(summary).data
    assert wire["payload_expired"] is True
    assert wire["execution_configuration"]["harness_version"] == "historic-harness"
    assert wire["execution_configuration"]["model"]["model_id"] == "synthetic-model"


def test_missing_scope_and_key_workspace_mismatch(
    principal, user, organization, workspace, hub_other_workspace
):
    with pytest.raises(SkillsHubError) as caught:
        principal_from_request(SimpleNamespace(user=user))
    assert caught.value.code == "workspace_required"
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic other workspace key",
        organization=organization,
        workspace=hub_other_workspace,
        user=user,
        type="mcp",
    )
    request = SimpleNamespace(
        user=user, organization=organization, workspace=workspace, org_api_key=key
    )
    with pytest.raises(SkillsHubError):
        principal_from_request(request)


def test_domain_error_schema_retains_the_existing_envelope_and_structured_detail():
    from model_hub.views.skills_hub import domain_error

    body = domain_error(
        SkillsHubError(
            "test_suite_conflict",
            "Reload the saved examples.",
            status=409,
            detail={"revision": 7, "updated_at": None},
        )
    ).data
    serializer = s.SkillsError(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["detail"] == {"revision": 7, "updated_at": None}
    assert serializer.validated_data["code"] == "test_suite_conflict"
    assert serializer.validated_data["status"] is False


def test_typed_conflict_and_check_details_survive_generic_error_extension():
    from model_hub.serializers import skills_hub as v1

    conflict = v1.SkillsConflictError().fields["detail"]
    checks = v1.SkillsChecksError().fields["detail"]
    assert isinstance(conflict, v1.ConflictDetail)
    assert "release_revision" in conflict.fields
    assert isinstance(checks, v1.ChecksResult)
    assert {"summary", "items"} <= checks.fields.keys()


@pytest.mark.parametrize(
    "detail",
    [
        "Read the current release.",
        {"release_revision": 7},
        {"retry_at": None},
        {"reason": "archive_invalid"},
    ],
)
def test_error_details_retain_typed_text_conflict_and_rate_limit_payloads(detail):
    serializer = s.SkillsError(data={"status": False, "detail": detail})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["detail"] == detail


def test_error_detail_rejects_undocumented_or_wrongly_typed_payloads():
    for detail in [{"revision": []}, {"undocumented": True}, ["not-an-error-object"]]:
        serializer = s.SkillsError(data={"status": False, "detail": detail})
        assert not serializer.is_valid()
