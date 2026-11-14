"""Provider privacy must be established before the first export byte is sent."""

import io
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from minio.error import S3Error

from model_hub.services.annotation_export_storage import verify_private_object
from model_hub.tasks.annotation_export import _store_export_file
from tfc.utils import storage_client

BLOCKS = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}


@pytest.fixture
def export_storage(settings):
    settings.ANNOTATION_EXPORT_BUCKET_NAME = "synthetic-private-exports"
    client = mock.Mock()
    client.bucket_exists.return_value = True
    client.get_bucket_policy.side_effect = S3Error(
        mock.Mock(), "NoSuchBucketPolicy", "absent", "", "", ""
    )
    job = SimpleNamespace(
        id="synthetic-job",
        queue_id="synthetic-queue",
        export_format="json",
        storage_key="synthetic.json",
    )
    with (
        mock.patch.object(storage_client, "get_storage_client", return_value=client),
        mock.patch(
            "model_hub.services.annotation_export_storage.requests.get"
        ) as probe,
    ):
        probe.return_value.status_code = 403
        yield client, job, probe


def upload(job):
    return _store_export_file(job, io.BytesIO(b"private"), 7)


@pytest.mark.parametrize(
    "pap,uniform", [("inherited", True), ("enforced", False), (None, None)]
)
def test_gcs_unsafe_bucket_never_receives_export(
    export_storage, monkeypatch, pap, uniform
):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "gcs")
    with mock.patch("google.cloud.storage.Client") as api:
        iam = api.return_value.get_bucket.return_value.iam_configuration
        iam.public_access_prevention = pap
        iam.uniform_bucket_level_access_enabled = uniform
        with pytest.raises(ValueError, match="public access prevention"):
            upload(job)
    client.put_object.assert_not_called()
    probe.assert_not_called()


def test_gcs_unverifiable_bucket_never_receives_export(export_storage, monkeypatch):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "gcs")
    with mock.patch("google.cloud.storage.Client") as api:
        api.return_value.get_bucket.side_effect = PermissionError("cannot read bucket")
        with pytest.raises(PermissionError):
            upload(job)
    client.put_object.assert_not_called()
    probe.assert_not_called()


@pytest.mark.parametrize("missing", list(BLOCKS))
def test_s3_incomplete_blocks_never_receive_export(
    export_storage, monkeypatch, missing
):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    with mock.patch("boto3.client") as api:
        api.return_value.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {**BLOCKS, missing: False}
        }
        with pytest.raises(ValueError, match="all public access blocks"):
            upload(job)
    client.put_object.assert_not_called()
    probe.assert_not_called()


def test_s3_unverifiable_blocks_never_receive_export(export_storage, monkeypatch):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    with mock.patch("boto3.client") as api:
        api.return_value.get_public_access_block.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            upload(job)
    client.put_object.assert_not_called()
    probe.assert_not_called()


def test_minio_public_policy_never_receives_export(export_storage, monkeypatch):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    client.get_bucket_policy.side_effect = None
    client.get_bucket_policy.return_value = json.dumps(
        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="anonymous access"):
        upload(job)
    client.put_object.assert_not_called()
    probe.assert_not_called()


@pytest.mark.parametrize("backend", ["minio", "s3", "gcs"])
def test_provider_privacy_precedes_upload(export_storage, monkeypatch, backend):
    client, job, probe = export_storage
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", backend)
    calls = mock.Mock()
    calls.attach_mock(client.put_object, "upload")
    with (
        mock.patch("boto3.client") as s3,
        mock.patch("google.cloud.storage.Client") as gcs,
    ):
        s3.return_value.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": BLOCKS
        }
        iam = gcs.return_value.get_bucket.return_value.iam_configuration
        iam.public_access_prevention = "enforced"
        iam.uniform_bucket_level_access_enabled = True
        privacy = {
            "minio": client.get_bucket_policy,
            "s3": s3.return_value.get_public_access_block,
            "gcs": gcs.return_value.get_bucket,
        }[backend]
        calls.attach_mock(privacy, "privacy")
        upload(job)
    assert [call[0] for call in calls.mock_calls] == ["privacy", "upload"]
    client.put_object.assert_called_once()
    probe.assert_called_once()


@pytest.mark.parametrize(
    "env,host,secure",
    [
        ({"S3_ENDPOINT_URL": "http://minio:9000"}, "minio:9000", False),
        ({"S3_ENDPOINT": "only-endpoint:9000"}, "only-endpoint:9000", False),
        (
            {
                "S3_ENDPOINT": "https://preferred:9443",
                "S3_ENDPOINT_URL": "http://ignored:9000",
            },
            "preferred:9443",
            True,
        ),
        ({"S3_ENDPOINT": "secure:9443", "S3_SECURE": "true"}, "secure:9443", True),
        ({"S3_ENDPOINT_URL": "secure:9443", "S3_SECURE": "TrUe"}, "secure:9443", True),
        (
            {"S3_ENDPOINT_URL": "https://plain:9000", "S3_SECURE": "false"},
            "plain:9000",
            False,
        ),
        (
            {"S3_ENDPOINT_URL": "http://secure:9443", "S3_SECURE": "true"},
            "secure:9443",
            True,
        ),
        ({}, "s3.amazonaws.com", False),
    ],
)
def test_probe_and_client_share_endpoint(monkeypatch, env, host, secure):
    for name in ("S3_ENDPOINT", "S3_ENDPOINT_URL", "S3_SECURE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    monkeypatch.setattr(storage_client, "_client", None)
    with (
        mock.patch.object(storage_client, "Minio") as factory,
        mock.patch(
            "model_hub.services.annotation_export_storage.requests.get"
        ) as probe,
    ):
        probe.return_value.status_code = 403
        storage_client.get_storage_client()
        verify_private_object("synthetic-bucket", "synthetic.json")
    assert factory.call_args.args[0] == host
    assert factory.call_args.kwargs["secure"] is secure
    assert probe.call_args.args[0] == (
        f"{'https' if secure else 'http'}://{host}/synthetic-bucket/synthetic.json"
    )
