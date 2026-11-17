"""HTTP-only admission, preview parity, transport and durable classic cells.

Written before the implementation. Not executed by the static-only writer.
The local endpoint fixture replaces only destination routing, never responses.
The create-route test also requires the separately owned create/runner wiring.
"""

import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests

from model_hub.models.choices import StatusType

from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentDetailV2Serializer,
    PromptConfigEntrySerializer,
)
from model_hub.services import http_experiment_source as http_source
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy
from tfc.utils import outbound_destination


tenants = tenancy.tenants
COLUMN = "c7868984-d789-4f1e-a97e-0c6ac7e71e58"


def entry():
    return {
        "target_type": "http",
        "name": "Answer endpoint",
        "variable_mapping": {"question": COLUMN},
        "http": {
            "url": "https://endpoint.example/answer",
            "method": "POST",
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "body_template": '{"question":"{{question}}","id":"{{row_id}}"}',
            "output_path": "$.reply",
            "streaming": False,
            "audio": "text",
        },
    }


def test_http_only_entry_retains_settings_without_prompt_defaults():
    body = entry()
    body["http"]["headers"].append({"name": "X-Test", "secret": "TEST_HEADER_REF"})
    serializer = PromptConfigEntrySerializer(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == body
    assert not {"model", "prompt_id", "prompt_version", "agent_id", "messages", "model_params", "configuration", "output_format"} & set(
        serializer.validated_data
    )
    # Exercise create-level branching without an evaluator database lookup.
    parent = ExperimentCreateV2Serializer()
    data = {"experiment_type": "llm", "prompt_config": [serializer.validated_data]}
    assert parent.validate(data)["prompt_config"] == [body]


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "unexpected-model"},
        {"prompt_id": str(uuid4())},
        {"agent_id": str(uuid4())},
        {"messages": [{"role": "user", "content": "not an HTTP body"}]},
        {"configuration": {"tools": []}},
        {"configuration": {}},
        {"model_params": {}},
        {"output_format": "string"},
        {"output_format": "object"},
    ],
)
def test_http_never_falls_back_to_prompt(extra):
    serializer = PromptConfigEntrySerializer(data={**entry(), **extra})
    assert not serializer.is_valid()


@pytest.mark.parametrize(
    "change",
    [
        {"streaming": True},
        {"audio": "audio"},
        {"output_path": "$.items[*].reply"},
        {"url": "not-a-url"},
        {"unexpected_setting": True},
        {"headers": [{"name": "X-Test", "secret": "REF", "value": "literal"}]},
    ],
)
def test_unimplemented_or_ambiguous_settings_are_not_silently_dropped(change):
    body = entry()
    body["http"].update(change)
    serializer = PromptConfigEntrySerializer(data=body)
    assert not serializer.is_valid()


def test_prompt_and_agent_admission_is_unchanged():
    assert not PromptConfigEntrySerializer(data={"name": "no model"}).is_valid()
    agent = PromptConfigEntrySerializer(
        data={"agent_id": str(uuid4()), "agent_version": str(uuid4())}
    )
    assert agent.is_valid(), agent.errors
    prompt = PromptConfigEntrySerializer(data={"model": "test-model"})
    assert prompt.is_valid(), prompt.errors
    unsupported = PromptConfigEntrySerializer(data={"target_type": "code"})
    assert not unsupported.is_valid()


def test_request_matches_preview_escaping_mapping_and_header_references():
    body = entry()
    body["http"]["url"] += "?q={{question}}"
    body["http"]["headers"].append({"name": "X-Test", "secret": "TEST_HEADER_REF"})
    body["http"]["body_template"] = (
        '{"q":"{{question}}","items":{{items}},"empty":"{{empty}}",'
        '"fixed":"{{fixed}}","unknown":"{{unknown}}"}'
    )
    body["variable_mapping"].update({
        "items": f"{COLUMN}::$['items']",
        "question": {"kind": "constant", "value": 'a "quote"\n/+'},
        "empty": {"kind": "empty"},
        "fixed": {"kind": "constant", "value": "fixed"},
    })
    request = http_source.build_request(body, values={COLUMN: {"items": [False, 0]}})
    assert request["url"].endswith("?q=a%20%22quote%22%0A%2F%2B")
    assert request["body"] == (
        r'{"q":"a \"quote\"\n/+","items":[false,0],"empty":"",'
        '"fixed":"fixed","unknown":"{{unknown}}"}'
    )
    assert request["headers"][-1] == {"name": "X-Test", "secret_ref": "TEST_HEADER_REF"}
    assert request["missing"] == ["unknown"]
    assert request["empty"] == ["empty"]


def test_named_columns_missing_cells_and_get_match_preview():
    body = entry()
    body["http"].update(method="GET", url="https://endpoint.example/{{question}}")
    body["variable_mapping"] = {}
    request = http_source.build_request(
        body, values={}, columns=[{"id": COLUMN, "name": "question"}], row_id="row-1"
    )
    assert request["url"] == "https://endpoint.example/"
    assert request["body"] == ""
    assert request["empty"] == ["question"]
    assert request["missing"] == []


@pytest.fixture
def endpoint(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
            received.append({"body": raw, "headers": dict(self.headers)})
            question = json.loads(raw)["question"]
            payload = json.dumps({"reply": f"answer:{question}"}).encode("utf-8")
            self.send_response(503 if question == "fail" else 200)
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
        assert url == "https://endpoint.example/answer"
        # Fixture-only local routing. Production uses outbound_destination.request.
        with requests.Session() as session:
            session.trust_env = False
            return session.request(
                method, f"http://127.0.0.1:{server.server_port}/answer",
                headers=headers, data=data.encode("utf-8") if data is not None else None,
                timeout=timeout, allow_redirects=False,
            )

    monkeypatch.setattr(outbound_destination, "request", send)
    try:
        yield received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_each_row_reaches_real_endpoint_with_previewed_bytes(endpoint):
    body = entry()
    for index, question in enumerate(['a "quote"\n', "fail"]):
        values = {COLUMN: question}
        preview = http_source.build_request(body, values=values, row_id=str(index))
        result = http_source.execute_http_row(body, values=values, row_id=str(index))
        assert endpoint[index]["body"] == preview["body"]
        assert result["latency_ms"] >= 0
        if question == "fail":
            assert result["output"] is None
            assert result["error"]["code"] == "http_status"
            assert result["error"]["retryable"] is True
            assert result["status_code"] == 503
        else:
            assert result["output"] == f"answer:{question}"
            assert result["error"] is None
    assert len(endpoint) == 2


def test_timeout_settles_once_with_measured_latency_and_recovery(monkeypatch):
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(http_source, "monotonic", lambda: next(ticks))
    with patch.object(
        outbound_destination, "request", side_effect=outbound_destination.DestinationError("deadline")
    ) as send:
        result = http_source.execute_http_row(entry(), values={COLUMN: "hello"}, row_id="row-1")
    send.assert_called_once()
    assert result["latency_ms"] == 125.0
    assert result["output"] is None
    assert result["error"]["code"] == "timeout"
    assert "Retry" in result["error"]["message"]


def test_header_references_resolve_only_at_dispatch_and_are_not_saved(endpoint):
    body = entry()
    body["http"]["headers"].append({"name": "X-Test", "secret_ref": "TEST_HEADER_REF"})
    before = deepcopy(body)
    result = http_source.execute_http_row(
        body, values={COLUMN: "hello"}, row_id="row-1", resolve_secret=lambda ref: "fixture-only-value"
    )
    assert endpoint[0]["headers"]["X-Test"] == "fixture-only-value"
    assert body == before
    assert "fixture-only-value" not in json.dumps(result)
    assert result["error"] is None


def test_unresolved_header_never_dispatches_an_unauthenticated_request():
    body = entry()
    body["http"]["headers"].append({"name": "X-Test", "secret": "TEST_HEADER_REF"})
    with patch.object(outbound_destination, "request") as send:
        result = http_source.execute_http_row(body, values={COLUMN: "hello"}, row_id="row-1")
    send.assert_not_called()
    assert result["error"]["code"] == "header_reference"


@pytest.mark.parametrize("payload, expected", [({"reply": None}, ""), ({"reply": False}, "false"), ({"reply": [0]}, "[0]")])
def test_output_types_and_explicit_null_follow_preview(payload, expected):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(entry(), values={COLUMN: "hello"}, row_id="row-1")
    assert result["output"] == expected
    assert result["error"] is None


def test_missing_output_is_a_row_error_not_a_fabricated_success():
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"different":"answer"}'
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(entry(), values={COLUMN: "hello"}, row_id="row-1")
    assert result["output"] is None
    assert result["error"]["code"] == "output_path"


@pytest.mark.django_db
def test_source_and_success_error_cells_survive_reload(tenants, endpoint):
    from model_hub.models.develop_dataset import Cell, Row
    from model_hub.models.experiments import ExperimentPromptConfig

    fixture = tenants["a"]["exp"]
    experiment = fixture["experiment"]
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    body = entry()
    input_column = experiment.dataset.column_set.get()
    body["variable_mapping"] = {"question": str(input_column.id)}
    source = http_source.persist_http_source(experiment, body, order=1)
    source.columns.add(fixture["output_column"])
    source.refresh_from_db()
    assert source.legacy_prompt_config["http"] == body["http"]
    assert source.legacy_prompt_config["variable_mapping"] == body["variable_mapping"]
    assert not ExperimentPromptConfig.no_workspace_objects.filter(experiment_dataset=source).exists()
    readback = ExperimentDetailV2Serializer().get_http_configs(experiment)
    assert readback[0]["http"] == body["http"]
    assert readback[0]["variable_mapping"] == body["variable_mapping"]
    assert readback[0]["target_type"] == "http"
    for index, question in enumerate(["ok", "fail"]):
        row = Row.objects.create(dataset=experiment.snapshot_dataset, order=index + 10)
        result = http_source.execute_http_row(
            source.legacy_prompt_config, values={str(input_column.id): question}, row_id=str(row.pk)
        )
        attempt_id = f"run-1:row-{index}"
        assert http_source.claim_http_attempt(
            source=source, row=row, column=fixture["output_column"], attempt_id=attempt_id,
        )
        cell = http_source.persist_http_result(
            source=source, row=row, column=fixture["output_column"], result=result,
            attempt_id=attempt_id,
        )
        cell = Cell.objects.get(pk=cell.pk)
        info = json.loads(cell.value_infos)
        assert info["metadata"]["response_time"] == result["latency_ms"]
        if question == "ok":
            assert cell.value == "answer:ok"
            assert cell.status == "pass"
        else:
            assert cell.status == "error"
            assert "HTTP 503" in info["reason"]
            assert "Retry" in info["reason"]


@pytest.mark.django_db
def test_http_only_create_route_dispatches_without_prompt_lookup(tenants):
    """Integration RED until the separately owned create view calls the helper."""
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate
    from model_hub.models.experiments import ExperimentsTable

    actor = tenants["a"]
    dataset = actor["exp"]["dataset"]
    evaluator = EvalTemplate.no_workspace_objects.create(
        name="http-output-check", organization=actor["org"], workspace=actor["workspace"],
        owner=OwnerChoices.USER.value, criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    source = entry()
    source["variable_mapping"] = {"question": str(dataset.column_set.get().pk)}
    body = {
        "name": "HTTP-only run", "dataset_id": str(dataset.pk), "prompt_config": [source],
        "user_eval_metrics": [{"template_id": str(evaluator.pk), "name": "http-output-check", "config": {"mapping": {"output": "output"}}}],
    }
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow", return_value="admitted") as start:
        response = actor["client"].post("/model-hub/experiments/v2/", body, format="json")
    assert response.status_code == 200, response.content
    start.assert_called_once()
    experiment = ExperimentsTable.no_workspace_objects.get(dataset=dataset, name=body["name"])
    source_record = experiment.experiment_datasets.get(legacy_prompt_config__target_type="http")
    assert source_record.legacy_prompt_config["variable_mapping"] == source["variable_mapping"]
    assert not hasattr(source_record, "prompt_config")
    assert not hasattr(source_record, "agent_config")


@pytest.mark.parametrize("index, expected", [("-1", "last"), ("-2", "first"), ("-3", "")])
def test_quoted_negative_mapping_matches_preview(index, expected):
    body = entry()
    body["http"]["body_template"] = '{"question":"{{question}}"}'
    body["variable_mapping"]["question"] = f'{COLUMN}::$.items["{index}"]'
    request = http_source.build_request(body, values={COLUMN: {"items": ["first", "last"]}})
    assert json.loads(request["body"]) == {"question": expected}


@pytest.mark.parametrize("index, expected", [("-1", "last"), ("-2", "first"), ("-3", None)])
def test_quoted_negative_output_matches_preview(index, expected):
    body = entry()
    body["http"]["output_path"] = f'$.items["{index}"]'
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"items":["first","last"]}'
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(body, values={COLUMN: "hello"}, row_id="row-1")
    assert result["output"] == expected
    if expected is None:
        assert result["error"]["code"] == "output_path"
    else:
        assert result["error"] is None


def test_unmapped_variables_do_not_dispatch():
    body = entry()
    body["http"]["body_template"] = '{"q":"{{unknown}}"}'
    with patch.object(outbound_destination, "request") as send:
        result = http_source.execute_http_row(body, values={})
    send.assert_not_called()
    assert result["error"]["code"] == "request_config"


def test_echoed_header_secret_is_not_persistable_output():
    body = entry()
    body["http"]["headers"].append({"name": "Authorization", "secret_ref": "TOKEN"})
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"reply":"echo fixture-only-value"}'
    with patch.object(outbound_destination, "request", return_value=response):
        result = http_source.execute_http_row(
            body, values={COLUMN: "hello"}, row_id="row-1",
            resolve_secret=lambda ref: "fixture-only-value",
        )
    assert result["error"] is None
    assert result["output"] == "echo [redacted]"
    assert "fixture-only-value" not in json.dumps(result)


@pytest.mark.django_db
def test_cross_org_mapping_is_rejected_before_persist(tenants):
    from model_hub.models.experiments import ExperimentDatasetTable

    a, b = tenants["a"]["exp"], tenants["b"]["exp"]
    body = entry()
    body["variable_mapping"] = {"question": str(b["dataset"].column_set.get().pk)}
    before = ExperimentDatasetTable.no_workspace_objects.count()
    with pytest.raises(ValueError, match="unavailable in this dataset"):
        http_source.persist_http_source(a["experiment"], body)
    assert ExperimentDatasetTable.no_workspace_objects.count() == before


@pytest.mark.django_db
def test_http_attempt_fences_duplicate_stale_cancelled_and_foreign_writes(tenants):
    from model_hub.models.develop_dataset import Cell

    a, b = tenants["a"]["exp"], tenants["b"]["exp"]
    experiment = a["experiment"]
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    body = entry()
    body["variable_mapping"] = {"question": str(a["dataset"].column_set.get().pk)}
    source = http_source.persist_http_source(experiment, body)
    source.columns.add(a["output_column"])
    Cell.no_workspace_objects.filter(pk=a["output_cell"].pk).delete()
    args = {"source": source, "row": a["snapshot_row"], "column": a["output_column"]}
    assert http_source.claim_http_attempt(**args, attempt_id="A")
    assert not http_source.claim_http_attempt(**args, attempt_id="A")
    assert not http_source.claim_http_attempt(**args, attempt_id="B", retry=True)
    result = {"output": None, "error": {"code": "timeout", "message": "Retry after checking the endpoint.", "retryable": True},
              "status_code": None, "latency_ms": 30}
    assert http_source.persist_http_result(**args, result=result, attempt_id="A")
    assert http_source.claim_http_attempt(**args, attempt_id="B", retry=True)
    assert http_source.persist_http_result(**args, result=result, attempt_id="A") is None
    with pytest.raises(ValueError, match="snapshot source"):
        http_source.claim_http_attempt(
            source=source, row=b["snapshot_row"], column=b["output_column"], attempt_id="foreign",
        )
    experiment.status = StatusType.CANCELLED.value
    experiment.save(update_fields=["status"])
    assert http_source.persist_http_result(**args, result=result, attempt_id="B") is None
    assert not http_source.claim_http_attempt(**args, attempt_id="C", retry=True)
