"""safe_fetch transport confidentiality (T6 #8, SEC-SCAN-FA-SEMGREP-HTTP-NOT-HTTPS-CONNECTION).

The public-IP guard stops SSRF; it does not stop a network observer from
reading or rewriting a cleartext fetch. These tests pin the transport rules:

- a hosted deployment (CLOUD_DEPLOYMENT names a Future AGI Cloud region)
  fetches over HTTPS only, on the first hop and on every redirect hop;
- no deployment follows an HTTPS -> HTTP downgrade redirect;
- caller credentials (Authorization, Cookie, X-Api-Key, X-Secret-Key,
  Proxy-Authorization, any custom auth header) reach only the exact HTTPS URL
  the caller named: never a redirect target (same host or not), never a
  cleartext hop.

A refusal under the hosted rule reaches the user naming https://
on every API-visible path that shows the user an error today.

DB-free and network-free: DNS and both urllib3 pool classes are faked.
"""

import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tfc.utils.ssrf_guard import SsrfBlocked, safe_fetch

PUBLIC_IP = "93.184.216.34"

# The user-facing text, literal on purpose: the tests pin the wording the
# release lead approved, not whatever the module happens to export.
HOSTED_HTTPS_MESSAGE = (
    "This URL must use https:// on Future AGI Cloud (http:// URLs are not fetched)."
)
HTTPS_DOWNGRADE_MESSAGE = "Redirect from https:// to http:// refused."
GENERIC_INVALID_URL = "The provided URL is invalid or inaccessible."

SENSITIVE_HEADER_NAMES = [
    "Authorization",
    "authorization",
    "AUTHORIZATION",
    "Cookie",
    "X-Api-Key",
    "x-secret-key",
    "X-Secret-Key",
    "Proxy-Authorization",
    "X-Custom-Auth-Token",
]
NON_SENSITIVE = {"User-Agent": "fa-test-agent", "Accept": "audio/*"}


def _response(status, headers=None, body=b""):
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.stream.return_value = iter([body] if body else [])
    return resp


def _header_names(headers):
    return {str(name).lower() for name in headers}


@pytest.fixture
def public_dns(monkeypatch):
    """Every name resolves to one public address; records what was looked up."""
    looked_up = []

    def fake_getaddrinfo(host, port):
        looked_up.append(host)
        return [(None, None, None, None, (PUBLIC_IP, 0))]

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", fake_getaddrinfo)
    return looked_up


@pytest.fixture
def transport(monkeypatch):
    """Fake urllib3 pools. Queue responses in ``responses``; every request
    lands in ``requests`` with the scheme of the pool that sent it."""
    state = SimpleNamespace(requests=[], responses=[])

    def pool_factory(scheme):
        def construct(*args, **kwargs):
            pool = MagicMock()

            def request(method, path, headers, redirect, preload_content):
                state.requests.append(
                    {
                        "scheme": scheme,
                        "method": method,
                        "path": path,
                        "headers": dict(headers),
                    }
                )
                return state.responses.pop(0)

            pool.request.side_effect = request
            return pool

        return construct

    state.http_pool_cls = MagicMock(side_effect=pool_factory("http"))
    state.https_pool_cls = MagicMock(side_effect=pool_factory("https"))
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool", state.http_pool_cls
    )
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.urllib3.HTTPSConnectionPool", state.https_pool_cls
    )
    return state


@pytest.fixture
def hosted(settings, monkeypatch):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    settings.CLOUD_DEPLOYMENT = "US"


@pytest.fixture
def self_hosted(settings, monkeypatch):
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    settings.CLOUD_DEPLOYMENT = ""


# --- hosted: HTTPS only, first hop and every redirect hop -------------------


def test_hosted_refuses_plain_http_url_before_dns_or_connect(
    hosted, public_dns, transport
):
    transport.responses.append(_response(200, {"Content-Type": "audio/mpeg"}, b"x"))

    with pytest.raises(SsrfBlocked, match=re.escape(HOSTED_HTTPS_MESSAGE)):
        safe_fetch("http://media.example.com/a.mp3", method="GET")

    assert transport.requests == []
    transport.http_pool_cls.assert_not_called()
    assert public_dns == []


@pytest.mark.parametrize(
    "location",
    ["http://cdn.example.net/a.mp3", "http://media.example.com/a.mp3"],
    ids=["cross-host", "same-host"],
)
def test_hosted_refuses_https_to_http_redirect(hosted, public_dns, transport, location):
    transport.responses += [
        _response(302, {"Location": location}),
        _response(200, {"Content-Type": "audio/mpeg"}, b"x"),
    ]

    with pytest.raises(SsrfBlocked, match=re.escape(HTTPS_DOWNGRADE_MESSAGE)):
        safe_fetch(
            "https://media.example.com/a.mp3",
            method="GET",
            headers={"Authorization": "Bearer secret"},
        )

    assert [r["scheme"] for r in transport.requests] == ["https"]
    transport.http_pool_cls.assert_not_called()


def test_hosted_https_fetch_still_works(hosted, public_dns, transport):
    body = b"ID3\x04\x00audio-bytes"
    transport.responses.append(_response(200, {"Content-Type": "audio/mpeg"}, body))

    response = safe_fetch(
        "https://media.example.com/a.mp3",
        method="GET",
        headers={"Authorization": "Bearer secret", **NON_SENSITIVE},
    )

    assert response.status_code == 200
    assert response.content == body
    assert response.final_url == "https://media.example.com/a.mp3"
    assert response.headers["content-type"] == "audio/mpeg"
    transport.http_pool_cls.assert_not_called()
    _, kwargs = transport.https_pool_cls.call_args
    assert kwargs["assert_hostname"] == "media.example.com"
    assert kwargs["server_hostname"] == "media.example.com"
    assert kwargs["cert_reqs"] == "CERT_REQUIRED"
    # The caller's own HTTPS target still gets the caller's headers.
    (sent,) = transport.requests
    assert sent["headers"]["Authorization"] == "Bearer secret"
    assert sent["headers"]["Host"] == "media.example.com"


# --- every deployment: no downgrade, credentials only to the named URL ------


def test_self_hosted_refuses_https_to_http_downgrade_on_the_same_host(
    self_hosted, public_dns, transport
):
    transport.responses += [
        _response(301, {"Location": "http://media.example.com/a.mp3"}),
        _response(200, {"Content-Type": "audio/mpeg"}, b"x"),
    ]

    with pytest.raises(SsrfBlocked, match=re.escape(HTTPS_DOWNGRADE_MESSAGE)):
        safe_fetch(
            "https://media.example.com/a.mp3",
            method="GET",
            headers={"Authorization": "Bearer secret", "Cookie": "session=abc"},
        )

    assert [r["scheme"] for r in transport.requests] == ["https"]
    transport.http_pool_cls.assert_not_called()


def test_self_hosted_refuses_a_downgrade_after_an_upgrade(
    self_hosted, public_dns, transport
):
    transport.responses += [
        _response(301, {"Location": "https://media.example.com/a.mp3"}),
        _response(302, {"Location": "http://media.example.com/b.mp3"}),
        _response(200, {"Content-Type": "audio/mpeg"}, b"x"),
    ]

    with pytest.raises(SsrfBlocked, match=re.escape(HTTPS_DOWNGRADE_MESSAGE)):
        safe_fetch("http://media.example.com/a.mp3", method="GET")

    assert [r["scheme"] for r in transport.requests] == ["http", "https"]


@pytest.mark.parametrize("deployment", ["hosted", "self_hosted"])
@pytest.mark.parametrize("sensitive", SENSITIVE_HEADER_NAMES)
@pytest.mark.parametrize(
    "location",
    ["/b.mp3", "https://media.example.com/b.mp3", "https://cdn.example.net/b.mp3"],
    ids=["same-host-relative", "same-host-absolute", "cross-host"],
)
def test_redirect_hops_never_carry_sensitive_headers(
    request, public_dns, transport, deployment, sensitive, location
):
    request.getfixturevalue(deployment)
    transport.responses += [
        _response(302, {"Location": location}),
        _response(200, {"Content-Type": "audio/mpeg"}, b"x"),
    ]

    response = safe_fetch(
        "https://media.example.com/a.mp3",
        method="GET",
        headers={sensitive: "secret-value", **NON_SENSITIVE},
    )

    assert response.status_code == 200
    first, second = transport.requests
    assert first["headers"][sensitive] == "secret-value"
    assert sensitive.lower() not in _header_names(second["headers"])
    assert "secret-value" not in second["headers"].values()
    # Non-sensitive request headers still follow the redirect.
    assert second["headers"]["User-Agent"] == NON_SENSITIVE["User-Agent"]
    assert second["headers"]["Accept"] == NON_SENSITIVE["Accept"]


def test_self_hosted_plain_http_fetch_never_carries_credentials(
    self_hosted, public_dns, transport
):
    """Self-hosted keeps public HTTP media working, but without credentials."""
    transport.responses.append(_response(200, {"Content-Type": "audio/mpeg"}, b"x"))

    response = safe_fetch(
        "http://media.example.com/a.mp3",
        method="GET",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=abc",
            "X-Api-Key": "k",
            "X-Secret-Key": "s",
            **NON_SENSITIVE,
        },
    )

    assert response.status_code == 200
    (sent,) = transport.requests
    assert sent["scheme"] == "http"
    assert _header_names(sent["headers"]).isdisjoint(
        {"authorization", "cookie", "x-api-key", "x-secret-key"}
    )
    assert sent["headers"]["User-Agent"] == NON_SENSITIVE["User-Agent"]


# --- callers: user- and provider-supplied URLs --------------------------------


def test_retell_provider_log_downgrade_redirect_is_never_followed(
    self_hosted, public_dns, transport
):
    """Provider-supplied URL: Retell's public_log_url must start https://, and a
    downgrade redirect from it must not be read over cleartext."""
    from tracer.utils.retell import _attach_provider_log_issues

    transport.responses += [
        _response(302, {"Location": "http://logs.example.net/call-123.log"}),
        _response(
            200,
            {"Content-Type": "text/plain"},
            b"2026-01-31 10:00:00.250 call-123 error: injected\n",
        ),
    ]
    attrs = {}

    _attach_provider_log_issues(
        {"public_log_url": "https://logs.example.net/call-123.log"}, attrs
    )

    assert attrs == {}
    transport.http_pool_cls.assert_not_called()


def test_hosted_audio_url_over_http_is_never_fetched(
    hosted, public_dns, transport, monkeypatch
):
    """User-supplied audio URL in a prompt (model_hub prompt_template)."""
    from model_hub.views import prompt_template

    monkeypatch.setattr(prompt_template, "get_model_mode", lambda model: "audio")
    monkeypatch.setattr(prompt_template, "detect_audio_format", lambda data: "mp3")
    transport.responses.append(
        _response(200, {"Content-Type": "audio/mpeg"}, b"ID3\x04\x00audio")
    )

    result = prompt_template.handle_media(
        {"type": "audio_url", "audio_url": {"url": "http://media.example.com/a.mp3"}},
        "some-audio-model",
    )

    assert result is None
    assert transport.requests == []
    transport.http_pool_cls.assert_not_called()


# --- the hosted refusal names https:// wherever the user sees it ---


@pytest.fixture
def private_dns(monkeypatch):
    """Every name resolves to a private address (an SSRF refusal, not HTTPS)."""
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("10.0.0.5", 0))],
    )


def test_check_image_names_https_for_a_hosted_http_url(hosted, public_dns, transport):
    """tfc.utils.validations.check_image used to collapse the refusal into the
    generic INVALID_URL text, so the user never learned https:// was needed."""
    from tfc.utils.validations import check_image

    with pytest.raises(ValueError) as excinfo:
        check_image("http://media.example.com/cat.png")

    assert str(excinfo.value) == HOSTED_HTTPS_MESSAGE
    assert transport.requests == []


def test_check_image_keeps_the_generic_error_for_a_private_host(
    hosted, private_dns, transport
):
    """Only the HTTPS rule is spelled out; an internal address stays generic."""
    from tfc.utils.validations import check_image

    with pytest.raises(ValueError) as excinfo:
        check_image("https://intranet.example.com/cat.png")

    assert str(excinfo.value) == GENERIC_INVALID_URL
    assert transport.requests == []


def test_log_model_payload_image_names_https(hosted, public_dns, transport):
    """check_image's caller: a chat_history image_url is validated by pydantic,
    whose error carries the ValueError text."""
    from pydantic import ValidationError

    from tfc.utils.validations import LogModelPayload

    with pytest.raises(ValidationError) as excinfo:
        LogModelPayload(
            model_id="m",
            model_type=8,
            environment=1,
            model_version="v1",
            conversation={
                "chat_history": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "http://media.example.com/a.png"},
                            }
                        ],
                    }
                ]
            },
        )

    assert HOSTED_HTTPS_MESSAGE in str(excinfo.value)
    assert transport.requests == []


@pytest.mark.parametrize("file_type", ["image", "document", "audio"])
def test_validate_file_url_names_https(hosted, public_dns, transport, file_type):
    """Dataset media URL validation (model_hub.views.utils.validate_file_url)."""
    from model_hub.views.utils.utils import validate_file_url

    with pytest.raises(ValueError) as excinfo:
        validate_file_url("http://media.example.com/a.bin", file_type)

    assert str(excinfo.value) == HOSTED_HTTPS_MESSAGE
    assert transport.requests == []


def test_run_prompt_audio_names_https(hosted, public_dns, transport, monkeypatch):
    """run_prompt re-raises the audio fetch ValueError; the cell shows its text."""
    from model_hub.views import run_prompt

    monkeypatch.setattr(run_prompt, "get_model_mode", lambda model: "audio")
    marker = "{{AUDIO_1}}"

    with pytest.raises(ValueError) as excinfo:
        run_prompt.process_media_markers(
            f"Listen: {marker}",
            {
                marker: {
                    "type": "audio",
                    "url": "http://media.example.com/a.mp3",
                    "counter": 1,
                }
            },
            "some-audio-model",
        )

    assert str(excinfo.value) == HOSTED_HTTPS_MESSAGE
    assert transport.requests == []


@pytest.mark.parametrize(
    "helper",
    [
        "download_image_from_url",
        "convert_image_from_url_to_base64",
        "download_document_from_url",
        "download_audio_from_url",
    ],
)
def test_storage_downloads_name_https(hosted, public_dns, transport, helper):
    """Media ingest downloads fail fast and keep the refusal text."""
    from tfc.utils import storage

    with pytest.raises(ValueError) as excinfo:
        getattr(storage, helper)("http://media.example.com/a.bin")

    assert HOSTED_HTTPS_MESSAGE in str(excinfo.value)
    assert transport.requests == []


@pytest.mark.parametrize("data_type", ["image", "audio", "document"])
def test_dataset_file_upload_cell_reason_names_https(
    hosted, public_dns, transport, data_type
):
    """Dataset file import: the failed cell's reason is what the user reads."""
    from model_hub.views.datasets.create.file_upload import upload_cell_media

    result = upload_cell_media(
        {
            "cell_id": "cell-1",
            "cell_value": "http://media.example.com/a.bin",
            "data_type": data_type,
            "value_infos": None,
        },
        "dataset-1",
    )

    assert result["success"] is False
    assert HOSTED_HTTPS_MESSAGE in result["value_infos"]["reason"]
    assert transport.requests == []


# --- the downgrade refusal keeps its own text, on every deployment --


@pytest.mark.parametrize("deployment", ["hosted", "self_hosted"])
def test_check_image_names_the_downgrade(request, public_dns, transport, deployment):
    """A downgrade is the URL's redirect, not the URL the user typed: the user
    reads the downgrade text, not the hosted text and not INVALID_URL."""
    from tfc.utils.validations import check_image

    request.getfixturevalue(deployment)
    transport.responses += [
        _response(302, {"Location": "http://media.example.com/cat.png"}),
        _response(200, {"Content-Type": "image/png"}, b"x"),
    ]

    with pytest.raises(ValueError) as excinfo:
        check_image("https://media.example.com/cat.png")

    assert str(excinfo.value) == HTTPS_DOWNGRADE_MESSAGE
    assert [r["scheme"] for r in transport.requests] == ["https"]


@pytest.mark.parametrize("deployment", ["hosted", "self_hosted"])
def test_validate_file_url_names_the_downgrade(
    request, public_dns, transport, deployment
):
    from model_hub.views.utils.utils import validate_file_url

    request.getfixturevalue(deployment)
    transport.responses += [
        _response(301, {"Location": "http://cdn.example.net/a.png"}),
        _response(200, {"Content-Type": "image/png"}),
    ]

    with pytest.raises(ValueError) as excinfo:
        validate_file_url("https://media.example.com/a.png", "image")

    assert str(excinfo.value) == HTTPS_DOWNGRADE_MESSAGE
    assert [r["scheme"] for r in transport.requests] == ["https"]


# --- the dataset cell editor's API response names the refusal -----
#
# POST /model-hub/develops/{dataset_id}/update_cell_value/ answers an image or
# images refusal with a fixed "Invalid image(s) value" and stores the real
# reason only in cell.value_infos. The storage helpers wrap the refusal
# (ValueError("ERROR_DOWNLOADING_IMAGE: ...") from HttpsRequired), so the
# response must recover the transport text from the exception chain, and must
# keep the generic text for every other failure.

GENERIC_CELL_ERRORS = {
    "image": "Invalid image value",
    "images": "Invalid images value",
}


@pytest.fixture(scope="module")
def develop_dataset():
    """Imported once, before any function-scoped fixture fakes DNS: importing
    the view module makes cache/broker clients try their own lookups."""
    from model_hub.views import develop_dataset

    return develop_dataset


@pytest.fixture
def update_cell(monkeypatch, develop_dataset):
    """Call UpdateCellValueView.post for one image/images cell. Only the cell
    lookup, the row scoping and the editability check are faked; the upload,
    download and SSRF code are real (DNS and the pools come from the other
    fixtures)."""
    from model_hub.services import dataset_validators

    monkeypatch.setattr(
        develop_dataset, "_request_row_queryset", lambda request: MagicMock()
    )
    monkeypatch.setattr(
        dataset_validators, "validate_column_is_editable", lambda column: (True, None)
    )

    def post(data_type, url):
        cell = SimpleNamespace(
            column=SimpleNamespace(data_type=data_type),
            column_metadata={},
            value=None,
            value_infos=None,
            status=None,
            save=MagicMock(),
        )
        lookup = MagicMock()
        lookup.select_related.return_value.first.return_value = cell
        monkeypatch.setattr(
            develop_dataset.Cell.objects, "filter", lambda *a, **kw: lookup
        )
        new_value = json.dumps([url]) if data_type == "images" else url
        payload = {"row_id": "row-1", "column_id": "column-1", "new_value": new_value}
        request = SimpleNamespace(validated_data=payload, data=payload, FILES={})
        view = develop_dataset.UpdateCellValueView()
        response = inspect.unwrap(view.post)(view, request, "dataset-1")
        return response, cell

    return post


@pytest.mark.parametrize("data_type", ["image", "images"])
def test_update_cell_api_names_https_for_a_hosted_http_url(
    hosted, public_dns, transport, update_cell, data_type
):
    response, cell = update_cell(data_type, "http://media.example.com/cat.png")

    assert response.status_code == 400
    assert HOSTED_HTTPS_MESSAGE in json.loads(cell.value_infos)["reason"]
    assert HOSTED_HTTPS_MESSAGE in json.dumps(response.data), response.data
    assert transport.requests == []


@pytest.mark.parametrize("deployment", ["hosted", "self_hosted"])
@pytest.mark.parametrize("data_type", ["image", "images"])
def test_update_cell_api_names_the_downgrade(
    request, public_dns, transport, update_cell, deployment, data_type
):
    request.getfixturevalue(deployment)
    transport.responses += [
        _response(302, {"Location": "http://media.example.com/cat.png"}),
    ]

    response, cell = update_cell(data_type, "https://media.example.com/cat.png")

    assert response.status_code == 400
    assert HTTPS_DOWNGRADE_MESSAGE in json.dumps(response.data), response.data
    assert HOSTED_HTTPS_MESSAGE not in json.dumps(response.data)
    assert [r["scheme"] for r in transport.requests] == ["https"]


@pytest.mark.parametrize("data_type", ["image", "images"])
def test_update_cell_api_keeps_the_generic_error_for_other_refusals(
    hosted, private_dns, transport, update_cell, data_type
):
    """Only the transport rule is spelled out: a private-address refusal (and
    any other failure) still answers with the fixed generic text."""
    response, cell = update_cell(data_type, "https://intranet.example.com/cat.png")

    assert response.status_code == 400
    assert GENERIC_CELL_ERRORS[data_type] in json.dumps(response.data)
    assert "https://" not in json.dumps(response.data)
    assert "10.0.0.5" not in json.dumps(response.data)
    assert transport.requests == []


def test_https_refusal_text_is_found_through_wrapping_exceptions():
    """The helper the views use: the refusal text survives `raise ... from`
    and implicit chaining; anything that is not the transport rule (including
    an address refusal) yields None; a cyclic chain still terminates."""
    from tfc.utils.ssrf_guard import HttpsRequired, https_refusal_message

    refusal = HttpsRequired(HOSTED_HTTPS_MESSAGE)
    assert https_refusal_message(refusal) == HOSTED_HTTPS_MESSAGE

    try:
        try:
            raise refusal
        except SsrfBlocked as e:
            raise ValueError(f"ERROR_DOWNLOADING_IMAGE: {e}") from e
    except ValueError as wrapped:
        assert https_refusal_message(wrapped) == HOSTED_HTTPS_MESSAGE

    try:
        try:
            raise HttpsRequired(HTTPS_DOWNGRADE_MESSAGE)
        except SsrfBlocked:
            raise ValueError("Failed to upload image") from None
    except ValueError as suppressed:
        # `from None` hides the cause on purpose; it is not surfaced.
        assert https_refusal_message(suppressed) is None

    try:
        try:
            raise HttpsRequired(HTTPS_DOWNGRADE_MESSAGE)
        except SsrfBlocked:
            raise ValueError("Failed to upload image")  # noqa: B904
    except ValueError as implicit:
        assert https_refusal_message(implicit) == HTTPS_DOWNGRADE_MESSAGE

    assert https_refusal_message(SsrfBlocked("private address")) is None
    assert https_refusal_message(ValueError("bad base64")) is None

    a, b = ValueError("a"), ValueError("b")
    a.__cause__, b.__cause__ = b, a
    assert https_refusal_message(a) is None
