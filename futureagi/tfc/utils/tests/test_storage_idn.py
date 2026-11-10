"""Public IDN URLs must work with an ASCII-only internal MinIO endpoint."""

import importlib
import uuid
from io import BytesIO
from urllib.parse import urlsplit

import pytest
import requests

PUBLIC_HOSTS = ["münchen.example", "xn--mnchen-3ya.example"]


@pytest.fixture
def idn_storage(monkeypatch):
    storage = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(storage, "STORAGE_BACKEND", "minio")
    for name in ("S3_ENDPOINT", "S3_SECURE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:19005")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    storage.reset_storage_client()
    yield storage
    storage.reset_storage_client()


@pytest.mark.parametrize(
    ("public_host", "ascii_host"),
    [
        ("münchen.example", "xn--mnchen-3ya.example"),
        ("xn--mnchen-3ya.example", "xn--mnchen-3ya.example"),
        # IDNA 2003 maps ß to ss, changing the destination. Match browsers'
        # non-transitional IDNA handling rather than Python's legacy codec.
        ("straße.example", "xn--strae-oqa.example"),
        ("xn--strae-oqa.example", "xn--strae-oqa.example"),
    ],
)
def test_public_idn_object_url(idn_storage, monkeypatch, public_host, ascii_host):
    monkeypatch.setenv("MINIO_URL", f"https://{public_host}:9443")
    url = idn_storage.get_object_url("uploads", "notes.txt")
    assert requests.Request("GET", url).prepare().url == (
        f"https://{ascii_host}:9443/uploads/notes.txt"
    )


@pytest.mark.parametrize("public_host", PUBLIC_HOSTS)
@pytest.mark.parametrize("stored_host", PUBLIC_HOSTS)
def test_existing_idn_url_rewrites_to_internal_endpoint(
    idn_storage, monkeypatch, public_host, stored_host
):
    monkeypatch.setenv("MINIO_URL", f"https://{public_host}:9443")
    url = f"https://{stored_host}:9443/uploads/notes.txt?download=1"
    assert idn_storage.server_reachable_url(url) == (
        "http://localhost:19005/uploads/notes.txt?download=1"
    )
    # An unrelated authority or port must not be rewritten.
    for other in ("other.example:9443", f"{stored_host}:9444"):
        foreign_url = f"https://{other}/uploads/notes.txt"
        assert idn_storage.server_reachable_url(foreign_url) == foreign_url


@pytest.mark.integration
@pytest.mark.parametrize("public_host", PUBLIC_HOSTS)
def test_dataset_upload_with_public_idn_roundtrips_real_minio(
    idn_storage, monkeypatch, public_host
):
    from model_hub.views.datasets.create import file_upload

    monkeypatch.setenv("MINIO_URL", f"https://{public_host}")
    bucket = f"idn-upload-{uuid.uuid4().hex}"
    monkeypatch.setattr(file_upload, "UPLOAD_BUCKET_NAME", bucket)
    # Other storage tests reload the factory module. Bind the current real
    # implementation rather than any stale import; no storage I/O is mocked.
    monkeypatch.setattr(file_upload, "get_storage_client", idn_storage.get_storage_client)
    monkeypatch.setattr(file_upload, "ensure_bucket", idn_storage.ensure_bucket)
    client = idn_storage.get_storage_client()
    key = "synthetic/notes.txt"
    payload = b"Synthetic IDN storage upload.\n"
    try:
        try:
            url = file_upload.upload_file_to_minio(BytesIO(payload), key)
        finally:
            # Prove the reproduction writes successfully even if URL generation
            # raises afterward, and always release the real response connection.
            response = client.get_object(bucket, key)
            try:
                assert response.read() == payload
                print(f"REAL_MINIO_WRITE_VERIFIED: {len(payload)} bytes")
            finally:
                response.close()
                response.release_conn()
        assert requests.Request("GET", url).prepare().url == (
            f"https://xn--mnchen-3ya.example/{bucket}/{key}"
        )
        internal_url = idn_storage.server_reachable_url(url)
        assert urlsplit(internal_url).netloc == "localhost:19005"
        with requests.get(internal_url, timeout=10) as response:
            response.raise_for_status()
            assert response.content == payload
        print(f"REAL_MINIO_UPLOAD_ROUNDTRIP_OK: {public_host}")
    finally:
        if client.bucket_exists(bucket):
            client.remove_object(bucket, key)
            client.remove_bucket(bucket)
