"""Provider-side prompt apply — "directly apply the fix" for agent-definition runs (TH-5642)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.services.provider_prompt_apply import (
    ElevenLabsPromptWriter,
    PromptApplyError,
    PromptApplyUnsupported,
    RetellPromptWriter,
    VapiPromptWriter,
    apply_prompt_to_provider_agent,
)

pytestmark = pytest.mark.unit


def _resp(json_data, status=200):
    return SimpleNamespace(
        status_code=status, json=lambda: json_data, text=str(json_data)
    )


class TestElevenLabsWriter:
    def test_apply_reads_writes_and_verifies(self):
        state = {"prompt": "old prompt"}

        def fake_get(url, **kw):
            return _resp(
                {
                    "conversation_config": {
                        "agent": {"prompt": {"prompt": state["prompt"]}}
                    }
                }
            )

        def fake_patch(url, **kw):
            state["prompt"] = kw["json"]["conversation_config"]["agent"]["prompt"][
                "prompt"
            ]
            return _resp({})

        with (
            patch("simulate.services.provider_prompt_apply.requests.get", fake_get),
            patch("simulate.services.provider_prompt_apply.requests.patch", fake_patch),
        ):
            out = ElevenLabsPromptWriter(api_key="xi-key").apply(
                "agent_1", "new prompt"
            )

        assert out["applied"] is True
        assert out["previous_prompt"] == "old prompt"
        assert state["prompt"] == "new prompt"

    def test_readback_mismatch_raises(self):
        with (
            patch(
                "simulate.services.provider_prompt_apply.requests.get",
                lambda url, **kw: _resp(
                    {"conversation_config": {"agent": {"prompt": {"prompt": "stale"}}}}
                ),
            ),
            patch(
                "simulate.services.provider_prompt_apply.requests.patch",
                lambda url, **kw: _resp({}),
            ),
        ):
            with pytest.raises(PromptApplyError, match="read-back mismatch"):
                ElevenLabsPromptWriter(api_key="xi-key").apply("agent_1", "new")


class TestVapiWriter:
    def test_apply_replaces_system_message_in_model(self):
        state = {
            "model": {
                "provider": "openai",
                "model": "gpt-4o",
                "messages": [{"role": "system", "content": "old"}],
            }
        }

        def fake_get(url, **kw):
            return _resp({"model": state["model"]})

        def fake_patch(url, **kw):
            state["model"] = kw["json"]["model"]
            return _resp({})

        with (
            patch("simulate.services.provider_prompt_apply.requests.get", fake_get),
            patch("simulate.services.provider_prompt_apply.requests.patch", fake_patch),
        ):
            out = VapiPromptWriter(api_key="k").apply("asst_1", "new sys")

        assert out["previous_prompt"] == "old"
        assert state["model"]["messages"] == [{"role": "system", "content": "new sys"}]
        # The rest of the model object must be preserved (Vapi PATCH replaces model).
        assert state["model"]["provider"] == "openai"

    def test_inserts_system_message_when_absent(self):
        state = {"model": {"provider": "openai", "model": "gpt-4o", "messages": []}}

        def fake_get(url, **kw):
            return _resp({"model": state["model"]})

        def fake_patch(url, **kw):
            state["model"] = kw["json"]["model"]
            return _resp({})

        with (
            patch("simulate.services.provider_prompt_apply.requests.get", fake_get),
            patch("simulate.services.provider_prompt_apply.requests.patch", fake_patch),
        ):
            VapiPromptWriter(api_key="k").apply("asst_1", "sys")

        assert state["model"]["messages"][0] == {"role": "system", "content": "sys"}


class TestRetellWriter:
    def test_apply_goes_through_llm_id(self):
        state = {"general_prompt": "old"}

        def fake_get(url, **kw):
            if "/get-agent/" in url:
                return _resp(
                    {"response_engine": {"type": "retell-llm", "llm_id": "llm_9"}}
                )
            assert url.endswith("/get-retell-llm/llm_9")
            return _resp({"general_prompt": state["general_prompt"]})

        def fake_patch(url, **kw):
            assert url.endswith("/update-retell-llm/llm_9")
            state["general_prompt"] = kw["json"]["general_prompt"]
            return _resp({})

        with (
            patch("simulate.services.provider_prompt_apply.requests.get", fake_get),
            patch("simulate.services.provider_prompt_apply.requests.patch", fake_patch),
        ):
            out = RetellPromptWriter(api_key="k").apply("agent_1", "new")

        assert out["previous_prompt"] == "old"
        assert state["general_prompt"] == "new"

    def test_non_retell_llm_engine_unsupported(self):
        with patch(
            "simulate.services.provider_prompt_apply.requests.get",
            lambda url, **kw: _resp({"response_engine": {"type": "conversation-flow"}}),
        ):
            with pytest.raises(PromptApplyUnsupported, match="retell-llm"):
                RetellPromptWriter(api_key="k").get_prompt("agent_1")


class TestDispatch:
    def _agent_def(self, provider, assistant_id="a1", api_key="key"):
        return SimpleNamespace(
            id="ad-1",
            provider=provider,
            assistant_id=assistant_id,
            api_key=api_key,
            credentials=None,
        )

    @pytest.mark.parametrize(
        "provider",
        ["bland", "twilio", "livekit", "pipecat", "deepgram", "agora"],
    )
    def test_unsupported_providers_raise_with_reason(self, provider):
        with pytest.raises(PromptApplyUnsupported):
            apply_prompt_to_provider_agent(self._agent_def(provider), "p")

    def test_missing_assistant_id_unsupported(self):
        with pytest.raises(PromptApplyUnsupported, match="assistant_id"):
            apply_prompt_to_provider_agent(
                self._agent_def("vapi", assistant_id=""), "p"
            )

    def test_missing_credentials_is_error(self):
        with pytest.raises(PromptApplyError, match="credentials"):
            apply_prompt_to_provider_agent(self._agent_def("vapi", api_key=""), "p")

    def test_elevenlabs_alias_dispatches(self):
        with patch.object(
            ElevenLabsPromptWriter, "apply", return_value={"applied": True}
        ) as mock_apply:
            out = apply_prompt_to_provider_agent(self._agent_def("elevenlabs"), "p")
        assert out == {"applied": True}
        mock_apply.assert_called_once_with("a1", "p")
