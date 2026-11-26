"""Indented trace-detail output of a tree too deep for the stock encoder.

``TraceDetailJSONRenderer`` re-encodes only compact output. An indented
request (``Accept: application/json; indent=N``, or ``indent`` in the renderer
context) for a tree the stock encoder cannot render is declined with a small
JSON 406 on the trace-detail and shared-issue trace endpoints, instead of a
render-time server error. Compact output and every body the stock renderer
can render are unchanged.

Deep bodies are compared as sha256 and length only (see
``test_trace_detail_renderer``).
"""

import json
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.renderers import JSONRenderer

from tfc.utils.api_errors import build_error_envelope
from tracer.renderers import TraceDetailJSONRenderer
from tracer.tests import test_trace_detail_deep_tree as deep_tree
from tracer.tests.test_shared_link_resolve import (
    _member_trace_id,
    _share_error_issue,
)
from tracer.tests.test_trace_detail_deep_tree import _envelope as _detail_envelope
from tracer.tests.test_trace_detail_deep_tree import (
    _patched_fetch,
    _stock_bytes,
    _v2_chain_result,
)
from tracer.tests.test_trace_detail_renderer import (
    _chain,
    _chain_oracle,
    _digest,
    _entry,
    _envelope,
)

licensed_error_issue = deep_tree.licensed_error_issue

# On 3.11 (production and CI) the stock encoder fails on a 999-deep chain.
IS_PY311 = sys.version_info[:2] == (3, 11)
INDENT = "application/json; indent=4"
DECLINED_MESSAGE = (
    "This trace is too deep to return as indented JSON. Request it without an indent."
)


def _declined_body(accepted_media_type=INDENT, indent=None):
    context = {} if indent is None else {"indent": indent}
    return JSONRenderer().render(
        build_error_envelope(
            DECLINED_MESSAGE,
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            code="not_acceptable",
        ),
        accepted_media_type,
        context,
    )


def _stock_fails_on(tree):
    """Patch the stock renderer to fail only on ``tree``, as on a deep chain."""

    original = JSONRenderer.render

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is tree:
            raise RecursionError("maximum recursion depth exceeded")
        return original(self, data, accepted_media_type, renderer_context)

    return patch.object(JSONRenderer, "render", render)


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize(
    ("accepted_media_type", "indent"),
    [(INDENT, None), ("application/json", 2), ("application/json; indent=0", None)],
    ids=["accept-indent", "context-indent", "accept-indent-0"],
)
def test_indented_render_of_too_deep_tree_becomes_a_406(accepted_media_type, indent):
    data = _envelope([_entry(1, [])])
    response = SimpleNamespace(status_code=status.HTTP_200_OK)
    context = {"response": response}
    if indent is not None:
        context["indent"] = indent

    with _stock_fails_on(data):
        try:
            body = TraceDetailJSONRenderer().render(data, accepted_media_type, context)
        except RecursionError:
            raise AssertionError(
                "indented tree was not declined with a JSON 406"
            ) from None

    assert response.status_code == status.HTTP_406_NOT_ACCEPTABLE
    assert body == _declined_body(accepted_media_type, indent)
    assert len(body) < 1024
    decoded = json.loads(body)
    assert decoded["status"] is False
    assert decoded["code"] == "not_acceptable"
    assert decoded["message"] == DECLINED_MESSAGE


@pytest.mark.unit
def test_compact_render_of_too_deep_tree_keeps_the_fallback_and_status():
    data = _envelope([_entry(1, [_entry(2, [])])])
    expected = JSONRenderer().render(data)
    response = SimpleNamespace(status_code=status.HTTP_200_OK)

    with _stock_fails_on(data):
        body = TraceDetailJSONRenderer().render(
            data, "application/json", {"response": response}
        )

    assert body == expected
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.unit
def test_indented_render_without_a_response_still_raises():
    data = _envelope([_entry(1, [])])

    with _stock_fails_on(data):
        with pytest.raises(RecursionError):
            TraceDetailJSONRenderer().render(data, INDENT, {})
        with pytest.raises(RecursionError):
            TraceDetailJSONRenderer().render(data, None, {"indent": 2})


@pytest.mark.unit
@pytest.mark.parametrize("accepted_media_type", [None, "application/json", INDENT])
def test_renderable_tree_keeps_stock_bytes_and_status(accepted_media_type):
    data = _chain(50)
    response = SimpleNamespace(status_code=status.HTTP_200_OK)

    body = TraceDetailJSONRenderer().render(
        data, accepted_media_type, {"response": response}
    )

    assert body == JSONRenderer().render(data, accepted_media_type, {})
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.unit
@pytest.mark.parametrize("depth", [999, 1000])
def test_real_deep_chain_with_indent_is_a_406(depth):
    data = _chain(depth)
    response = SimpleNamespace(status_code=status.HTTP_200_OK)

    try:
        body = TraceDetailJSONRenderer().render(data, INDENT, {"response": response})
    except RecursionError:
        if IS_PY311:
            raise AssertionError(
                "deep indented tree was not declined with a JSON 406"
            ) from None
        raise

    if IS_PY311:
        assert response.status_code == status.HTTP_406_NOT_ACCEPTABLE
        assert body == _declined_body()
    # The same tree, compact, still renders in full.
    compact = TraceDetailJSONRenderer().render(data)
    assert _digest(compact) == _digest(_chain_oracle(data))


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def _assert_declined(response):
    assert response.status_code == status.HTTP_406_NOT_ACCEPTABLE
    assert type(response.accepted_renderer) is TraceDetailJSONRenderer
    assert response["Content-Type"] == "application/json"
    assert response.content == _declined_body()
    assert response.data == build_error_envelope(
        DECLINED_MESSAGE,
        status_code=status.HTTP_406_NOT_ACCEPTABLE,
        code="not_acceptable",
    )
    assert json.loads(response.content)["code"] == "not_acceptable"


@pytest.mark.django_db
def test_trace_detail_endpoint_declines_indented_deep_chain(auth_client):
    result = _v2_chain_result(999)
    with _patched_fetch(lambda handler: result):
        try:
            response = auth_client.get(
                f"/tracer/trace/{uuid.uuid4()}/", HTTP_ACCEPT=INDENT
            )
        except RecursionError:
            if IS_PY311:
                raise AssertionError(
                    "trace-detail view raised while rendering indented output"
                ) from None
            raise

    if IS_PY311:
        _assert_declined(response)
    else:
        assert response.status_code in {
            status.HTTP_200_OK,
            status.HTTP_406_NOT_ACCEPTABLE,
        }


@pytest.mark.django_db
def test_trace_detail_endpoint_compact_deep_chain_is_unchanged(auth_client):
    result = _v2_chain_result(999)
    expected = _chain_oracle(_detail_envelope(result))
    with _patched_fetch(lambda handler: result):
        response = auth_client.get(f"/tracer/trace/{uuid.uuid4()}/")

    assert response.status_code == status.HTTP_200_OK
    assert _digest(response.content) == _digest(expected)


@pytest.mark.django_db
def test_trace_detail_endpoint_indented_shallow_tree_is_unchanged(auth_client):
    result = _v2_chain_result(3)
    with _patched_fetch(lambda handler: result):
        response = auth_client.get(f"/tracer/trace/{uuid.uuid4()}/", HTTP_ACCEPT=INDENT)

    assert response.status_code == status.HTTP_200_OK
    assert response.content == _stock_bytes(response)
    assert b"\n    " in response.content


def _get_shared_trace_with_accept(api_client, auth_client, issue, result, accept):
    token = _share_error_issue(auth_client, issue.cluster_id).json()["result"]["token"]
    with patch(
        "tracer.services.clickhouse.v2.query_builders.trace_detail."
        "retrieve_trace_detail_ch",
        return_value=result,
    ):
        return api_client.get(
            f"/tracer/shared/{token}/traces/{_member_trace_id(issue)}/",
            HTTP_ACCEPT=accept,
        )


@pytest.mark.django_db
def test_shared_issue_trace_declines_indented_deep_chain(
    api_client, auth_client, licensed_error_issue
):
    result = _v2_chain_result(999)
    try:
        response = _get_shared_trace_with_accept(
            api_client, auth_client, licensed_error_issue, result, INDENT
        )
    except RecursionError:
        if IS_PY311:
            raise AssertionError(
                "shared trace view raised while rendering indented output"
            ) from None
        raise

    if IS_PY311:
        _assert_declined(response)
    else:
        assert response.status_code in {
            status.HTTP_200_OK,
            status.HTTP_406_NOT_ACCEPTABLE,
        }


@pytest.mark.django_db
def test_shared_issue_trace_indented_shallow_tree_is_unchanged(
    api_client, auth_client, licensed_error_issue
):
    result = _v2_chain_result(3)
    response = _get_shared_trace_with_accept(
        api_client, auth_client, licensed_error_issue, result, INDENT
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.content == _stock_bytes(response)
