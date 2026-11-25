"""Storage endpoint validation must happen before constructing a transport."""

import importlib
from io import BytesIO
from unittest.mock import Mock

import pytest
from minio import Minio

from tfc.utils.storage_client import _parse_endpoint, public_base_url

ENDPOINT_ERROR = "Storage endpoint must be a host[:port] or an HTTP(S) URL"


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
        # Only the authority is used; anything after it is ignored, as before.
        ("http://minio:9000/path", "minio:9000", False),
        ("https://storage.example.test/minio/", "storage.example.test", True),
        ("minio:9000/path", "minio:9000", None),
        ("http://minio?", "minio", False),
        ("minio#fragment", "minio", None),
        # Surrounding whitespace, e.g. a secret file's trailing newline.
        ("http://minio:9000\n", "minio:9000", False),
        (" minio:9000\r\n", "minio:9000", None),
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
        "http://user:password@minio:9000",
        "http://minio\\other",
        "http://minio\n:9000",
        "http://minio :9000",
        "\n",
        "http://[::1",
        ".",
    ],
)
def test_endpoint_rejects_malformed_authorities_without_echoing_config(raw):
    with pytest.raises(ValueError) as exc:
        _parse_endpoint(raw)
    assert str(exc.value) == ENDPOINT_ERROR


@pytest.mark.parametrize(
    "public_url",
    [
        "http://user:password@storage.example.test",
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
    assert str(exc.value) == ENDPOINT_ERROR


@pytest.mark.parametrize(
    ("public_url", "object_url"),
    [
        ("https://münchen.example/path", "https://münchen.example/uploads/notes.txt"),
        ("localhost:29005/", "http://localhost:29005/uploads/notes.txt"),
        ("localhost:29005\n", "http://localhost:29005/uploads/notes.txt"),
        (
            " https://localhost:29005/storage/ ",
            "https://localhost:29005/uploads/notes.txt",
        ),
    ],
)
def test_object_url_is_path_style_at_the_public_authority(
    monkeypatch, public_url, object_url
):
    # A path prefix is not part of the object URL, which must keep the shape
    # tfc.utils.storage.is_own_storage_url recognises as first-party.
    storage = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(storage, "STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_URL", public_url)
    assert storage.get_object_url("uploads", "notes.txt") == object_url


@pytest.mark.parametrize(
    ("public_url", "base_url"),
    [
        ("http://localhost:29005/storage", "http://localhost:29005/storage"),
        ("http://localhost:29005/storage/", "http://localhost:29005/storage"),
        ("http://localhost:29005/", "http://localhost:29005"),
        ("http://localhost:29005\n", "http://localhost:29005"),
        (
            "HTTPS://münchen.example:9443/minio?x=1#f",
            "https://münchen.example:9443/minio",
        ),
        ("test-minio:9000", "test-minio:9000"),
        ("test-minio:9000/", "test-minio:9000"),
    ],
)
def test_public_base_url_keeps_prefix_and_configured_host(public_url, base_url):
    assert public_base_url(public_url) == base_url


@pytest.mark.parametrize(
    "public_url", ["", "\n", "http://", "http://user:secret@storage.example.test"]
)
def test_public_base_url_validates_like_the_storage_client(public_url):
    with pytest.raises(ValueError) as exc:
        public_base_url(public_url)
    assert str(exc.value) == ENDPOINT_ERROR


def test_dataset_upload_checks_public_endpoint_before_writing(monkeypatch):
    from model_hub.views.datasets.create import file_upload

    storage = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(storage, "STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_URL", "http://")
    client = Mock()
    monkeypatch.setattr(file_upload, "get_storage_client", lambda: client)
    monkeypatch.setattr(file_upload, "ensure_bucket", Mock())

    with pytest.raises(ValueError) as exc:
        file_upload.upload_file_to_minio(BytesIO(b"a,b\n"), "synthetic/notes.csv")

    assert str(exc.value) == ENDPOINT_ERROR
    # No object is left behind that no dataset row would reference.
    client.put_object.assert_not_called()
