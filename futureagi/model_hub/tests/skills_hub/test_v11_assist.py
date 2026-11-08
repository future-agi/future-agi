"""Synthetic assistance coverage. Provider transport is always mocked."""

import json
from copy import deepcopy

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.assist import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    _outbound_findings,
    _request_input,
    _validate_review,
    replace_description,
    validate_assist_output,
)


@pytest.fixture
def assist_files():
    return [
        {
            "path": "SKILL.md",
            "content": "---\nname: help-desk\n# Keep this comment\ndescription: Use when a support request needs a clear next step.\nmetadata:\n  owner: support\n---\n\nDo not invent refund policy.\n",
        }
    ]


def test_description_changes_only_scalar(assist_files):
    before = deepcopy(assist_files)
    description = 'Use for refunds: explain "eligibility".\nDo not use for tracking.'
    result = replace_description(assist_files, description, name="help-desk")
    assert assist_files == before
    assert "# Keep this comment\n" in result[0]["content"]
    assert (
        "metadata:\n  owner: support\n---\n\nDo not invent refund policy.\n"
        in result[0]["content"]
    )
    assert json.dumps(description) in result[0]["content"]


@pytest.mark.parametrize(
    "scalar",
    [
        ">\n  Use when support\n  needs help.\n",
        "|-\n  Use when support needs help.\n",
        '"Use when support needs help." # Keep inline comment\n',
    ],
)
def test_description_multiline_and_quoted_scalars(scalar):
    content = f"---\nname: help-desk\ndescription: {scalar}license: MIT\n---\nBody stays exactly.\n"
    result = replace_description(
        [{"path": "SKILL.md", "content": content}],
        "Use when a support request needs a clear and accurate next step.",
        name="help-desk",
    )
    assert "license: MIT\n---\nBody stays exactly.\n" in result[0]["content"]
    if "# Keep inline comment" in content:
        assert "# Keep inline comment" in result[0]["content"]


def test_description_alias_cannot_change_unrelated_yaml():
    files: list[FileInput] = [
        {
            "path": "SKILL.md",
            "content": "---\nname: help-desk\ndescription: &shared Use when support needs help with a request.\nlicense: *shared\n---\nBody\n",
        }
    ]
    with pytest.raises(SkillsHubError, match="safe, complete"):
        replace_description(
            files,
            "Use when a support question requires accurate next steps.",
            name="help-desk",
        )


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"summary":"Note"}\n```',
        '{"summary":"A","summary":"B"}',
        '{"summary":"A","labels":["production"]}',
        '{"summary":NaN}',
        "[]",
    ],
)
def test_output_requires_exact_json_not_repair(content, assist_files):
    with pytest.raises(SkillsHubError) as error:
        validate_assist_output(
            kind="summary", content=content, name="help-desk", files=assist_files
        )
    assert error.value.code == "assist_output_invalid"


def test_draft_keeps_missing_information_visible(assist_files):
    output = validate_assist_output(
        kind="draft",
        content=json.dumps(
            {
                "files": assist_files,
                "missing_information": ["What is the approved refund period?"],
            }
        ),
        name="help-desk",
        files=assist_files,
    )
    assert output["proposal"]["files"] == assist_files
    assert output["missing_information"] == ["What is the approved refund period?"]
    assert output["checks"]["summary"]["errors"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "scripts/run.py",
        ".hidden.md",
        "tests/answers.md",
        "../policy.md",
        "references/run.js",
    ],
)
def test_draft_rejects_unsafe_or_evaluation_files(path, assist_files):
    with pytest.raises(SkillsHubError) as error:
        validate_assist_output(
            kind="draft",
            content=json.dumps(
                {
                    "files": assist_files
                    + [{"path": path, "content": "Synthetic content"}],
                    "missing_information": [],
                }
            ),
            name="help-desk",
            files=assist_files,
        )
    assert error.value.code == "assist_output_invalid"


def test_draft_rejects_wrong_immutable_name(assist_files):
    with pytest.raises(SkillsHubError):
        validate_assist_output(
            kind="draft",
            content=json.dumps({"files": assist_files, "missing_information": []}),
            name="different-name",
            files=assist_files,
        )


def test_draft_rejects_oversize_and_too_many_files(assist_files):
    for extra in [
        [{"path": f"file-{i}.md", "content": "Synthetic text"} for i in range(3)],
        [{"path": "policy.md", "content": "a" * MAX_OUTPUT_BYTES}],
    ]:
        with pytest.raises(SkillsHubError):
            validate_assist_output(
                kind="draft",
                content=json.dumps(
                    {"files": assist_files + extra, "missing_information": []}
                ),
                name="help-desk",
                files=assist_files,
            )


def test_description_preview_is_actual_server_patch(assist_files):
    output = validate_assist_output(
        kind="description",
        content=json.dumps(
            {
                "description": "Use when a refund request needs review, not for order tracking.",
                "reason": "Narrow the routing boundary.",
                "tradeoffs": ["Does not cover ordinary tracking."],
            }
        ),
        name="help-desk",
        files=assist_files,
    )
    assert output["proposal"]["files"] == replace_description(
        assist_files, output["proposal"]["description"], name="help-desk"
    )
    assert output["proposal"]["tradeoffs"] == ["Does not cover ordinary tracking."]


def test_summary_is_note_only(assist_files):
    output = validate_assist_output(
        kind="summary",
        content='{"summary":"Clarify the documented routing boundary."}',
        name="help-desk",
        files=assist_files,
    )
    assert output["proposal"] == {
        "files": None,
        "description": None,
        "summary": "Clarify the documented routing boundary.",
    }


def test_findings_are_plan_bound_and_redacted():
    # Intentionally fake detector fixture, assembled rather than a credential literal.
    fake = "ghp_" + "syntheticfixture0123456789"
    payload = {
        "instruction": "Use the supplied example",
        "context_text": fake,
        "routing_examples": [
            {
                "id": "case",
                "prompt": fake,
                "fixtures": [{"id": "fixture", "name": "Sample", "text": fake}],
            }
        ],
    }
    findings = _outbound_findings(payload, "plan-one")
    assert len(findings) == 3
    assert fake not in json.dumps(findings)
    assert len({item["finding_id"] for item in findings}) == 3
    assert findings != _outbound_findings(payload, "plan-two")
    assert findings == _outbound_findings(payload, "plan-one")
    assert {item["location"]["kind"] for item in findings} == {
        "assist_input",
        "case_prompt",
        "fixture",
    }
    context_finding = next(
        item for item in findings if item["location"]["kind"] == "assist_input"
    )
    assert context_finding["location"]["pointer"] == "/context_text"


def test_review_rejects_missing_fabricated_duplicate_and_changed_findings():
    findings = [{"finding_id": "known"}]
    plan = {"input_fingerprint": "plan", "outbound_findings": findings}
    request: dict = {
        "expected_input_fingerprint": "plan",
        "acknowledge_provider_processing": True,
        "outbound_review": {
            "fingerprint": "plan",
            "false_positive_secret_findings": [],
        },
    }
    for decisions, code in [
        ([], "test_input_unsafe"),
        (
            [{"finding_id": "fabricated", "reason": "Synthetic"}],
            "invalid_inventory_review",
        ),
        ([{"finding_id": "known", "reason": ""}], "invalid_inventory_review"),
        (
            [{"finding_id": "known", "reason": "Synthetic"}] * 2,
            "invalid_inventory_review",
        ),
    ]:
        request["outbound_review"]["false_positive_secret_findings"] = decisions
        with pytest.raises(SkillsHubError) as error:
            _validate_review(request, plan)
        assert error.value.code == code
    request["outbound_review"]["false_positive_secret_findings"] = [
        {
            "finding_id": "known",
            "reason": "This is a synthetic example, not a credential.",
        }
    ]
    assert _validate_review(request, plan) == request["outbound_review"]
    request["expected_input_fingerprint"] = "changed"
    with pytest.raises(SkillsHubError) as error:
        _validate_review(request, plan)
    assert error.value.code == "test_plan_changed"


def test_assist_input_rejects_publication_and_unbounded_output():
    request: dict = {
        "kind": "draft",
        "revision": 1,
        "expected_digest": "digest",
        "instruction": "Describe routing",
        "context_text": "Approved policy",
        "model": {
            "model_id": "fixture-model",
            "temperature": 0,
            "max_output_tokens": 8192,
        },
        "max_estimated_cost_usd": "1.00",
    }
    assert _request_input(request) == request
    changes: list[dict] = [
        {"labels": ["production"]},
        {"instruction": "x" * (MAX_INPUT_BYTES + 1)},
        {"model": {**request["model"], "max_output_tokens": 8193}},
    ]
    for change in changes:
        with pytest.raises(SkillsHubError) as error:
            _request_input({**request, **change})
        assert error.value.code == "invalid_test_config"


@pytest.fixture
def assist_principal(organization, workspace, user):
    from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def assist_connection(monkeypatch, organization, workspace, user):
    from decimal import Decimal

    from model_hub.models.api_key import ApiKey
    from model_hub.services.skills_hub.v11 import model_calls

    monkeypatch.setattr(
        model_calls,
        "_model_spec",
        lambda model_id: {
            "provider": "openai",
            "context_limit": 128000,
            "input_price": Decimal("0.000001"),
            "output_price": Decimal("0.000002"),
            "pricing_source": "synthetic-fixture-v1",
        },
    )
    return ApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        provider="openai",
        key="synthetic-offline-transport",
    )


@pytest.fixture
def assist_request(hub_skill, user):
    from model_hub.services.skills_hub.packages import manifest_digest

    draft = hub_skill.drafts.get(user=user)
    return {
        "kind": "draft",
        "revision": draft.revision,
        "expected_digest": manifest_digest(draft.files),
        "instruction": "Draft support instructions without inventing policy.",
        "context_text": "No approved refund period is supplied; ask for it.",
        "model": {
            "model_id": "fixture-model",
            "temperature": 0,
            "max_output_tokens": 4096,
        },
        "max_estimated_cost_usd": "1.00",
    }


@pytest.fixture
def no_dispatch(monkeypatch):
    from unittest.mock import Mock

    from model_hub.services.skills_hub.v11 import jobs

    dispatch = Mock()
    monkeypatch.setattr(jobs, "dispatch_job", dispatch)
    return dispatch


@pytest.fixture
def accept_proposal(assist_principal, hub_skill, assist_connection, no_dispatch):
    from uuid import uuid4

    from model_hub.services.skills_hub.v11.assist import (
        create_assist_proposal,
        preview_assist_proposal,
    )

    def accept(request, *, key=None):
        plan = preview_assist_proposal(
            principal=assist_principal, skill_id=hub_skill.pk, request=request
        )
        submission = {
            **request,
            "expected_input_fingerprint": plan["input_fingerprint"],
            "acknowledge_provider_processing": True,
            "outbound_review": {
                "fingerprint": plan["input_fingerprint"],
                "false_positive_secret_findings": [],
            },
        }
        key = key or uuid4()
        return (
            create_assist_proposal(
                principal=assist_principal,
                skill_id=hub_skill.pk,
                idempotency_key=key,
                request=submission,
            ),
            submission,
            key,
        )

    return accept


@pytest.fixture
def provider_stub(monkeypatch, assist_files):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import litellm

    message = {
        "role": "assistant",
        "content": json.dumps(
            {
                "files": assist_files,
                "missing_information": ["What is the approved refund period?"],
            }
        ),
        "tool_calls": None,
    }
    response = SimpleNamespace(
        id="offline-request",
        model="fixture-model-returned",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(model_dump=lambda **kwargs: message),
            )
        ],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 200}
        ),
    )
    transport = AsyncMock(return_value=response)
    monkeypatch.setattr(litellm, "acompletion", transport)
    return transport, response


@pytest.mark.django_db(transaction=True)
def test_preview_local_contract_no_jobs_or_calls(
    assist_principal, hub_skill, assist_request, assist_connection, provider_stub
):
    from model_hub.models.skills_hub_v11 import HubSkillJob, HubSkillModelCall
    from model_hub.serializers.skills_hub_v11 import AssistPreview
    from model_hub.services.skills_hub.v11.assist import preview_assist_proposal

    plan = preview_assist_proposal(
        principal=assist_principal, skill_id=hub_skill.pk, request=assist_request
    )
    serializer = AssistPreview(data=plan)
    assert serializer.is_valid(), serializer.errors
    assert not HubSkillJob.objects.exists()
    assert not HubSkillModelCall.objects.exists()
    provider_stub[0].assert_not_called()
    assert plan["allowance"]["max_output_tokens"] == 4096
    assert plan["provider_disclosure"] == {"provider": "openai"}
    assert "synthetic-offline-transport" not in json.dumps(plan)


@pytest.mark.django_db(transaction=True)
def test_idempotency_precedes_changed_draft_and_expired_payload(
    accept_proposal, assist_request, assist_principal, hub_skill, user
):
    from model_hub.services.skills_hub.v11.assist import create_assist_proposal

    proposal, submission, key = accept_proposal(assist_request)
    hub_skill.drafts.filter(user=user).update(revision=10, files=[])
    proposal.input = None
    proposal.save(update_fields=["input"])
    repeated = create_assist_proposal(
        principal=assist_principal,
        skill_id=hub_skill.pk,
        idempotency_key=key,
        request=submission,
    )
    assert repeated.pk == proposal.pk
    with pytest.raises(SkillsHubError) as error:
        create_assist_proposal(
            principal=assist_principal,
            skill_id=hub_skill.pk,
            idempotency_key=key,
            request={**submission, "instruction": "Different request"},
        )
    assert error.value.code == "idempotency_conflict"


@pytest.mark.django_db(transaction=True)
def test_price_or_context_change_requires_new_preview(
    assist_principal, hub_skill, assist_request, assist_connection, no_dispatch
):
    from uuid import uuid4

    from model_hub.services.skills_hub.v11.assist import (
        create_assist_proposal,
        preview_assist_proposal,
    )

    plan = preview_assist_proposal(
        principal=assist_principal, skill_id=hub_skill.pk, request=assist_request
    )
    submission = {
        **assist_request,
        "context_text": "Changed approved context",
        "expected_input_fingerprint": plan["input_fingerprint"],
        "acknowledge_provider_processing": True,
        "outbound_review": {
            "fingerprint": plan["input_fingerprint"],
            "false_positive_secret_findings": [],
        },
    }
    with pytest.raises(SkillsHubError) as error:
        create_assist_proposal(
            principal=assist_principal,
            skill_id=hub_skill.pk,
            idempotency_key=uuid4(),
            request=submission,
        )
    assert error.value.code == "test_plan_changed"
    no_dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_generation_one_call_no_apply_and_replay_is_noop(
    accept_proposal, assist_request, assist_principal, hub_skill, user, provider_stub
):
    from asgiref.sync import async_to_sync

    from model_hub.models.skills_hub_v11 import HubSkillModelCall
    from model_hub.services.skills_hub.v11.assist import generate_assist_proposal

    proposal, _, _ = accept_proposal(assist_request)
    before = hub_skill.drafts.get(user=user).files
    result = async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    assert result["status"] == "completed"
    proposal.refresh_from_db()
    assert proposal.proposal["files"] != before
    assert hub_skill.drafts.get(user=user).files == before
    assert not hub_skill.versions.exists() and not hub_skill.labels.exists()
    replay = async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    assert replay["already_claimed"]
    provider_stub[0].assert_awaited_once()
    call = HubSkillModelCall.objects.get(job=proposal.job)
    assert call.state == "completed" and call.returned_model == "fixture-model-returned"
    kwargs = provider_stub[0].call_args.kwargs
    assert kwargs["num_retries"] == 0 and kwargs["drop_params"] is False
    assert not kwargs.get("tools")
    assert proposal.application_receipt is None


@pytest.mark.django_db(transaction=True)
def test_bad_output_is_durable_failure_without_repair(
    accept_proposal, assist_request, assist_principal, provider_stub
):
    from asgiref.sync import async_to_sync

    from model_hub.services.skills_hub.v11.assist import generate_assist_proposal

    provider_stub[1].choices[0].finish_reason = "length"
    proposal, _, _ = accept_proposal(assist_request)
    result = async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    assert result["status"] == "failed"
    proposal.refresh_from_db()
    assert proposal.proposal is None
    proposal.job.refresh_from_db()
    assert proposal.job.failure_code == "assist_output_invalid"
    assert proposal.job.known_output_tokens == 200
    provider_stub[0].assert_awaited_once()


@pytest.mark.django_db(transaction=True)
def test_apply_replay_preserves_later_edits(
    accept_proposal, assist_request, assist_principal, hub_skill, user, provider_stub
):
    from asgiref.sync import async_to_sync

    from model_hub.services.skills_hub.drafts import save_draft
    from model_hub.services.skills_hub.v11.assist import (
        apply_assist_proposal,
        generate_assist_proposal,
    )

    proposal, _, _ = accept_proposal(assist_request)
    async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    applied = apply_assist_proposal(
        principal=assist_principal,
        proposal_id=proposal.pk,
        revision=proposal.base_revision,
        expected_digest=proposal.base_digest,
    )
    later = save_draft(
        skill=hub_skill,
        user=user,
        files=[
            *applied.files,
            {"path": "later.md", "content": "Keep this later edit."},
        ],
        note="Later note",
        base_version=applied.base_version,
        revision=applied.revision,
    )
    repeated = apply_assist_proposal(
        principal=assist_principal,
        proposal_id=proposal.pk,
        revision=proposal.base_revision,
        expected_digest=proposal.base_digest,
    )
    assert repeated.revision == applied.revision
    assert repeated.files == applied.files
    assert hub_skill.drafts.get(user=user).revision == later.revision
    assert hub_skill.drafts.get(user=user).files == later.files
    assert not hub_skill.versions.exists()


@pytest.mark.django_db(transaction=True)
def test_stale_apply_never_overwrites(
    accept_proposal, assist_request, assist_principal, hub_skill, user, provider_stub
):
    from asgiref.sync import async_to_sync

    from model_hub.services.skills_hub.v11.assist import (
        apply_assist_proposal,
        generate_assist_proposal,
    )

    proposal, _, _ = accept_proposal(assist_request)
    async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    hub_skill.drafts.filter(user=user).update(revision=10)
    with pytest.raises(SkillsHubError) as error:
        apply_assist_proposal(
            principal=assist_principal,
            proposal_id=proposal.pk,
            revision=proposal.base_revision,
            expected_digest=proposal.base_digest,
        )
    assert error.value.code == "draft_conflict"
    assert hub_skill.drafts.get(user=user).revision == 10


@pytest.mark.django_db(transaction=True)
def test_system_key_rejected_before_owner_lookup(
    assist_principal, hub_skill, assist_request
):
    from dataclasses import replace

    from model_hub.services.skills_hub.v11.assist import preview_assist_proposal

    with pytest.raises(SkillsHubError) as error:
        preview_assist_proposal(
            principal=replace(assist_principal, kind="system_key"),
            skill_id=hub_skill.pk,
            request=assist_request,
        )
    assert error.value.code == "principal_not_supported"


@pytest.mark.django_db(transaction=True)
def test_foreign_owner_cannot_apply(
    accept_proposal, assist_request, assist_principal, hub_other_user
):
    from dataclasses import replace

    from accounts.models.organization_membership import OrganizationMembership
    from model_hub.services.skills_hub.v11.assist import apply_assist_proposal

    proposal, _, _ = accept_proposal(assist_request)
    # Give the other author current org access: failure must be ownership, not membership.
    membership = OrganizationMembership.no_workspace_objects.get(
        organization_id=assist_principal.scope.organization_id,
        user_id=assist_principal.user_id,
    )
    OrganizationMembership.no_workspace_objects.create(
        organization_id=assist_principal.scope.organization_id,
        user=hub_other_user,
        role=membership.role,
        level=membership.level,
        is_active=True,
    )
    with pytest.raises(SkillsHubError) as error:
        apply_assist_proposal(
            principal=replace(assist_principal, user_id=hub_other_user.pk),
            proposal_id=proposal.pk,
            revision=1,
            expected_digest=proposal.base_digest,
        )
    assert error.value.code == "resource_not_found"


@pytest.fixture
def description_request(assist_principal, hub_skill, assist_request):
    from datetime import timedelta
    from uuid import uuid4

    from django.utils import timezone

    from model_hub.models.skills_hub_v11 import HubSkillJob, HubSkillTestRun

    job_id = uuid4()
    job = HubSkillJob.objects.create(
        id=job_id,
        organization_id=assist_principal.scope.organization_id,
        workspace_id=assist_principal.scope.workspace_id,
        owner_user_id=assist_principal.user_id,
        skill=hub_skill,
        kind="test",
        idempotency_key=uuid4(),
        request_digest="synthetic",
        workflow_id=f"offline-{job_id}",
        status="completed",
        reservation_released_at=timezone.now(),
        deadline_at=timezone.now() + timedelta(seconds=60),
    )
    cases = [
        {
            "id": str(uuid4()),
            "title": title,
            "source_group_id": group,
            "prompt": prompt,
            "fixtures": [],
            "checks": [],
            "expected_trigger": expected,
            "reviewed": True,
        }
        for title, group, prompt, expected in [
            ("Refund request", "refund", "Explain the approved refund policy.", True),
            ("Near miss", "tracking", "Where is my package?", False),
        ]
    ]
    run = HubSkillTestRun.objects.create(
        job=job,
        profile="quick",
        candidate_digest=assist_request["expected_digest"],
        snapshot={"cases": cases},
        validity="reviewed",
    )
    return {
        key: value
        for key, value in assist_request.items()
        if key not in {"instruction", "context_text", "kind"}
    } | {
        "kind": "description",
        "source_run_id": str(run.pk),
        "case_ids": [case["id"] for case in cases],
    }


@pytest.mark.django_db(transaction=True)
def test_description_requires_reviewed_positive_and_negative(
    assist_principal, hub_skill, description_request, assist_connection
):
    from model_hub.models.skills_hub_v11 import HubSkillTestRun
    from model_hub.services.skills_hub.v11.assist import preview_assist_proposal

    plan = preview_assist_proposal(
        principal=assist_principal, skill_id=hub_skill.pk, request=description_request
    )
    assert plan["input_fingerprint"]
    run = HubSkillTestRun.objects.get(pk=description_request["source_run_id"])
    for change in [{"reviewed": False}, {"expected_trigger": True}]:
        assert run.snapshot is not None
        snapshot = deepcopy(run.snapshot)
        snapshot["cases"][1].update(change)
        HubSkillTestRun.objects.filter(pk=run.pk).update(snapshot=snapshot)
        with pytest.raises(SkillsHubError) as error:
            preview_assist_proposal(
                principal=assist_principal,
                skill_id=hub_skill.pk,
                request=description_request,
            )
        assert error.value.code == "invalid_test_config"


@pytest.mark.django_db(transaction=True)
def test_description_apply_keeps_body_and_records_exposure(
    accept_proposal,
    description_request,
    assist_principal,
    hub_skill,
    user,
    provider_stub,
):
    from asgiref.sync import async_to_sync

    from model_hub.services.skills_hub.v11.assist import (
        apply_assist_proposal,
        generate_assist_proposal,
    )

    provider_stub[1].choices[0].message.model_dump = lambda **kwargs: {
        "role": "assistant",
        "content": json.dumps(
            {
                "description": "Use when a refund request needs policy review; not for package tracking.",
                "reason": "Separate refund review from order status.",
                "tradeoffs": ["Tracking remains outside the skill."],
            }
        ),
    }
    proposal, _, _ = accept_proposal(description_request)
    before = hub_skill.drafts.get(user=user).files
    result = async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    assert result["status"] == "completed"
    proposal.refresh_from_db()
    assert proposal.exposed_source_group_ids == ["refund", "tracking"]
    assert proposal.selected_case_ids == description_request["case_ids"]
    applied = apply_assist_proposal(
        principal=assist_principal,
        proposal_id=proposal.pk,
        revision=proposal.base_revision,
        expected_digest=proposal.base_digest,
    )
    assert applied.files == replace_description(
        before, proposal.proposal["description"], name=hub_skill.name
    )
    outbound = json.loads(provider_stub[0].call_args.kwargs["messages"][1]["content"])
    assert all("checks" not in case for case in outbound["routing_examples"])


@pytest.mark.django_db(transaction=True)
def test_summary_cannot_apply_and_expired_proposal_is_not_inspectable(
    accept_proposal, assist_request, assist_principal
):
    from model_hub.services.skills_hub.v11.assist import apply_assist_proposal

    proposal, _, _ = accept_proposal(assist_request)
    proposal.kind = "summary"
    proposal.save(update_fields=["kind"])
    with pytest.raises(SkillsHubError) as error:
        apply_assist_proposal(
            principal=assist_principal,
            proposal_id=proposal.pk,
            revision=proposal.base_revision,
            expected_digest=proposal.base_digest,
        )
    assert error.value.code == "assist_apply_not_supported"
    proposal.kind, proposal.input = "draft", None
    proposal.save(update_fields=["kind", "input"])
    with pytest.raises(SkillsHubError) as error:
        apply_assist_proposal(
            principal=assist_principal,
            proposal_id=proposal.pk,
            revision=proposal.base_revision,
            expected_digest=proposal.base_digest,
        )
    assert error.value.code == "evidence_expired"


@pytest.mark.django_db(transaction=True)
def test_cancelled_or_revoked_owner_never_sends(
    accept_proposal, assist_request, assist_principal, user, provider_stub
):
    from asgiref.sync import async_to_sync

    from model_hub.services.skills_hub.v11.assist import generate_assist_proposal
    from model_hub.services.skills_hub.v11.jobs import cancel_job

    proposal, _, _ = accept_proposal(assist_request)
    cancel_job(principal=assist_principal, job_id=proposal.pk)
    with pytest.raises(SkillsHubError) as error:
        async_to_sync(generate_assist_proposal)(
            scope=assist_principal.scope, proposal_id=proposal.pk
        )
    assert error.value.code == "execution_cancelled"
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(SkillsHubError) as error:
        async_to_sync(generate_assist_proposal)(
            scope=assist_principal.scope, proposal_id=proposal.pk
        )
    assert error.value.code == "access_revoked"
    provider_stub[0].assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_secret_review_is_accepted_without_changing_plan(
    assist_request, assist_principal, hub_skill, assist_connection, no_dispatch
):
    from uuid import uuid4

    from model_hub.serializers.skills_hub_v11 import AssistPreview
    from model_hub.services.skills_hub.v11.assist import (
        create_assist_proposal,
        preview_assist_proposal,
    )

    request: dict = {
        **assist_request,
        "context_text": "ghp_" + "syntheticfixture0123456789",
    }
    plan = preview_assist_proposal(
        principal=assist_principal, skill_id=hub_skill.pk, request=request
    )
    serializer = AssistPreview(data=plan)
    assert serializer.is_valid(), serializer.errors
    submission = {
        **request,
        "expected_input_fingerprint": plan["input_fingerprint"],
        "acknowledge_provider_processing": True,
        "outbound_review": {
            "fingerprint": plan["input_fingerprint"],
            "false_positive_secret_findings": [
                {
                    "finding_id": item["finding_id"],
                    "reason": "Explicit synthetic detector fixture.",
                }
                for item in plan["outbound_findings"]
            ],
        },
    }
    proposal = create_assist_proposal(
        principal=assist_principal,
        skill_id=hub_skill.pk,
        idempotency_key=uuid4(),
        request=submission,
    )
    assert proposal.input_fingerprint == plan["input_fingerprint"]
    assert proposal.input is not None
    assert proposal.input["accepted_input_fingerprint"] != plan["input_fingerprint"]


@pytest.mark.django_db(transaction=True)
def test_complete_input_limit_never_silently_truncates(
    assist_request, assist_principal, hub_skill, assist_connection
):
    from model_hub.services.skills_hub.v11.assist import preview_assist_proposal

    with pytest.raises(SkillsHubError) as error:
        preview_assist_proposal(
            principal=assist_principal,
            skill_id=hub_skill.pk,
            request={**assist_request, "context_text": "x" * MAX_INPUT_BYTES},
        )
    assert error.value.code == "payload_too_large"


@pytest.mark.django_db(transaction=True)
def test_overlapping_delivery_uses_one_paid_reservation(
    accept_proposal, assist_request, assist_principal, provider_stub
):
    import asyncio

    from asgiref.sync import async_to_sync

    from model_hub.models.skills_hub_v11 import HubSkillModelCall
    from model_hub.services.skills_hub.v11.assist import generate_assist_proposal

    proposal, _, _ = accept_proposal(assist_request)

    async def overlap():
        entered, release = asyncio.Event(), asyncio.Event()

        async def response(**kwargs):
            entered.set()
            await release.wait()
            return provider_stub[1]

        provider_stub[0].side_effect = response
        first = asyncio.create_task(
            generate_assist_proposal(
                scope=assist_principal.scope, proposal_id=proposal.pk
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            duplicate = await generate_assist_proposal(
                scope=assist_principal.scope, proposal_id=proposal.pk
            )
            assert duplicate["already_claimed"]
        finally:
            release.set()
        return await first

    assert async_to_sync(overlap)()["status"] == "completed"
    provider_stub[0].assert_awaited_once()
    assert HubSkillModelCall.objects.filter(job=proposal.job).count() == 1


@pytest.mark.django_db(transaction=True)
def test_cancel_during_call_retains_spend_but_not_proposal(
    accept_proposal, assist_request, assist_principal, provider_stub
):
    from asgiref.sync import async_to_sync, sync_to_async
    from django.db import connection

    from model_hub.services.skills_hub.v11.assist import generate_assist_proposal
    from model_hub.services.skills_hub.v11.jobs import cancel_job

    proposal, _, _ = accept_proposal(assist_request)

    def cancel_without_transaction():
        assert not connection.in_atomic_block
        cancel_job(principal=assist_principal, job_id=proposal.pk)

    async def response(**kwargs):
        await sync_to_async(cancel_without_transaction)()
        return provider_stub[1]

    provider_stub[0].side_effect = response
    result = async_to_sync(generate_assist_proposal)(
        scope=assist_principal.scope, proposal_id=proposal.pk
    )
    assert result["status"] == "cancelled"
    proposal.refresh_from_db()
    proposal.job.refresh_from_db()
    assert proposal.proposal is None
    assert proposal.job.known_output_tokens == 200
    provider_stub[0].assert_awaited_once()


@pytest.mark.django_db(transaction=True)
def test_independent_assist_gate_blocks_new_spend_but_preserves_operation_recovery(
    accept_proposal,
    assist_request,
    assist_principal,
    hub_skill,
    provider_stub,
    settings,
):
    from uuid import uuid4

    from asgiref.sync import async_to_sync

    from model_hub.models.skills_hub_v11 import HubSkillJob, HubSkillModelCall
    from model_hub.services.skills_hub.v11.assist import (
        create_assist_proposal,
        generate_assist_proposal,
        preview_assist_proposal,
    )

    proposal, submission, key = accept_proposal(assist_request)
    settings.SKILLS_ASSIST_ENABLED = False
    assert settings.SKILLS_TESTING_ENABLED is True
    assert (
        create_assist_proposal(
            principal=assist_principal,
            skill_id=hub_skill.pk,
            idempotency_key=key,
            request=submission,
        ).pk
        == proposal.pk
    )
    with pytest.raises(SkillsHubError) as denied:
        preview_assist_proposal(
            principal=assist_principal, skill_id=hub_skill.pk, request=assist_request
        )
    assert denied.value.code == "skills_unavailable"
    with pytest.raises(SkillsHubError) as denied:
        create_assist_proposal(
            principal=assist_principal,
            skill_id=hub_skill.pk,
            idempotency_key=uuid4(),
            request=submission,
        )
    assert denied.value.code == "skills_unavailable"
    with pytest.raises(SkillsHubError) as denied:
        async_to_sync(generate_assist_proposal)(
            scope=assist_principal.scope, proposal_id=proposal.pk
        )
    assert denied.value.code == "skills_unavailable"
    assert HubSkillJob.objects.count() == 1
    assert not HubSkillModelCall.objects.exists()
    provider_stub[0].assert_not_called()
