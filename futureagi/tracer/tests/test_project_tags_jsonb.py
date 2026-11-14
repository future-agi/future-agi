"""Raw JSON tag input must be validated before reaching PostgreSQL jsonb.

Every write path that stores ``Project.tags`` is covered: the tags action
(PATCH /tracer/project/<id>/tags/) and the generic detail update
(PATCH and PUT /tracer/project/<id>/).

The tests run inside one rolled-back transaction each. Nothing here needs a
real commit, and flushing every table after each test dominated the runtime.
"""

import io
import json
import math
import sys
import uuid
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection
from rest_framework.parsers import JSONParser
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.integration,
    pytest.mark.api,
]

# The advertised nesting limit. The outermost tags list is level 1.
TAG_LIMIT = 500
TOO_DEEP = f"tags support at most {TAG_LIMIT} nested levels"
INVALID_STRING = "tags must not contain null characters or unpaired Unicode surrogates"
NON_FINITE = "tags must only contain finite numbers"

TAGS_ACTION = pytest.param("tags", id="tags-action")
DETAIL_PATCH = pytest.param("patch", id="detail-patch")
DETAIL_PUT = pytest.param("put", id="detail-put")
ALL_ROUTES = [TAGS_ACTION, DETAIL_PATCH, DETAIL_PUT]
# PUT shares PATCH's serializer, so deep cases need only one detail method.
DEEP_ROUTES = [TAGS_ACTION, DETAIL_PATCH]


@pytest.fixture
def tag_endpoint(organization, workspace, user, settings):
    assert connection.vendor == "postgresql"
    settings.DEBUG = False
    settings.DEBUG_PROPAGATE_EXCEPTIONS = False
    project = Project.objects.create(
        name="Synthetic tags project",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
        tags=["original"],
    )
    key = OrgApiKey.objects.create(
        name="Synthetic tags key",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
        enabled=True,
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    client = APIClient(raise_request_exception=False)
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    # Start requests from an empty model-manager scope, as
    # WorkspaceContextMiddleware does outside tests (the test settings leave it
    # out). Otherwise the session's first request imports every view under this
    # test's workspace, which class-level querysets keep for later tests.
    clear_workspace_context()
    return client, project, f"/tracer/project/{project.id}/tags/"


def send_tags(tag_endpoint, route, raw_body):
    """Send a raw ``{"tags": ...}`` JSON body through one tag write path."""
    client, project, tags_url = tag_endpoint
    if route == "tags":
        return client.patch(tags_url, data=raw_body, content_type="application/json")
    detail_url = f"/tracer/project/{project.id}/"
    if route == "patch":
        return client.patch(detail_url, data=raw_body, content_type="application/json")
    # A full update also carries the other writable required fields.
    assert raw_body.startswith(b'{"tags":')
    full_body = (
        b'{"name":"Synthetic tags project","model_type":"GenerativeLLM",'
        b'"trace_type":"observe",' + raw_body[1:]
    )
    return client.put(detail_url, data=full_body, content_type="application/json")


def returned_tags(route, response):
    body = response.json()
    return body["result"]["tags"] if route == "tags" else body["tags"]


def expected_message(route, reason):
    # Serializer errors on the detail routes are prefixed with the field name.
    return reason if route == "tags" else f"tags: {reason}"


def persisted_state(project):
    project.refresh_from_db()
    return project.tags, project.updated_at


def assert_validation_400(response, before, project):
    assert response.status_code == 400, response.content[:300]
    tags, updated_at = persisted_state(project)
    # Compare the timestamp first so a deep accidental write is not repr'd.
    assert updated_at == before[1]
    assert tags == before[0]
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["code"]
    assert body["detail"] == body["message"] == body["result"]
    assert response.exc_info is None
    return body


def nested_tag_body(raw_leaf, depth, container):
    opening, closing = (b"[", b"]") if container == "array" else (b'{"label":', b"}")
    return b'{"tags":[' + opening * depth + raw_leaf + closing * depth + b"]}"


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize(
    "raw_body",
    [
        pytest.param(b'{"tags":["\\u0000"]}', id="escaped-nul"),
        pytest.param(b'{"tags":["a\\u0000b"]}', id="embedded-nul"),
        pytest.param(b'{"tags":["\\ud800"]}', id="unpaired-high-surrogate"),
        pytest.param(b'{"tags":["\\udc00"]}', id="unpaired-low-surrogate"),
        pytest.param(b'{"tags":[["\\u0000"]]}', id="nested-nul"),
        pytest.param(b'{"tags":[{"\\ud800":"value"}]}', id="surrogate-key"),
    ],
)
def test_database_incompatible_tag_returns_400_without_mutation(
    tag_endpoint, route, raw_body
):
    _, project, _ = tag_endpoint
    # These are syntactically valid JSON bodies, accepted by the real parser.
    assert isinstance(json.loads(raw_body)["tags"], list)
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["message"] == expected_message(route, INVALID_STRING)


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize(
    ("raw_body", "expected"),
    [
        pytest.param(b'{"tags":["release","blue"]}', ["release", "blue"], id="ascii"),
        pytest.param(
            b'{"tags":["caf\\u00e9","\\u65e5\\u672c\\u8a9e","\\ud83d\\ude80","line\\nbreak"]}',
            ["café", "日本語", "\U0001f680", "line\nbreak"],
            id="valid-unicode-and-controls",
        ),
        pytest.param(b'{"tags":["\\\\u0000"]}', [r"\u0000"], id="literal-escape"),
        pytest.param(b'{"tags":[]}', [], id="clear-tags"),
        pytest.param(
            b'{"tags":[{"label":["release",1,true,null]}]}',
            [{"label": ["release", 1, True, None]}],
            id="existing-json-list-values",
        ),
    ],
)
def test_valid_tag_round_trip(tag_endpoint, route, raw_body, expected):
    _, project, _ = tag_endpoint
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    assert response.status_code == 200, response.content[:300]
    assert returned_tags(route, response) == expected
    tags, updated_at = persisted_state(project)
    assert tags == expected
    assert updated_at > before[1]
    assert response.exc_info is None


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize("container", ["array", "object"])
def test_tags_at_nesting_limit_round_trip(tag_endpoint, route, container):
    _, project, _ = tag_endpoint
    # The tags list, TAG_LIMIT - 2 containers, then the leaf list: TAG_LIMIT levels.
    raw_body = nested_tag_body(b'["release",1,true,null]', TAG_LIMIT - 2, container)
    # Compare serialized values so the test's equality check does not recurse.
    expected_json = json.dumps(json.loads(raw_body)["tags"])
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    assert response.status_code == 200, response.content[:300]
    assert json.dumps(returned_tags(route, response)) == expected_json
    tags, updated_at = persisted_state(project)
    assert json.dumps(tags) == expected_json
    assert updated_at > before[1]
    assert response.exc_info is None


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize("container", ["array", "object"])
def test_tags_past_nesting_limit_return_limit_message(tag_endpoint, route, container):
    # One level past the limit still parses, so the client is told the limit.
    _, project, _ = tag_endpoint
    raw_body = nested_tag_body(b'"release"', TAG_LIMIT, container)
    assert isinstance(json.loads(raw_body)["tags"], list)
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["message"] == expected_message(route, TOO_DEEP)


@pytest.mark.parametrize("route", DEEP_ROUTES)
@pytest.mark.parametrize("container", ["array", "object"])
@pytest.mark.parametrize(
    "raw_leaf",
    [
        pytest.param(b'"\\u0000"', id="nul-value"),
        pytest.param(b'"\\ud800"', id="high-surrogate-value"),
        pytest.param(b'"\\udc00"', id="low-surrogate-value"),
        pytest.param(b'{"\\u0000":"value"}', id="nul-key"),
        pytest.param(b'{"\\ud800":"value"}', id="high-surrogate-key"),
        pytest.param(b'{"\\udc00":"value"}', id="low-surrogate-key"),
    ],
)
def test_deep_invalid_tag_returns_400_without_mutation(
    tag_endpoint, route, container, raw_leaf
):
    # The bad string sits at the deepest level the limit allows.
    _, project, _ = tag_endpoint
    raw_body = nested_tag_body(raw_leaf, TAG_LIMIT - 2, container)
    assert isinstance(json.loads(raw_body)["tags"], list)
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["message"] == expected_message(route, INVALID_STRING)


@pytest.mark.parametrize("raw_body", [b"[]", b"{}", b'{"tags":"release"}'])
def test_existing_shape_validation_remains_400(tag_endpoint, raw_body):
    client, project, url = tag_endpoint
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"
    assert persisted_state(project) == before
    assert response.exc_info is None


def test_operational_database_failure_remains_503(tag_endpoint):
    client, project, url = tag_endpoint
    before = persisted_state(project)
    with patch.object(
        Project, "save", side_effect=OperationalError("synthetic DB unavailable")
    ) as save:
        response = client.patch(
            url, data=b'{"tags":["release"]}', content_type="application/json"
        )

    save.assert_called_once()
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "synthetic DB unavailable" not in response.content.decode()
    assert persisted_state(project) == before
    assert response.exc_info is None


def contains_non_finite(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            return True
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item.values())
    return False


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize(
    "raw_body",
    [
        pytest.param(b'{"tags":[1e400]}', id="positive-overflow"),
        pytest.param(b'{"tags":[-1e400]}', id="negative-overflow"),
        pytest.param(b'{"tags":[-1e309]}', id="just-past-max-float"),
        pytest.param(b'{"tags":[{"x":1e400}]}', id="object-value"),
        pytest.param(b'{"tags":[{"label":[1e999]}]}', id="list-in-object"),
        pytest.param(
            nested_tag_body(b"1e400", TAG_LIMIT - 1, "array"), id="deep-array"
        ),
        pytest.param(
            nested_tag_body(b"1e400", TAG_LIMIT - 1, "object"), id="deep-object"
        ),
    ],
)
def test_overflowing_number_tag_returns_400_without_mutation(
    tag_endpoint, route, raw_body
):
    _, project, _ = tag_endpoint
    # DRF's strict parser rejects only the NaN/Infinity literals; an overflowing
    # exponent becomes float("inf"), which PostgreSQL jsonb cannot store.
    parsed = JSONParser().parse(io.BytesIO(raw_body), "application/json", {})
    assert contains_non_finite(parsed["tags"])
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["message"] == expected_message(route, NON_FINITE)


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize("container", ["array", "object"])
def test_tags_deeper_than_json_parser_guard_return_400(tag_endpoint, route, container):
    _, project, _ = tag_endpoint
    raw_body = nested_tag_body(b'"release"', 100_000, container)
    # The C decoder raises RecursionError, which DRF does not map to a 400.
    with pytest.raises(RecursionError):
        json.loads(raw_body)
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["code"] == "parse_error"


@pytest.mark.parametrize("route", DEEP_ROUTES)
@pytest.mark.parametrize("depth", [1_001, 9_990])
def test_tags_far_past_nesting_limit_return_400(tag_endpoint, route, depth):
    # Python 3.11 cannot parse these depths in a request at all; interpreters
    # with a separate C recursion guard parse them and the limit rejects them.
    # Either way the client gets a 400, never a 500.
    _, project, _ = tag_endpoint
    raw_body = nested_tag_body(b'"release"', depth, "array")
    before = persisted_state(project)

    response = send_tags(tag_endpoint, route, raw_body)

    body = assert_validation_400(response, before, project)
    assert body["code"] == "parse_error" or body["message"] == expected_message(
        route, TOO_DEEP
    )


def nested_value(depth):
    value = "release"
    for _ in range(depth):
        value = [value]
    return value


def test_tag_nesting_limit_boundary():
    from tracer.utils.project_tags import TAG_MAX_NESTING_DEPTH, tag_validation_error

    assert TAG_MAX_NESTING_DEPTH == TAG_LIMIT
    assert tag_validation_error(nested_value(TAG_LIMIT)) is None
    assert tag_validation_error(nested_value(TAG_LIMIT + 1)) == TOO_DEEP
    deep_object = {"label": "release"}
    for _ in range(TAG_LIMIT - 1):
        deep_object = {"label": deep_object}
    assert tag_validation_error([deep_object]) == TOO_DEEP
    assert tag_validation_error([deep_object["label"]]) is None
    # The detail routes accept any JSON value for tags, including scalars.
    assert tag_validation_error(None) is None
    assert tag_validation_error("release") is None
    assert tag_validation_error(1.5) is None
    assert tag_validation_error("a\x00b") == INVALID_STRING
    assert tag_validation_error("\ud800") == INVALID_STRING
    assert tag_validation_error(float("inf")) == NON_FINITE
    assert tag_validation_error({"\ud800": "value"}) == INVALID_STRING


def test_save_time_recursion_error_returns_400_without_mutation(tag_endpoint):
    # Defensive path: a RecursionError from the database adapter's encoder.
    client, project, url = tag_endpoint
    before = persisted_state(project)
    with patch.object(
        Project, "save", side_effect=RecursionError("synthetic encoder depth")
    ) as save:
        response = client.patch(
            url, data=b'{"tags":["release"]}', content_type="application/json"
        )

    save.assert_called_once()
    body = assert_validation_400(response, before, project)
    assert body["message"] == "tags are nested too deeply to store"
    assert "synthetic encoder depth" not in response.content.decode()


def python_calls_during(fn):
    calls = 0

    def profiler(frame, event, arg):
        nonlocal calls
        if event == "call":
            calls += 1

    previous = sys.getprofile()
    sys.setprofile(profiler)
    try:
        result = fn()
    finally:
        sys.setprofile(previous)
    return calls, result


@pytest.mark.parametrize(
    "char",
    [
        pytest.param("a", id="ascii"),
        pytest.param("é", id="latin-1"),
        pytest.param("日", id="bmp"),
        pytest.param("\U0001f680", id="astral"),
    ],
)
def test_tag_validation_does_not_run_python_code_per_character(tag_endpoint, char):
    client, project, url = tag_endpoint

    def patch_tag(length):
        body = json.dumps({"tags": [char * length]}).encode()
        return client.patch(url, data=body, content_type="application/json")

    short_calls, short_response = python_calls_during(lambda: patch_tag(10))
    long_calls, long_response = python_calls_during(lambda: patch_tag(200_000))

    assert short_response.status_code == long_response.status_code == 200
    assert long_response.json()["result"]["tags"] == [char * 200_000]
    # A per-character Python scan adds a call per character (200,000 here).
    assert long_calls - short_calls < 1_000, (short_calls, long_calls)


def test_multipart_tags_keep_existing_400(tag_endpoint):
    client, project, url = tag_endpoint
    before = persisted_state(project)

    response = client.patch(url, data={"tags": "release"}, format="multipart")

    body = assert_validation_400(response, before, project)
    assert body["message"] == "tags must be a list"


@pytest.mark.parametrize(
    ("tags_text", "reason"),
    [
        pytest.param('["a\\u0000b"]', INVALID_STRING, id="escaped-nul"),
        pytest.param('[{"\\ud800":"value"}]', INVALID_STRING, id="surrogate-key"),
        pytest.param("[1e400]", NON_FINITE, id="overflow"),
        pytest.param("[" * 100_000 + "]" * 100_000, TOO_DEEP, id="past-parser"),
    ],
)
def test_detail_multipart_tags_text_is_validated(tag_endpoint, tags_text, reason):
    # Form input carries tags as JSON text, which the serializer decodes.
    client, project, _ = tag_endpoint
    before = persisted_state(project)

    response = client.patch(
        f"/tracer/project/{project.id}/", data={"tags": tags_text}, format="multipart"
    )

    body = assert_validation_400(response, before, project)
    assert body["message"] == expected_message("patch", reason)


def test_detail_multipart_valid_tags_text_round_trips(tag_endpoint):
    client, project, _ = tag_endpoint

    response = client.patch(
        f"/tracer/project/{project.id}/",
        data={"tags": '["release","caf\\u00e9"]'},
        format="multipart",
    )

    assert response.status_code == 200, response.content[:300]
    assert response.json()["tags"] == ["release", "café"]
    assert persisted_state(project)[0] == ["release", "café"]
