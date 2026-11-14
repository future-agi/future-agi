"""End-to-end tests for VAPI transcriber selection via create_assistant."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ee.voice.services.vapi_service import VapiService


@pytest.fixture
def vapi_service():
    with patch.dict(
        "os.environ",
        {"VAPI_API_KEY": "test-key", "VAPI_API_BASE_URL": "https://api.vapi.ai"},
    ):
        return VapiService(api_key="test-key")


def _captured_assistant_payload(vapi_service, *, language: str) -> dict:
    fake_response = MagicMock(status_code=201)
    fake_response.json.return_value = {"id": "asst_test_id"}

    with patch.object(
        vapi_service,
        "_make_api_request_with_retry",
        return_value=fake_response,
    ) as send:
        vapi_service.create_assistant(
            name="test",
            system_prompt="test",
            voice_settings={"language": language, "initial_message": "hi"},
            language=language,
            assistant_type="voice",
        )

    assert send.called
    return send.call_args.kwargs["json"]


def test_arabic_persona_produces_azure_transcriber(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="arabic")
    assert payload["transcriber"] == {
        "provider": "azure",
        "language": "ar-SA",
    }


def test_arabic_region_code_still_routes_to_azure(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="ar-SA")
    assert payload["transcriber"]["provider"] == "azure"
    assert payload["transcriber"]["language"] == "ar-SA"


def test_spanish_persona_stays_on_deepgram_multi(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="spanish")
    assert payload["transcriber"] == {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "multi",
    }

    payload = _captured_assistant_payload(vapi_service, language="es-419")
    assert payload["transcriber"]["language"] == "multi"


def test_english_persona_uses_deepgram_nova3_with_normalised_code(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="english")
    assert payload["transcriber"] == {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "en-US",
    }


def test_other_supported_languages_route_to_deepgram(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="hindi")
    assert payload["transcriber"] == {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "hi",
    }
    payload = _captured_assistant_payload(vapi_service, language="french")
    assert payload["transcriber"]["language"] == "fr"


def test_unknown_persona_value_falls_back_to_english(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="klingon")
    assert payload["transcriber"] == {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "en-US",
    }


def test_arabic_does_not_change_voice_provider(vapi_service):
    payload = _captured_assistant_payload(vapi_service, language="arabic")
    assert payload["voice"]["provider"] == "11labs"
    assert payload["voice"]["model"] == "eleven_multilingual_v2"
