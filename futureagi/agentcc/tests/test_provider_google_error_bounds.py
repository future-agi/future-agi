"""Bounded Google error discrimination using real Requests and inert transport.

Run with the pre-import socket/DNS guard, including before Django startup.
The 47 discovery tests in the sibling module remain unchanged controls.
"""

import json
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest
import requests

from agentcc.tests import test_provider_google_auth_discovery as support
from agentcc.tests.r19i_error_support import block_network
from agentcc.views import provider_credential as module

# Independent contract literals: changing a product limit must not move the tests.
BYTE_LIMIT = 8192
DEPTH_LIMIT = 8
CONTAINER_LIMIT = 128
DETAIL_LIMIT = 16
PRIVATE_LOCATION = "https://redirect.invalid/private-location-sentinel"


def _encoded(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _padded(size):
    body = _encoded(support._refusal())
    return body + b" " * (size - len(body))


def _nested(depth):
    return b"[" * depth + b"null" + b"]" * depth


def _with_extra(raw_value):
    body = _encoded(support._refusal())
    return body[:-1] + b', "extra":' + raw_value + b"}"


def _many_containers(count):
    # Canonical envelope has five containers; the extra array adds one.
    payload = support._refusal()
    payload["extra"] = [[] for _ in range(count - 6)]
    return _encoded(payload)


def _case(case):
    payload = support._refusal()
    encoding = "utf-8"
    if case == "depth-over":
        return _with_extra(_nested(DEPTH_LIMIT)), encoding
    if case == "decoder-depth":
        return _with_extra(_nested(1200)), encoding
    if case == "bytes-over":
        return _padded(BYTE_LIMIT + 1), encoding
    if case == "multibyte-over":
        payload["extra"] = "é" * (BYTE_LIMIT // 2)
    elif case == "containers-over":
        return _many_containers(CONTAINER_LIMIT + 1), encoding
    elif case in ("details-over-first", "details-over-last", "huge-details"):
        detail = payload["error"]["details"][0]
        others = [None] * (512 if case == "huge-details" else DETAIL_LIMIT)
        payload["error"]["details"] = (
            others + [detail] if case == "details-over-last" else [detail] + others
        )
    elif case in ("huge-type", "huge-domain", "huge-reason"):
        field = case.split("-", 1)[1]
        field = "@type" if field == "type" else field
        # An oversized unrelated discriminator cannot precede a valid verdict.
        payload["error"]["details"].insert(0, {field: "x" * BYTE_LIMIT})
    elif case == "malformed-tail":
        return _encoded(payload) + b"!", encoding
    elif case == "truncated":
        return _encoded(payload)[:-1], encoding
    elif case == "invalid-utf8":
        return _with_extra(b'"\xff"'), encoding
    elif case in ("bom-declared", "bom-unspecified"):
        return b"\xef\xbb\xbf" + _encoded(payload), (
            None if case == "bom-unspecified" else encoding
        )
    elif case in ("utf16-le", "utf16-be", "utf32"):
        codec = {"utf16-le": "utf-16-le", "utf16-be": "utf-16-be", "utf32": "utf-32"}[
            case
        ]
        return json.dumps(payload).encode(codec), codec
    elif case in ("latin1", "latin1-ascii"):
        payload["extra"] = "é" if case == "latin1" else "ascii"
        return json.dumps(payload, ensure_ascii=False).encode("latin-1"), "latin-1"
    elif case == "unknown-encoding":
        encoding = "not-a-codec"
    elif case == "unrelated":
        payload["error"]["details"][0]["reason"] = "API_DISABLED"
    elif case == "large-unknown-discriminator":
        payload["error"]["details"][0]["reason"] = "x" * 4096
    elif case == "bytes-at":
        return _padded(BYTE_LIMIT), encoding
    elif case == "bytes-under":
        return _padded(BYTE_LIMIT - 1), encoding
    elif case == "depth-at":
        return _with_extra(_nested(DEPTH_LIMIT - 1)), encoding
    elif case == "containers-at":
        return _many_containers(CONTAINER_LIMIT), encoding
    elif case == "details-at":
        payload["error"]["details"][:0] = [None] * (DETAIL_LIMIT - 1)
    elif case == "quoted-delimiters":
        payload["extra"] = '\\"' + "[{]}" * 140 + '\\\\"'
    elif case == "utf8-unspecified":
        encoding = None
    elif case == "utf8-uppercase":
        encoding = "UTF-8"
    else:
        assert case == "canonical"
    return _encoded(payload), encoding


@contextmanager
def _transport(payload, encoding):
    errors = []
    responses = []

    class Adapter(support._NoNetworkAdapter):
        def send(self, request, **kwargs):
            response = super().send(request, **kwargs)
            response.encoding = encoding
            response.headers["Location"] = PRIVATE_LOCATION
            original_raise = response.raise_for_status

            def observed_raise():
                try:
                    original_raise()
                except requests.HTTPError as error:
                    errors.append(error)
                    raise

            response.raise_for_status = observed_raise
            responses.append(response)
            return response

    with ExitStack() as stack:
        block_network(stack)
        # Requests can optionally select simplejson. Pin its real stdlib decoder
        # so BOM RED expectations do not depend on that optional installation.
        stack.enter_context(patch.object(requests.models, "complexjson", json))
        stack.enter_context(patch.object(support, "_NoNetworkAdapter", Adapter))
        normalizer = stack.enter_context(
            patch.object(module, "exception_response", wraps=module.exception_response)
        )
        yield errors, responses, normalizer


def _assert_rejection(body, encoding):
    with _transport(body, encoding) as (errors, responses, normalizer):
        response, logger, _ = support._fetch(body)
    assert response.status_code == 400
    assert response.data["code"] == "provider_request_rejected"
    assert isinstance(response.data["result"], str)
    assert len(errors) == len(responses) == 1
    # The same Requests exception, not a replacement/parser exception, escapes.
    assert normalizer.call_args.args[1] is errors[0]
    assert errors[0].response is responses[0]
    logger.warning.assert_called_once_with(
        "fetch_models_error", error_type="HTTPError", status_code=400
    )
    emitted = json.dumps(response.data) + repr(logger.mock_calls)
    assert PRIVATE_LOCATION not in emitted, "Private Location escaped"


@pytest.mark.parametrize(
    "case",
    [
        "depth-over",
        "decoder-depth",
        "bytes-over",
        "multibyte-over",
        "containers-over",
        "details-over-first",
        "details-over-last",
        "huge-details",
        "huge-type",
        "huge-domain",
        "huge-reason",
        "malformed-tail",
        "truncated",
        "invalid-utf8",
        "bom-declared",
        "bom-unspecified",
        "utf16-le",
        "utf16-be",
        "utf32",
        "latin1",
        "latin1-ascii",
        "unknown-encoding",
        "unrelated",
        "large-unknown-discriminator",
    ],
)
def test_refused_google_body_preserves_private_http_400(case):
    _assert_rejection(*_case(case))


@pytest.mark.parametrize(
    "case",
    [
        "canonical",
        "bytes-under",
        "bytes-at",
        "depth-at",
        "containers-at",
        "details-at",
        "quoted-delimiters",
        "utf8-unspecified",
        "utf8-uppercase",
    ],
)
def test_bounded_canonical_body_keeps_private_authentication_verdict(case):
    body, encoding = _case(case)
    with _transport(body, encoding):
        response, logger, _ = support._fetch(body)
    support._assert_verdict(response)
    logger.warning.assert_called_once_with(
        "fetch_models_error",
        error_type="ProviderAuthenticationRejected",
        status_code=400,
    )
    assert PRIVATE_LOCATION not in json.dumps(response.data) + repr(logger.mock_calls)


@contextmanager
def _received_response(body, encoding="utf-8"):
    with _transport(body, encoding):
        with requests.Session() as session:
            session.trust_env = False
            session.mount("https://", support._NoNetworkAdapter(body, 400))
            yield session.get("https://transport.invalid/models")


@pytest.mark.parametrize(
    "case", ["bytes-over", "depth-over", "containers-over", "latin1-ascii"]
)
def test_bounds_are_checked_before_json_decoding(case):
    body, encoding = _case(case)
    with _received_response(body, encoding) as response:
        # create=True keeps the tests runnable at the tests-only parent.
        with patch.object(module, "loads", wraps=json.loads, create=True) as decoder:
            assert module._google_key_rejected(response) is False
        decoder.assert_not_called()


@pytest.mark.parametrize("failure", ["value", "recursion", "unicode"])
def test_decoder_refusal_preserves_original_http_error(failure):
    errors = {
        "value": ValueError(support.PRIVATE_BODY),
        "recursion": RecursionError(support.PRIVATE_BODY),
        "unicode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, support.PRIVATE_BODY),
    }
    with patch.object(module, "loads", side_effect=errors[failure], create=True):
        _assert_rejection(*_case("canonical"))


@pytest.mark.parametrize(
    "failure", [MemoryError, KeyboardInterrupt, SystemExit, GeneratorExit]
)
def test_parser_does_not_swallow_fatal_exceptions(failure):
    with _received_response(_encoded(support._refusal())) as response:
        with patch.object(module, "loads", side_effect=failure, create=True):
            with pytest.raises(failure):
                module._google_key_rejected(response)


def test_error_discriminator_never_uses_requests_json():
    with _received_response(_encoded(support._refusal())) as response:
        with patch.object(
            response, "json", side_effect=AssertionError("Unbounded decoder")
        ):
            assert module._google_key_rejected(response) is True


def test_error_discriminator_never_reads_an_unreceived_stream():
    class NoRead:
        def stream(self, *args, **kwargs):
            raise AssertionError("Must not read transport")

    with _received_response(_encoded(support._refusal())) as response:
        response._content = False
        response._content_consumed = False
        response.raw = NoRead()
        assert module._google_key_rejected(response) is False
