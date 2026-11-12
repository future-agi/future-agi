"""OpenAI Videos SDK contract against the gateway's /v1/videos route.

Run by TestVideoOpenAISDKContract with a gateway handler backed by a fake
upstream adapter. Uses only the public openai-python surface; the gateway
requires an Idempotency-Key, which SDK users pass through extra_headers.
"""

import hashlib
import json
import os
import struct
import sys
import time
import urllib.request
import uuid
import zlib

import openai
from openai import OpenAI


def png(width, height):
    raw = b"".join(b"\x00" + bytes([(x * 7 + y * 3) % 256 for x in range(width)] * 3)[: width * 3] for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def check(condition, message):
    if not condition:
        print("CONTRACT_FAIL:", message, flush=True)
        sys.exit(1)


def main():
    base, key, model = os.environ["GATEWAY_BASE_URL"], os.environ["GATEWAY_KEY"], os.environ["GATEWAY_MODEL"]
    client = OpenAI(base_url=base, api_key=key, max_retries=0)
    report = {"openai_version": openai.__version__}

    idem = {"Idempotency-Key": "sdk-" + uuid.uuid4().hex}
    video = client.videos.create(model=model, prompt="synthetic sdk contract clip", seconds="5", size="1280x720", extra_headers=idem)
    check(video.object == "video" and video.status == "queued", f"create: {video}")
    check(video.seconds == "5" and video.size == "1280x720" and video.model == model, f"create fields: {video}")
    replay = client.videos.create(model=model, prompt="synthetic sdk contract clip", seconds="5", size="1280x720", extra_headers=idem)
    check(replay.id == video.id, "idempotent replay returned a different job")

    deadline = time.time() + 90
    current = client.videos.retrieve(video.id)
    while current.status in ("queued", "in_progress") and time.time() < deadline:
        time.sleep(0.5)
        current = client.videos.retrieve(video.id)
    check(current.status == "completed", f"terminal status: {current.status} {current}")
    check(current.progress == 100 and current.completed_at, f"completed fields: {current}")
    report["retrieve_terminal"] = current.status

    content = client.videos.download_content(video.id, variant="video")
    data = content.read()
    digest = hashlib.sha256(data).hexdigest()
    check(digest == os.environ["EXPECTED_SHA256"], f"content sha256 {digest}")
    report["content_sha256"] = digest

    # The fake model only accepts first frames with adaptive ratio, so no size.
    framed = client.videos.create(model=model, prompt="first frame contract", seconds="5", input_reference=("frame.png", png(320, 320), "image/png"), extra_headers={"Idempotency-Key": "sdk-" + uuid.uuid4().hex})
    check(framed.status == "queued", f"input_reference create: {framed}")

    poll = getattr(client.videos, "poll", None)
    if poll is not None:
        try:
            polled = poll(framed.id, poll_interval_ms=300)
        except TypeError:
            polled = poll(framed.id)
        check(polled.status == "completed", f"sdk poll helper: {polled.status}")
        report["sdk_poll_helper"] = polled.status

    # A job created by a native JSON client reads in the OpenAI dialect here,
    # and a list through the SDK never mixes dialects.
    native_req = urllib.request.Request(base + "/videos", method="POST", data=json.dumps({"model": model, "prompt": "native client job"}).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Idempotency-Key": "native-" + uuid.uuid4().hex})
    with urllib.request.urlopen(native_req) as r:
        native = json.loads(r.read())
    check(native["status"] == "submitting", f"native create: {native}")
    print("NATIVE_JOB", native["id"], flush=True)
    enum = {"queued", "in_progress", "completed", "failed"}
    check(client.videos.retrieve(native["id"]).status in enum, "native job read through the SDK is not in OpenAI's enum")
    listed = list(client.videos.list(limit=1))
    seen = [v.id for v in listed]
    check(video.id in seen and framed.id in seen and native["id"] in seen and len(seen) == len(set(seen)), f"cursor pagination: {seen}")
    check(all(v.status in enum for v in listed), f"mixed dialect list: {[v.status for v in listed]}")
    report["listed_via_cursor"] = len(seen)

    other = OpenAI(base_url=base, api_key=os.environ["GATEWAY_OTHER_KEY"], max_retries=0)
    try:
        other.videos.retrieve(video.id)
        check(False, "cross-tenant retrieve succeeded")
    except openai.NotFoundError:
        report["cross_tenant"] = "404"

    try:
        client.videos.create(model=model, prompt="no key", seconds="5")
        check(False, "missing Idempotency-Key accepted")
    except openai.BadRequestError as e:
        check(e.code == "missing_idempotency_key", f"error code {e.code}")
        report["missing_idempotency_key"] = e.code

    try:
        client.videos.create(model="sora-2", prompt="retired", seconds="4", extra_headers={"Idempotency-Key": "sdk-" + uuid.uuid4().hex})
        check(False, "retired Sora accepted")
    except openai.BadRequestError as e:
        report["sora"] = e.code

    deleted = client.videos.delete(video.id)
    check(deleted.deleted and deleted.object == "video.deleted" and deleted.id == video.id, f"delete: {deleted}")
    try:
        client.videos.download_content(video.id)
        check(False, "content still served after delete")
    except (openai.NotFoundError, openai.APIStatusError) as e:
        report["after_delete"] = getattr(e, "status_code", None)

    print(json.dumps(report, sort_keys=True))
    print("OPENAI_SDK_CONTRACT_OK", flush=True)


if __name__ == "__main__":
    main()
