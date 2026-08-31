import os

NON_STREAMING_TIMEOUT = 30.0


def resolve_harness_internal_url():
    return os.environ.get("HARNESS_INTERNAL_URL", "http://harness:8777").rstrip("/")
