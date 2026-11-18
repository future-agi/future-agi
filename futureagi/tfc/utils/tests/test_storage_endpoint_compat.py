"""Storage endpoint settings that worked before endpoint validation keep working.

Validation must reject what cannot name a storage authority, not values that
installs already run with: a path prefix or trailing slash on MINIO_URL or
S3_ENDPOINT_URL, or the trailing newline a secret file or YAML block scalar
leaves. Every row below worked with the earlier parser. ``_earlier_parse_endpoint``
is a frozen copy of it, so each row is checked against both parsers, and against
the address the Minio SDK actually connects to.
"""

import importlib
import uuid
from io import BytesIO
from urllib.parse import urlparse

import pytest
import requests
from minio import Minio


def _earlier_parse_endpoint(raw_endpoint):
    """tfc.utils.storage_client._parse_endpoint before endpoint validation."""
    if "://" in raw_endpoint:
        parsed = urlparse(raw_endpoint)
        return parsed.netloc or parsed.path, parsed.scheme == "https"
    return raw_endpoint, None


def _current_parse_endpoint(raw_endpoint):
    storage = importlib.import_module("tfc.utils.storage_client")
    return storage._parse_endpoint(raw_endpoint)


def _sdk_base_url(host, tls):
    """Where a Minio client built from a parsed endpoint sends bucket requests."""
    client = Minio(host, secure=bool(tls), region="us-east-1")
    url = client._base_url.build(
        method="HEAD", region="us-east-1", bucket_name="uploads"
    )
    return f"{url.scheme}://{url.netloc}{url.path}"


# (S3_ENDPOINT_URL, where the SDK sends a request for bucket "uploads")
CLIENT_ENDPOINTS = [
    ("minio:9000", "http://minio:9000/uploads"),
    ("minio:9000/", "http://minio:9000/uploads"),
    ("minio:9000\n", "http://minio:9000/uploads"),
    ("http://minio:9000", "http://minio:9000/uploads"),
    ("http://minio:9000/", "http://minio:9000/uploads"),
    ("http://minio:9000//", "http://minio:9000/uploads"),
    ("http://minio:9000/prefix", "http://minio:9000/uploads"),
    ("http://minio:9000/prefix/", "http://minio:9000/uploads"),
    ("http://minio:9000\n", "http://minio:9000/uploads"),
    ("http://minio:9000\r\n", "http://minio:9000/uploads"),
    (" http://minio:9000", "http://minio:9000/uploads"),
    ("\thttp://minio:9000\t", "http://minio:9000/uploads"),
    ("http://minio:9000?region=local", "http://minio:9000/uploads"),
    ("http://minio:9000#storage", "http://minio:9000/uploads"),
    ("https://storage.example.test:9443/", "https://storage.example.test:9443/uploads"),
    ("https://storage.example.test/minio", "https://storage.example.test/uploads"),
    ("HTTP://minio:9000", "http://minio:9000/uploads"),
    ("http://[::1]:9000/", "http://[::1]:9000/uploads"),
]

# (MINIO_URL, get_object_url("uploads", "notes.txt"))
PUBLIC_ENDPOINTS = [
    ("http://localhost:29005", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005/", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005/storage", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005/storage/", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005\n", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005\r\n", "http://localhost:29005/uploads/notes.txt"),
    ("http://localhost:29005?x=1", "http://localhost:29005/uploads/notes.txt"),
    (
        "https://storage.example.test/minio",
        "https://storage.example.test/uploads/notes.txt",
    ),
    ("localhost:29005", "http://localhost:29005/uploads/notes.txt"),
]


@pytest.fixture
def minio_storage(monkeypatch):
    storage = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(storage, "STORAGE_BACKEND", "minio")
    for name in ("S3_ENDPOINT", "S3_SECURE", "MINIO_URL", "S3_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("S3_ACCESS_KEY", "synthetic-access")
    monkeypatch.setenv("S3_SECRET_KEY", "synthetic-secret")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    storage.reset_storage_client()
    yield storage
    storage.reset_storage_client()


@pytest.mark.parametrize(
    "parse", [_earlier_parse_endpoint, _current_parse_endpoint], ids=["earlier", "now"]
)
@pytest.mark.parametrize(("raw", "sdk_url"), CLIENT_ENDPOINTS)
def test_endpoint_reaches_the_same_storage_with_either_parser(parse, raw, sdk_url):
    assert _sdk_base_url(*parse(raw)) == sdk_url


@pytest.mark.parametrize(("raw", "sdk_url"), CLIENT_ENDPOINTS)
def test_storage_client_accepts_endpoint(minio_storage, monkeypatch, raw, sdk_url):
    monkeypatch.setenv("S3_ENDPOINT_URL", raw)
    client = minio_storage.get_storage_client()
    url = client._base_url.build(
        method="HEAD", region="us-east-1", bucket_name="uploads"
    )
    assert f"{url.scheme}://{url.netloc}{url.path}" == sdk_url


@pytest.mark.parametrize(("public_url", "object_url"), PUBLIC_ENDPOINTS)
def test_object_url_for_public_endpoint(
    minio_storage, monkeypatch, public_url, object_url
):
    monkeypatch.setenv("MINIO_URL", public_url)
    assert minio_storage.get_object_url("uploads", "notes.txt") == object_url


@pytest.mark.parametrize(("public_url", "object_url"), PUBLIC_ENDPOINTS)
def test_dataset_upload_returns_object_url(
    minio_storage, monkeypatch, public_url, object_url
):
    from model_hub.views.datasets.create import file_upload

    written = []

    class Client:
        def put_object(self, bucket_name, object_name, data, length, content_type):
            written.append((bucket_name, object_name, data.read()))

    monkeypatch.setenv("MINIO_URL", public_url)
    monkeypatch.setattr(file_upload, "UPLOAD_BUCKET_NAME", "uploads")
    monkeypatch.setattr(file_upload, "get_storage_client", Client)
    monkeypatch.setattr(file_upload, "ensure_bucket", lambda client, bucket: None)

    url = file_upload.upload_file_to_minio(BytesIO(b"a,b\n1,2\n"), "notes.txt")

    assert url == object_url
    assert written == [("uploads", "notes.txt", b"a,b\n1,2\n")]


@pytest.mark.parametrize(
    ("public_url", "internal_url"),
    [
        ("http://localhost:29005", "http://minio:9000"),
        ("http://localhost:29005/", "http://minio:9000/"),
        ("http://localhost:29005/storage", "http://minio:9000"),
        ("http://localhost:29005\n", "http://minio:9000\n"),
    ],
)
def test_stored_url_is_rewritten_to_internal_endpoint(
    minio_storage, monkeypatch, public_url, internal_url
):
    monkeypatch.setenv("MINIO_URL", public_url)
    monkeypatch.setenv("S3_ENDPOINT_URL", internal_url)
    stored = "http://localhost:29005/uploads/call.wav"
    assert minio_storage.server_reachable_url(stored) == (
        "http://minio:9000/uploads/call.wav"
    )


@pytest.mark.parametrize(
    ("public_url", "internal_url"),
    [
        ("", "http://minio:9000"),
        ("http://", "http://minio:9000"),
        ("http://localhost:29005", ""),
        ("http://localhost:29005", "http://"),
    ],
)
def test_stored_url_is_left_alone_without_both_endpoints(
    minio_storage, monkeypatch, public_url, internal_url
):
    # Callers fetch recordings with the result; an unusable setting leaves the
    # stored URL as it is rather than failing them.
    monkeypatch.setenv("MINIO_URL", public_url)
    monkeypatch.setenv("S3_ENDPOINT_URL", internal_url)
    stored = "http://localhost:29005/uploads/call.wav"
    assert minio_storage.server_reachable_url(stored) == stored


@pytest.mark.integration
@pytest.mark.parametrize(
    ("internal_url", "public_url"),
    [
        ("http://localhost:19005\n", "http://localhost:19005/storage/"),
        ("http://localhost:19005/", "http://localhost:19005\n"),
    ],
)
def test_dataset_upload_roundtrips_real_minio(
    minio_storage, monkeypatch, internal_url, public_url
):
    from model_hub.views.datasets.create import file_upload

    monkeypatch.setenv("S3_ENDPOINT_URL", internal_url)
    monkeypatch.setenv("MINIO_URL", public_url)
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    bucket = f"compat-upload-{uuid.uuid4().hex}"
    monkeypatch.setattr(file_upload, "UPLOAD_BUCKET_NAME", bucket)
    # Other storage tests reload the factory module; bind the current one.
    monkeypatch.setattr(
        file_upload, "get_storage_client", minio_storage.get_storage_client
    )
    monkeypatch.setattr(file_upload, "ensure_bucket", minio_storage.ensure_bucket)
    client = minio_storage.get_storage_client()
    key = "synthetic/notes.csv"
    payload = b"a,b\n1,2\n"
    try:
        url = file_upload.upload_file_to_minio(BytesIO(payload), key)
        assert url == f"http://localhost:19005/{bucket}/{key}"
        reachable = minio_storage.server_reachable_url(url)
        with requests.get(reachable, timeout=10) as response:
            response.raise_for_status()
            assert response.content == payload
    finally:
        if client.bucket_exists(bucket):
            for item in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, item.object_name)
            client.remove_bucket(bucket)
