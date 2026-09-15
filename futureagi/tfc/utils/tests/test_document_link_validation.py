"""Unit tests for document-cell link validation helpers (#2433)."""

import pytest

from tfc.utils.document_link import (
    DOCUMENT_ADDRESS_NOT_A_DOCUMENT,
    DOCUMENT_ADDRESS_TOO_LARGE,
    DOCUMENT_ADDRESS_UNREACHABLE,
    DOCUMENT_NOT_A_WEB_ADDRESS,
    document_link_failure_message,
    is_http_web_address,
    resolve_document_cell_input,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://example.com/report.pdf", True),
        ("http://example.com/a.docx", True),
        ("HTTPS://EXAMPLE.COM/REPORT.PDF", True),
        ("HTTP://EXAMPLE.COM/A.DOCX", True),
        ("sssss", False),
        ("not a url", False),
        ("ftp://example.com/a.pdf", False),
        ("javascript:alert(1)", False),
        ("data:application/pdf;base64,AAA", False),
        ("https://", False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_is_http_web_address(value, expected):
    assert is_http_web_address(value) is expected


def test_resolve_clears_intentional_empty():
    assert resolve_document_cell_input(None, None) == ("clear", None)
    assert resolve_document_cell_input("", None) == ("clear", None)
    assert resolve_document_cell_input("   ", None) == ("clear", None)


def test_resolve_rejects_non_url_garbage():
    action, message = resolve_document_cell_input("sssss", None)
    assert action == "reject"
    assert message == DOCUMENT_NOT_A_WEB_ADDRESS


def test_resolve_rejects_non_http_converted_value():
    action, message = resolve_document_cell_input("ftp://x/a.pdf", "ftp://x/a.pdf")
    assert action == "reject"
    assert message == DOCUMENT_NOT_A_WEB_ADDRESS


def test_resolve_stores_http_url():
    url = "https://cdn.example.com/paper.pdf"
    assert resolve_document_cell_input(url, url) == ("store", url)


def test_resolve_stores_data_uri_upload():
    data_uri = "data:application/pdf;base64,AAAA"
    assert resolve_document_cell_input(data_uri, data_uri) == ("store", data_uri)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (
            ValueError("Invalid document data (Content-Type: text/html)"),
            DOCUMENT_ADDRESS_NOT_A_DOCUMENT,
        ),
        (
            ValueError("The provided file type is not supported."),
            DOCUMENT_ADDRESS_NOT_A_DOCUMENT,
        ),
        (
            ValueError("Downloaded file is empty"),
            DOCUMENT_ADDRESS_NOT_A_DOCUMENT,
        ),
        (
            ValueError("The provided URL is not valid."),
            DOCUMENT_NOT_A_WEB_ADDRESS,
        ),
        (
            ValueError("ERROR_DOWNLOADING_DOCUMENT: Max retries exceeded"),
            DOCUMENT_ADDRESS_UNREACHABLE,
        ),
        (
            ValueError("Unable to process link. Status Code: 404"),
            DOCUMENT_ADDRESS_UNREACHABLE,
        ),
        (
            ValueError("ERROR_DOWNLOADING_DOCUMENT: blocked"),
            DOCUMENT_ADDRESS_UNREACHABLE,
        ),
        (
            ValueError("URL body exceeds 104857600 byte limit."),
            DOCUMENT_ADDRESS_TOO_LARGE,
        ),
    ],
)
def test_document_link_failure_message(exc, expected):
    assert document_link_failure_message(exc) == expected


def test_resolve_stores_uppercase_scheme_url():
    url = "HTTPS://EXAMPLE.COM/REPORT.PDF"
    assert is_http_web_address(url) is True
    assert resolve_document_cell_input(url, url) == ("store", url)
