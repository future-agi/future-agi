import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from simulate.services import harness_sources
from simulate.services.harness_versioning import read as version_reads
from simulate.services.hosted_harness import HostedHarnessError
from simulate.views.harness_environment import HarnessEnvironmentViewSet


def test_prepare_upload_accepts_repeated_multipart_files(monkeypatch):
    environment = SimpleNamespace(id=uuid4())
    version_id = uuid4()
    monkeypatch.setattr(version_reads, "require_versioning", lambda: None)
    monkeypatch.setattr(HarnessEnvironmentViewSet, "_job", lambda *_: environment)

    def prepare(job, uploads, **kwargs):
        assert job is environment
        assert [(upload.name, upload.read()) for upload in uploads] == [
            ("first.txt", b"reference"),
            ("second.csv", b"id\n42"),
        ]
        assert kwargs["expected_environment_version_id"] == version_id
        assert kwargs["target_table"] == "customers"
        assert kwargs["expected_draft"] == {"id": None, "revision": 0}
        return {"import_ref": "test-review"}

    monkeypatch.setattr(harness_sources, "prepare_upload_import", prepare)
    url = (
        f"/simulate/api/harness-environments/{environment.id}/sources/uploads/prepare/"
    )
    request = APIRequestFactory().post(
        url,
        {
            "expected_environment_version_id": str(version_id),
            "target_table": "customers",
            "expected_draft_revision": "0",
            "files": [
                SimpleUploadedFile("first.txt", b"reference"),
                SimpleUploadedFile("second.csv", b"id\n42"),
            ],
        },
        format="multipart",
    )
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    action = HarnessEnvironmentViewSet.prepare_source_upload
    view = HarnessEnvironmentViewSet.as_view(
        {"post": "prepare_source_upload"}, **action.kwargs
    )
    response = view(request, pk=environment.id)
    assert response.status_code == 200
    assert response.data["import_ref"] == "test-review"


def _capture(monkeypatch, name, content, purpose=None):
    monkeypatch.setattr(
        harness_sources,
        "_require_import_basis",
        lambda *args: harness_sources.drafts.NO_DRAFT,
    )
    monkeypatch.setattr(
        harness_sources,
        "_prepare_captured",
        lambda environment, captured, current_ref, target_table: captured,
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


def test_valid_multisheet_and_malformed_workbooks_have_distinct_actionable_results():
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["id"])
    book.active.append(["001"])
    body = io.BytesIO()
    book.save(body)
    assert harness_sources._spreadsheet_as_csv(body.getvalue()) == b"id\r\n001\r\n"
    book.create_sheet("Plan B")
    body = io.BytesIO()
    book.save(body)
    with pytest.raises(HostedHarnessError) as ambiguous:
        harness_sources._spreadsheet_as_csv(body.getvalue())
    assert ambiguous.value.code == "source_workbook_needs_repair"
    assert "separate CSV" in ambiguous.value.message
    with pytest.raises(HostedHarnessError) as corrupt:
        harness_sources._spreadsheet_as_csv(b"not a workbook")
    assert corrupt.value.code == "source_workbook_invalid"


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
