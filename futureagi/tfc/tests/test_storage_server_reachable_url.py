"""A stored object URL is built for a browser; a process inside the deployment needs another address.

`get_object_url` returns `MINIO_URL` on the self-hosted stack, typically `http://localhost:9005`, and
inside a container that names the container. Anything server side that fetches a recorded object by its
URL therefore gets a refused connection, and an eval bound to a call recording reports as though the
call had no audio.
"""

import importlib

import pytest


def _reload_with(monkeypatch, **environment):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module("tfc.utils.storage_client")
    return importlib.reload(module)


def test_a_minio_url_is_rewritten_to_the_internal_endpoint(monkeypatch):
    module = _reload_with(
        monkeypatch,
        STORAGE_BACKEND="minio",
        MINIO_URL="http://localhost:9005",
        S3_ENDPOINT_URL="http://minio:9000",
    )
    stored = "http://localhost:9005/fi-content-dev/alk-harness/w/j/abc123"

    assert (
        module.server_reachable_url(stored)
        == "http://minio:9000/fi-content-dev/alk-harness/w/j/abc123"
    )


def test_the_object_key_survives_the_rewrite(monkeypatch):
    """Only the address changes. A key with slashes and a query string has to come through whole."""
    module = _reload_with(
        monkeypatch,
        STORAGE_BACKEND="minio",
        MINIO_URL="http://localhost:9005",
        S3_ENDPOINT_URL="http://minio:9000",
    )
    stored = "http://localhost:9005/bucket/a/b/c.wav?x=1"

    assert module.server_reachable_url(stored).endswith("/bucket/a/b/c.wav?x=1")


@pytest.mark.parametrize(
    "url",
    [
        "https://fi-content.s3.us-east-2.amazonaws.com/alk-harness/w/j/abc123",
        "https://storage.googleapis.com/fi-content/alk-harness/w/j/abc123",
    ],
)
def test_a_cloud_url_is_left_alone(monkeypatch, url):
    """S3 and GCS URLs resolve the same everywhere, so rewriting them would only break them."""
    module = _reload_with(
        monkeypatch,
        STORAGE_BACKEND="minio",
        MINIO_URL="http://localhost:9005",
        S3_ENDPOINT_URL="http://minio:9000",
    )

    assert module.server_reachable_url(url) == url


def test_another_backend_is_untouched(monkeypatch):
    """On S3 or GCS the browser URL is already reachable from anywhere."""
    module = _reload_with(
        monkeypatch,
        STORAGE_BACKEND="s3",
        MINIO_URL="http://localhost:9005",
        S3_ENDPOINT_URL="http://minio:9000",
    )
    stored = "http://localhost:9005/bucket/key"

    assert module.server_reachable_url(stored) == stored


def test_one_endpoint_for_both_needs_no_rewrite(monkeypatch):
    """A deployment where browsers and processes share the endpoint must not be disturbed."""
    module = _reload_with(
        monkeypatch,
        STORAGE_BACKEND="minio",
        MINIO_URL="http://minio:9000",
        S3_ENDPOINT_URL="http://minio:9000",
    )
    stored = "http://minio:9000/bucket/key"

    assert module.server_reachable_url(stored) == stored
