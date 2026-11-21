"""Moto 5.1.4 server for the E2E attachment fixture (not in the app image)."""

import base64
import hashlib

from moto.s3.exceptions import S3ClientError
from moto.s3.responses import S3Response
from moto.server import main

_moto_checksum = S3Response._get_checksum


def verified_checksum(self, response_headers):
    # Moto 5.1.4 ignores a standalone x-amz-checksum-sha256 and, when an
    # algorithm is supplied, just echoes its value. Verify the received body
    # before Moto persists it. Leave Moto's conditional writes, encryption,
    # version assignment and exact-version HEAD entirely unchanged.
    provided = self.headers.get("x-amz-checksum-sha256")
    if provided is None:
        return _moto_checksum(self, response_headers)
    actual = base64.b64encode(hashlib.sha256(self.body or b"").digest()).decode("ascii")
    if provided != actual:
        raise S3ClientError("BadDigest", "SHA256 differs from the received object")
    response_headers["x-amz-checksum-sha256"] = actual
    return "SHA256", actual


if __name__ == "__main__":
    S3Response._get_checksum = verified_checksum
    main()
