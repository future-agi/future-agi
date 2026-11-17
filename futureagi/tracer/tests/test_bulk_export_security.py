"""Regression coverage for export endpoint admission and private downloads."""
import csv
import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from minio import Minio

from tracer.models.bulk_export import BulkExportJob
from tracer.models.project import Project
from tracer.services.export_destination import S3ExportDestination, StorageExportDestination

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolate_legacy_public_upload():
    # RED must not upload anything through the old public-storage path.
    with patch("tfc.utils.storage.upload_stream_to_s3"):
        yield


@pytest.fixture
def export_project(organization, workspace):
    return Project.objects.create(
        name="Private exports", organization=organization, workspace=workspace,
        trace_type="observe", model_type="GenerativeLLM",
    )


def test_endpoint_admission_is_operator_controlled(settings):
    settings.BULK_EXPORT_S3_ENDPOINT_ALLOWLIST = ["https://storage.example.test"]
    credentials = {"aws_access_key_id": "test-key", "aws_secret_access_key": "test-secret"}
    with patch("boto3.client") as client, patch("tfc.utils.ssrf_guard.assert_url_host_public"):
        for endpoint in (None, "https://s3.us-east-1.amazonaws.com", "https://storage.example.test"):
            S3ExportDestination(bucket="exports", region="us-east-1", credentials=credentials, endpoint_url=endpoint)
        assert client.call_count == 3
        for endpoint in ("https://untrusted.example.test", "https://10.0.0.1"):
            with pytest.raises(ValueError, match="operator allowlist"):
                S3ExportDestination(bucket="exports", region="us-east-1", credentials=credentials, endpoint_url=endpoint)
        assert client.call_count == 3


def test_schedule_rejects_unapproved_endpoint_before_enqueue(settings):
    from tracer.serializers.export_schedule import S3DestinationSerializer

    settings.BULK_EXPORT_S3_ENDPOINT_ALLOWLIST = []
    serializer = S3DestinationSerializer(data={
        "bucket": "exports", "region": "us-east-1", "endpoint_url": "https://untrusted.example.test",
    })
    assert not serializer.is_valid()
    assert "operator allowlist" in str(serializer.errors)


def test_default_endpoint_allowlist_is_empty(settings):
    settings.BULK_EXPORT_S3_ENDPOINT_ALLOWLIST = []
    with patch("boto3.client") as client, patch("tfc.utils.ssrf_guard.assert_url_host_public"):
        with pytest.raises(ValueError, match="operator allowlist"):
            S3ExportDestination(
                bucket="exports", region="us-east-1", endpoint_url="https://storage.example.test",
                credentials={"aws_access_key_id": "test-key", "aws_secret_access_key": "test-secret"},
            )
    client.assert_not_called()


def test_one_shot_storage_enforces_private_bucket_before_upload(settings):
    settings.UPLOAD_BUCKET_NAME = "content"
    settings.STORAGE_BACKEND = "minio"
    client = Mock()
    client.bucket_exists.return_value = False
    destination = StorageExportDestination()
    with patch("tfc.utils.storage_client.get_storage_client", return_value=client):
        destination.write("private-observe-exports/project/job.csv", BytesIO(b"id\n1\n"))
    client.make_bucket.assert_called_once_with("content-private-exports")
    policy = json.loads(client.set_bucket_policy.call_args.args[1])
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Deny"
    assert statement["Principal"] == "*"
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::content-private-exports/*"]
    assert statement["Condition"] == {"StringNotEquals": {"s3:signatureversion": "AWS4-HMAC-SHA256"}}
    assert client.put_object.call_args.kwargs["bucket_name"] == "content-private-exports"
    assert [call[0] for call in client.mock_calls].index("set_bucket_policy") < [call[0] for call in client.mock_calls].index("put_object")


def test_private_policy_failure_prevents_upload(settings):
    settings.STORAGE_BACKEND = "minio"
    client = Mock()
    client.set_bucket_policy.side_effect = RuntimeError("policy unavailable")
    with patch("tfc.utils.storage_client.get_storage_client", return_value=client):
        with pytest.raises(ValueError, match="private export storage"):
            StorageExportDestination().write("private-observe-exports/project/job.csv", b"id\n")
    client.put_object.assert_not_called()


def test_gcs_fails_closed_until_private_export_storage_is_supported(settings):
    settings.STORAGE_BACKEND = "gcs"
    with pytest.raises(ValueError, match="private export storage"):
        StorageExportDestination().write("private-observe-exports/project/job.csv", b"id\n")


def test_legacy_public_keys_are_not_signed():
    with pytest.raises(ValueError, match="private export"):
        StorageExportDestination().url("observe-exports/project/job.csv")


def test_status_signs_private_key_only_after_project_access(auth_client, user, export_project, tmp_path, settings):
    from accounts.models import Organization
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.services.export_destination import FilesystemExportDestination

    settings.STORAGE_BACKEND = "s3"
    settings.UPLOAD_BUCKET_NAME = "content"
    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces")
    run_export(job.id, destination=FilesystemExportDestination(tmp_path), read_page=lambda *_: ExportPage(rows=[{"id": "1"}]))
    job.refresh_from_db()
    assert job.status == "completed"
    assert job.file_location.startswith("private-observe-exports/")
    assert "://" not in job.file_location and "?" not in job.file_location
    signer = Minio("s3.us-east-1.amazonaws.com", access_key="test-key", secret_key="test-secret", region="us-east-1")
    with patch("tfc.utils.storage_client.get_storage_client", return_value=signer):
        response = auth_client.get(f"/tracer/bulk-exports/{job.id}/")
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.data["download_url"]).query)
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Signature"] and query["X-Amz-Date"]
    assert "file_location" not in response.data
    job.refresh_from_db()
    assert "?" not in job.file_location
    other = Organization.objects.create(name="Other export organization")
    project = Project.no_workspace_objects.create(name="Other", organization=other, trace_type="observe", model_type="GenerativeLLM")
    BulkExportJob.objects.filter(pk=job.pk).update(project=project)
    with patch("tracer.views.bulk_export.get_export_destination") as destination:
        assert auth_client.get(f"/tracer/bulk-exports/{job.id}/").status_code == 404
    destination.assert_not_called()


@pytest.mark.parametrize("endpoint,authority,scheme", [
    ("localhost:9005", "localhost:9005", "http"),
    ("downloads.example.test:9005", "downloads.example.test:9005", "http"),
    ("http://downloads.example.test:9005", "downloads.example.test:9005", "http"),
    ("https://downloads.example.test:9443", "downloads.example.test:9443", "https"),
])
def test_minio_signed_download_uses_browser_endpoint(settings, monkeypatch, endpoint, authority, scheme):
    settings.STORAGE_BACKEND = "minio"
    settings.MINIO_URL = endpoint
    settings.UPLOAD_BUCKET_NAME = "content"
    monkeypatch.setenv("S3_ACCESS_KEY", "test-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    url = StorageExportDestination().url("private-observe-exports/project/job.csv")
    assert urlsplit(url).netloc == authority
    assert urlsplit(url).scheme == scheme
    assert urlsplit(url).path == "/content-private-exports/private-observe-exports/project/job.csv"
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Signature"] and query["X-Amz-Date"]


@pytest.mark.parametrize("age_minutes,expected", [(31, "failed"), (29, "queued")])
def test_queued_one_shot_deadline(auth_client, user, export_project, age_minutes, expected):
    from django.utils import timezone

    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces")
    BulkExportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
    response = auth_client.get(f"/tracer/bulk-exports/{job.id}/")
    assert response.status_code == 200
    assert response.data["status"] == expected
    if expected == "failed":
        assert "queue" in response.data["error"].lower()
        assert response.data["finished_at"]
        from tracer.services.bulk_export import run_export
        run_export(job.id, read_page=lambda *_: pytest.fail("expired job must not start"))


@pytest.mark.parametrize("file_format", ["csv", "parquet"])
def test_requested_attributes_survive_create_adapter_and_output(auth_client, export_project, tmp_path, file_format):
    from rest_framework.response import Response
    from tracer.services.bulk_export import run_export
    from tracer.services.export_destination import FilesystemExportDestination

    key = "metadata.path,with-comma"
    with patch("tracer.views.bulk_export.process_bulk_export.delay"):
        response = auth_client.post("/tracer/bulk-exports/", {
            "project_id": str(export_project.id), "row_type": "traces", "format": file_format,
            "filters": {"filters": [], "attribute_keys": [key]},
        }, format="json")
    assert response.status_code == 202
    job = BulkExportJob.objects.get(pk=response.data["id"])
    assert job.filters["attribute_keys"] == [key]

    def page(view, request, **kwargs):
        assert request.validated_query_data["attribute_keys"] == [key]
        return Response({"result": {
            "table": [{"trace_id": "1", key: "displayed value"}],
            "config": [{"id": "trace_id"}, {"id": key}],
            "metadata": {"query_complete": True, "has_more": False},
        }})

    with patch("tracer.views.trace.TraceView.list_traces_of_session", new=page):
        run_export(job.id, destination=FilesystemExportDestination(tmp_path))
    job.refresh_from_db()
    assert job.status == "completed"
    if file_format == "csv":
        with (tmp_path / job.file_location).open(newline="") as stream:
            rows = list(csv.DictReader(stream))
    else:
        import pyarrow.parquet as pq
        rows = pq.read_table(tmp_path / job.file_location).to_pylist()
    assert rows[0][key] == "displayed value"


def test_private_bucket_policy_roundtrip_and_public_bucket_refusal(settings):
    settings.STORAGE_BACKEND = "minio"
    settings.UPLOAD_BUCKET_NAME = "content"
    client = Mock()
    client.bucket_exists.return_value = False
    destination = StorageExportDestination()
    with patch("tfc.utils.storage_client.get_storage_client", return_value=client):
        destination.write("private-observe-exports/project/first.csv", b"id\n")
        policy = json.loads(client.set_bucket_policy.call_args.args[1])
        policy["Statement"][0]["Principal"] = {"AWS": ["*"]}
        client.bucket_exists.return_value = True
        client.get_bucket_policy.return_value = json.dumps(policy)
        destination.write("private-observe-exports/project/second.csv", b"id\n")
        assert client.put_object.call_count == 2
        policy["Statement"].append({
            "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::content-private-exports/*",
        })
        client.get_bucket_policy.return_value = json.dumps(policy)
        with pytest.raises(ValueError, match="private export storage"):
            destination.write("private-observe-exports/project/refused.csv", b"id\n")
        assert client.put_object.call_count == 2
        assert client.set_bucket_policy.call_count == 2


def test_queue_expiration_does_not_fail_a_claimed_worker(user, export_project):
    from django.utils import timezone
    from tracer.services.bulk_export import expire_queued_export

    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces")
    BulkExportJob.objects.filter(pk=job.pk).update(
        status="running", started_at=timezone.now(), created_at=timezone.now() - timedelta(hours=1),
    )
    assert expire_queued_export(job.id) == 0
    job.refresh_from_db()
    assert job.status == "running"


def test_creation_payload_does_not_sign_even_if_worker_already_finished(user, export_project):
    from tracer.views.bulk_export import _status

    job = BulkExportJob.objects.create(
        project=export_project, created_by=user, row_type="traces", status="completed",
        file_location="private-observe-exports/project/job.csv",
    )
    with patch("tracer.views.bulk_export.get_export_destination") as destination:
        assert _status(job)["download_url"] is None
    destination.assert_not_called()
