"""Project privacy settings and the ingestion redaction modes."""

import contextlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.cache import cache

from tracer.serializers.project import ProjectNameUpdateSerializer, ProjectSerializer
from tracer.utils.pii_scrubber import scrub_pii_in_span_batch
from tracer.utils.pii_settings import _cache_key, get_pii_settings_for_projects


@pytest.fixture(autouse=True)
def clear_privacy_cache():
    cache.clear()
    with mock.patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("External HTTP is not expected in privacy tests"),
    ):
        yield
    cache.clear()


@pytest.mark.parametrize("mode", ["mask_pii", "hide_io"])
def test_update_serializer_accepts_privacy_modes(mode):
    serializer = ProjectNameUpdateSerializer(
        data={
            "project_id": "00000000-0000-0000-0000-000000000001",
            "name": "Test project",
            "pii_redaction_enabled": True,
            "pii_redaction_mode": mode,
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["pii_redaction_enabled"] is True
    assert serializer.validated_data["pii_redaction_mode"] == mode


@pytest.mark.parametrize("mode", ["invalid", "", None])
def test_update_serializer_rejects_invalid_privacy_mode(mode):
    serializer = ProjectNameUpdateSerializer(
        data={
            "project_id": "00000000-0000-0000-0000-000000000001",
            "name": "Test project",
            "pii_redaction_mode": mode,
        }
    )
    assert not serializer.is_valid()
    assert "pii_redaction_mode" in serializer.errors


def test_update_serializer_rejects_invalid_privacy_enabled():
    serializer = ProjectNameUpdateSerializer(
        data={
            "project_id": "00000000-0000-0000-0000-000000000001",
            "name": "Test project",
            "pii_redaction_enabled": "invalid",
        }
    )
    assert not serializer.is_valid()
    assert "pii_redaction_enabled" in serializer.errors


@pytest.mark.django_db
def test_project_privacy_defaults(project):
    data = ProjectSerializer(project).data
    assert data["pii_redaction_enabled"] is False
    assert data["pii_redaction_mode"] == "mask_pii"


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["mask_pii", "hide_io"])
def test_update_api_round_trips_privacy_and_invalidates_cache(auth_client, project, mode):
    project.metadata = {"keep": "test-value"}
    project.save(update_fields=["metadata"])
    old_name = project.name
    org_id = str(project.organization_id)
    assert get_pii_settings_for_projects({old_name}, org_id, include_modes=True) == {
        old_name: {"enabled": False, "mode": "mask_pii"}
    }
    get_pii_settings_for_projects({old_name}, org_id)
    response = auth_client.post(
        "/tracer/project/update_project_name/",
        {
            "project_id": str(project.id),
            "name": "Renamed privacy project",
            "pii_redaction_enabled": True,
            "pii_redaction_mode": mode,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    project.refresh_from_db()
    assert project.metadata == {
        "keep": "test-value",
        "pii_redaction_enabled": True,
        "pii_redaction_mode": mode,
    }
    assert cache.get(_cache_key(org_id, str(project.id))) is None
    assert cache.get(_cache_key(org_id, str(project.id), include_modes=True)) is None
    response = auth_client.get(f"/tracer/project/{project.id}/")
    assert response.status_code == 200, response.data
    assert response.data["result"]["pii_redaction_enabled"] is True
    assert response.data["result"]["pii_redaction_mode"] == mode
    assert get_pii_settings_for_projects({project.name}, org_id, include_modes=True) == {
        project.name: {"enabled": True, "mode": mode}
    }
    # Disable without changing the stored mode; invalidate the same-name cache too.
    response = auth_client.post(
        "/tracer/project/update_project_name/",
        {
            "project_id": str(project.id),
            "name": project.name,
            "pii_redaction_enabled": False,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert get_pii_settings_for_projects({project.name}, org_id, include_modes=True) == {
        project.name: {"enabled": False, "mode": mode}
    }


@pytest.mark.django_db
def test_project_patch_merges_privacy_metadata(auth_client, project):
    project.metadata = {"keep": "test-value", "pii_redaction_enabled": True}
    project.save(update_fields=["metadata"])
    response = auth_client.patch(
        f"/tracer/project/{project.id}/",
        {"pii_redaction_mode": "hide_io"},
        format="json",
    )
    assert response.status_code == 200, response.data
    project.refresh_from_db()
    assert project.metadata == {
        "keep": "test-value",
        "pii_redaction_enabled": True,
        "pii_redaction_mode": "hide_io",
    }


@pytest.mark.parametrize(
    "data",
    [
        {"pii_redaction_mode": "invalid"},
        {"metadata": {"pii_redaction_mode": "invalid"}},
        {"metadata": {"pii_redaction_enabled": "invalid"}},
    ],
)
def test_project_serializer_validates_privacy_in_both_update_shapes(data):
    serializer = ProjectSerializer(data=data, partial=True)
    assert not serializer.is_valid()


def test_hide_io_replaces_content_without_presidio():
    attributes = {
        "input.value": {"test": ["private input"]},
        "output.value": "private output",
        "fi.llm.input": "private input",
        "fi.llm.output": "private output",
        "raw.input": "private input",
        "raw.output": "private output",
        "gen_ai.input.messages": [{"content": "private input"}],
        "gen_ai.output.messages": [{"content": "private output"}],
        "llm.input_messages.0.message.content": "private input",
        "llm.output_messages.0.message.tool_calls.0.function.arguments": "private output",
        "response": "private output",
    }
    spans = [{
        "project_name": "test-project",
        "attributes": {**attributes, "llm.model_name": "test-model"},
        "resource_attributes": {"input.value": "private resource", "service.name": "test-service"},
    }]
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines", return_value=False) as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
    # Recursive hiding keeps containers and numeric/boolean leaves, not payload text.
    assert spans[0]["attributes"] == {
        **dict.fromkeys(attributes, "[hidden]"),
        "input.value": {"test": ["[hidden]"]},
        "gen_ai.input.messages": [{"content": "[hidden]"}],
        "gen_ai.output.messages": [{"content": "[hidden]"}],
        "llm.model_name": "test-model",
    }
    assert spans[0]["resource_attributes"] == {"input.value": "[hidden]", "service.name": "test-service"}
    engines.assert_not_called()


@pytest.mark.parametrize("mode", ["mask_pii", "hide_io"])
def test_disabled_privacy_leaves_spans_unchanged(mode):
    spans = [{"project_name": "test-project", "attributes": {"input.value": "private input"}}]
    original = deepcopy(spans)
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines", return_value=False) as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": False, "mode": mode}})
    assert spans == original
    engines.assert_not_called()


def test_mixed_modes_keep_mask_pii_on_existing_strict_scrub_path():
    spans = [
        {"project_name": "mask", "attributes": {"input.value": "private input"}},
        {"project_name": "hide", "attributes": {"input.value": "private input"}},
    ]
    with (
        mock.patch("tracer.utils.pii_scrubber._ensure_engines", return_value=True),
        mock.patch("tracer.utils.pii_scrubber.scrub_span_attributes", return_value={"input.value": "<PERSON>"}) as scrub,
    ):
        scrub_pii_in_span_batch(spans, {
            "mask": {"enabled": True, "mode": "mask_pii"},
            "hide": {"enabled": True, "mode": "hide_io"},
        })
    assert spans[0]["attributes"]["input.value"] == "<PERSON>"
    assert spans[1]["attributes"]["input.value"] == "[hidden]"
    scrub.assert_called_once_with({"input.value": "private input"}, strict=True)


def test_ingestion_requests_modes_before_converting_spans():
    from tracer.utils import trace_ingestion

    spans = [{"project_name": "test-project", "project_type": "observe", "attributes": {"input.value": "private input"}}]
    project = SimpleNamespace(id="resolved-project")

    def resolve_policy(*args, resolved_projects, **kwargs):
        resolved_projects[("test-project", "observe")] = project
        return {("test-project", "observe"): {"enabled": True, "mode": "hide_io"}}

    task = trace_ingestion.bulk_create_observation_span_task
    with (
        mock.patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=True),
        mock.patch.object(trace_ingestion.transaction, "atomic", contextlib.nullcontext),
        mock.patch.object(trace_ingestion.payload_storage, "retrieve", return_value=b"test-payload"),
        mock.patch.object(trace_ingestion, "deserialize_trace_payload"),
        mock.patch.object(trace_ingestion, "_parse_otel_request", return_value=spans),
        mock.patch.object(trace_ingestion, "normalize_span_attributes"),
        mock.patch.object(trace_ingestion, "get_pii_settings_for_projects", side_effect=resolve_policy) as settings,
        mock.patch("tracer.utils.pii_scrubber._ensure_engines", return_value=True),
        mock.patch("tracer.utils.pii_scrubber.scrub_span_attributes", side_effect=lambda attrs, **kwargs: attrs),
        mock.patch.object(trace_ingestion, "bulk_convert_otel_spans_to_observation_spans", return_value=[]) as convert,
    ):
        task._original_func("test-payload", "test-org", "test-user", "test-workspace", "protobuf")
    settings.assert_called_once_with(
        {("test-project", "observe")}, "test-org", include_modes=True,
        workspace_id="test-workspace", user_id="test-user",
        resolved_projects={("test-project", "observe"): project},
    )
    assert convert.call_args.args[0][0]["attributes"]["input.value"] == "[hidden]"
    assert convert.call_args.kwargs["projects"] == {("test-project", "test-org", "observe"): project}


def _parsed_privacy_spans(*, attributes=None, resource_attributes=None, events=None, event_name="message", status=None):
    """Use the production parser, including its nested resources and event shape."""
    from tracer.utils.adapters import normalize_span_attributes
    from tracer.utils.trace_ingestion import _parse_otel_request

    def kv(items):
        types = {str: "string_value", int: "int_value", float: "double_value"}
        return [
            {"key": key, "value": {types[type(value)]: value}}
            for key, value in items.items()
        ]

    spans = _parse_otel_request({
        "resource_spans": [{
            "resource": {"attributes": kv({
                "project_name": "test-project",
                "project_type": "observe",
                "service.name": "privacy-test-service",
                **(resource_attributes or {}),
            })},
            "scope_spans": [{
                # An unqualified producer retains events during conversion.
                "scope": {"name": "privacy-test-producer", "version": "1.0"},
                "spans": [{
                    "trace_id": "0123456789abcdef0123456789abcdef",
                    "span_id": "0123456789abcdef",
                    "name": "privacy-test-span",
                    "status": status or {},
                    "start_time_unix_nano": "1792024600000000000",
                    "end_time_unix_nano": "1792024601000000000",
                    "attributes": kv({
                        "fi.span.kind": "LLM",
                        "input.value": "private input",
                        "output.value": "private output",
                        **(attributes or {}),
                    }),
                    "events": [
                        {
                            "name": event_name,
                            "time_unix_nano": "1792024600500000000",
                            "dropped_attributes_count": 2,
                            "attributes": kv(event_attrs),
                        }
                        for event_attrs in (events or [])
                    ],
                }],
            }],
        }],
    })
    normalize_span_attributes(spans)
    return spans


def _convert_privacy_batch(spans):
    """Only related-object lookup is stubbed; span conversion and costing are real."""
    from tracer.utils import otel

    projects = {("test-project", "test-org", "observe"): SimpleNamespace(id="project")}
    with (
        mock.patch.object(otel, "_bulk_get_or_create_projects", return_value=projects),
        mock.patch.object(otel, "_bulk_get_or_create_project_versions", return_value={}),
    ):
        return otel.bulk_convert_otel_spans_to_observation_spans(
            spans, organization_id="test-org", user_id="test-user"
        )


@pytest.mark.parametrize("explicit_cost", [False, True], ids=["token-pricing", "explicit-cost"])
def test_hide_io_parsed_batch_preserves_token_counts_and_cost(explicit_cost):
    metadata = {
        "llm.token_count.prompt": 120,
        "llm.token_count.completion": 30,
        "llm.token_count.total": 150,
        "gen_ai.usage.prompt_tokens": 120,
        "gen_ai.usage.completion_tokens": 30,
        "llm.prompt_template.id": "template-id",
        "llm.prompt_template.version": "v2",
        "llm.input_messages.0.message.role": "user",
        "gen_ai.request.max_tokens": 500,
        "input.mime_type": "text/plain",
    }
    if explicit_cost:
        metadata.update({"llm.cost.prompt": 0.12, "llm.cost.completion": 0.06})
    spans = _parsed_privacy_spans(attributes={
        **metadata,
        "llm.model_name": "privacy-test-model",
        "llm.prompts": "private prompt",
        "gen_ai.completion": "private completion",
        "llm.input_messages.0.message.content": "private message",
    })
    # Use deterministic custom pricing, but do not mock calculate_cost(_from_tokens).
    cache.set("custom_model_pricing:test-org:privacy-test-model", {
        "input_cost": 1.0, "output_cost": 2.0,
    })
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines") as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
        converted = _convert_privacy_batch(spans)
    engines.assert_not_called()
    assert len(converted) == 1
    row = converted[0]["observation_span"]
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 30
    assert row["total_tokens"] == 150
    assert row["cost"] == pytest.approx(0.18)
    assert row["input"] == row["output"] == "[hidden]"
    for key, value in metadata.items():
        assert row["span_attributes"][key] == value, key
    for key in ("llm.prompts", "gen_ai.completion", "llm.input_messages.0.message.content"):
        assert row["span_attributes"][key] == "[hidden]", key


def test_hide_io_parsed_resource_content_hidden_before_conversion():
    content = {"input.value": "private resource input", "output.value": "private resource output"}
    metadata = {"service.version": "1.2", "llm.token_count.prompt": 12, "llm.cost.prompt": 0.1}
    spans = _parsed_privacy_spans(resource_attributes={**content, **metadata})
    assert "resource_attributes" not in spans[0]
    scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
    expected = {
        "project_name": "test-project",
        "project_type": "observe",
        "service.name": "privacy-test-service",
        **metadata,
        **dict.fromkeys(content, "[hidden]"),
    }
    assert spans[0]["resource"]["attributes"] == expected
    row = _convert_privacy_batch(spans)[0]["observation_span"]
    assert row["resource_attributes"] == expected


def test_hide_io_parsed_event_content_hidden_before_conversion():
    event_attrs = [
        {"input.value": "private event input", "gen_ai.prompt": "private prompt", "llm.token_count.prompt": 12},
        {"output.value": "private event output", "gen_ai.completion": "private completion", "message.role": "assistant"},
    ]
    spans = _parsed_privacy_spans(events=event_attrs)
    assert "fi.normalization.profile" not in spans[0]["attributes"]
    expected = deepcopy(spans[0]["events"])
    for event, keys in zip(expected, [("input.value", "gen_ai.prompt"), ("output.value", "gen_ai.completion")]):
        for key in keys:
            event["attributes"][key] = "[hidden]"
    scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
    assert spans[0]["events"] == expected
    row = _convert_privacy_batch(spans)[0]["observation_span"]
    assert row["span_events"] == expected


def test_hide_io_genai_chat_removes_all_persisted_payload_copies():
    payloads = {
        "gen_ai.prompt.0.content": "private-chat-payload-19ac",
        "gen_ai.completion.0.content": "private-answer-payload-19ac",
        "gen_ai.tool.call.arguments": "private-tool-arguments-19ac",
        "gen_ai.tool.call.result": "private-tool-result-19ac",
        "gen_ai.system_instructions": "private-system-instructions-19ac",
    }
    accounting = {
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 30,
        "gen_ai.usage.total_tokens": 150,
        "gen_ai.cost.input": 0.12,
        "gen_ai.cost.output": 0.06,
    }
    spans = _parsed_privacy_spans(
        attributes={
            **payloads,
            **accounting,
            "gen_ai.operation.name": "chat",
            "gen_ai.prompt.0.role": "user",
            "gen_ai.completion.0.role": "assistant",
        },
        resource_attributes=payloads,
        events=[payloads],
    )
    # Guard the real adapter path: normalization must create the derived copy
    # that used to survive even though input.value was hidden.
    assert spans[0]["attributes"]["query"] == payloads["gen_ai.prompt.0.content"]
    assert spans[0]["attributes"]["response"] == payloads["gen_ai.completion.0.content"]
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines") as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
        converted = _convert_privacy_batch(spans)
    engines.assert_not_called()
    assert len(converted) == 1
    # Search the entire result, not just the input/output columns or one copy
    # of attributes: eval_attributes, resources, events and prompt details count.
    serialized = json.dumps(converted, default=str)
    for secret in payloads.values():
        assert secret not in serialized
    row = converted[0]["observation_span"]
    hidden_message = {"role": "[hidden]", "content": "[hidden]"}
    assert row["input"] == [hidden_message]
    assert row["output"] == hidden_message
    for field in ("span_attributes", "eval_attributes"):
        assert row[field]["raw.input"] == [hidden_message]
        assert row[field]["raw.output"] == hidden_message
        for key in (*payloads, "query", "response"):
            assert row[field][key] == "[hidden]", (field, key)
        for key, value in accounting.items():
            assert row[field][key] == value, (field, key)
    assert row["resource_attributes"]["gen_ai.system_instructions"] == "[hidden]"
    assert row["span_events"][0]["attributes"] == dict.fromkeys(payloads, "[hidden]")
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 30
    assert row["total_tokens"] == 150
    assert row["cost"] == pytest.approx(0.18)


def test_hide_io_hides_eval_payload_copies_and_exception_stack_traces():
    private_text = "private-eval-exception-payload-71de"
    accounting = {
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 30,
        "gen_ai.usage.total_tokens": 150,
        "gen_ai.cost.input": 0.12,
        "gen_ai.cost.output": 0.06,
    }
    spans = _parsed_privacy_spans(
        attributes={
            "input.value": private_text,
            "eval.input": private_text,
            **accounting,
        },
        resource_attributes={"eval.input": private_text},
        events=[{
            "eval.input": private_text,
            "exception.type": "ValueError",
            "exception.message": f"Invalid input: {private_text}",
            "exception.stacktrace": (
                "Traceback (most recent call last):\n"
                f"ValueError: Invalid input: {private_text}"
            ),
        }],
        event_name="exception",
    )
    # Exercise an unqualified producer: conversion must retain the exception,
    # rather than incidentally discard its payload through profile allowlists.
    assert "fi.normalization.profile" not in spans[0]["attributes"]
    assert spans[0]["attributes"]["input.value"] == private_text
    assert spans[0]["attributes"]["eval.input"] == private_text
    assert spans[0]["events"][0]["name"] == "exception"
    assert private_text in spans[0]["events"][0]["attributes"]["exception.stacktrace"]
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines") as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": True, "mode": "hide_io"}})
        converted = _convert_privacy_batch(spans)
    engines.assert_not_called()
    assert len(converted) == 1
    assert private_text not in json.dumps(converted, default=str)
    row = converted[0]["observation_span"]
    assert row["input"] == "[hidden]"
    for field in ("span_attributes", "eval_attributes"):
        assert row[field]["eval.input"] == "[hidden]"
        for key, value in accounting.items():
            assert row[field][key] == value, (field, key)
    assert row["resource_attributes"]["eval.input"] == "[hidden]"
    assert len(row["span_events"]) == 1
    event = row["span_events"][0]
    assert event["name"] == "exception"
    assert event["attributes"] == {
        "eval.input": "[hidden]",
        "exception.type": "ValueError",
        "exception.message": "[hidden]",
        "exception.stacktrace": "[hidden]",
    }
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 30
    assert row["total_tokens"] == 150
    assert row["cost"] == pytest.approx(0.18)


@pytest.mark.parametrize("location", ["attributes", "resource_attributes", "resource", "events"])
def test_hide_io_covers_semantic_and_adapter_payload_aliases(location):
    # Explicit examples from semantic_conventions.py and the adapters, including
    # whole containers, indexed records, and non-message text payloads.
    payload_keys = (
        "query", "user.query", "response",
        "input", "output", "input.value", "output.value", "fi.llm.input", "fi.llm.output",
        "raw.input", "raw.output", "eval.input", "exception.message", "exception.stacktrace",
        "traceloop.entity.input", "traceloop.entity.output",
        "langfuse.observation.input", "langfuse.observation.output",
        "llm.input_messages", "llm.output_messages", "llm.prompts", "llm.completions",
        "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.prompt.0", "gen_ai.completion.0",
        "gen_ai.prompt.0.content", "llm.input_messages.0.message.contents.0.message_content.text",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments",
        "gen_ai.tool.call.arguments", "gen_ai.tool.call.result", "gen_ai.system_instructions",
        "llm.tools", "gen_ai.tool.definitions", "gen_ai.tool.description",
        "llm.prompt.template", "llm.prompt_template.template", "llm.prompt_template.variables",
        "gen_ai.prompt.template.variables", "traceloop.prompt.template", "traceloop.prompt.template_variables",
        "gen_ai.retrieval.query", "gen_ai.retrieval.documents", "retrieval.documents",
        "retrieval.documents.0", "retrieval.documents.0.document.content",
        "gen_ai.reranker.query", "reranker.query", "gen_ai.reranker.input_documents",
        "reranker.input_documents", "gen_ai.reranker.output_documents", "reranker.output_documents",
        "gen_ai.embeddings.vectors", "embedding.embeddings", "embedding.embeddings.0.embedding.text",
        "gen_ai.evaluation.explanation", "gen_ai.agent.description", "error.message",
        "gen_ai.guardrail.modified_output", "gen_ai.voice.transcript",
        "gen_ai.voice.recording.url", "gen_ai.voice.recording.stereo_url",
        "gen_ai.voice.recording.customer_url", "gen_ai.voice.recording.assistant_url",
        "gen_ai.image.prompt", "gen_ai.image.negative_prompt", "gen_ai.image.revised_prompt",
        "gen_ai.image.output_urls", "gen_ai.computer_use.text", "gen_ai.computer_use.screenshot",
        "gen_ai.computer_use.current_url", "gen_ai.computer_use.element_selector", "gen_ai.computer_use.result",
        "gen_ai.audio.url", "audio.url", "gen_ai.audio.transcript", "audio.transcript",
    )
    payloads = dict.fromkeys(payload_keys, "private-content-alias-42bc")
    metadata = {
        "gen_ai.usage.input_tokens": 120,
        "llm.token_count.completion": 30,
        "gen_ai.cost.input": 0.12,
        "llm.cost.completion": 0.06,
        "gen_ai.tool.call.id": "call-id",
        "gen_ai.tool.name": "lookup",
        "gen_ai.tool.type": "function",
        "gen_ai.prompt.0.role": "user",
        "gen_ai.prompt.template.name": "template-name",
        "gen_ai.prompt.template.version": "v2",
        "gen_ai.prompt.template.label": "production",
        "gen_ai.request.max_tokens": 500,
        "gen_ai.embeddings.dimension.count": 3,
        "retrieval.documents.0.document.id": "doc-id",
        "retrieval.documents.0.document.score": 0.9,
        "metadata.query": "kept metadata",
        "gen_ai.usage.prompt": 120,
        "gen_ai.cost.completion": 0.06,
        "input.mime_type": "text/plain",
    }
    attrs = {**payloads, **metadata}
    span = {"project_name": "test-project"}
    if location == "resource":
        span[location] = {"attributes": attrs}
    elif location == "events":
        span[location] = [{"name": "message", "attributes": attrs}]
    else:
        span[location] = attrs
    scrub_pii_in_span_batch([span], {"test-project": {"enabled": True, "mode": "hide_io"}})
    if location == "resource":
        actual = span[location]["attributes"]
    elif location == "events":
        actual = span[location][0]["attributes"]
    else:
        actual = span[location]
    assert actual == {
        **dict.fromkeys(payload_keys, "[hidden]"), **metadata,
        # Metadata is no longer an unconditional exemption for arbitrary strings.
        "metadata.query": "[hidden]",
    }
    assert "private-content-alias-42bc" not in json.dumps(span)


@pytest.mark.parametrize("mode", ["mask_pii", "hide_io"])
def test_disabled_privacy_preserves_parsed_resources_and_events(mode):
    spans = _parsed_privacy_spans(
        resource_attributes={"input.value": "private resource"},
        events=[{"output.value": "private event"}],
    )
    original = deepcopy(spans)
    with mock.patch("tracer.utils.pii_scrubber._ensure_engines") as engines:
        scrub_pii_in_span_batch(spans, {"test-project": {"enabled": False, "mode": mode}})
    assert spans == original
    engines.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("metadata", [["imported"], "production"], ids=["list", "string"])
def test_non_object_metadata_reads_and_updates_as_unconfigured(auth_client, project, metadata):
    # Existing projects may hold non-object JSON metadata; privacy treats it as unset.
    project.metadata = metadata
    project.save(update_fields=["metadata"])
    response = auth_client.get(f"/tracer/project/{project.id}/")
    assert response.status_code == 200, response.data
    assert response.data["result"]["pii_redaction_enabled"] is False
    assert response.data["result"]["pii_redaction_mode"] == "mask_pii"

    response = auth_client.patch(
        f"/tracer/project/{project.id}/", {"pii_redaction_mode": "hide_io"}, format="json"
    )
    assert response.status_code == 200, response.data
    project.refresh_from_db()
    assert project.metadata == {"pii_redaction_mode": "hide_io"}

    project.metadata = metadata
    project.save(update_fields=["metadata"])
    response = auth_client.post(
        "/tracer/project/update_project_name/",
        {"project_id": str(project.id), "name": project.name, "pii_redaction_enabled": True},
        format="json",
    )
    assert response.status_code == 200, response.data
    project.refresh_from_db()
    assert project.metadata == {"pii_redaction_enabled": True}
