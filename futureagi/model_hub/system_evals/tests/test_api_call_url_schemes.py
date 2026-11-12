"""The built-in api_call evaluator calls only http and https URLs, and follows a redirect only to
another http or https URL; any other URL is answered with a score of 0 and nothing is opened."""

from __future__ import annotations

import builtins
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

YAML_PATH = Path(__file__).resolve().parent.parent / "function" / "api_call.yaml"


def load_evaluate():
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    ns: dict = {}
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), ns)
    return ns["evaluate"]


class _Response(io.BytesIO):
    status = 200


@pytest.fixture
def opened(monkeypatch):
    """Every request the evaluator opens, through urlopen or an opener; each answers 200 with a score."""
    calls = []

    def open_(request, *args, **kwargs):
        calls.append(
            request.full_url
            if isinstance(request, urllib.request.Request)
            else str(request)
        )
        return _Response(b'{"score": 1.0, "reason": "ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", open_)
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, request, *a, **k: open_(request),
    )
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hostname",
        "FILE:///etc/hostname",
        "ftp://example.com/x",
        "data:text/plain,hi",
        "gopher://example.com/",
    ],
)
def test_other_schemes_are_answered_without_opening_anything(opened, url):
    result = load_evaluate()(None, "out", None, None, url=url)
    assert result["score"] == 0.0
    assert result["reason"] == "Only http and https URLs are supported."
    assert opened == []


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/check",
        "https://example.com/check",
        "HTTPS://example.com/check",
    ],
)
def test_http_and_https_are_called(opened, url):
    result = load_evaluate()(None, "out", None, None, url=url)
    assert opened == [url]
    assert result["score"] == 1.0


def test_a_redirect_to_another_scheme_is_not_followed():
    evaluate_ns: dict = {}
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), evaluate_ns)
    handler = evaluate_ns.get("_HttpOnlyRedirects")
    assert handler is not None, (
        "the evaluator must install a redirect handler limited to http and https"
    )
    request = urllib.request.Request("https://example.com/start")
    with pytest.raises(urllib.error.HTTPError):
        handler().redirect_request(
            request, None, 302, "Found", {}, "file:///etc/hostname"
        )
    followed = handler().redirect_request(
        request, None, 302, "Found", {}, "https://example.com/next"
    )
    assert followed.full_url == "https://example.com/next"
