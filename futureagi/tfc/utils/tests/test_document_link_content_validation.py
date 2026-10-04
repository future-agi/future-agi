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


def _ole(stream_name):
    """A minimal valid CFB directory with one application-specific stream."""
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    fat_sector = 0xFFFFFFFD
    content = bytearray(512 * 3)
    content[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    content[28:30] = b"\xfe\xff"
    content[30:32] = (9).to_bytes(2, "little")
    content[32:34] = (6).to_bytes(2, "little")
    content[44:48] = (1).to_bytes(4, "little")
    content[48:52] = (0).to_bytes(4, "little")
    content[68:72] = free_sector.to_bytes(4, "little")
    content[72:76] = (0).to_bytes(4, "little")
    for offset in range(76, 512, 4):
        content[offset : offset + 4] = free_sector.to_bytes(4, "little")
    content[76:80] = (1).to_bytes(4, "little")

    name = stream_name.encode("utf-16-le") + b"\x00\x00"
    content[512 : 512 + len(name)] = name
    content[512 + 64 : 512 + 66] = len(name).to_bytes(2, "little")
    content[512 + 66] = 2  # user stream

    content[1024:1028] = end_of_chain.to_bytes(4, "little")
    content[1028:1032] = fat_sector.to_bytes(4, "little")
    for offset in range(1032, 1536, 4):
        content[offset : offset + 4] = free_sector.to_bytes(4, "little")
    return bytes(content)


@pytest.mark.parametrize(
    ("content", "declared_type", "expected_type"),
    [
        (b"%PDF-1.7\n%%EOF", "application/pdf", "application/pdf"),
        (_ooxml("docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (_ooxml("docx"), "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (_ooxml("xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (_ooxml("pptx"), "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        (_ole("WordDocument"), "application/msword", "application/msword"),
        (_ole("Workbook"), "application/vnd.ms-excel", "application/vnd.ms-excel"),
        (_ole("PowerPoint Document"), "application/vnd.ms-powerpoint", "application/vnd.ms-powerpoint"),
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
