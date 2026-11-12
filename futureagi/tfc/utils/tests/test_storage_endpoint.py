"""Storage endpoint validation must happen before constructing a transport."""

import importlib

import pytest
from minio import Minio

from tfc.utils.storage_client import _parse_endpoint


@pytest.mark.parametrize(
    ("raw", "host", "tls"),
    [
        ("minio:9000", "minio:9000", None),
        ("localhost:9000", "localhost:9000", None),
        ("storage.example.test", "storage.example.test", None),
        ("http://minio:9000/", "minio:9000", False),
        ("https://storage.example.test:9443", "storage.example.test:9443", True),
        ("[::1]:9000", "[::1]:9000", None),
        ("https://[::1]:9443", "[::1]:9443", True),
    ],
)
def test_endpoint_constructs_real_minio_url(raw, host, tls):
    assert _parse_endpoint(raw) == (host, tls)
    client = Minio(host, secure=bool(tls), region="us-east-1")
    url = client._base_url.build(
        method="HEAD", region="us-east-1", bucket_name="test-bucket"
    )
    assert url.netloc == host
    assert url.scheme == ("https" if tls else "http")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "http://",
        "https:///missing-host",
        ":9000",
        "http://:9000",
        "ftp://storage.example.test",
        "storage.example.test:bad",
        "storage.example.test:0",
        "storage.example.test:65536",
        "minio:",
        "http://minio:9000/path",
        "minio:9000/path",
        "http://minio?",
        "minio#fragment",
        "http://user:password@minio:9000",
        "http://minio\\other",
        "http://minio\n:9000",
        "http://[::1",
        ".",
    ],
)
def test_endpoint_rejects_malformed_authorities_without_echoing_config(raw):
    with pytest.raises(ValueError) as exc:
        _parse_endpoint(raw)
    assert str(exc.value) == "Storage endpoint must be a host[:port] or an HTTP(S) URL"


@pytest.mark.parametrize(
    "public_url",
    [
        "http://user:password@storage.example.test",
        "https://münchen.example/path",
        "http://",
        "ftp://münchen.example",
    ],
)
def test_object_url_still_validates_public_endpoint(monkeypatch, public_url):
    # Generated URLs keep MINIO_URL's configured host form, but only after the
    # same validation the storage client applies to every endpoint.
    storage = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(storage, "STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_URL", public_url)
    with pytest.raises(ValueError) as exc:
        storage.get_object_url("uploads", "notes.txt")
    assert str(exc.value) == "Storage endpoint must be a host[:port] or an HTTP(S) URL"
