"""Tests for LLM final fallback logic.

Unit tests force `_get_completion_content()` to enter `_handle_final_fallback` and assert
payload scrubbing + model selection.

Includes an opt-in live test that exercises `handle_vertex_ai_fallback` end-to-end.

To Test:
docker exec -it backend pip install pytest pytest-django
docker exec -it backend pip install pytest-xdist

# NOTE: agentic_eval/pytest.ini enables xdist (-n auto). For stdout/log visibility,
# run without xdist and without capture.
docker exec -it backend bash -c "export DJANGO_SETTINGS_MODULE=tfc.settings.test && python -m pytest agentic_eval/tests/test_llm_fallback.py -v -s -n 0"
"""

import asyncio
import os

import pytest
import requests

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm.audio_utils import download_audio_url_to_base64
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.llm_payloads import (
    build_audio_content,
    build_image_content,
    build_pdf_content,
    build_text_content,
)
from agentic_eval.core.utils.model_config import ModelConfigs

# Reuse the same static URLs as agentic_eval/tests/test_deterministic_evaluator.py
TEST_IMAGE_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/dcc00d97-fde8-4721-bd3b-de75e2898f63/2ec84fac-886d-4fd8-be4f-e061ab070104"
TEST_PDF_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/documents/a16850ec-bbbc-4075-acc8-c1e553d4514d/a4bab4d8-da56-4831-95ed-e576cc767739"
TEST_AUDIO_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/audio/a16850ec-bbbc-4075-acc8-c1e553d4514d/ee571c8f-26c7-4b20-9b05-2a88c0b9df4b"
SAMPLE_IMAGE_DATA_URL = "data:image/jpeg;base64,AAAA"
SAMPLE_AUDIO_DATA_URL = "data:audio/mp3;base64,AAAA"
SAMPLE_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 1, completion_tokens: int = 1):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str = "ok"):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


def _build_sonnet_messages(modality: str) -> list[dict]:
    content = [build_text_content("Hello")]

    if modality == "image":
        content.append(
            build_image_content(
                provider="aws_bedrock_anthropic", image_input=SAMPLE_IMAGE_DATA_URL
            )
        )
    elif modality == "audio":
        content.append(
            {
                "type": "audio_content",
                "audio_content": {"url": SAMPLE_AUDIO_DATA_URL, "format": "mp3"},
            }
        )
    elif modality == "pdf":
        content.append(
            build_pdf_content(
                provider="aws_bedrock_anthropic",
                pdf_input=SAMPLE_PDF_BYTES,
                filename="x.pdf",
                mime_type="application/pdf",
            )
        )

    return [{"role": "user", "content": content}]


def _build_vertex_messages(modality: str) -> list[dict]:
    content = [build_text_content("Hello")]

    if modality == "image":
        content.append(
            build_image_content(provider="vertex_ai", image_input=SAMPLE_IMAGE_DATA_URL)
        )
    elif modality == "audio":
        content.append(
            build_audio_content(provider="vertex_ai", audio_input=SAMPLE_AUDIO_DATA_URL)
        )
    elif modality == "pdf":
        content.append(build_pdf_content(provider="vertex_ai", file_id=TEST_PDF_URL))

    return [{"role": "user", "content": content}]


@pytest.mark.unit
@pytest.mark.parametrize("format_name", ["sonnet", "vertex"])
@pytest.mark.parametrize("modality", ["text", "image", "audio", "pdf"])
def test_sync_final_fallback_vertex_then_openai_by_modality(
    monkeypatch, format_name, modality
):
    """Final fallback tries Vertex then OpenAI for each modality + input format."""

    monkeypatch.setattr(llm_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)

    llm = LLM(provider="openai", model_name="gpt-5.1", temperature=0, max_tokens=50000)
    llm.provider = "vertex_ai" if format_name == "vertex" else "anthropic"

    if format_name == "vertex":
        messages = _build_vertex_messages(modality)
    else:
        messages = _build_sonnet_messages(modality)

    monkeypatch.setattr(LLM, "_update_cost", lambda self: None)

    calls = {"n": 0}

    def _fake_completion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            assert kwargs.get("model") == ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
            content = kwargs.get("messages", [{}])[0].get("content", [])
            assert any(block.get("type") == "text" for block in content)
            if modality == "image":
                assert any(block.get("type") == "image_url" for block in content)
            if modality == "audio":
                assert any(block.get("type") == "image_url" for block in content)
            if modality == "pdf":
                assert any(block.get("type") == "file" for block in content)
            raise RuntimeError("vertex failed")

        assert kwargs.get("model") == ModelConfigs.OPENAI_GPT_5_1.model_name
        assert kwargs.get("max_tokens") == ModelConfigs.OPENAI_GPT_5_1.max_tokens
        assert kwargs.get("drop_params") is True
        assert "max_output_tokens" not in kwargs
        assert "thinking" not in kwargs

        content = kwargs.get("messages", [{}])[0].get("content", [])
        assert any(block.get("type") == "text" for block in content)
        if modality == "image":
            assert any(block.get("type") == "image_url" for block in content)
        if modality == "audio":
            assert any(block.get("type") == "input_audio" for block in content)
        if modality == "pdf":
            assert any(block.get("type") == "file" for block in content)

        return _FakeResponse("fallback ok")

    monkeypatch.setattr(llm_module.litellm, "completion", _fake_completion)

    out = llm._handle_final_fallback(messages=messages, payload={"messages": messages})
    assert out == "fallback ok"


@pytest.mark.parametrize("format_name", ["sonnet", "vertex"])
@pytest.mark.parametrize("modality", ["text", "image", "audio", "pdf"])
def test_async_final_fallback_vertex_then_openai_by_modality(
    monkeypatch, format_name, modality
):
    """Async final fallback tries Vertex then OpenAI for each modality + input format."""

    monkeypatch.setattr(llm_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)

    llm = LLM(provider="openai", model_name="gpt-5.1", temperature=0, max_tokens=50000)
    llm.provider = "vertex_ai" if format_name == "vertex" else "anthropic"

    if format_name == "vertex":
        messages = _build_vertex_messages(modality)
    else:
        messages = _build_sonnet_messages(modality)

    monkeypatch.setattr(LLM, "_update_cost", lambda self: None)

    calls = {"n": 0}

    async def _fake_acompletion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            assert kwargs.get("model") == ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
            raise RuntimeError("vertex failed")

        assert kwargs.get("model") == ModelConfigs.OPENAI_GPT_5_1.model_name
        content = kwargs.get("messages", [{}])[0].get("content", [])
        assert any(block.get("type") == "text" for block in content)
        if modality == "image":
            assert any(block.get("type") == "image_url" for block in content)
        if modality == "audio":
            assert any(block.get("type") == "input_audio" for block in content)
        if modality == "pdf":
            assert any(block.get("type") == "file" for block in content)

        return _FakeResponse("fallback ok")

    monkeypatch.setattr(llm_module.litellm, "acompletion", _fake_acompletion)

    out = asyncio.run(
        llm._handle_final_fallback_async(
            messages=messages, payload={"messages": messages}
        )
    )
    assert out == "fallback ok"


@pytest.mark.network
@pytest.mark.serial
def test_live_handle_vertex_ai_fallback_audio_to_openai():
    """Live check: Vertex-style audio passed via image_url converts and completes."""

    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    # In production, callers often pass audio as a URL (e.g. TEST_AUDIO_URL).
    # DeterministicAgent normalizes that into a data:audio/...;base64,... URI.
    base64_data, fmt = download_audio_url_to_base64(TEST_AUDIO_URL)
    audio_data_url = f"data:audio/{fmt};base64,{base64_data}"

    vertex_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Reply with the single word OK."},
                {"type": "image_url", "image_url": {"url": audio_data_url}},
            ],
        }
    ]

    llm = LLM(
        provider="openai",
        model_name="gpt-4o-audio-preview",
        temperature=0,
        max_tokens=128,
    )
    openai_messages = llm.handle_vertex_ai_fallback(vertex_messages)
    resp = llm._get_completion_content(messages=openai_messages, model=llm.model_name)
    print(f"live_response={resp}")
    assert isinstance(resp, str)
    assert resp.strip()


@pytest.mark.network
@pytest.mark.serial
def test_live_final_fallback_vertex_then_openai_multimodal():
    """Live check: final fallback with Vertex-formatted multimodal payload."""

    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    base64_data, fmt = download_audio_url_to_base64(TEST_AUDIO_URL)
    audio_data_url = f"data:audio/{fmt};base64,{base64_data}"

    messages = [
        {
            "role": "user",
            "content": [
                build_text_content("Reply with the single word OK."),
                build_image_content(provider="vertex_ai", image_input=TEST_IMAGE_URL),
                build_audio_content(provider="vertex_ai", audio_input=audio_data_url),
                build_pdf_content(provider="vertex_ai", file_id=TEST_PDF_URL),
            ],
        }
    ]

    llm = LLM(
        provider="anthropic",
        model_name="claude-3-5-sonnet-20240620",
        temperature=0,
        max_tokens=128,
    )

    resp = llm._handle_final_fallback(messages=messages, payload={"messages": messages})
    assert isinstance(resp, str)
    assert resp.strip()


@pytest.mark.network
@pytest.mark.serial
def test_live_final_fallback_vertex_then_openai_sonnet_payload():
    """Live check: final fallback with Sonnet-formatted multimodal payload."""

    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    base64_data, fmt = download_audio_url_to_base64(TEST_AUDIO_URL)
    audio_data_url = f"data:audio/{fmt};base64,{base64_data}"
    pdf_bytes = requests.get(TEST_PDF_URL, timeout=30).content

    messages = [
        {
            "role": "user",
            "content": [
                build_text_content("Reply with the single word OK."),
                build_image_content(
                    provider="aws_bedrock_anthropic", image_input=TEST_IMAGE_URL
                ),
                {
                    "type": "audio_content",
                    "audio_content": {"url": audio_data_url, "format": fmt},
                },
                build_pdf_content(
                    provider="aws_bedrock_anthropic",
                    pdf_input=pdf_bytes,
                    filename="x.pdf",
                    mime_type="application/pdf",
                ),
            ],
        }
    ]

    llm = LLM(
        provider="anthropic",
        model_name="claude-3-5-sonnet-20240620",
        temperature=0,
        max_tokens=128,
    )

    resp = llm._handle_final_fallback(messages=messages, payload={"messages": messages})
    assert isinstance(resp, str)
    assert resp.strip()
