import importlib
import json
import sys

STORAGE_ENV_KEYS = (
    "STORAGE_BACKEND",
    "S3_ENDPOINT",
    "S3_ENDPOINT_URL",
    "S3_SECURE",
    "MINIO_URL",
    "MINIO_REGION",
    "GCS_HMAC_ACCESS_KEY",
    "GCS_HMAC_SECRET_KEY",
    "AWS_DEFAULT_REGION",
)


def reload_storage_client(monkeypatch, **env):
    for key in STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("tfc.utils.storage_client", None)
    return importlib.import_module("tfc.utils.storage_client")


def test_minio_object_url_uses_public_minio_url(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="minio",
        S3_ENDPOINT_URL="http://minio:9000",
        MINIO_URL="http://localhost:9005",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "http://localhost:9005/futureagi/tempcust/image.png"
    )


def test_minio_object_url_falls_back_to_default_when_minio_url_unset(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="minio",
        S3_ENDPOINT_URL="http://minio:9000",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "http://localhost:9005/futureagi/tempcust/image.png"
    )


def test_s3_object_url_ignores_minio_url(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="s3",
        MINIO_URL="http://localhost:9005",
        AWS_DEFAULT_REGION="us-east-1",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "https://futureagi.s3.us-east-1.amazonaws.com/tempcust/image.png"
    )


def test_gcs_object_url_ignores_s3_and_minio_urls(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        S3_ENDPOINT_URL="http://minio:9000",
        MINIO_URL="http://localhost:9005",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "https://storage.googleapis.com/futureagi/tempcust/image.png"
    )


def test_gcs_client_uses_minio_region(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        MINIO_REGION="europe-west3",
        GCS_HMAC_ACCESS_KEY="access",
        GCS_HMAC_SECRET_KEY="secret",
    )
    captured = {}

    def fake_minio(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(storage_client, "Minio", fake_minio)
    storage_client.reset_storage_client()
    storage_client.get_storage_client()

    assert captured["args"][0] == "storage.googleapis.com"
    assert captured["kwargs"]["region"] == "europe-west3"


def test_gcs_client_falls_back_to_auto_region(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        GCS_HMAC_ACCESS_KEY="access",
        GCS_HMAC_SECRET_KEY="secret",
    )
    captured = {}

    def fake_minio(*args, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(storage_client, "Minio", fake_minio)
    storage_client.reset_storage_client()
    storage_client.get_storage_client()

    assert captured["kwargs"]["region"] == "auto"


class FakeBucketClient:
    """Records bucket calls; holds one bucket's policy like MinIO does."""

    def __init__(self, exists=False, policy=None, policy_error=None, set_error=None):
        self.exists = exists
        self.policy = policy
        self.policy_error = policy_error
        self.set_error = set_error
        self.set_calls = []
        self.get_calls = 0

    def bucket_exists(self, bucket_name):
        return self.exists

    def make_bucket(self, bucket_name):
        self.exists = True

    def get_bucket_policy(self, bucket_name):
        self.get_calls += 1
        if self.policy_error:
            raise self.policy_error
        return self.policy

    def set_bucket_policy(self, bucket_name, policy):
        if self.set_error:
            raise self.set_error
        self.set_calls.append(json.loads(policy))
        self.policy = policy


def _anonymous_statements(policy):
    return [
        s
        for s in policy["Statement"]
        if s["Effect"] == "Allow" and s["Principal"] in ("*", {"AWS": ["*"]})
    ]


def _legacy_public_policy(bucket="futureagi", principal="*"):
    """What ensure_bucket applied before: every action for anyone."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": principal,
                    "Action": "s3:*",
                    "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


def test_new_bucket_is_readable_by_url_but_not_writable_or_listable(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(exists=False)

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    (statement,) = _anonymous_statements(policy)
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::futureagi/*"]


def test_existing_minio_bucket_with_the_old_policy_is_cut_back_to_reads(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True, policy=_legacy_public_policy(principal={"AWS": ["*"]})
    )

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    (statement,) = _anonymous_statements(policy)
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::futureagi/*"]


def test_tightening_keeps_statements_for_named_principals(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    named = {
        "Effect": "Allow",
        "Principal": {"AWS": ["arn:aws:iam::123456789012:user/backup"]},
        "Action": ["s3:ListBucket"],
        "Resource": ["arn:aws:s3:::futureagi"],
    }
    legacy = json.loads(_legacy_public_policy())
    legacy["Statement"].append(named)
    client = FakeBucketClient(exists=True, policy=json.dumps(legacy))

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    assert named in policy["Statement"]
    assert [s["Action"] for s in _anonymous_statements(policy)] == [["s3:GetObject"]]


def test_read_only_bucket_is_left_alone(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    read_only = {
        "Version": "2012-10-17",
        "Statement": [storage_client._anonymous_read_statement("futureagi")],
    }
    client = FakeBucketClient(exists=True, policy=json.dumps(read_only))

    storage_client.ensure_bucket(client, "futureagi")

    assert client.set_calls == []


def test_existing_bucket_policy_is_checked_once_per_process(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(exists=True, policy=_legacy_public_policy())

    storage_client.ensure_bucket(client, "futureagi")
    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 1
    assert len(client.set_calls) == 1


def test_operator_owned_s3_bucket_policy_is_not_touched(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="s3")
    client = FakeBucketClient(exists=True, policy=_legacy_public_policy())

    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 0
    assert client.set_calls == []


def test_unreadable_or_missing_policy_does_not_fail_the_upload(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True, policy_error=RuntimeError("NoSuchBucketPolicy")
    )

    storage_client.ensure_bucket(client, "futureagi")

    assert client.set_calls == []


def test_failed_tightening_does_not_fail_the_upload(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True,
        policy=_legacy_public_policy(),
        set_error=RuntimeError("AccessDenied"),
    )

    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 1
