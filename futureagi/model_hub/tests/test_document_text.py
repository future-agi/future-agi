import pytest

from model_hub.services import document_text


def test_text_extraction_is_bounded_and_normalized(monkeypatch):
    assert (
        document_text.extract_document_text(b"  alpha\x00\n beta  ", "notes.txt")
        == "alpha beta"
    )
    monkeypatch.setattr(document_text, "MAX_DOCUMENT_BYTES", 3)
    with pytest.raises(document_text.DocumentTextError, match="25 MiB"):
        document_text.extract_document_text(b"four", "notes.txt")


@pytest.mark.parametrize("name", ["notes.csv", "notes", "notes.exe"])
def test_unsupported_document_type_is_rejected(name):
    with pytest.raises(document_text.DocumentTextError, match="not supported"):
        document_text.extract_document_text(b"text", name)
