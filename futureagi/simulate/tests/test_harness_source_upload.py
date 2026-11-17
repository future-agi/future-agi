import io
from types import SimpleNamespace

import pytest

from simulate.services import harness_sources
from simulate.services.hosted_harness import HostedHarnessError


def _capture(monkeypatch, name, content, purpose=None):
    monkeypatch.setattr(
        harness_sources,
        "_require_import_basis",
        lambda *args: harness_sources.drafts.NO_DRAFT,
    )
    monkeypatch.setattr(
        harness_sources,
        "_prepare_captured",
        lambda environment, captured, current_ref: captured,
    )
    uploaded = io.BytesIO(content)
    uploaded.name = name
    return harness_sources.prepare_upload_import(
        SimpleNamespace(),
        uploaded,
        purpose=purpose,
        expected_environment_version_id="version",
        expected_draft={"id": None, "revision": 0},
    )


@pytest.mark.parametrize(
    ("name", "purpose", "role", "media_type"),
    [
        ("guide.pdf", "policy", "policy", "application/pdf"),
        ("notes.txt", None, "reference", "text/plain"),
        ("records.csv", None, "seed_records", "text/csv"),
        ("records.json", None, "seed_records", "application/json"),
    ],
)
def test_upload_purpose_and_media_type(monkeypatch, name, purpose, role, media_type):
    source = _capture(monkeypatch, name, b"content", purpose)[0]
    assert (source.role, source.media_type) == (role, media_type)
    assert source.origin["kind"] == "upload"
    assert "uploaded_url" not in source.origin


def test_upload_rejects_unsupported_purpose(monkeypatch):
    with pytest.raises(HostedHarnessError) as caught:
        _capture(monkeypatch, "records.csv", b"id\n1", "policy")
    assert caught.value.code == "source_format_unsupported"


def test_upload_size_is_read_with_a_hard_bound(monkeypatch):
    uploaded = SimpleNamespace(name="notes.txt")
    uploaded.read = lambda size: b"x" * size
    monkeypatch.setattr(
        harness_sources,
        "_require_import_basis",
        lambda *args: harness_sources.drafts.NO_DRAFT,
    )
    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.prepare_upload_import(
            SimpleNamespace(),
            uploaded,
            purpose=None,
            expected_environment_version_id="version",
            expected_draft={"id": None, "revision": 0},
        )
    assert caught.value.code == "source_limit_exceeded"


def test_upload_batch_has_a_total_size_limit(monkeypatch):
    monkeypatch.setattr(harness_sources, "MAX_SELECTION_BYTES", 3)
    monkeypatch.setattr(
        harness_sources,
        "_require_import_basis",
        lambda *args: harness_sources.drafts.NO_DRAFT,
    )
    uploads = [io.BytesIO(b"aa"), io.BytesIO(b"bb")]
    uploads[0].name = "first.txt"
    uploads[1].name = "second.txt"

    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.prepare_upload_import(
            SimpleNamespace(),
            uploads,
            purpose=None,
            expected_environment_version_id="version",
            expected_draft={"id": None, "revision": 0},
        )

    assert caught.value.code == "source_limit_exceeded"
