"""Keep recognized video URLs as text through the real agent request path."""

import base64
import copy
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from PIL import Image

from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator, EvalLLMClient


@pytest.fixture
def media_server():
    """Serve synthetic media on loopback; no provider or external media calls."""
    video = (Path(__file__).parent / "fixtures" / "clip.mp4").read_bytes()
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
    png = image.getvalue()
    resources = {
        "/clip.mp4": (video, "video/mp4"),
        "/clip.MP4": (video, "application/octet-stream"),
        "/video": (video, "application/octet-stream"),
        "/video-image-header": (video, "image/png"),
        "/video-header": (b"synthetic video body", "Video/MP4"),
        "/picture.png": (png, "image/png"),
        "/picture": (png, "application/octet-stream"),
        "/picture.mp4": (png, "image/png"),
    }
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(
                {"path": self.path, "user_agent": self.headers.get("User-Agent")}
            )
            body, content_type = resources[urlsplit(self.path).path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, png
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def judge_requests(monkeypatch):
    """Only replace the provider boundary, not AgentLoop or media processing."""
    captured = []

    async def stream(_self, messages, tools=None, max_retries=3):
        captured.append(copy.deepcopy(messages))
        yield {
            "choices": [
                {
                    "delta": {
                        "content": '{"result": "Pass", "explanation": "URL text only"}'
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(EvalLLMClient, "stream_with_retry", stream)
    return captured


def run_eval(url, judge_requests, **kwargs):
    evaluator = AgentEvaluator(
        rule_prompt="Assess the supplied value: {{clip}}", model="gpt-4o"
    )
    evaluator._evaluate(clip=url, required_keys=["clip"], **kwargs)
    assert len(judge_requests) == 1
    return next(m for m in judge_requests[0] if m["role"] == "user")


@pytest.mark.parametrize(
    "path",
    [
        "/clip.mp4",
        "/clip.MP4?download=1",
        "/video",
        "/video-image-header",
        "/video-header",
    ],
)
def test_video_reaches_agent_judge_as_url_text(media_server, judge_requests, path):
    base_url, requests, _ = media_server
    url = base_url + path

    message = run_eval(url, judge_requests)
    print(json.dumps({"case": path, "message": message, "requests": requests}))

    assert isinstance(message["content"], str), "Video must not become image blocks"
    assert f"<clip>{url}</clip>" in message["content"]
    assert "image content provided" not in message["content"]
    assert len(requests) == 1
    assert requests[0]["user_agent"] != "FutureAGI-EvalAgent/1.0"


@pytest.mark.parametrize("path", ["/clip.mp4", "/clip.MP4?download=1", "/video"])
def test_rendered_video_prompt_keeps_url(media_server, path):
    base_url, requests, _ = media_server
    url = base_url + path

    rendered = AgentEvaluator.render_eval_prompt("Assess {{clip}}", {"clip": url})
    print(json.dumps({"case": path, "rendered": rendered}))

    assert rendered == f"Assess {url}"
    assert requests == []


@pytest.mark.parametrize("path", ["/picture.png", "/picture", "/picture.mp4"])
def test_real_images_still_reach_agent_judge(media_server, judge_requests, path):
    base_url, requests, png = media_server

    message = run_eval(base_url + path, judge_requests)
    blocks = message["content"]
    assert isinstance(blocks, list)
    image_blocks = [block for block in blocks if block["type"] == "image_url"]
    assert len(image_blocks) == 1
    data_url = image_blocks[0]["image_url"]["url"]
    assert base64.b64decode(data_url.split(",", 1)[1]) == png
    assert requests[-1]["user_agent"] == "FutureAGI-EvalAgent/1.0"
    if path == "/picture.png":
        assert len(requests) == 1  # Preserve the image-extension fast path.
        assert "image content provided" in blocks[0]["text"]
    else:
        assert len(requests) == 2  # Sniff, then fetch the image content.


def test_explicit_image_urls_remain_attached(media_server, judge_requests):
    base_url, requests, png = media_server
    url = base_url + "/picture.mp4"

    # image_urls is a supported explicit override, independent of mapped inputs.
    message = run_eval("plain text", judge_requests, image_urls=[url])
    image_block = next(b for b in message["content"] if b["type"] == "image_url")
    assert base64.b64decode(image_block["image_url"]["url"].split(",", 1)[1]) == png
    assert len(requests) == 1


def test_explicit_text_type_keeps_image_url_as_text(media_server):
    base_url, requests, _ = media_server
    url = base_url + "/picture.png"

    rendered, blocks = AgentEvaluator.build_eval_input_blocks(
        "Assess {{clip}}", {"clip": url}, input_types={"clip": "text"}
    )

    assert rendered == f"Assess {url}"
    assert not any(b.get("type") == "image_url" for b in blocks)
    assert requests == []
