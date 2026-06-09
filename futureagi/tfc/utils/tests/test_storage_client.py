import importlib
import sys


STORAGE_ENV_KEYS = (
    "STORAGE_BACKEND",
    "S3_ENDPOINT",
    "S3_ENDPOINT_URL",
    "S3_SECURE",
    "MINIO_URL",
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
