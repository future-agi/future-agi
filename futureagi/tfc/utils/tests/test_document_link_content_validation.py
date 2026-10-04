"""Regression coverage for the stricter Dataset document Link path (TH-2258)."""

import io
import zipfile

import pytest

from tfc.utils.document_link import (
    DocumentLinkValidationError,
    document_link_display_name,
    inspect_document_link_bytes,
)


def _ooxml(kind):
    path = {
        "docx": "word/document.xml",
        "xlsx": "xl/workbook.xml",
        "pptx": "ppt/presentation.xml",
    }[kind]
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(path, "<document />")
    return result.getvalue()


def _odt():
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", "<document-content />")
    return result.getvalue()


@pytest.mark.parametrize(
    ("content", "declared_type", "expected_type"),
    [
        (b"%PDF-1.7\n%%EOF", "application/pdf", "application/pdf"),
        (_ooxml("docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (_ooxml("xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (_ooxml("pptx"), "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        (b"name,score\nAda,100\n", "text/csv", "text/csv"),
        (b"plain notes\n", "text/plain; charset=utf-8", "text/plain"),
        (b"{\\rtf1\\ansi hello}", "text/rtf", "text/rtf"),
    ],
)
def test_signed_or_extensionless_links_are_classified_from_bytes(
    content, declared_type, expected_type
):
    assert inspect_document_link_bytes(content, declared_type) == expected_type


@pytest.mark.parametrize(
    ("content", "declared_type"),
    [
        (b"<html><body>not a document link</body></html>", "text/html"),
        (b"<?xml version='1.0'?><root />", "application/xml"),
        (_odt(), "application/vnd.oasis.opendocument.text"),
        (b"<html><body>pretending to be a PDF</body></html>", "application/pdf"),
        (_ooxml("docx"), "application/pdf"),
        (b"\x00\x01\x02", "application/octet-stream"),
    ],
)
def test_link_rejects_disallowed_or_mismatched_content(content, declared_type):
    with pytest.raises(DocumentLinkValidationError):
        inspect_document_link_bytes(content, declared_type)


def test_plain_text_with_markup_examples_and_login_is_not_blacklisted():
    text = b"Example markup: <tag>login</tag> is literal documentation.\n"
    assert inspect_document_link_bytes(text, "text/plain") == "text/plain"


def test_display_name_excludes_signed_query_bytes():
    assert (
        document_link_display_name(
            "https://cdn.example.com/files/report.pdf?X-Amz-Signature=secret&X-Amz-Credential=also-secret"
        )
        == "report.pdf"
    )
