"""Adapt the deployment storage client to the bounded audio reader's S3 seam."""

from contextlib import contextmanager


@contextmanager
def _storage_errors():
    from botocore.exceptions import ClientError
    from minio.error import S3Error

    try:
        yield
    except S3Error as exc:
        # Never propagate storage URLs or response bodies to Temporal history.
        status = getattr(exc.response, "status", 500)
        raise ClientError(
            {
                "Error": {"Code": exc.code},
                "ResponseMetadata": {"HTTPStatusCode": status},
            },
            "audio_storage",
        ) from None


class _Body:
    def __init__(self, response, checkpoint=None):
        self.response = response
        self.checkpoint = checkpoint

    def read(self, size):
        if self.checkpoint:
            self.checkpoint()
        return self.response.read(size)

    def close(self):
        self.response.close()
        self.response.release_conn()


class AudioStorage:
    def __init__(self, client, checkpoint=None):
        self.client = client
        self.checkpoint = checkpoint

    def head_object(self, *, Bucket, Key):
        with _storage_errors():
            stat = self.client.stat_object(Bucket, Key)
        return {
            "ContentLength": stat.size,
            "ETag": f'"{stat.etag}"' if stat.etag else None,
            "VersionId": stat.version_id,
        }

    def get_object(self, *, Bucket, Key, VersionId=None, IfMatch=None):
        with _storage_errors():
            response = self.client.get_object(
                Bucket,
                Key,
                version_id=VersionId,
                request_headers={"If-Match": IfMatch} if IfMatch else None,
            )
        return {
            "Body": _Body(response, self.checkpoint),
            "ETag": response.headers.get("ETag"),
            "VersionId": response.headers.get("x-amz-version-id"),
        }


class _NoRedirectTransport:
    """Reuse the deployment pool with redirects off and Temporal owning retries."""

    def __init__(self, transport):
        self.transport = transport

    def clear(self):
        # Minio.__del__ clears its transport. This copy does not own the
        # deployment pool, which must remain live for other storage consumers.
        pass

    def urlopen(self, method, url, **kwargs):
        from urllib3 import Timeout

        kwargs.update(
            redirect=False, retries=False, timeout=Timeout(connect=10, read=30)
        )
        return self.transport.urlopen(method, url, **kwargs)


def get_audio_storage(checkpoint=None):
    import copy

    from tfc.utils.storage_client import get_storage_client

    # Minio accepts the deployment transport at construction, but exposes no
    # public clone/configuration API. Copy only this client handle so the pool
    # and credentials remain deployment-owned and other consumers are unchanged.
    client = copy.copy(get_storage_client())
    client._http = _NoRedirectTransport(client._http)
    return AudioStorage(client, checkpoint)


def is_storage_transport_error(exc):
    from urllib3.exceptions import HTTPError

    return isinstance(exc, HTTPError)
