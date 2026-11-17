"""Whole-output privacy regressions: parser -> adapters -> policy -> converter."""

import json
from unittest import mock

import pytest
from tracer.tests.test_project_privacy import (
    _convert_privacy_batch,
    _parsed_privacy_spans,
)
from tracer.tests.test_project_privacy import clear_privacy_cache as clear_privacy_cache
from tracer.utils.pii_scrubber import PIIScrubError, scrub_pii_in_span_batch
from tracer.utils.pii_settings import get_pii_settings_for_projects


PRIVATE = "private-payload-allowlist-73fa"
HIDE = {"enabled": True, "mode": "hide_io"}
ACCOUNTING = {
    "llm.model_name": "privacy-test-model",
    "llm.token_count.prompt": 120,
    "llm.token_count.completion": 30,
    "llm.token_count.total": 150,
    "llm.cost.prompt": 0.12,
    "llm.cost.completion": 0.06,
    "session.id": "session-73fa",
    "user.id": "user-73fa",
}


def _assert_private_absent(spans, policies=None):
    scrub_pii_in_span_batch(spans, policies or {"test-project": HIDE})
    converted = _convert_privacy_batch(spans)
    assert len(converted) == 1
    assert PRIVATE not in json.dumps(converted, default=str)
    row = converted[0]["observation_span"]
    assert row["model"] == "privacy-test-model"
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 30
    assert row["total_tokens"] == 150
    assert row["cost"] == pytest.approx(0.18)
    assert row["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert row["id"] == "0123456789abcdef"
    assert converted[0]["session_name"] == "session-73fa"
    assert converted[0]["end_user"]["user_id"] == "user-73fa"
    return row


@pytest.mark.parametrize("key", [
    "message.function_call_arguments_json",
    "llm.input_messages.0.message.function_call_arguments_json",
    "conversation.transcript", "conversation.provider_log.issues",
    "workflow.voicemail_message", "gen_ai.input.images",
    "conversation.recording", "conversation.recording.stereo_url",
    "conversation.recording.mono.combined_url",
    "conversation.recording.mono.customer_url",
    "conversation.recording.mono.assistant_url",
    # Future payloads and misleading suffixes must not require another alias patch.
    "custom.payload", "custom.name", "custom.type", "custom.model",
    "metadata.query", "telemetry.sdk.payload", "process.command_line",
])
def test_unknown_and_legacy_strings_are_hidden_everywhere(key):
    spans = _parsed_privacy_spans(
        attributes={**ACCOUNTING, key: PRIVATE},
        resource_attributes={key: PRIVATE}, events=[{key: PRIVATE}],
    )
    _assert_private_absent(spans)


@pytest.mark.parametrize("container", [
    "metadata", "user.metadata", "llm.invocation_parameters",
    "gen_ai.request.parameters", "gen_ai",
    "langfuse.observation.metadata", "traceloop.association.properties",
])
def test_json_containers_and_propagated_metadata_are_hidden(container):
    value = json.dumps({
        "input.value": PRIVATE,
        "system_instructions": [PRIVATE, {"description": PRIVATE}],
        "temperature": 0.5, "stream": True,
    })
    spans = _parsed_privacy_spans(
        attributes={**ACCOUNTING, container: value},
        resource_attributes={container: value}, events=[{container: value}],
    )
    # Exercise both nested objects and lists without relying on flat OTLP strings.
    spans[0]["attributes"]["custom.nested"] = {
        "items": [PRIVATE, {"input.value": PRIVATE, "count": 3}], "enabled": True,
    }
    row = _assert_private_absent(spans)
    nested = row["span_attributes"]["custom.nested"]
    assert nested["items"] == ["[hidden]", {"input.value": "[hidden]", "count": 3}]
    assert nested["enabled"] is True


def test_status_description_and_free_text_event_name_are_hidden():
    spans = _parsed_privacy_spans(
        attributes=ACCOUNTING, events=[{"exception.type": "ValueError"}],
        event_name=f"Invalid input: {PRIVATE}",
        status={"code": "STATUS_CODE_ERROR", "message": f"Invalid input: {PRIVATE}"},
    )
    row = _assert_private_absent(spans)
    assert row["status"] == "ERROR"
    assert row["status_message"] == "[hidden]"
    assert row["span_events"][0]["name"] == "[hidden]"
    assert row["span_events"][0]["attributes"]["exception.type"] == "ValueError"


@pytest.mark.parametrize("event_name", ["exception", "gen_ai.user.message", "gen_ai.client.inference.operation.details"])
def test_standard_event_names_survive(event_name):
    spans = _parsed_privacy_spans(attributes=ACCOUNTING, events=[{}], event_name=event_name)
    assert _assert_private_absent(spans)["span_events"][0]["name"] == event_name


def test_hide_io_preserves_tags_through_conversion():
    tags = ["production", "checkout"]
    spans = _parsed_privacy_spans(attributes={
        **ACCOUNTING, "tag.tags": json.dumps(tags), "input.value": PRIVATE,
    })
    row = _assert_private_absent(spans)
    assert json.loads(spans[0]["attributes"]["tag.tags"]) == tags
    assert row["span_attributes"]["tag.tags"] == tags
    assert row["tags"] == tags
    assert row["input"] == "[hidden]"


@pytest.mark.parametrize("name_key,score_key,label_key", [
    ("gen_ai.evaluation.name", "gen_ai.evaluation.score.value", "gen_ai.evaluation.score.label"),
    ("eval.name", "eval.score", "eval.label"),
], ids=["otel", "fi"])
@pytest.mark.parametrize("score", [0.9, "0.9"], ids=["numeric-score", "string-score"])
def test_hide_io_preserves_evaluation_identity_and_result_not_explanations(
    name_key, score_key, label_key, score,
):
    identity_and_result = {
        name_key: "relevance", score_key: score, label_key: "pass",
        "gen_ai.evaluation.target_span_id": "0123456789abcdef",
    }
    payloads = {
        key: PRIVATE for key in (
            "gen_ai.evaluation.explanation", "gen_ai.evaluation.reason",
            "eval.explanation", "eval.reason", "eval.input",
        )
    }
    spans = _parsed_privacy_spans(
        attributes={**ACCOUNTING, **identity_and_result, **payloads},
        events=[{**identity_and_result, **payloads}],
        event_name="gen_ai.evaluation.result",
    )
    row = _assert_private_absent(spans)
    event = row["span_events"][0]
    assert event["name"] == "gen_ai.evaluation.result"
    for attributes in (spans[0]["attributes"], event["attributes"]):
        for key, value in identity_and_result.items():
            assert attributes[key] == value
        for key in payloads:
            assert attributes[key] == "[hidden]"
    # Conversion decodes JSON scalar strings in span_attributes, but not events.
    for key, value in {**identity_and_result, score_key: 0.9}.items():
        assert row["span_attributes"][key] == value
    for key in payloads:
        assert row["span_attributes"][key] == "[hidden]"


@pytest.mark.django_db
@pytest.mark.parametrize("metadata", [["imported"], "production"], ids=["list", "string"])
def test_legacy_non_object_project_metadata_ingests_with_privacy_disabled(project, metadata):
    project.name = "test-project"
    project.trace_type = "observe"
    project.metadata = metadata
    project.save(update_fields=["name", "trace_type", "metadata"])
    identity = (project.name, project.trace_type)
    policies = get_pii_settings_for_projects(
        {identity}, str(project.organization_id), include_modes=True,
        workspace_id=str(project.workspace_id),
    )
    assert policies == {identity: {"enabled": False, "mode": "mask_pii"}}
    spans = _parsed_privacy_spans(attributes={"input.value": PRIVATE, "output.value": PRIVATE})
    spans[0]["project_type"] = project.trace_type
    scrub_pii_in_span_batch(spans, policies)
    converted = _convert_privacy_batch(spans)
    assert len(converted) == 1
    row = converted[0]["observation_span"]
    assert row["input"] == PRIVATE
    assert row["output"] == PRIVATE
    project.refresh_from_db()
    assert project.metadata == metadata


def _set_project(project, name="test-project", *, enabled=True):
    project.name = name
    project.metadata = {"pii_redaction_enabled": enabled, "pii_redaction_mode": "hide_io"}
    project.save(update_fields=["name", "metadata"])


@pytest.mark.django_db
def test_same_name_trace_types_have_independent_policy_and_output(project):
    from tracer.models.project import Project

    _set_project(project, enabled=False)
    observe = Project.no_workspace_objects.create(
        name=project.name, organization_id=project.organization_id,
        workspace_id=project.workspace_id, model_type=project.model_type,
        trace_type="observe", metadata={"pii_redaction_enabled": True, "pii_redaction_mode": "hide_io"},
    )
    identities = {(project.name, "observe"), (project.name, "experiment")}
    for _ in range(2):  # Exercise lookup and cache reuse.
        policies = get_pii_settings_for_projects(
            identities, str(project.organization_id), include_modes=True,
            workspace_id=str(project.workspace_id),
        )
        assert policies[(project.name, "observe")] == HIDE
        assert policies[(project.name, "experiment")]["enabled"] is False
        spans = _parsed_privacy_spans(attributes={**ACCOUNTING, "input.value": PRIVATE})
        _assert_private_absent(spans, policies)
    assert observe.id != project.id


@pytest.mark.django_db
def test_same_name_workspace_policies_do_not_collide(project, user):
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    _set_project(project, enabled=False)
    other_workspace = Workspace.objects.create(
        name="privacy-second-workspace", organization_id=project.organization_id,
        created_by=user,
    )
    Project.no_workspace_objects.create(
        name=project.name, organization_id=project.organization_id,
        workspace=other_workspace, model_type=project.model_type,
        trace_type=project.trace_type,
        metadata={"pii_redaction_enabled": True, "pii_redaction_mode": "hide_io"},
    )
    for _ in range(2):
        for workspace_id, enabled in ((None, False), (str(other_workspace.id), True)):
            policies = get_pii_settings_for_projects(
                {(project.name, project.trace_type)}, str(project.organization_id),
                include_modes=True, workspace_id=workspace_id,
            )
            assert policies[(project.name, project.trace_type)]["enabled"] is enabled
            if enabled:
                _assert_private_absent(
                    _parsed_privacy_spans(attributes={**ACCOUNTING, "input.value": PRIVATE}),
                    {"test-project": policies[(project.name, project.trace_type)]},
                )


@pytest.mark.django_db
def test_cache_write_failure_keeps_fetched_policy_through_conversion(project):
    _set_project(project)
    with mock.patch("tracer.utils.pii_settings.cache.set", side_effect=RuntimeError("cache unavailable")):
        policies = get_pii_settings_for_projects({project.name}, str(project.organization_id), include_modes=True)
    assert policies[project.name] == HIDE
    _assert_private_absent(_parsed_privacy_spans(attributes={**ACCOUNTING, "input.value": PRIVATE}), policies)


@pytest.mark.django_db
def test_unknown_policy_raises_retryable_scrub_error(project):
    # Both the old name query and the new identity resolver must fail closed.
    with (
        mock.patch("tracer.models.project.Project.objects.filter", side_effect=RuntimeError("DB unavailable")),
        mock.patch("tracer.utils.otel.get_or_create_project", side_effect=RuntimeError("DB unavailable")),
        pytest.raises(PIIScrubError),
    ):
        get_pii_settings_for_projects({project.name}, str(project.organization_id), include_modes=True)


@pytest.mark.django_db
def test_rename_does_not_inherit_a_disabled_namesake_policy(auth_client, project):
    from tracer.models.project import Project

    _set_project(project, name="privacy-old-disabled", enabled=False)
    org = str(project.organization_id)
    get_pii_settings_for_projects({project.name}, org, include_modes=True)
    hidden = Project.no_workspace_objects.create(
        name="privacy-hidden", organization_id=project.organization_id,
        workspace_id=project.workspace_id, model_type=project.model_type,
        trace_type=project.trace_type,
        metadata={"pii_redaction_enabled": True, "pii_redaction_mode": "hide_io"},
    )
    for target, name in ((project, "privacy-vacated"), (hidden, "privacy-old-disabled")):
        response = auth_client.post("/tracer/project/update_project_name/", {
            "project_id": str(target.id), "name": name,
        }, format="json")
        assert response.status_code == 200, response.data
    policies = get_pii_settings_for_projects({"privacy-old-disabled"}, org, include_modes=True)
    assert policies["privacy-old-disabled"] == HIDE
    _assert_private_absent(
        _parsed_privacy_spans(attributes={**ACCOUNTING, "input.value": PRIVATE}),
        {"test-project": policies["privacy-old-disabled"]},
    )
