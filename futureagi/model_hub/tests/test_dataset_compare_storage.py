"""Where comparison data is stored, and when it expires.

The storage tests run against the MinIO of docker-compose.test.yml (published
on localhost:19005, or MINIO_ENDPOINT) and skip when it is not reachable.
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.tests.test_dataset_compare_paging import (
    DELETE_URL,
    ROW_URL,
    _download,
    _make_dataset,
    _post,
    _replica,
    _result,
    _row_ids,
)
from tfc.utils import storage_client

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT") or "localhost:19005"
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY") or "minioadmin"
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY") or "minioadmin"


def _anonymous_get(bucket, key):
    """Status of a GET of the object by URL, with no credentials."""
    url = f"http://{MINIO_ENDPOINT}/{bucket}/{key}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


@pytest.fixture
def minio_storage(monkeypatch, settings):
    """The test MinIO as the platform's storage, with buckets of this test."""
    from minio import Minio

    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    try:
        client.list_buckets()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test MinIO not reachable at {MINIO_ENDPOINT}: {exc}")

    suffix = uuid.uuid4().hex[:10]
    upload_bucket = f"test-upload-{suffix}"
    compare_bucket = f"test-upload-{suffix}-compare"
    settings.UPLOAD_BUCKET_NAME = upload_bucket
    settings.COMPARE_BUCKET_NAME = compare_bucket
    # The upload helpers read the bucket name once, at import.
    monkeypatch.setattr("tfc.utils.storage.UPLOAD_BUCKET_NAME", upload_bucket)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    yield SimpleNamespace(
        client=client, upload_bucket=upload_bucket, compare_bucket=compare_bucket
    )
    for bucket in (upload_bucket, compare_bucket):
        if client.bucket_exists(bucket):
            for obj in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, obj.object_name)
            client.remove_bucket(bucket)


def _compare_objects(storage, compare_id):
    """{bucket: [object names]} of one comparison, in every test bucket."""
    found = {}
    for bucket in (storage.upload_bucket, storage.compare_bucket):
        if not storage.client.bucket_exists(bucket):
            continue
        names = [
            obj.object_name
            for obj in storage.client.list_objects(
                bucket, prefix=f"compare/{compare_id}/", recursive=True
            )
        ]
        if names:
            found[bucket] = sorted(names)
    return found


@pytest.mark.django_db(transaction=True)
def test_compare_pages_and_metadata_are_not_readable_by_url(
    auth_client, organization, workspace, minio_storage, monkeypatch, tmp_path
):
    shared = [f"private-{index:02d}" for index in range(12)]
    left, left_input, _ = _make_dataset(
        organization, workspace, "Private left", shared, "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Private right", shared, "right"
    )
    _replica(monkeypatch, tmp_path, "replica-a")
    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    compare_id = first["metadata"]["compare_id"]

    found = _compare_objects(minio_storage, compare_id)
    names = sorted(name for bucket_names in found.values() for name in bucket_names)
    assert names == [
        f"compare/{compare_id}/metadata.json",
        f"compare/{compare_id}/page_1.json",
        f"compare/{compare_id}/page_2.json",
    ]
    # Nothing of the comparison is in the bucket whose objects anyone may read.
    assert minio_storage.upload_bucket not in found
    assert list(found) == [minio_storage.compare_bucket]
    for name in names:
        assert _anonymous_get(minio_storage.compare_bucket, name) in (401, 403)
    with pytest.raises(Exception) as no_policy:
        minio_storage.client.get_bucket_policy(minio_storage.compare_bucket)
    assert getattr(no_policy.value, "code", None) == "NoSuchBucketPolicy"

    # The backend still serves the comparison from any replica.
    _replica(monkeypatch, tmp_path, "replica-b")
    second = _result(_post(auth_client, left.id, [right.id], 1, compare_id))
    returned = [
        row[str(left_input.id)]["cell_value"]
        for row in first["table"] + second["table"]
    ]
    assert sorted(returned) == shared
    detail = auth_client.get(
        ROW_URL.format(compare_id=compare_id, row_id=_row_ids(first)[0])
    )
    assert detail.status_code == status.HTTP_200_OK, detail.content[:500]

    # Exit removes it from the private bucket.
    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no worker")),
    )
    deleted = auth_client.delete(DELETE_URL.format(compare_id=compare_id))
    assert deleted.status_code == status.HTTP_200_OK, deleted.content[:500]
    assert _compare_objects(minio_storage, compare_id) == {}


@pytest.mark.django_db
def test_a_public_or_upload_bucket_is_refused_for_comparison_data(
    minio_storage, settings
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused, ensure_bucket

    compare_id = str(uuid.uuid4())
    settings.COMPARE_BUCKET_NAME = minio_storage.upload_bucket
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})

    # A bucket an operator made readable by URL is not used either.
    settings.COMPARE_BUCKET_NAME = minio_storage.compare_bucket
    ensure_bucket(minio_storage.client, minio_storage.compare_bucket)
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert _compare_objects(minio_storage, compare_id) == {}


class _CreatedFirstWithPolicy:
    """Storage where another process creates the bucket, with a policy that
    lets anyone read it, just before this one's creation, which still
    answers success."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def make_bucket(self, bucket):
        self._client.make_bucket(bucket)
        policy = {
            "Version": "2012-10-17",
            "Statement": [storage_client._anonymous_read_statement(bucket)],
        }
        self._client.set_bucket_policy(bucket, json.dumps(policy))


def test_a_bucket_given_a_policy_while_it_was_created_is_refused(
    minio_storage, monkeypatch
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    monkeypatch.setattr(
        storage_client, "_client", _CreatedFirstWithPolicy(minio_storage.client)
    )
    compare_id = str(uuid.uuid4())
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert _compare_objects(minio_storage, compare_id) == {}
    # Refused, not remembered as private: the next write is checked again.
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})


class _PolicyClient:
    """An existing bucket whose policy read gives the error code."""

    def __init__(self, code):
        self.code = code
        self.written = []

    def bucket_exists(self, bucket):
        return True

    def get_bucket_policy(self, bucket):
        error = Exception(self.code)
        error.code = self.code
        raise error

    def put_object(self, **kwargs):
        self.written.append(kwargs["object_name"])


@pytest.mark.parametrize(
    "code, accepted", [("NoSuchBucketPolicy", True), ("AccessDenied", False)]
)
def test_a_bucket_whose_policy_cannot_be_read_is_not_used(
    monkeypatch, settings, code, accepted
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = f"test-upload-compare-{uuid.uuid4().hex[:8]}"
    client = _PolicyClient(code)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    compare_id = str(uuid.uuid4())
    if accepted:
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert client.written == [f"compare/{compare_id}/metadata.json"]
    else:
        with pytest.raises(PrivateBucketRefused):
            write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert client.written == []


class _CreationClient:
    """A bucket that may not exist yet. ``make_bucket`` creates it, or fails
    with ``create_code`` as when another request created it first."""

    def __init__(self, exists, create_code=None):
        self.exists = exists
        self.create_code = create_code
        self.created = []
        self.written = []
        # The buckets of the credentials' project, as GCS lists them.
        self.project_buckets = []
        self.list_error = None
        self.listings = 0

    def bucket_exists(self, bucket):
        return self.exists

    def make_bucket(self, bucket):
        self.created.append(bucket)
        if self.create_code is not None:
            error = Exception(self.create_code)
            error.code = self.create_code
            raise error
        self.exists = True

    def get_bucket_policy(self, bucket):
        error = Exception("no policy")
        error.code = "NoSuchBucketPolicy"
        raise error

    def put_object(self, **kwargs):
        self.written.append(kwargs["object_name"])

    def list_buckets(self):
        self.listings += 1
        if self.list_error is not None:
            raise self.list_error
        return [SimpleNamespace(name=name) for name in self.project_buckets]


def _use_creation_client(monkeypatch, settings, client, backend):
    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = f"test-upload-compare-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", backend)
    client.project_buckets = [settings.COMPARE_BUCKET_NAME]
    return settings.COMPARE_BUCKET_NAME


def test_s3_uses_an_existing_compare_bucket_without_creating_one(monkeypatch, settings):
    from model_hub.utils.compare_storage import write_compare_json

    client = _CreationClient(exists=True)
    _use_creation_client(monkeypatch, settings, client, "s3")
    compare_id = str(uuid.uuid4())
    write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert client.created == []
    assert client.written == [f"compare/{compare_id}/metadata.json"]


def test_s3_does_not_create_a_missing_compare_bucket(monkeypatch, settings):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    client = _CreationClient(exists=False)
    _use_creation_client(monkeypatch, settings, client, "s3")
    compare_id = str(uuid.uuid4())
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert client.created == []
    assert client.written == []

    # Not remembered: once an operator creates it, the next write uses it.
    client.exists = True
    write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert client.created == []
    assert client.written == [f"compare/{compare_id}/metadata.json"]


@pytest.mark.parametrize(
    "create_code, accepted",
    [(None, True), ("BucketAlreadyOwnedByYou", True), ("BucketAlreadyExists", False)],
    ids=["created", "already-owned-by-this-account", "owned-by-another-account"],
)
def test_minio_creates_a_missing_compare_bucket_only_for_this_account(
    monkeypatch, settings, create_code, accepted
):
    from model_hub.utils.compare_storage import write_compare_json

    client = _CreationClient(exists=False, create_code=create_code)
    bucket = _use_creation_client(monkeypatch, settings, client, "minio")
    compare_id = str(uuid.uuid4())
    if accepted:
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert client.written == [f"compare/{compare_id}/metadata.json"]
    else:
        with pytest.raises(Exception) as refused:
            write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert getattr(refused.value, "code", None) == "BucketAlreadyExists"
        assert client.written == []
    assert client.created == [bucket]


class _ReadsCountingClient(_CreationClient):
    """_CreationClient that counts bucket policy reads and can be given a
    policy later."""

    def __init__(self, exists, policy=None):
        super().__init__(exists)
        self.policy = policy
        self.policy_reads = 0

    def get_bucket_policy(self, bucket):
        self.policy_reads += 1
        if self.policy is None:
            return super().get_bucket_policy(bucket)
        return self.policy


_PUBLIC_READ = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::bucket/*"],
            }
        ],
    }
)


@pytest.mark.parametrize("backend", ["s3", "minio"])
def test_a_bucket_whose_policy_opens_later_is_refused_after_the_recheck(
    monkeypatch, settings, backend
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    client = _ReadsCountingClient(exists=True)
    _use_creation_client(monkeypatch, settings, client, backend)
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        storage_client,
        "time",
        SimpleNamespace(monotonic=lambda: clock.now),
        raising=False,
    )
    recheck = getattr(storage_client, "PRIVATE_BUCKET_RECHECK_SECONDS", 300)
    compare_id = str(uuid.uuid4())
    write_compare_json(compare_id, "page_1.json", {"rows": []})
    assert client.policy_reads == 1

    # Someone opens the bucket. Until the recheck it is not read again.
    client.policy = _PUBLIC_READ
    clock.now += recheck - 1
    write_compare_json(compare_id, "page_2.json", {"rows": []})
    assert client.policy_reads == 1

    clock.now += 2
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "page_3.json", {"rows": []})
    assert client.policy_reads == 2
    assert client.written == [
        f"compare/{compare_id}/page_1.json",
        f"compare/{compare_id}/page_2.json",
    ]
    # Refused, not remembered: the next write reads the policy again.
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "page_3.json", {"rows": []})
    assert client.policy_reads == 3


@pytest.mark.parametrize("backend", ["s3", "minio"])
def test_a_slow_private_check_does_not_override_a_newer_refusal(
    monkeypatch, settings, backend
):
    from tfc.utils.storage_client import PrivateBucketRefused, ensure_private_bucket

    class _OpenedDuringCheckClient(_ReadsCountingClient):
        """The first check reads a private policy; before it finishes, the
        bucket is opened and a second check reads and refuses it."""

        def get_bucket_policy(self, bucket):
            if self.policy_reads == 0:
                self.policy_reads += 1
                self.policy = _PUBLIC_READ
                with pytest.raises(PrivateBucketRefused):
                    ensure_private_bucket(self, bucket)
                error = Exception("no policy")
                error.code = "NoSuchBucketPolicy"
                raise error
            return super().get_bucket_policy(bucket)

    client = _OpenedDuringCheckClient(exists=True)
    bucket = _use_creation_client(monkeypatch, settings, client, backend)

    # The slow first check found the bucket private, but a newer check
    # refused it meanwhile: the private verdict is neither used nor remembered.
    with pytest.raises(PrivateBucketRefused):
        ensure_private_bucket(client, bucket)
    assert client.policy_reads == 2
    with pytest.raises(PrivateBucketRefused):
        ensure_private_bucket(client, bucket)
    assert client.policy_reads == 3


def _storage_error(code):
    error = Exception(code)
    error.code = code
    return error


@pytest.mark.parametrize(
    "backend, failure",
    [
        ("s3", "bucket-exists-fails"),
        ("minio", "bucket-exists-fails"),
        ("s3", "policy-read-finds-no-bucket"),
        ("minio", "policy-read-finds-no-bucket"),
        ("minio", "bucket-creation-fails"),
    ],
)
def test_a_failed_check_also_refuses_a_slower_check_running_with_it(
    monkeypatch, settings, backend, failure
):
    from tfc.utils.storage_client import PrivateBucketRefused, ensure_private_bucket

    class _FailsDuringCheckClient(_ReadsCountingClient):
        """The first check reads a private policy; before it finishes, a
        second check fails with ``failure``."""

        def __init__(self):
            super().__init__(exists=True)
            self.during_first_check = False
            self.second_check_error = None

        def bucket_exists(self, bucket):
            if self.during_first_check and failure == "bucket-exists-fails":
                raise RuntimeError("storage unavailable")
            if self.during_first_check and failure == "bucket-creation-fails":
                return False
            return True

        def make_bucket(self, bucket):
            self.created.append(bucket)
            raise _storage_error("BucketAlreadyExists")

        def get_bucket_policy(self, bucket):
            self.policy_reads += 1
            if self.policy_reads == 1:
                self.during_first_check = True
                try:
                    ensure_private_bucket(self, bucket)
                except Exception as error:  # noqa: BLE001
                    self.second_check_error = error
                self.during_first_check = False
            elif self.during_first_check:
                raise _storage_error("NoSuchBucket")
            raise _storage_error("NoSuchBucketPolicy")

    client = _FailsDuringCheckClient()
    bucket = _use_creation_client(monkeypatch, settings, client, backend)

    with pytest.raises(PrivateBucketRefused):
        ensure_private_bucket(client, bucket)
    # The failing check raised its own storage error.
    assert client.second_check_error is not None
    assert not isinstance(client.second_check_error, PrivateBucketRefused)

    # Nothing was remembered: the next check reads the policy again, finds the
    # bucket private and is then used until the recheck.
    reads = client.policy_reads
    ensure_private_bucket(client, bucket)
    assert client.policy_reads == reads + 1
    ensure_private_bucket(client, bucket)
    assert client.policy_reads == reads + 1


def test_gcs_refuses_a_missing_compare_bucket_with_a_clear_error(monkeypatch, settings):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    client = _ReadsCountingClient(exists=False)
    bucket = _use_creation_client(monkeypatch, settings, client, "gcs")
    logged = []
    monkeypatch.setattr(
        storage_client.logger,
        "error",
        lambda event, **fields: logged.append((event, fields)),
    )
    compare_id = str(uuid.uuid4())
    with pytest.raises(PrivateBucketRefused) as missing:
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert str(missing.value) == (
        f"compare bucket {bucket} does not exist; create it private"
    )
    assert logged == [
        (
            "storage_private_bucket_missing",
            {"bucket": bucket, "error": str(missing.value)},
        )
    ]
    assert client.created == [] and client.written == []

    # Once it is created, it is used. GCS access is IAM, not a bucket policy.
    client.exists = True
    write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert client.written == [f"compare/{compare_id}/metadata.json"]
    assert client.policy_reads == 0


@pytest.mark.parametrize(
    "case",
    ["in-this-project", "in-another-project", "listing-fails"],
)
def test_gcs_uses_a_compare_bucket_only_from_the_credentials_project(
    monkeypatch, settings, case
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    client = _ReadsCountingClient(exists=True)
    bucket = _use_creation_client(monkeypatch, settings, client, "gcs")
    logged = []
    monkeypatch.setattr(
        storage_client.logger,
        "error",
        lambda event, **fields: logged.append((event, fields)),
    )
    if case == "in-another-project":
        # A bucket of that name exists, but is not listed among this project's buckets.
        client.project_buckets = ["test-upload", "unrelated-bucket"]
    elif case == "listing-fails":
        client.list_error = _storage_error("AccessDenied")
    compare_id = str(uuid.uuid4())

    if case == "in-this-project":
        write_compare_json(compare_id, "page_1.json", {"rows": []})
        write_compare_json(compare_id, "page_2.json", {"rows": []})
        assert client.written == [
            f"compare/{compare_id}/page_1.json",
            f"compare/{compare_id}/page_2.json",
        ]
        # Found in the project once, then used until the recheck.
        assert client.listings == 1
        assert logged == []
        return

    with pytest.raises(PrivateBucketRefused) as refused:
        write_compare_json(compare_id, "page_1.json", {"rows": []})
    assert client.written == []
    if case == "in-another-project":
        assert str(refused.value) == f"compare bucket {bucket} is not in this project"
        assert [event for event, _ in logged] == [
            "storage_private_bucket_not_in_project"
        ]
    else:
        assert "could not be checked" in str(refused.value)
        assert [event for event, _ in logged] == [
            "storage_private_bucket_project_unknown"
        ]
    assert logged[0][1]["bucket"] == bucket

    # A refusal is not remembered: once the bucket is the project's, it is
    # used.
    client.list_error = None
    client.project_buckets = [bucket]
    write_compare_json(compare_id, "page_1.json", {"rows": []})
    assert client.written == [f"compare/{compare_id}/page_1.json"]
    assert client.listings == 2


class _ObjectClient(_CreationClient):
    """An existing private bucket whose object reads answer ``outcome``."""

    def __init__(self, outcome):
        super().__init__(exists=True)
        self.outcome = outcome

    def get_object(self, bucket, name):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        body = self.outcome

        class _Response:
            def read(self):
                return body

            def close(self):
                pass

            def release_conn(self):
                pass

        return _Response()


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (_storage_error("NoSuchKey"), "missing"),
        (b"{not json", "missing"),
        (b"\xff\xfe", "missing"),
        (_storage_error("InternalError"), "unavailable"),
        (_storage_error("AccessDenied"), "unavailable"),
        (ConnectionError("storage unreachable"), "unavailable"),
    ],
    ids=[
        "no-such-key",
        "not-json",
        "not-text",
        "server-error",
        "access-denied",
        "unreachable",
    ],
)
def test_a_missing_object_is_told_apart_from_a_storage_error(
    monkeypatch, settings, outcome, expected
):
    from model_hub.utils.compare_storage import (
        CompareObjectUnavailable,
        CompareStorageUnavailable,
        read_compare_json,
    )

    _use_creation_client(monkeypatch, settings, _ObjectClient(outcome), "s3")
    expected_error = (
        CompareObjectUnavailable if expected == "missing" else CompareStorageUnavailable
    )
    with pytest.raises(expected_error):
        read_compare_json(str(uuid.uuid4()), "metadata.json")


def test_a_refused_compare_bucket_is_a_storage_error(monkeypatch, settings):
    from model_hub.utils.compare_storage import (
        CompareStorageUnavailable,
        read_compare_json,
    )
    from tfc.utils.storage_client import PrivateBucketRefused

    client = _ObjectClient(b"{}")
    client.exists = False
    _use_creation_client(monkeypatch, settings, client, "s3")
    with pytest.raises(CompareStorageUnavailable) as unavailable:
        read_compare_json(str(uuid.uuid4()), "metadata.json")
    assert isinstance(unavailable.value.__cause__, PrivateBucketRefused)


class _PolicyTextClient:
    """An existing bucket whose policy read returns the given text."""

    def __init__(self, policy):
        self.policy = policy
        self.written = []

    def bucket_exists(self, bucket):
        return True

    def get_bucket_policy(self, bucket):
        return self.policy

    def put_object(self, **kwargs):
        self.written.append(kwargs["object_name"])


_NAMED_READER = {
    "Effect": "Allow",
    "Principal": {"AWS": ["arn:aws:iam::000000000000:role/reader"]},
    "Action": ["s3:GetObject"],
    "Resource": ["arn:aws:s3:::bucket/*"],
}


@pytest.mark.parametrize(
    "policy, accepted",
    [
        ("{not json", False),
        ("[]", False),
        ('"text"', False),
        (json.dumps({"Statement": "Allow"}), False),
        (json.dumps({"Statement": ["Allow"]}), False),
        (json.dumps({"Statement": None}), False),
        (json.dumps({"Statement": False}), False),
        (json.dumps({"Statement": ""}), False),
        (
            json.dumps(
                {
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "NotPrincipal": {"AWS": ["arn:aws:iam::0:root"]},
                            "Action": ["s3:GetObject"],
                        }
                    ]
                }
            ),
            False,
        ),
        (json.dumps({"Version": "2012-10-17", "Statement": [_NAMED_READER]}), True),
    ],
    ids=[
        "not-json",
        "not-an-object",
        "a-string",
        "statement-text",
        "statement-list-of-text",
        "statement-null",
        "statement-false",
        "statement-empty-text",
        "allow-not-principal",
        "named-principal",
    ],
)
def test_a_policy_that_cannot_be_read_as_private_is_refused(
    monkeypatch, settings, policy, accepted
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused

    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = f"test-upload-compare-{uuid.uuid4().hex[:8]}"
    client = _PolicyTextClient(policy)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    compare_id = str(uuid.uuid4())
    if accepted:
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert client.written == [f"compare/{compare_id}/metadata.json"]
    else:
        with pytest.raises(PrivateBucketRefused):
            write_compare_json(compare_id, "metadata.json", {"status": "completed"})
        assert client.written == []


class _FakeBucketClient:
    """Object storage holding objects with their modification times."""

    def __init__(self, objects, fail_remove=()):
        self.objects = dict(objects)
        self.fail_remove = set(fail_remove)

    def bucket_exists(self, bucket):
        return True

    def get_bucket_policy(self, bucket):
        error = Exception("no policy")
        error.code = "NoSuchBucketPolicy"
        raise error

    def list_objects(self, bucket, prefix="", recursive=False):
        return [
            SimpleNamespace(object_name=key.split("/", 1)[1], last_modified=modified)
            for key, modified in sorted(self.objects.items())
            if key.startswith(f"{bucket}/{prefix}")
        ]

    def remove_object(self, bucket, name):
        if name.split("/")[1] in self.fail_remove:
            raise RuntimeError("storage unavailable")
        self.objects.pop(f"{bucket}/{name}", None)


@pytest.mark.django_db
def test_scheduled_cleanup_removes_expired_comparisons_and_keeps_fresh_ones(
    monkeypatch, tmp_path, settings
):
    from model_hub.tasks.develop_dataset import delete_unused_compare_folder

    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = "test-upload-compare"
    root = tmp_path / "compare-root"
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(root))
    now = datetime.now(UTC)
    expired_id, fresh_id, stuck_id, remote_only_id, legacy_id = (
        str(uuid.uuid4()) for _ in range(5)
    )
    old, recent = now - timedelta(hours=3), now - timedelta(minutes=10)
    client = _FakeBucketClient(
        {
            f"test-upload-compare/compare/{expired_id}/metadata.json": old,
            f"test-upload-compare/compare/{expired_id}/page_1.json": old,
            f"test-upload-compare/compare/{fresh_id}/metadata.json": recent,
            f"test-upload-compare/compare/{stuck_id}/page_1.json": old,
            f"test-upload-compare/compare/{remote_only_id}/page_1.json": old,
            f"test-upload/compare/{legacy_id}/page_1.json": old,
            "test-upload/images/kept.png": old,
        },
        fail_remove={stuck_id},
    )
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    for compare_id in (expired_id, fresh_id, stuck_id):
        (root / compare_id).mkdir(parents=True)
    hours_ago = time.time() - 3 * 60 * 60
    real_getctime = os.path.getctime
    monkeypatch.setattr(
        "os.path.getctime",
        lambda path: (
            hours_ago
            if os.path.basename(path) in (expired_id, stuck_id)
            else real_getctime(path)
        ),
    )

    delete_unused_compare_folder()

    assert sorted(p.name for p in root.iterdir()) == sorted([fresh_id, stuck_id])
    assert sorted(client.objects) == sorted(
        [
            f"test-upload-compare/compare/{fresh_id}/metadata.json",
            # Remote deletion failed, so its local folder stays for a retry.
            f"test-upload-compare/compare/{stuck_id}/page_1.json",
            "test-upload/images/kept.png",
        ]
    )


class _SlowListingClient(_FakeBucketClient):
    """Storage whose listing advances the clock for every object it returns."""

    def __init__(self, objects, clock, seconds_per_object):
        super().__init__(objects)
        self.clock = clock
        self.seconds_per_object = seconds_per_object

    def list_objects(self, bucket, prefix="", recursive=False):
        for obj in super().list_objects(bucket, prefix, recursive):
            self.clock[0] += self.seconds_per_object
            yield obj


def test_a_slow_listing_still_removes_the_expired_comparisons_it_reached(
    monkeypatch, settings
):
    from model_hub.utils import compare_storage

    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = "test-upload-compare"
    now = datetime.now(UTC)
    old, recent = now - timedelta(hours=3), now - timedelta(minutes=10)
    # Listings return keys in order: two expired comparisons come first, then
    # more fresh ones than the sweep can list before its deadline.
    first, second, legacy = (str(uuid.UUID(int=number)) for number in (1, 2, 3))
    fresh = [str(uuid.UUID(int=100 + number)) for number in range(20)]
    objects = {
        f"test-upload-compare/compare/{first}/page_1.json": old,
        f"test-upload-compare/compare/{second}/page_1.json": old,
        f"test-upload/compare/{legacy}/page_1.json": old,
        "test-upload/images/kept.png": old,
    }
    objects.update(
        {f"test-upload-compare/compare/{key}/page_1.json": recent for key in fresh}
    )
    clock = [0.0]
    client = _SlowListingClient(objects, clock, seconds_per_object=30)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    monkeypatch.setattr(
        compare_storage,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], time=time.time),
    )

    # Listing every object would take 11 minutes; the sweep has 8.
    compare_storage.sweep_remote_compare_objects(deadline=8 * 60, now=now)

    assert sorted(client.objects) == sorted(
        [
            "test-upload/images/kept.png",
            *(f"test-upload-compare/compare/{key}/page_1.json" for key in fresh),
        ]
    )


def _private_comparison(auth_client, organization, workspace, monkeypatch, tmp_path):
    shared = [f"deleted-{index:02d}" for index in range(12)]
    left, _, _ = _make_dataset(organization, workspace, "Deleted left", shared, "l")
    right, _, _ = _make_dataset(organization, workspace, "Deleted right", shared, "r")
    _replica(monkeypatch, tmp_path, "replica-a")
    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    return left, right, first["metadata"]["compare_id"], _row_ids(first)[0]


@pytest.mark.django_db(transaction=True)
def test_a_deleted_comparison_is_not_served_before_its_cleanup_runs(
    auth_client, organization, workspace, minio_storage, monkeypatch, tmp_path
):
    left, right, compare_id, row_id = _private_comparison(
        auth_client, organization, workspace, monkeypatch, tmp_path
    )
    requested = []
    # The cleanup activity is queued but has not run yet.
    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        lambda *args, **kwargs: requested.append(kwargs.get("args")),
    )

    deleted = auth_client.delete(DELETE_URL.format(compare_id=compare_id))
    assert deleted.status_code == status.HTTP_200_OK, deleted.content[:500]
    assert requested == [(compare_id,)]

    # Another replica, without the local copy, answers as for an unknown id.
    _replica(monkeypatch, tmp_path, "replica-b")
    unknown = auth_client.get(ROW_URL.format(compare_id=uuid.uuid4(), row_id=row_id))
    for response in (
        auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=row_id)),
        _post(auth_client, left.id, [right.id], 1, compare_id),
        _download(auth_client, left.id, [right.id], compare_id),
    ):
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[:500]
        assert response.json() == unknown.json()
    # The pages are left to the cleanup activity.
    found = _compare_objects(minio_storage, compare_id)
    assert sorted(found[minio_storage.compare_bucket]) == [
        f"compare/{compare_id}/page_1.json",
        f"compare/{compare_id}/page_2.json",
    ]


@pytest.mark.django_db(transaction=True)
def test_a_comparison_whose_metadata_was_not_removed_is_not_reported_deleted(
    auth_client, organization, workspace, minio_storage, monkeypatch, tmp_path
):
    left, right, compare_id, row_id = _private_comparison(
        auth_client, organization, workspace, monkeypatch, tmp_path
    )
    requested = []
    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        lambda *args, **kwargs: requested.append(kwargs.get("args")),
    )

    def storage_unavailable(compare_id):
        raise RuntimeError("synthetic storage outage")

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata",
        storage_unavailable,
        raising=False,
    )

    deleted = auth_client.delete(DELETE_URL.format(compare_id=compare_id))
    assert deleted.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, (
        deleted.content[:500]
    )
    assert requested == []
    found = _compare_objects(minio_storage, compare_id)
    assert f"compare/{compare_id}/metadata.json" in found[minio_storage.compare_bucket]
