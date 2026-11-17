"""Continuous task webhook contract; external delivery is always mocked."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalTargetType
from tracer.serializers.eval_task import EditEvalTaskSerializer, EvalTaskSerializer
from tracer.tests.eval_task_factories import make_row



def _caller_request(user, organization, workspace):
    # Serializers scope related fields to the caller; without a request they grant no rows.
    request = APIRequestFactory().post("/")
    request.user = user
    request.organization = organization
    request.workspace = workspace
    return request

@pytest.fixture
def webhook_task(project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="Webhook task",
        run_type=RunType.CONTINUOUS,
        webhook_url="https://hooks.example.test/evaluations",
        webhook_condition={
            "eval_config_id": str(custom_eval_config.id),
            "operator": "gt",
            "value": 0.5,
        },
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.mark.django_db
@pytest.mark.parametrize("target_type", [EvalTargetType.SPAN, EvalTargetType.TRACE])
def test_matching_result_posts_payload(webhook_task, observation_span, custom_eval_config, target_type):
    from tracer.services.eval_tasks.webhook import notify_eval_task_result

    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task,
                     output_float=0.8, eval_explanation="Above threshold")
    entry.target_type = target_type
    with patch("tracer.services.eval_tasks.webhook.post_webhook") as post:
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
    post.assert_called_once()
    args = post.call_args.kwargs
    assert args["timeout"] == 10
    assert args["json"] == {
        "project": str(webhook_task.project_id),
        "eval_task": str(webhook_task.id),
        "source_type": target_type,
        "source_id": str(entry.observation_span_id if target_type == EvalTargetType.SPAN else entry.trace_id),
        "trace_id": str(entry.trace_id),
        "eval_config_id": str(custom_eval_config.id),
        "eval_name": custom_eval_config.name,
        "score": 0.8,
        "value": 0.8,
        "explanation": "Above threshold",
        "created_at": entry.created_at.isoformat(),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["no_url", "no_match", "other_eval", "historical", "errored", "session"])
def test_result_without_matching_rule_does_not_post(webhook_task, observation_span, custom_eval_config, change):
    from tracer.services.eval_tasks.webhook import notify_eval_task_result

    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task, output_float=0.8)
    if change == "no_url":
        webhook_task.webhook_url = ""
    elif change == "no_match":
        webhook_task.webhook_condition["value"] = 0.9
    elif change == "other_eval":
        webhook_task.webhook_condition["eval_config_id"] = "00000000-0000-4000-8000-000000000001"
    elif change == "historical":
        webhook_task.run_type = RunType.HISTORICAL
    elif change == "errored":
        entry.error = True
    else:
        entry.target_type = EvalTargetType.SESSION
    with patch("tracer.services.eval_tasks.webhook.post_webhook") as post:
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
    post.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("recover", [True, False])
def test_delivery_retries_once_and_swallows_failure(webhook_task, observation_span, custom_eval_config, recover, caplog):
    from tracer.services.eval_tasks.webhook import notify_eval_task_result

    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task, output_float=0.8)
    with patch("tracer.services.eval_tasks.webhook.post_webhook", side_effect=[RuntimeError("private destination details"), None if recover else RuntimeError("private destination details")]) as post:
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
    assert post.call_count == 2
    assert "private destination details" not in caplog.text
    if not recover:
        assert "eval_task_webhook_failed" in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize("operator,value,output,expected", [
    ("lt", 0.5, 0.2, True), ("gt", 0.5, 0.5, False),
    ("eq", 0, 0, True), ("eq", "bad", "bad", True),
    ("eq", False, 0, False), ("fail", None, False, True),
    ("fail", None, True, False), ("gt", 0, "bad", False),
])
def test_condition_operators(webhook_task, observation_span, custom_eval_config, operator, value, output, expected):
    from tracer.services.eval_tasks.webhook import notify_eval_task_result

    field = "output_bool" if isinstance(output, bool) else "output_float" if isinstance(output, (int, float)) else "output_str"
    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task, **{field: output})
    webhook_task.webhook_condition.update(operator=operator, value=value)
    with patch("tracer.services.eval_tasks.webhook.post_webhook") as post:
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
    assert post.called is expected


@pytest.mark.django_db
@pytest.mark.parametrize("result,expected_value", [
    ({"score": 0.2, "choice": "bad"}, "bad"),
    ({"score": 0.2, "choices": ["bad"]}, "bad"),
    ([{"score": 0.2, "choice": "bad"}], "bad"),
    (["bad"], "bad"),
    ({"choices": ["bad", "unsafe"]}, ["bad", "unsafe"]),
])
def test_choice_equality_uses_writer_selected_values(
    webhook_task, observation_span, custom_eval_config, result, expected_value
):
    from tracer.services.eval_tasks.webhook import notify_eval_task_result
    from tracer.utils.eval import _dual_write_eval_value

    fields = {}
    _dual_write_eval_value(result, "choices", fields)
    entry = make_row(
        span=observation_span, cfg=custom_eval_config, task=webhook_task, **fields
    )
    entry.refresh_from_db()
    webhook_task.webhook_condition.update(operator="eq", value="bad")
    with patch("tracer.services.eval_tasks.webhook.post_webhook") as post:
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
        post.assert_called_once()
        assert post.call_args.kwargs["json"]["value"] == expected_value
        post.reset_mock()
        webhook_task.webhook_condition["value"] = "good"
        notify_eval_task_result(webhook_task, entry, custom_eval_config)
        post.assert_not_called()


def test_task_webhook_url_validation_does_not_resolve_dns():
    from tracer.serializers.eval_task_webhook import WebhookURLField

    url = "https://hooks.example.test/evaluations"
    with patch("tfc.utils.ssrf_guard.socket.getaddrinfo", side_effect=AssertionError("DNS during save")) as dns:
        assert WebhookURLField().run_validation(url) == url
        dns.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://hooks.example.test/evaluations",
    "https://127.0.0.1/evaluations",
    "https://10.0.0.1/evaluations",
    "https://[::1]/evaluations",
    "https://[::ffff:127.0.0.1]/evaluations",
])
def test_task_webhook_url_rejects_unsafe_syntax_without_dns(url):
    from rest_framework.exceptions import ValidationError
    from tracer.serializers.eval_task_webhook import WebhookURLField

    with patch("tfc.utils.ssrf_guard.socket.getaddrinfo", side_effect=AssertionError("DNS during save")) as dns:
        with pytest.raises(ValidationError):
            WebhookURLField().run_validation(url)
        dns.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("serializer_class", [EvalTaskSerializer, EditEvalTaskSerializer])
@pytest.mark.parametrize("condition", [
    {}, {"eval_config_id": "not-a-uuid", "operator": "fail"},
    {"eval_config_id": "00000000-0000-4000-8000-000000000001", "operator": "gte", "value": 1},
    {"eval_config_id": "00000000-0000-4000-8000-000000000001", "operator": "gt", "value": "1"},
    {"eval_config_id": "00000000-0000-4000-8000-000000000001", "operator": "eq"},
    {"eval_config_id": "00000000-0000-4000-8000-000000000001", "operator": "lt", "value": True},
])
def test_serializers_reject_invalid_conditions(serializer_class, condition):
    serializer = serializer_class(data={"webhook_condition": condition}, partial=True)
    assert not serializer.is_valid()
    assert "webhook_condition" in serializer.errors


@pytest.mark.django_db
def test_serializer_persists_rule_and_rejects_unselected_eval(webhook_task, custom_eval_config):
    rule = {"eval_config_id": str(custom_eval_config.id), "operator": "fail", "value": None}
    serializer = EvalTaskSerializer(webhook_task, data={"webhook_condition": rule}, partial=True)
    assert serializer.is_valid(), serializer.errors
    saved = serializer.save()
    saved.refresh_from_db()
    assert saved.webhook_condition == rule
    assert EvalTaskSerializer(saved).data["webhook_url"] == webhook_task.webhook_url
    rule["eval_config_id"] = "00000000-0000-4000-8000-000000000001"
    serializer = EvalTaskSerializer(saved, data={"webhook_condition": rule}, partial=True)
    assert not serializer.is_valid()
    assert "webhook_condition" in serializer.errors


@pytest.mark.django_db
def test_webhook_can_be_cleared(webhook_task):
    serializer = EvalTaskSerializer(webhook_task, data={"webhook_url": "", "webhook_condition": None}, partial=True)
    assert serializer.is_valid(), serializer.errors
    saved = serializer.save()
    assert saved.webhook_url == ""
    assert saved.webhook_condition is None


@pytest.mark.django_db
def test_runner_delivers_only_after_terminal_result_is_stored(webhook_task, observation_span, custom_eval_config):
    from tracer.services.eval_tasks.run_entry import run_entry

    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task, status=EvalEntryStatus.RUNNING)

    def evaluate(row, config, **kwargs):
        type(row).objects.filter(pk=row.pk).update(output_float=0.8, error=False)

    def delivered(task, row, config):
        row.refresh_from_db()
        assert row.status == EvalEntryStatus.COMPLETED
        assert row.output_float == 0.8
        assert task.id == webhook_task.id

    with patch("tracer.services.eval_tasks.run_entry._run_for_target", side_effect=evaluate), patch("tracer.services.eval_tasks.run_entry._reseed_eval_clustering"), patch("tracer.services.eval_tasks.run_entry.notify_eval_task_result", side_effect=delivered) as notify:
        assert run_entry(entry) == EvalEntryStatus.COMPLETED
    notify.assert_called_once()


@pytest.mark.django_db
def test_lost_terminal_fence_does_not_deliver(webhook_task, observation_span, custom_eval_config):
    from tracer.services.eval_tasks.run_entry import RECLAIMED, run_entry

    entry = make_row(span=observation_span, cfg=custom_eval_config, task=webhook_task, status=EvalEntryStatus.RUNNING)
    with patch("tracer.services.eval_tasks.run_entry._run_for_target"), patch("tracer.services.eval_tasks.run_entry.mark_terminal", return_value=False), patch("tracer.services.eval_tasks.run_entry.notify_eval_task_result") as notify:
        assert run_entry(entry) == RECLAIMED
    notify.assert_not_called()


@pytest.mark.django_db
def test_create_requires_selected_eval_and_condition(user, organization, workspace, project, custom_eval_config):
    context = {"request": _caller_request(user, organization, workspace)}
    payload = {
        "project": str(project.id), "name": "Quality", "run_type": "continuous",
        "sampling_rate": 100, "evals": [str(custom_eval_config.id)],
        "webhook_url": "https://hooks.example.test/evaluations",
    }
    with patch("tracer.serializers.eval_task_webhook.validate_webhook_url"):
        serializer = EvalTaskSerializer(data=payload, context=context)
        assert not serializer.is_valid()
        assert "webhook_condition" in serializer.errors
        payload["webhook_condition"] = {"eval_config_id": str(custom_eval_config.id), "operator": "eq", "value": False}
        serializer = EvalTaskSerializer(data=payload, context=context)
        assert serializer.is_valid(), serializer.errors
        saved = serializer.save()
    saved.refresh_from_db()
    assert saved.webhook_condition == payload["webhook_condition"]


@pytest.mark.django_db
def test_api_edit_and_detail_round_trip(webhook_task, auth_client):
    from tracer.models.eval_task import EvalTaskStatus

    webhook_task.status = EvalTaskStatus.PAUSED
    webhook_task.save(update_fields=["status"])
    rule = {**webhook_task.webhook_condition, "operator": "lt", "value": 0.2}
    with patch("tracer.views.eval_task.start_eval_task_workflow_sync"), patch("tracer.views.eval_task.transaction.on_commit", side_effect=lambda callback: callback()):
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {"eval_task_id": str(webhook_task.id), "edit_type": "edit_rerun", "webhook_condition": rule},
            format="json",
        )
    assert response.status_code == 200, response.data
    webhook_task.refresh_from_db()
    assert webhook_task.webhook_condition == rule
    response = auth_client.get("/tracer/eval-task/get_eval_details/", {"eval_id": str(webhook_task.id)})
    assert response.status_code == 200, response.data
    assert response.data["result"]["webhook_condition"] == rule
    assert response.data["result"]["webhook_url"] == webhook_task.webhook_url
