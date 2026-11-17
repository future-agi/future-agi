"""Scheduled exports exercise real job/CSV writing with a filesystem S3 fake."""
import csv
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Organization
from tracer.models.project import Project

pytestmark = pytest.mark.django_db


class FilesystemS3Client:
    """Only the boto3 calls used by S3ExportDestination; never makes a request."""

    def __init__(self, root):
        self.root = root
        self.calls = []

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None):
        self.calls.append((Bucket, Key, ExtraArgs))
        target = self.root / Bucket / Key
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            while chunk := Fileobj.read(1024):
                output.write(chunk)
        # Managed S3 uploads may close the supplied file object.
        Fileobj.close()

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        assert ClientMethod == "get_object"
        assert ExpiresIn == 900
        return (self.root / Params["Bucket"] / Params["Key"]).as_uri()


@pytest.fixture
def export_project(organization, workspace):
    return Project.objects.create(
        name="Scheduled exports", organization=organization, workspace=workspace,
        trace_type="observe", model_type="GenerativeLLM",
    )


@pytest.fixture
def credentials(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    return {"aws_access_key_id": "test-access", "aws_secret_access_key": "test-secret"}


@pytest.fixture
def schedule(user, export_project, credentials):
    from integrations.services.credentials import CredentialManager
    from tracer.models.export_schedule import ExportSchedule

    item = ExportSchedule.objects.create(
        project=export_project, created_by=user, row_type="traces", format="csv",
        filters={"filters": []}, interval="hourly",
        destination={"bucket": "export-test", "prefix": "archive", "region": "us-east-1"},
        encrypted_credentials=CredentialManager.encrypt(credentials),
    )
    ExportSchedule.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(hours=2))
    item.refresh_from_db()
    return item


def dispatch(now, django_capture_on_commit_callbacks):
    from tracer.services.export_schedule import dispatch_due_exports

    with patch("tracer.tasks.bulk_export.process_bulk_export.delay") as enqueue:
        with django_capture_on_commit_callbacks(execute=True):
            ids = dispatch_due_exports(now=now)
    assert [call.args[0] for call in enqueue.call_args_list] == ids
    return ids


@pytest.mark.parametrize("row_type", ["traces", "spans", "sessions"])
def test_due_schedule_writes_s3_object_and_second_run_only_new_rows(
    schedule, tmp_path, monkeypatch, django_capture_on_commit_callbacks, row_type,
):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.serializers.bulk_export import validate_export_query

    schedule.row_type = row_type
    schedule.save(update_fields=["row_type"])
    now = timezone.now()
    fake = FilesystemS3Client(tmp_path)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake)
    rows = [
        {"id": "old", "created_at": (now - timedelta(minutes=10)).isoformat()},
        {"id": "boundary", "created_at": now.isoformat()},
        {"id": "new", "created_at": (now + timedelta(minutes=30)).isoformat()},
    ]

    def read_page(job, cursor):
        assert cursor is None
        query = validate_export_query(job.row_type, job.filters, job.project_id)
        window = query["filters"][-1]
        assert window["column_id"] == "created_at"
        config = window["filter_config"]
        assert config["filter_op"] == "between"
        lower, upper = map(parse_datetime, config["filter_value"])
        return ExportPage(rows=[row for row in rows if lower <= parse_datetime(row["created_at"]) < upper])

    first_ids = dispatch(now, django_capture_on_commit_callbacks)
    assert len(first_ids) == 1
    # Concurrent/repeated ticks do not create a second in-flight job.
    assert dispatch(now, django_capture_on_commit_callbacks) == []
    first = BulkExportJob.objects.get(pk=first_ids[0])
    run_export(first.id, read_page=read_page)
    first.refresh_from_db()
    schedule.refresh_from_db()
    assert first.status == "completed"
    assert schedule.last_run_at == now
    expected_key = f"archive/observe-exports/{schedule.project.organization_id}/{schedule.project_id}/{first.id}.csv"
    assert fake.calls == [("export-test", expected_key, {"ContentType": "text/csv"})]
    assert (tmp_path / "export-test" / expected_key).read_bytes() == (
        f"id,created_at\r\nold,{rows[0]['created_at']}\r\n".encode()
    )
    second_ids = dispatch(now + timedelta(hours=1), django_capture_on_commit_callbacks)
    assert len(second_ids) == 1
    second = BulkExportJob.objects.get(pk=second_ids[0])
    run_export(second.id, read_page=read_page)
    second.refresh_from_db()
    assert second.status == "completed"
    with (tmp_path / "export-test" / fake.calls[-1][1]).open() as stream:
        assert [row["id"] for row in csv.DictReader(stream)] == ["boundary", "new"]


@pytest.mark.parametrize("state", ["disabled", "not_due", "new"])
def test_disabled_or_not_due_schedules_do_nothing(schedule, django_capture_on_commit_callbacks, state):
    from tracer.models.export_schedule import ExportSchedule

    now = timezone.now()
    if state == "disabled":
        ExportSchedule.objects.filter(pk=schedule.pk).update(enabled=False)
    elif state == "not_due":
        ExportSchedule.objects.filter(pk=schedule.pk).update(last_run_at=now - timedelta(minutes=5))
    else:
        ExportSchedule.objects.filter(pk=schedule.pk).update(created_at=now)
    assert dispatch(now, django_capture_on_commit_callbacks) == []


def test_failed_upload_does_not_advance_watermark(schedule, django_capture_on_commit_callbacks):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export

    now = timezone.now()
    ids = dispatch(now, django_capture_on_commit_callbacks)
    with patch("tracer.services.bulk_export.get_export_destination", side_effect=ValueError("private vendor diagnostic")):
        run_export(ids[0], read_page=lambda *_: ExportPage(rows=[]))
    schedule.refresh_from_db()
    failed = BulkExportJob.objects.get(pk=ids[0])
    assert schedule.last_run_at is None
    assert failed.status == "failed"
    assert "private vendor diagnostic" not in failed.error
    retry_ids = dispatch(now + timedelta(minutes=1), django_capture_on_commit_callbacks)
    assert len(retry_ids) == 1
    retry = BulkExportJob.objects.get(pk=retry_ids[0])
    assert retry.filters["filters"][-1]["filter_config"]["filter_value"][0] == schedule.created_at.isoformat()


def test_destination_preserves_stream_and_signs_prefixed_key(tmp_path, credentials):
    from tracer.services.export_destination import S3ExportDestination

    fake = FilesystemS3Client(tmp_path)
    with patch("boto3.client", return_value=fake) as client:
        destination = S3ExportDestination(bucket="export-test", prefix="archive/", region="us-east-1", credentials=credentials)
    assert client.call_args.kwargs["aws_access_key_id"] == credentials["aws_access_key_id"]
    stream = BytesIO(b"export bytes")
    destination.write("rows.csv", stream)
    assert not stream.closed
    assert (tmp_path / "export-test/archive/rows.csv").read_bytes() == b"export bytes"
    assert destination.url("rows.csv") == (tmp_path / "export-test/archive/rows.csv").as_uri()


def test_schedule_crud_never_returns_credentials(auth_client, export_project, credentials):
    from integrations.services.credentials import CredentialManager
    from tracer.models.export_schedule import ExportSchedule

    response = auth_client.post("/tracer/export-schedules/", {
        "project_id": str(export_project.id), "row_type": "spans", "filters": {"filters": []},
        "format": "csv", "interval": "daily", "credentials": credentials,
        "destination": {"bucket": "export-test", "prefix": "archive", "region": "us-east-1"},
    }, format="json")
    assert response.status_code == 201
    item = ExportSchedule.objects.get(pk=response.data["id"])
    assert CredentialManager.decrypt(bytes(item.encrypted_credentials)) == credentials
    assert credentials["aws_secret_access_key"].encode() not in bytes(item.encrypted_credentials)
    detail_url = f"/tracer/export-schedules/{item.id}/"
    listing = auth_client.get("/tracer/export-schedules/", {"project_id": str(export_project.id)})
    detail = auth_client.get(detail_url)
    update = auth_client.patch(detail_url, {"enabled": False, "interval": "hourly"}, format="json")
    assert listing.status_code == detail.status_code == update.status_code == 200
    for result in [response, listing, detail, update]:
        assert "credentials" not in str(result.data)
        assert "test-secret" not in str(result.data)
        assert "test-access" not in str(result.data)
    item.refresh_from_db()
    assert not item.enabled
    assert item.interval == "hourly"
    assert auth_client.delete(detail_url).status_code == 204
    assert not ExportSchedule.objects.filter(pk=item.pk).exists()


def test_schedule_snapshot_survives_delete(schedule, auth_client, tmp_path, monkeypatch, django_capture_on_commit_callbacks):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export

    job_id = dispatch(timezone.now(), django_capture_on_commit_callbacks)[0]
    assert auth_client.delete(f"/tracer/export-schedules/{schedule.id}/").status_code == 204
    fake = FilesystemS3Client(tmp_path)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake)
    run_export(job_id, read_page=lambda *_: ExportPage(rows=[{"id": "one"}]))
    job = BulkExportJob.objects.get(pk=job_id)
    assert job.status == "completed"
    assert job.schedule_id is None
    response = auth_client.get(f"/tracer/bulk-exports/{job.id}/")
    assert response.status_code == 200
    assert response.data["download_url"] == (tmp_path / "export-test" / fake.calls[0][1]).as_uri()


def test_schedule_scope_is_enforced(auth_client, schedule):
    from tracer.models.export_schedule import ExportSchedule

    other = Organization.objects.create(name="Other scheduled export organization")
    project = Project.no_workspace_objects.create(name="Other", organization=other, trace_type="observe", model_type="GenerativeLLM")
    ExportSchedule.objects.filter(pk=schedule.pk).update(project=project)
    url = f"/tracer/export-schedules/{schedule.id}/"
    assert auth_client.get(url).status_code == 404
    assert auth_client.patch(url, {"enabled": False}, format="json").status_code == 404
    assert auth_client.delete(url).status_code == 404
    assert auth_client.get("/tracer/export-schedules/", {"project_id": str(project.id)}).status_code == 404


@pytest.mark.parametrize("invalid", [
    {"filters": {"project_id": "not-allowed"}},
    {"filters": {"page_size": 10}},
    {"interval": "weekly"},
    {"last_run_at": "2026-01-01T00:00:00Z"},
    {"destination": {"bucket": "export-test", "region": "us-east-1", "endpoint_url": "https://user:password@store.invalid"}},
])
def test_invalid_schedule_updates_are_rejected(auth_client, schedule, invalid):
    response = auth_client.patch(f"/tracer/export-schedules/{schedule.id}/", invalid, format="json")
    assert response.status_code == 400


def test_periodic_task_is_registered():
    from tfc.temporal.schedules.tracer import TRACER_SCHEDULES
    from tracer.tasks import dispatch_export_schedules

    assert callable(dispatch_export_schedules)
    assert any(item.activity_name == "dispatch_export_schedules" and item.interval_seconds == 60 for item in TRACER_SCHEDULES)
