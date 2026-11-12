"""A2A agent experiments: saved at create, one message/send per row, reply text stored.

An A2A agent is saved as an HTTP source with http.protocol = "a2a", so it runs on
the HTTP source workflow and activities. The fake agent below is a local JSON-RPC
server; only destination routing is replaced, never the agent's answers.
Written statically; the cloud pair runs it.
"""

import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import pytest
import requests

from model_hub.models.choices import CellStatus, StatusType
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentDetailV2Serializer,
    PromptConfigEntrySerializer,
)
from model_hub.services import http_experiment_source as http_source
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy
from model_hub.views import http_endpoint_test
from tfc.utils import outbound_destination

tenants = tenancy.tenants
COLUMN = "c7868984-d789-4f1e-a97e-0c6ac7e71e58"
AGENT_URL = "https://agent.example/a2a"


def card(**extra):
    return {
        "protocolVersion": "0.3.0",
        "name": "Support agent",
        "version": "1.0.0",
        "url": AGENT_URL,
        "capabilities": {"streaming": False},
        "skills": [{"id": "answer", "name": "Answer", "description": "", "tags": []}],
        **extra,
    }


def a2a_entry(column=COLUMN, **a2a):
    return {
        "target_type": "a2a",
        "name": "Support agent · answer",
        "a2a": {
            "card_url": "https://agent.example",
            "card": card(),
            "skill_id": "answer",
            "text_column": column,
            "data_columns": [],
            "context_column": None,
            "auth": {"kind": "bearer", "secret": "ACME_TOKEN"},
            **a2a,
        },
    }


def saved(entry):
    serializer = PromptConfigEntrySerializer(data=entry)
    assert serializer.is_valid(), serializer.errors
    return serializer.validated_data


def errors_text(serializer):
    assert not serializer.is_valid()
    return json.dumps(serializer.errors)


# --- Saved at create ---------------------------------------------------------


def test_a2a_entry_is_saved_as_http_source_with_secret_name_only():
    data = saved(a2a_entry())
    assert data["target_type"] == "http"
    assert data["name"] == "Support agent · answer"
    assert data["http"]["protocol"] == "a2a"
    assert data["http"]["url"] == AGENT_URL
    assert data["http"]["method"] == "POST"
    assert data["http"]["body_template"] == http_source.A2A_BODY_TEMPLATE
    assert data["http"]["headers"] == [
        {"name": "Authorization", "secret_ref": "ACME_TOKEN", "scheme": "bearer"}
    ]
    assert data["variable_mapping"] == {http_source.A2A_TEXT_VARIABLE: COLUMN}
    # persist_http_source validates the saved entry again; it must not change.
    assert saved(deepcopy(data)) == data
    # Create-level rules treat it as an HTTP source (no prompt or model needed).
    assert ExperimentCreateV2Serializer().validate(
        {"experiment_type": "llm", "prompt_config": [data]}
    )["prompt_config"] == [data]


def test_plain_http_entry_is_unchanged_by_a2a_fields():
    from model_hub.tests.test_http_experiment_source import entry

    body = entry()
    assert saved(deepcopy(body)) == body
    assert "protocol" not in saved(deepcopy(body))["http"]


@pytest.mark.parametrize(
    ("auth", "headers"),
    [
        (None, []),
        ({"kind": "none"}, []),
        ({"name": "Authorization", "secret": ""}, []),
        (
            {"name": "Authorization", "secret": "ACME_TOKEN"},
            [{"name": "Authorization", "secret_ref": "ACME_TOKEN", "scheme": "bearer"}],
        ),
        (
            {"kind": "api_key", "header": "X-Api-Key", "secret": "ACME_KEY"},
            [{"name": "X-Api-Key", "secret_ref": "ACME_KEY"}],
        ),
    ],
)
def test_auth_becomes_secret_reference_headers(auth, headers):
    assert saved(a2a_entry(auth=auth))["http"]["headers"] == headers


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"data_columns": [COLUMN]}, "Clear Data part (JSON) from"),
        ({"context_column": COLUMN}, "Clear the context column"),
        (
            {"auth": {"kind": "basic", "username": "bot", "secret": "S"}},
            "Basic and OAuth 2 sign-in",
        ),
        (
            {
                "auth": {
                    "kind": "oauth2",
                    "token_url": "https://auth.example/t",
                    "client_id": "c",
                    "secret": "S",
                }
            },
            "Basic and OAuth 2 sign-in",
        ),
        ({"auth": {"kind": "bearer", "secret": ""}}, "Pick the workspace secret"),
        ({"text_column": None}, "Pick the column the message text comes from"),
        ({"card": card(url="not a url")}, "not a public HTTP(S) URL"),
        ({"card": card(preferredTransport="GRPC")}, "names no JSON-RPC URL"),
        ({"unexpected": True}, "Unknown"),
        # Display-only on the form's card; the form must not send it.
        (
            {"fetched_from": "https://agent.example/.well-known/agent-card.json"},
            "Unknown",
        ),
    ],
)
def test_unsupported_a2a_settings_are_refused_with_what_to_change(change, words):
    assert words in errors_text(PromptConfigEntrySerializer(data=a2a_entry(**change)))


def test_jsonrpc_url_comes_from_additional_interfaces_when_not_preferred():
    other = card(
        preferredTransport="GRPC",
        url="https://agent.example/grpc",
        additionalInterfaces=[{"transport": "JSONRPC", "url": AGENT_URL}],
    )
    assert saved(a2a_entry(card=other))["http"]["url"] == AGENT_URL


def test_a2a_settings_on_a_prompt_entry_are_refused():
    body = {"model": "test-model", "a2a": a2a_entry()["a2a"]}
    assert not PromptConfigEntrySerializer(data=body).is_valid()


@pytest.mark.django_db
def test_create_route_saves_a2a_source_without_prompt_lookup(tenants):
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate
    from model_hub.models.experiments import ExperimentsTable

    actor = tenants["a"]
    dataset = actor["exp"]["dataset"]
    evaluator = EvalTemplate.no_workspace_objects.create(
        name="a2a-output-check",
        organization=actor["org"],
        workspace=actor["workspace"],
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    column = str(dataset.column_set.get().pk)
    body = {
        "name": "A2A run",
        "dataset_id": str(dataset.pk),
        "prompt_config": [a2a_entry(column)],
        "user_eval_metrics": [
            {
                "template_id": str(evaluator.pk),
                "name": "a2a-output-check",
                "config": {"mapping": {"output": "output"}},
            }
        ],
    }
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow", return_value="admitted"
    ) as start:
        response = actor["client"].post(
            "/model-hub/experiments/v2/", body, format="json"
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()
    experiment = ExperimentsTable.no_workspace_objects.get(
        dataset=dataset, name=body["name"]
    )
    source = experiment.experiment_datasets.get(
        legacy_prompt_config__target_type="http"
    )
    config = source.legacy_prompt_config
    assert source.name == "Support agent · answer"
    assert config["http"]["protocol"] == "a2a"
    assert config["http"]["url"] == AGENT_URL
    assert config["variable_mapping"] == {"text": column}
    assert config["http"]["headers"] == [
        {"name": "Authorization", "secret_ref": "ACME_TOKEN", "scheme": "bearer"}
    ]
    assert "card" not in json.dumps(config)
    assert str(source.pk) in http_source.http_source_ids(experiment)
    # The detail readback names it as an A2A variant of an HTTP source.
    readback = ExperimentDetailV2Serializer().get_http_configs(experiment)
    assert readback[0]["target_type"] == "http"
    assert readback[0]["http"]["protocol"] == "a2a"
    assert readback[0]["http"]["headers"][0]["scheme"] == "bearer"


# --- One message/send per row -------------------------------------------------


def _reply(text):
    """What the fake agent answers for the message text it received."""
    if text.startswith("message:"):
        return {
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": "m-1",
                "parts": [{"kind": "text", "text": "reply to " + text}],
            }
        }
    if text.startswith("task:"):
        return {
            "result": {
                "kind": "task",
                "id": "t-1",
                "contextId": "c-1",
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": "a-1",
                        "parts": [
                            {"kind": "text", "text": "artifact for " + text},
                            {"kind": "data", "data": {"ignored": True}},
                        ],
                    }
                ],
            }
        }
    if text.startswith("status:"):
        # The gateway's types.go shape: part "type" and status.message as parts.
        return {
            "result": {
                "id": "t-2",
                "status": {
                    "state": "completed",
                    "message": [{"type": "text", "text": "status for " + text}],
                },
            }
        }
    if text == "working":
        return {"result": {"kind": "task", "id": "t-3", "status": {"state": "working"}}}
    if text == "input":
        return {
            "result": {
                "kind": "task",
                "id": "t-4",
                "status": {"state": "input-required"},
            }
        }
    if text == "failed":
        return {"result": {"kind": "task", "id": "t-5", "status": {"state": "failed"}}}
    if text == "no-text":
        return {
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": "m-2",
                "parts": [{"kind": "data", "data": {"a": 1}}],
            }
        }
    return {"error": {"code": -32601, "message": "Method not found"}}


@pytest.fixture
def agent(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
            request = json.loads(raw)
            received.append({"body": request, "headers": dict(self.headers)})
            text = request["params"]["message"]["parts"][0]["text"]
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": request["id"], **_reply(text)}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def send(*, method, url, headers, data, timeout):
        assert url == AGENT_URL
        # Fixture-only local routing. Production uses outbound_destination.request.
        with requests.Session() as session:
            session.trust_env = False
            return session.request(
                method,
                f"http://127.0.0.1:{server.server_port}/a2a",
                headers=headers,
                data=data.encode("utf-8") if data is not None else None,
                timeout=timeout,
                allow_redirects=False,
            )

    monkeypatch.setattr(outbound_destination, "request", send)
    try:
        yield received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_each_row_sends_one_message_send_with_the_row_text_and_bearer_token(agent):
    source = saved(a2a_entry())
    result = http_source.execute_http_row(
        source,
        values={COLUMN: 'message: a "quote"\n'},
        row_id="row-1",
        resolve_secret=lambda name: "fixture-token" if name == "ACME_TOKEN" else None,
    )
    assert result["error"] is None, result
    assert result["output"] == 'reply to message: a "quote"\n'
    assert result["status_code"] == 200
    assert len(agent) == 1
    sent = agent[0]["body"]
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "message/send"
    assert sent["id"] == "row-1"
    message = sent["params"]["message"]
    assert message == {
        "kind": "message",
        "role": "user",
        "messageId": "row-1",
        "parts": [{"kind": "text", "text": 'message: a "quote"\n'}],
    }
    assert agent[0]["headers"]["Authorization"] == "Bearer fixture-token"
    assert "fixture-token" not in json.dumps(result)


@pytest.mark.parametrize(
    ("text", "output", "code"),
    [
        ("task: refund", "artifact for task: refund", None),
        ("status: hello", "status for status: hello", None),
        ("working", None, "a2a_unfinished"),
        ("input", None, "a2a_unfinished"),
        ("failed", None, "a2a_task_failed"),
        ("no-text", None, "a2a_no_text"),
        ("rpc-error", None, "a2a_error"),
    ],
)
def test_reply_text_comes_from_message_or_task_and_unfinished_tasks_are_row_errors(
    agent, text, output, code
):
    result = http_source.execute_http_row(
        saved(a2a_entry(auth=None)),
        values={COLUMN: text},
        row_id="row-1",
    )
    assert result["output"] == output
    if code is None:
        assert result["error"] is None
    else:
        assert result["error"]["code"] == code
        assert "Retry this row" in result["error"]["message"]
    assert len(agent) == 1


def test_error_messages_say_what_failed_and_what_to_do():
    _, working = http_source.a2a_reply_text(
        {"result": {"kind": "task", "status": {"state": "working"}}}
    )
    assert working["message"] == (
        "The agent's task is still working. Future AGI sends one message per row and does not wait. "
        "Make the agent finish before it answers, then Retry this row."
    )
    _, rpc = http_source.a2a_reply_text(
        {"error": {"code": -32001, "message": "secret detail"}}
    )
    assert (
        rpc["message"]
        == "The agent answered with A2A error -32001. Check the agent and its settings, then Retry this row."
    )
    _, odd = http_source.a2a_reply_text(
        {"result": {"kind": "task", "status": {"state": "<b>odd</b>"}}}
    )
    assert "<b>" not in odd["message"]
    # A state that is not a string is an unfinished task, not a settings error.
    _, listed = http_source.a2a_reply_text(
        {"result": {"kind": "task", "status": {"state": ["working"]}}}
    )
    assert listed["code"] == "a2a_unfinished"


@pytest.mark.parametrize("artifacts", [7, True, "text", {"parts": []}])
def test_artifacts_that_are_not_a_list_are_a_malformed_reply(artifacts):
    reply = {
        "result": {
            "kind": "task",
            "id": "t-6",
            "status": {"state": "completed"},
            "artifacts": artifacts,
        }
    }
    text, error = http_source.a2a_reply_text(reply)
    assert text is None
    assert error["code"] == "a2a_reply"
    assert (
        error["message"]
        == "The agent's reply is not an A2A message/send result. Check the agent URL, then retry."
    )
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"jsonrpc": "2.0", "id": "r", **reply}).encode()
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(
            saved(a2a_entry(auth=None)), values={COLUMN: "hi"}, row_id="r"
        )
    # The row error names the reply, never the row's settings or text column.
    assert result["output"] is None
    assert result["error"]["code"] == "a2a_reply"
    assert "text column" not in result["error"]["message"]


def test_private_or_unreachable_agent_is_refused_before_any_reply():
    with patch.object(
        outbound_destination,
        "request",
        side_effect=outbound_destination.DestinationError("address_class"),
    ) as send:
        result = http_source.execute_http_row(
            saved(a2a_entry(auth=None)), values={COLUMN: "message: hi"}, row_id="r"
        )
    send.assert_called_once()
    assert result["output"] is None
    assert result["error"]["code"] == "destination"
    assert result["error"]["message"] == (
        "The A2A agent is unavailable or not a public destination. Check the agent URL and retry."
    )


def test_missing_secret_never_sends_an_unauthenticated_message():
    with patch.object(outbound_destination, "request") as send:
        result = http_source.execute_http_row(
            saved(a2a_entry()), values={COLUMN: "message: hi"}, row_id="r"
        )
    send.assert_not_called()
    assert result["error"]["code"] == "header_reference"


def test_http_status_error_names_the_agent():
    response = requests.Response()
    response.status_code = 401
    response._content = b'{"error":"unauthorized"}'
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(
            saved(a2a_entry(auth=None)), values={COLUMN: "hi"}, row_id="r"
        )
    assert (
        result["error"]["message"]
        == "The A2A agent returned HTTP 401. Check the agent URL and auth, then Retry this row."
    )


# --- Create, run, rows with outputs --------------------------------------------


def stage_a2a_execution(actor):
    from django.db import transaction

    from model_hub.models.experiments import ExperimentsTable
    from model_hub.services.dataset_snapshot import create_dataset_snapshot
    from model_hub.services.experiment_submit import stage_submission
    from model_hub.tests.test_http_runner_integration import acknowledge

    dataset = actor["exp"]["dataset"]
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="A2A runner",
        dataset=dataset,
        user=actor["user"],
        experiment_type="llm",
        prompt_config=[],
    )
    create_dataset_snapshot(dataset, experiment)
    source = http_source.persist_http_source(
        experiment,
        a2a_entry(str(dataset.column_set.get().pk), auth=None),
    )
    http_source.create_http_output_column(experiment, source)
    with transaction.atomic():
        submission = stage_submission(
            experiment, {}, "create", authorized_workspace=actor["workspace"]
        )
        http_source.bind_http_submission(experiment, submission["admission_id"])
    acknowledge(experiment, submission["admission_id"])
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    plan = http_source.setup_http_source(
        str(experiment.pk), str(source.pk), submission["admission_id"]
    )
    return experiment, source, plan


@pytest.mark.django_db
def test_runner_sends_each_snapshot_row_and_stores_the_agent_reply(tenants, agent):
    from model_hub.models.develop_dataset import Cell
    from model_hub.views.experiment_runner import ExperimentRunner

    actor = tenants["a"]
    # The fake agent answers a Message for text that starts with "message:".
    Cell.no_workspace_objects.filter(dataset=actor["exp"]["dataset"]).update(
        value="message: refund?"
    )
    experiment, source, plan = stage_a2a_execution(actor)
    assert plan["row_ids"]
    for row_id in plan["row_ids"]:
        result = ExperimentRunner(experiment.pk).process_http_row(
            source_id=str(source.pk),
            row_id=row_id,
            admission_id=plan["admission_id"],
        )
        assert result["status"] == "COMPLETED", result
        cell = Cell.no_workspace_objects.get(row_id=row_id, column_id=plan["column_id"])
        assert cell.value == "reply to message: refund?"
        assert cell.status == CellStatus.PASS.value
        assert json.loads(cell.value_infos)["http_attempt_id"].startswith(
            plan["admission_id"] + ":"
        )
    assert len(agent) == len(plan["row_ids"])
    assert {sent["body"]["method"] for sent in agent} == {"message/send"}
    finished = http_source.finish_http_source(
        str(experiment.pk), str(source.pk), plan["admission_id"]
    )
    assert finished == {
        "completed_rows": len(plan["row_ids"]),
        "failed_rows": 0,
        "status": "COMPLETED",
    }
    # The parent workflow enumerates it with the HTTP sources, so the same
    # evaluation step (process_batch_template_eval_activity) scores its rows.
    assert str(source.pk) in http_source.http_source_ids(experiment)


# --- Test on one row (the configuration card uses the endpoint test route) ------


def test_endpoint_test_header_accepts_bearer_from_a_secret_only():
    ok = http_endpoint_test.HttpEndpointHeader(
        data={"name": "Authorization", "secret_ref": "T", "scheme": "bearer"}
    )
    assert ok.is_valid(), ok.errors
    assert not http_endpoint_test.HttpEndpointHeader(
        data={"name": "Authorization", "value": "x", "scheme": "bearer"}
    ).is_valid()


def test_endpoint_test_sends_bearer_token_and_shows_only_the_secret_name():
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(
        _reply("message: hi") | {"jsonrpc": "2.0", "id": "1"}
    ).encode()
    with patch.object(outbound_destination, "request", return_value=response) as send:
        result = http_endpoint_test.execute_endpoint_request(
            {
                "url": AGENT_URL,
                "method": "POST",
                "body": "{}",
                "headers": [
                    {
                        "name": "Authorization",
                        "secret_ref": "ACME_TOKEN",
                        "scheme": "bearer",
                    }
                ],
            },
            resolve_secret=lambda name: "fixture-token",
        )
    assert send.call_args.kwargs["headers"]["Authorization"] == "Bearer fixture-token"
    assert (
        result["request"]["headers"]["Authorization"]
        == "Bearer •••• (from secret ACME_TOKEN)"
    )
    assert "fixture-token" not in json.dumps(result)
    assert result["response"]["result"]["parts"][0]["text"] == "reply to message: hi"
