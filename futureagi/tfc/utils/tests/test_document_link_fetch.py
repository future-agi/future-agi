"""Fetch limits and query-redaction coverage for Dataset document Links."""

from unittest.mock import patch

import pytest
from requests.exceptions import RequestException

from tfc.utils.document_link import DocumentLinkTooLargeError
from tfc.utils.ssrf_guard import SsrfResponse
from tfc.utils.storage import (
    DOCUMENT_LINK_FETCH_BUDGET_SECONDS,
    download_document_link_from_url,
)


def _response(status, content=b"%PDF-1.7\n%%EOF", content_type="application/pdf"):
    return SsrfResponse(
        status,
        {"Content-Type": content_type},
        content,
        "https://cdn.example.com/download",
    )


def test_document_link_fetch_uses_at_most_one_retry_within_aggregate_budget():
    signed_url = "https://cdn.example.com/download?X-Amz-Signature=secret"
    with patch(
        "tfc.utils.storage.time.monotonic",
        side_effect=[0, 0, 16],
    ), patch(
        "tfc.utils.storage._ssrf_safe_get",
        side_effect=[_response(503), _response(200)],
    ) as fetch:
        content, content_type = download_document_link_from_url(signed_url)

    assert content == b"%PDF-1.7\n%%EOF"
    assert content_type == "application/pdf"
    assert fetch.call_count == 2
    assert sum(call.kwargs["timeout"] for call in fetch.call_args_list) <= (
        DOCUMENT_LINK_FETCH_BUDGET_SECONDS
    )
    assert all(call.kwargs["strict_redirect_origins"] for call in fetch.call_args_list)
    # The request must use the untouched query to retrieve a signed object.
    assert fetch.call_args.args[0] == signed_url


def test_document_link_fetch_reports_the_existing_100_mib_bound_without_url():
    signed_url = "https://cdn.example.com/download?X-Amz-Signature=secret"
    with patch(
        "tfc.utils.storage._ssrf_safe_get",
        side_effect=RequestException("URL body exceeds 104857600 byte limit."),
    ):
        with pytest.raises(DocumentLinkTooLargeError) as error:
            download_document_link_from_url(signed_url)

    assert "secret" not in str(error.value)
