"""Public IDN URLs must work with an ASCII-only internal MinIO endpoint."""

import importlib
import uuid
from io import BytesIO
from types import SimpleNamespace
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


@pytest.fixture
def own_idn_storage(idn_storage, monkeypatch):
    """The helpers that decide whether a stored URL is a first-party object."""
    storage = importlib.import_module("tfc.utils.storage")

    def configure(public_url):
        # get_object_url reads the environment; storage.py bound MINIO_URL
        # from settings when it was imported.
        monkeypatch.setenv("MINIO_URL", public_url)
        monkeypatch.setattr(storage, "MINIO_URL", public_url)

    return SimpleNamespace(client=idn_storage, storage=storage, configure=configure)


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


@pytest.mark.parametrize("port", ["", ":9443"])
@pytest.mark.parametrize("public_host", [*PUBLIC_HOSTS, "straße.example"])
def test_generated_url_is_recognised_as_own_storage(own_idn_storage, public_host, port):
    own_idn_storage.configure(f"https://{public_host}{port}")
    url = own_idn_storage.client.get_object_url("uploads", "images/synthetic.png")

    # The configured host form is kept: own-storage matching compares it with
    # the raw MINIO_URL setting, so a punycode rewrite made it look foreign.
    assert url == f"https://{public_host}{port}/uploads/images/synthetic.png"
    assert own_idn_storage.storage.is_own_storage_url(url, "uploads") is True
    assert own_idn_storage.client.server_reachable_url(url) == (
        "http://localhost:19005/uploads/images/synthetic.png"
    )


def test_upload_image_returns_own_generated_url_without_download(
    own_idn_storage, monkeypatch
):
    own_idn_storage.configure("https://münchen.example:9443")
    storage = own_idn_storage.storage
    bucket = storage.UPLOAD_BUCKET_NAME
    url = own_idn_storage.client.get_object_url(bucket, "images/synthetic.png")
    downloads = []

    def download(image_url, *args, **kwargs):
        downloads.append(image_url)
        raise RuntimeError("synthetic: foreign image download attempted")

    def no_storage_client():
        raise AssertionError("an own URL must not be re-uploaded")

    monkeypatch.setattr(storage, "download_image_from_url", download)
    monkeypatch.setattr(storage, "get_storage_client", no_storage_client)

    assert storage.upload_image_to_s3(url, bucket_name=bucket) == url
    assert downloads == []


def test_audio_from_own_generated_url_reads_through_storage_client(
    own_idn_storage, monkeypatch
):
    own_idn_storage.configure("https://münchen.example:9443")
    storage = own_idn_storage.storage
    bucket = storage.UPLOAD_BUCKET_NAME
    url = own_idn_storage.client.get_object_url(bucket, "audio/synthetic.wav")
    payload = b"RIFF-synthetic-audio"
    reads, downloads = [], []

    class Response:
        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _size):
            return self._chunks.pop(0)

        def close(self):
            pass

        def release_conn(self):
            pass

    class Client:
        def get_object(self, bucket_name, key):
            reads.append((bucket_name, key))
            return Response()

    def download(**kwargs):
        downloads.append(kwargs.get("audio_url"))
        raise RuntimeError("synthetic: public-host HTTP download attempted")

    monkeypatch.setattr(storage, "get_storage_client", Client)
    monkeypatch.setattr(storage, "download_audio_from_url", download)

    audio = storage._audio_bytes_from_url(
        url, min_duration_seconds=None, pad_silence=False, timeout=5
    )

    assert audio == payload
    assert reads == [(bucket, "audio/synthetic.wav")]
    assert downloads == []


@pytest.mark.django_db
def test_dataset_image_conversion_links_own_generated_url(own_idn_storage, monkeypatch):
    own_idn_storage.configure("https://münchen.example:9443")
    develop_dataset = importlib.import_module("model_hub.views.develop_dataset")
    converter_cls = develop_dataset.DatatypeConverter
    bucket = converter_cls._OWN_S3_BUCKETS[0]
    url = own_idn_storage.client.get_object_url(bucket, "images/synthetic.png")
    validated, uploaded = [], []

    def validate(value, kind):
        validated.append((value, kind))
        raise ValueError("synthetic: private storage host blocked")

    def upload(*args, **kwargs):
        uploaded.append(args)
        return "https://example.invalid/duplicate.png"

    monkeypatch.setattr(develop_dataset, "validate_file_url", validate)
    monkeypatch.setattr(develop_dataset, "upload_image_to_s3", upload)
    converter = converter_cls("image", dataset_id=uuid.uuid4())

    assert converter._convert_cell_to_image(SimpleNamespace(value=url)) == (url, {})
    assert validated == []
    assert uploaded == []


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
    monkeypatch.setattr(
        file_upload, "get_storage_client", idn_storage.get_storage_client
    )
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
