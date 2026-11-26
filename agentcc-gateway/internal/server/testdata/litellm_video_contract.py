"""LiteLLM video client against the gateway's /v1/videos route.

Run by TestVideoLiteLLMContract with a gateway handler backed by a fake
upstream adapter. LiteLLM is pointed at the gateway as an OpenAI-compatible
base URL; the gateway requires an Idempotency-Key, passed via extra_headers.
"""

import hashlib
import json
import os
import sys
import time
import uuid

import importlib.metadata

import litellm


def check(condition, message):
    if not condition:
        print("CONTRACT_FAIL:", message, flush=True)
        sys.exit(1)


def field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def main():
    base, key, model = os.environ["GATEWAY_BASE_URL"], os.environ["GATEWAY_KEY"], os.environ["GATEWAY_MODEL"]
    common = {"api_base": base, "api_key": key, "custom_llm_provider": "openai"}
    report = {"litellm_version": importlib.metadata.version("litellm")}

    video = litellm.video_generation(prompt="synthetic litellm contract clip", model=model, seconds="5", size="1280x720", extra_headers={"Idempotency-Key": "litellm-" + uuid.uuid4().hex}, **common)
    vid = field(video, "id")
    check(vid and field(video, "status") == "queued", f"create: {video}")
    report["create_status"] = field(video, "status")

    deadline = time.time() + 90
    status = litellm.video_status(video_id=vid, **common)
    while field(status, "status") in ("queued", "in_progress") and time.time() < deadline:
        time.sleep(0.5)
        status = litellm.video_status(video_id=vid, **common)
    check(field(status, "status") == "completed", f"terminal: {status}")
    report["terminal_status"] = field(status, "status")

    content = litellm.video_content(video_id=vid, **common)
    data = content if isinstance(content, (bytes, bytearray)) else getattr(content, "content", None) or content.read()
    digest = hashlib.sha256(data).hexdigest()
    check(digest == os.environ["EXPECTED_SHA256"], f"content sha256 {digest}")
    report["content_sha256"] = digest

    print(json.dumps(report, sort_keys=True))
    print("LITELLM_CONTRACT_OK", flush=True)


if __name__ == "__main__":
    main()
