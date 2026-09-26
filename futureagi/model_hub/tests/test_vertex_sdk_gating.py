"""Vertex AI models that need the optional `gcp` extra (the vertexai SDK).

The default backend image ships without google-cloud-aiplatform. litellm
imports `vertexai` for every Vertex route except Gemini, so the catalog hides
those models and custom-model creation refuses them with a clear message.
"""

from __future__ import annotations

import importlib.util

import pytest

from agentic_eval.core_evals.run_prompt import available_models as catalog

EXTRA_NAMES = [
    "vertex_ai/gemma/gemma-3-12b-it",
    "vertex_ai/openai/gpt-oss-120b",
    "vertex_ai/openai/1234567890",
    "vertex_ai/text-bison@002",
    "vertex_ai/chat-bison",
    "vertex_ai/code-bison",
    "vertex_ai/1234567890",
    "vertex_ai/deepseek-ai/deepseek-r1-0528-maas",
    "vertex_ai/qwen/qwen3-coder-480b-a35b-instruct-maas",
    "vertex_ai/gemini-2.5-pro",
]


def _vertex_chat_models():
    names = {
        model["model_name"]
        for model in catalog.OSS_AVAILABLE_MODELS
        if (
            model.get("providers") == "vertex_ai"
            or str(model["model_name"]).startswith("vertex_ai/")
        )
        and model.get("mode") in (None, "chat", "completion")
    }
    return sorted(names | set(EXTRA_NAMES))


@pytest.mark.parametrize("model_name", _vertex_chat_models())
def test_requires_sdk_matches_litellm_routing(model_name):
    """Our mirror agrees with the pinned litellm's own route choice."""
    from litellm.llms.vertex_ai.common_utils import (
        VertexAIModelRoute,
        get_vertex_ai_model_route,
    )

    route = get_vertex_ai_model_route(model_name.removeprefix("vertex_ai/"))
    needs_sdk = route in {
        VertexAIModelRoute.PARTNER_MODELS,
        VertexAIModelRoute.GEMMA,
        VertexAIModelRoute.MODEL_GARDEN,
        VertexAIModelRoute.NON_GEMINI,
    }

    assert catalog.vertex_model_requires_sdk(model_name, "vertex_ai") is needs_sdk


def test_catalog_without_sdk_keeps_gemini_imagen_and_embeddings():
    kept = {
        m["model_name"]
        for m in catalog.without_vertex_sdk_models(catalog.OSS_AVAILABLE_MODELS)
    }
    dropped = {m["model_name"] for m in catalog.OSS_AVAILABLE_MODELS} - kept

    assert dropped, "the partner models must be hidden without the SDK"
    assert all(name.startswith("vertex_ai/") for name in dropped)
    assert "vertex_ai/claude-3-5-sonnet-v2@20241022" in dropped
    assert "vertex_ai/meta/llama3-405b-instruct-maas" in dropped
    for name in (
        "vertex_ai/gemini-2.0-flash-001",
        "gemini-1.5-pro",
        "vertex_ai/imagen-3.0-generate-001",
        "vertex_ai/gemini-2.5-flash-preview-tts",
        "text-multilingual-embedding-002",
        "gpt-4o",
    ):
        if any(m["model_name"] == name for m in catalog.OSS_AVAILABLE_MODELS):
            assert name in kept, name
    # Other providers are never touched.
    assert not any(not name.startswith("vertex_ai/") for name in dropped)


def test_sdk_detection(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert catalog.vertex_ai_sdk_available() is False

    def broken(name):
        raise ValueError("vertexai.__spec__ is None")

    monkeypatch.setattr(importlib.util, "find_spec", broken)
    assert catalog.vertex_ai_sdk_available() is False


def test_custom_model_refuses_sdk_only_vertex_models(monkeypatch):
    from model_hub.views import custom_model

    monkeypatch.setattr(custom_model, "vertex_ai_sdk_available", lambda: False)

    assert custom_model._vertex_sdk_missing("vertex_ai", "vertex_ai/claude-3-7@x")
    assert custom_model._vertex_sdk_missing(" Vertex_AI ", "vertex_ai/meta/llama")
    assert not custom_model._vertex_sdk_missing("vertex_ai", "vertex_ai/gemini-2.5-pro")
    assert not custom_model._vertex_sdk_missing("custom", "vertex_ai/claude-x")

    monkeypatch.setattr(custom_model, "vertex_ai_sdk_available", lambda: True)
    assert not custom_model._vertex_sdk_missing("vertex_ai", "vertex_ai/claude-x")
