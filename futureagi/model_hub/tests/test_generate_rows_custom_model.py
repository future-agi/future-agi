"""Generate rows with a Settings › AI Providers custom model (provider "custom").

The rows arrive inside the model's chat-completions answer
(``choices[0].message.content``). Generate rows reads that text, not the HTTP
body; it unwraps a Markdown code fence around the JSON and reads a bare list
as the rows. An answer that still is not usable rows says in plain words what
was wrong, and nothing is added or saved.

No network: the endpoint's host name resolves to a fixed public address and
``FakeTransport`` stands in for the connection ``guarded_post`` makes.
"""

import json

import pytest

from model_hub.models.custom_models import CustomAIModel
from model_hub.models.develop_dataset import Cell, Row
from model_hub.services.row_generation import GenerationOutputError, validate_output
from model_hub.tests import test_generate_rows as base
from tfc.utils.tests.guarded_post_fakes import (
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

grid = base.grid
path = base.path

PUBLIC_IP = "93.184.216.34"
ENDPOINT = "https://api.example.com/v1/chat/completions"
MODEL = "liquid/lfm-3-1.7b"
TENANT_AUTH = "Bearer tenant-key"
ROWS = {"rows": [{"question": "first"}, {"question": "second"}]}
AFTER = " No rows were added. Try again or choose another model."
QUESTION = {"num_rows": 2, "columns": [{"name": "question", "data_type": "text"}]}


class _EndpointResolver(FakeResolver):
    """The endpoint's host resolves to PUBLIC_IP; every other name (the test
    database, the cache) resolves as usual."""

    def __call__(self, host, port=None, *args, **kwargs):
        if str(host).lower().rstrip(".") in self.table:
            return super().__call__(host, port, *args, **kwargs)
        return self._real_getaddrinfo(host, port, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture
def transport(monkeypatch):
    resolver = _EndpointResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return FakeTransport().install(monkeypatch)


@pytest.fixture
def custom_model(user, workspace):
    """The workspace's model from Settings › AI Providers, provider "custom"."""
    return CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={
            "headers": {"Authorization": TENANT_AUTH},
            "api_base": ENDPOINT,
            "custom_provider": True,
        },
        user=user,
        deleted=False,
    )


@pytest.fixture
def config(grid, custom_model):
    """What Generate rows sends for 2 rows written by the custom model."""
    return {
        "generation_mode": "append",
        "model": MODEL,
        "num_rows": 2,
        "columns": [
            {
                "name": "question",
                "data_type": "text",
                "description": "A question",
                "property": [],
            }
        ],
        "dataset": {
            "name": grid.name,
            "description": "Refund questions",
            "objective": "Support",
            "patterns": "Short",
        },
    }


def _answer(content):
    """The endpoint's 200 chat-completions body with ``content`` as the
    assistant message."""
    body = {
        "id": "gen-1",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }
    return FakeResponse(200, JSON_HEADERS, json.dumps(body).encode())


def _row_ids(grid):
    return set(Row.objects.filter(dataset=grid).values_list("id", flat=True))


def _new_values(grid, before):
    return list(
        Cell.objects.filter(dataset=grid)
        .exclude(row_id__in=before)
        .order_by("row__order")
        .values_list("value", flat=True)
    )


@pytest.mark.django_db
def test_rows_in_the_custom_models_answer_are_added(
    auth_client, grid, config, transport
):
    # The answer is the message text, not the endpoint's JSON body.
    before = _row_ids(grid)
    transport.respond(_answer(json.dumps(ROWS)))
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 200, response.content
    assert _new_values(grid, before) == ["first", "second"]
    assert len(transport.requests) == 1
    sent = json.loads(transport.requests[0]["body"])
    assert "Generate synthetic dataset rows" in json.dumps(sent)
    assert transport.sent_headers["authorization"] == TENANT_AUTH
    grid.refresh_from_db()
    assert grid.dataset_config["row_generation"]["model"] == MODEL


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content",
    [
        "```json\n" + json.dumps(ROWS) + "\n```",
        json.dumps(ROWS["rows"]),
    ],
    ids=["code-fence", "bare-list"],
)
def test_fenced_or_bare_rows_from_the_custom_model_are_added(
    auth_client, grid, config, transport, content
):
    # A fenced or bare-list answer adds its rows.
    before = _row_ids(grid)
    transport.respond(_answer(content))
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 200, response.content
    assert _new_values(grid, before) == ["first", "second"]
    assert len(transport.requests) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content, reason",
    [
        ("France: Paris. Japan: Tokyo.", "The model's answer was not JSON."),
        (
            json.dumps({"data": ROWS["rows"]}),
            "The model's answer was not a list of rows.",
        ),
        (
            json.dumps({"rows": [{"question": "only one"}]}),
            "The model returned 1 row, not 2.",
        ),
        (
            json.dumps({"rows": [{"question": "first"}, {"answer": "second"}]}),
            'Row 2 in the model\'s answer was missing the column "question".',
        ),
    ],
    ids=["not-json", "no-rows-list", "row-count", "missing-column"],
)
def test_an_unusable_answer_says_what_was_wrong_and_adds_nothing(
    auth_client, grid, config, transport, content, reason
):
    # Each unusable answer names what was wrong and adds nothing.
    transport.respond(_answer(content))
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 502, response.content
    assert response.json()["message"] == reason + AFTER
    assert len(transport.requests) == 1
    assert Row.objects.filter(dataset=grid).count() == 1
    grid.refresh_from_db()
    assert not (grid.dataset_config or {}).get("row_generation")


@pytest.mark.parametrize(
    "content",
    [
        "```json\n" + json.dumps(ROWS) + "\n```",
        "```\n" + json.dumps(ROWS) + "\n```",
        "```json" + json.dumps(ROWS) + "```",
        json.dumps(ROWS["rows"]),
        "  " + json.dumps(ROWS) + "\n",
    ],
    ids=["json-fence", "bare-fence", "one-line-fence", "bare-list", "plain"],
)
def test_rows_are_read_from_a_fenced_or_bare_answer(content):
    assert validate_output(content, QUESTION) == ROWS["rows"]


@pytest.mark.parametrize(
    "content, reason",
    [
        ("", "The model's answer was empty."),
        ("  \n", "The model's answer was empty."),
        (None, "The model's answer was empty."),
        ("France: Paris.", "The model's answer was not JSON."),
        ("```json\nnot json\n```", "The model's answer was not JSON."),
        ('{"data": []}', "The model's answer was not a list of rows."),
        ('{"rows": "first, second"}', "The model's answer was not a list of rows."),
        ('"first"', "The model's answer was not a list of rows."),
        ('{"rows": []}', "The model returned 0 rows, not 2."),
        ('{"rows": [{"question": "a"}]}', "The model returned 1 row, not 2."),
        (
            '{"rows": [{"question": "a"}, {"question": "b"}, {"question": "c"}]}',
            "The model returned 3 rows, not 2.",
        ),
        (
            '{"rows": ["a", "b"]}',
            "Row 1 in the model's answer was not a set of column values.",
        ),
        (
            '{"rows": [{"question": "a"}, {}]}',
            'Row 2 in the model\'s answer was missing the column "question".',
        ),
        (
            '{"rows": [{"question": "a", "extra": true}, {"question": "b"}]}',
            "Row 1 in the model's answer had a column this dataset does not have.",
        ),
        (
            '{"rows": [{"question": 42}, {"question": "b"}]}',
            'Row 1 in the model\'s answer had a value for "question" that was not text.',
        ),
    ],
)
def test_an_unusable_answer_names_what_was_wrong(content, reason):
    with pytest.raises(GenerationOutputError) as raised:
        validate_output(content, QUESTION)
    assert str(raised.value) == reason + AFTER


@pytest.mark.parametrize(
    "kind, invalid, words",
    [
        ("integer", 1.5, "a whole number"),
        ("integer", True, "a whole number"),
        ("float", "1.5", "a number"),
        ("boolean", 0, "true or false"),
        ("json", [], "a JSON object"),
        ("array", {}, "a list"),
        ("datetime", "not-a-date", "a date and time"),
        ("datetime", "2026-13-45T00:00:00", "a date and time"),
    ],
)
def test_a_value_of_the_wrong_type_names_the_column_and_the_type(kind, invalid, words):
    config = {"num_rows": 1, "columns": [{"name": "value", "data_type": kind}]}
    with pytest.raises(GenerationOutputError) as raised:
        validate_output(json.dumps({"rows": [{"value": invalid}]}), config)
    assert str(raised.value) == (
        f'Row 1 in the model\'s answer had a value for "value" that was not {words}.'
        + AFTER
    )


def test_a_value_that_is_not_valid_json_is_named():
    config = {"num_rows": 1, "columns": [{"name": "value", "data_type": "json"}]}
    with pytest.raises(GenerationOutputError) as raised:
        validate_output('{"rows": [{"value": {"score": NaN}}]}', config)
    assert str(raised.value) == (
        'Row 1 in the model\'s answer had a value for "value" that was not valid '
        "JSON." + AFTER
    )


def test_a_number_too_large_for_a_float_is_not_a_float():
    from model_hub.services.row_generation import valid_value

    assert valid_value(10**400, "float") is False
