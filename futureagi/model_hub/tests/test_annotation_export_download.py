"""Downloads reauthorize the current tenant and membership, not possession of a URL."""

import io
import json
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from minio.error import S3Error

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.services.annotation_export_storage import (
    DOWNLOAD_MAX_AGE,
    ExportFile,
    download_url,
    ensure_export_bucket,
    export_bucket_name,
    verify_private_object,
)
from model_hub.tasks.annotation_export import _store_export_file
from model_hub.tests.test_annotation_export_async import JOB_URL, _make_job
from model_hub.tests.test_annotation_export_batch_cap import (
    _build_dataset_queue,
    _unwrap,
)


@pytest.fixture
def completed_export(organization, workspace, user):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=2
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    job.status = "succeeded"
    job.storage_key = f"annotation-exports/{job.queue_id}/{job.pk}.json"
    job.finished_at = timezone.now()
    job.file_url = "http://old-storage.invalid/must-not-be-returned"
    job.save()
    return job


@pytest.fixture
def storage():
    with mock.patch("tfc.utils.storage_client.get_storage_client") as factory:
        stored_file = mock.Mock()
        stored_file.read.side_effect = io.BytesIO(
            b'[{"private":"synthetic-export"}]'
        ).read
        factory.return_value.get_object.return_value = stored_file
        yield factory.return_value


@pytest.mark.django_db
def test_authorized_download_replaces_plain_url(auth_client, completed_export, storage):
    job = completed_export
    result = _unwrap(
        auth_client.get(JOB_URL.format(queue_id=job.queue_id, job_id=job.pk)).data
    )
    assert result["file_url"].startswith(
        f"/model-hub/annotation-queues/{job.queue_id}/"
    )
    assert "old-storage" not in result["file_url"]
    response = auth_client.get(result["file_url"])
    assert response.status_code == 200
    assert b"synthetic-export" in b"".join(response.streaming_content)
    assert response["Cache-Control"] == "private, no-store"
    assert "attachment" in response["Content-Disposition"]
    response.close()
    storage.get_object.assert_called_once_with(export_bucket_name(), job.storage_key)
    storage.get_object.return_value.close.assert_called()
    storage.get_object.return_value.release_conn.assert_called_once()


@pytest.mark.django_db
def test_anonymous_download_is_denied(api_client, completed_export, storage):
    assert api_client.get(download_url(completed_export)).status_code in (401, 403)
    storage.get_object.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("revocation", ["inactive", "deleted", "no_role"])
def test_removed_member_cannot_reuse_link(
    auth_client, completed_export, user, revocation, storage
):
    link = download_url(completed_export)
    changes = (
        {"is_active": False}
        if revocation == "inactive"
        else ({"deleted": True} if revocation == "deleted" else {"level": 0})
    )
    OrganizationMembership.no_workspace_objects.filter(user=user).update(**changes)
    assert auth_client.get(link).status_code == 404
    storage.get_object.assert_not_called()


@pytest.mark.django_db
def test_other_organization_cannot_download(
    auth_client, completed_export, user, storage
):
    other = Organization.objects.create(name="Second synthetic organization")
    other_workspace = Workspace.no_workspace_objects.create(
        name="Second workspace", organization=other, created_by=user, is_default=True
    )
    auth_client.set_workspace(other_workspace)
    assert auth_client.get(download_url(completed_export)).status_code == 404
    storage.get_object.assert_not_called()


@pytest.mark.django_db
def test_other_workspace_cannot_download(
    auth_client, completed_export, user, organization, storage
):
    other = Workspace.no_workspace_objects.create(
        name="Other workspace", organization=organization, created_by=user
    )
    auth_client.set_workspace(other)
    assert auth_client.get(download_url(completed_export)).status_code == 404
    storage.get_object.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "active,level,expected", [(True, 1, 200), (False, 1, 404), (True, 0, 404)]
)
def test_current_workspace_role(
    auth_client, completed_export, user, storage, active, level, expected
):
    # Viewers may read queues; a removed/invalid workspace role may not.
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        is_active=active, level=level
    )
    response = auth_client.get(download_url(completed_export))
    assert response.status_code == expected
    if expected == 200:
        response.close()
    else:
        storage.get_object.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("hard", [False, True])
def test_deleted_queue_cannot_download(auth_client, completed_export, storage, hard):
    link = download_url(completed_export)
    if hard:
        completed_export.queue.__class__.all_objects.filter(
            pk=completed_export.queue_id
        ).delete()
    else:
        completed_export.queue.deleted = True
        completed_export.queue.save(update_fields=["deleted"])
    assert auth_client.get(link).status_code == 404
    storage.get_object.assert_not_called()


@pytest.mark.django_db
def test_expired_link_is_denied_but_new_link_works(
    auth_client, completed_export, storage
):
    with mock.patch("django.core.signing.time.time", return_value=0):
        expired = download_url(completed_export)
    assert auth_client.get(expired).status_code == 404
    storage.get_object.assert_not_called()
    response = auth_client.get(download_url(completed_export))
    assert response.status_code == 200
    response.close()
    assert DOWNLOAD_MAX_AGE == 300


@pytest.mark.django_db
def test_retention_expiry_denies_existing_link(auth_client, completed_export, storage):
    link = download_url(completed_export)
    completed_export.finished_at = timezone.now() - timedelta(days=31)
    completed_export.save(update_fields=["finished_at"])
    assert auth_client.get(link).status_code == 404
    result = _unwrap(
        auth_client.get(
            JOB_URL.format(
                queue_id=completed_export.queue_id, job_id=completed_export.pk
            )
        ).data
    )
    assert result["status"] == "failed"
    assert result["file_url"] is None
    storage.get_object.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("replacement", ["invalid", str(uuid.uuid4())])
def test_wrong_export_id(auth_client, completed_export, replacement, storage):
    link = download_url(completed_export).replace(str(completed_export.pk), replacement)
    assert auth_client.get(link).status_code == 404
    storage.get_object.assert_not_called()


@pytest.mark.django_db
def test_signature_is_bound_to_one_export(auth_client, completed_export, storage):
    link = download_url(completed_export)
    other = _make_job(
        completed_export.queue,
        completed_export.organization,
        completed_export.workspace,
        completed_export.created_by,
    )
    other.status = "succeeded"
    other.storage_key = "other.json"
    other.finished_at = timezone.now()
    other.save()
    assert (
        auth_client.get(
            link.replace(str(completed_export.pk), str(other.pk))
        ).status_code
        == 404
    )
    storage.get_object.assert_not_called()


@pytest.mark.django_db
def test_upload_uses_private_bucket_and_rejects_public_object(completed_export):
    client = mock.Mock()
    client.bucket_exists.return_value = False
    client.get_bucket_policy.side_effect = S3Error(
        mock.Mock(), "NoSuchBucketPolicy", "absent", "", "", ""
    )
    with (
        mock.patch("tfc.utils.storage_client.get_storage_client", return_value=client),
        mock.patch(
            "model_hub.services.annotation_export_storage.requests.get"
        ) as anonymous,
    ):
        anonymous.return_value.status_code = 200
        with pytest.raises(ValueError, match="anonymous read verification failed"):
            _store_export_file(completed_export, io.BytesIO(b"private"), 7)
    assert client.put_object.call_args.kwargs["bucket_name"] == export_bucket_name()
    client.remove_object.assert_called_once_with(
        export_bucket_name(), completed_export.storage_key
    )
    client.set_bucket_policy.assert_not_called()


@pytest.mark.parametrize("backend", ["minio", "s3", "gcs"])
def test_object_anonymous_probe_requires_explicit_denial(backend):
    with (
        mock.patch("tfc.utils.storage_client.STORAGE_BACKEND", backend),
        mock.patch(
            "model_hub.services.annotation_export_storage.requests.get"
        ) as anonymous,
    ):
        anonymous.return_value.status_code = 403
        verify_private_object(export_bucket_name(), "annotation-exports/synthetic.json")
        assert "synthetic.json" in anonymous.call_args.args[0]
        anonymous.return_value.status_code = 200
        with pytest.raises(ValueError, match="HTTP 200"):
            verify_private_object(
                export_bucket_name(), "annotation-exports/synthetic.json"
            )
        anonymous.return_value.status_code = 404
        with pytest.raises(ValueError, match="HTTP 404"):
            verify_private_object(
                export_bucket_name(), "annotation-exports/synthetic.json"
            )


def test_gcs_requires_explicit_preprovisioned_bucket(settings):
    client = mock.Mock()
    with mock.patch("tfc.utils.storage_client.STORAGE_BACKEND", "gcs"):
        with pytest.raises(ValueError, match="explicitly configured"):
            ensure_export_bucket(client)
        settings.ANNOTATION_EXPORT_BUCKET_NAME = "private-synthetic-export"
        client.bucket_exists.return_value = False
        with pytest.raises(ValueError, match="provisioned first"):
            ensure_export_bucket(client)
        client.bucket_exists.return_value = True
        assert ensure_export_bucket(client) == "private-synthetic-export"
        client.make_bucket.assert_not_called()
        client.set_bucket_policy.assert_not_called()


@pytest.mark.parametrize("principal", ["*", {"AWS": ["*"]}])
def test_existing_public_export_bucket_is_refused(principal):
    client = mock.Mock()
    client.bucket_exists.return_value = True
    client.get_bucket_policy.return_value = json.dumps(
        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": principal,
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{export_bucket_name()}/*"],
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="anonymous"):
        ensure_export_bucket(client)
    client.set_bucket_policy.assert_not_called()


def test_new_bucket_has_no_public_policy():
    client = mock.Mock()
    client.bucket_exists.return_value = False
    client.get_bucket_policy.side_effect = S3Error(
        mock.Mock(), "NoSuchBucketPolicy", "absent", "", "", ""
    )
    assert ensure_export_bucket(client) == export_bucket_name()
    client.make_bucket.assert_called_once_with(export_bucket_name())
    client.set_bucket_policy.assert_not_called()


def test_cannot_reuse_public_upload_bucket(settings):
    settings.ANNOTATION_EXPORT_BUCKET_NAME = settings.UPLOAD_BUCKET_NAME
    with pytest.raises(ValueError, match="separate private bucket"):
        ensure_export_bucket(mock.Mock())


def test_unreadable_policy_is_not_assumed_private():
    client = mock.Mock()
    client.get_bucket_policy.side_effect = S3Error(
        mock.Mock(), "AccessDenied", "denied", "", "", ""
    )
    with pytest.raises(S3Error):
        ensure_export_bucket(client)


@pytest.mark.asyncio
async def test_asgi_stream_reads_bounded_chunks():
    stored = mock.Mock()
    stored.read.side_effect = io.BytesIO(b"x" * 200_000).read
    file = ExportFile(stored)
    chunks = [chunk async for chunk in file.chunks()]
    assert sum(map(len, chunks)) == 200_000
    assert max(map(len, chunks)) == 64 * 1024
    file.close()
    stored.release_conn.assert_called_once()
