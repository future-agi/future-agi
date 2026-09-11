"""Unit tests for the Vapi migration wiring in ee.voice.services.vapi_service.

Covers the two integration points where this branch routes through the
OSS-side `tracer.utils.vapi_recording.VapiRecordingService`:

 - `iter_call_log_entries` delegates the gzip-JSONL fetch to
   `VapiRecordingService.iter_parsed_call_log_records`.
 - `persist_audio_to_s3` prefers `VapiRecordingService.download_artifact_sync`
   when the artifact type resolves and the api_key is set; falls back to
   `download_audio_from_url` otherwise.

Run with: pytest ee/voice/tests/test_vapi_migration_wiring.py -v
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from ee.voice.services.types.voice import PersistAudioInput
from ee.voice.services.vapi_service import _update_recording_payload
from ee.voice.services.vapi_service import VapiService


@pytest.fixture
def vapi_service():
    with patch.dict(
        "os.environ",
        {"VAPI_API_KEY": "test-key", "VAPI_API_BASE_URL": "https://api.vapi.ai"},
    ):
        return VapiService(api_key="test-key")


class TestIterCallLogEntriesDelegation:
    def test_delegates_to_vapi_recording_service_with_kwargs(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.iter_parsed_call_log_records",
            return_value=iter([{"severity": "info"}]),
        ) as mock_iter:
            result = list(
                vapi_service.iter_call_log_entries(
                    url="https://calllogs.vapi.ai/legacy",
                    verify_ssl=False,
                    timeout=30,
                    call_id="cid",
                    api_key="ck",
                )
            )
        assert result == [{"severity": "info"}]
        mock_iter.assert_called_once_with(
            call_id="cid",
            api_key="ck",
            legacy_url="https://calllogs.vapi.ai/legacy",
            timeout_seconds=30,
            verify_ssl=False,
        )

    def test_forwards_none_kwargs_when_not_supplied(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.iter_parsed_call_log_records",
            return_value=iter([]),
        ) as mock_iter:
            list(
                vapi_service.iter_call_log_entries(
                    url="https://calllogs.vapi.ai/legacy",
                )
            )
        kwargs = mock_iter.call_args.kwargs
        assert kwargs["call_id"] is None
        assert kwargs["api_key"] is None
        assert kwargs["legacy_url"] == "https://calllogs.vapi.ai/legacy"


class TestIterCallLogsThunk:
    def test_iter_call_logs_forwards_to_iter_call_log_entries(self, vapi_service):
        with patch.object(
            vapi_service,
            "iter_call_log_entries",
            return_value=iter([{"severity": "info"}]),
        ) as mock_entries:
            list(
                vapi_service.iter_call_logs(
                    url="https://calllogs.vapi.ai/x",
                    verify_ssl=True,
                    call_id="cid",
                    api_key="ck",
                )
            )
        mock_entries.assert_called_once()
        kw = mock_entries.call_args.kwargs
        assert kw["url"] == "https://calllogs.vapi.ai/x"
        assert kw["verify_ssl"] is True
        assert kw["call_id"] == "cid"
        assert kw["api_key"] == "ck"


class TestPersistAudioToS3Routing:
    def _input(self, *, audio_url, url_type="mono_combined", call_id="cid"):
        return PersistAudioInput(
            audio_url=audio_url,
            url_type=url_type,
            call_id=call_id,
        )

    def test_returns_original_when_audio_url_missing(self, vapi_service):
        out = vapi_service.persist_audio_to_s3(self._input(audio_url=""))
        assert out == ""

    def test_returns_original_when_already_s3(self, vapi_service):
        s3_url = "https://bucket.s3.amazonaws.com/x.mp3"
        out = vapi_service.persist_audio_to_s3(self._input(audio_url=s3_url))
        assert out == s3_url

    def test_returns_original_when_not_a_vapi_host(self, vapi_service):
        other_url = "https://other-provider.com/x.mp3"
        out = vapi_service.persist_audio_to_s3(self._input(audio_url=other_url))
        assert out == other_url

    def test_uses_authenticated_endpoint_when_artifact_type_and_key_present(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.download_artifact_sync",
            return_value=b"mp3-bytes",
        ) as mock_auth, patch(
            "ee.voice.services.vapi_service.download_audio_from_url"
        ) as mock_legacy, patch(
            "ee.voice.services.vapi_service.upload_audio_to_s3",
            return_value="https://bucket.s3.amazonaws.com/uploaded.mp3",
        ):
            out = vapi_service.persist_audio_to_s3(
                self._input(audio_url="https://storage.vapi.ai/x.mp3")
            )
        mock_auth.assert_called_once()
        mock_legacy.assert_not_called()
        assert out == "https://bucket.s3.amazonaws.com/uploaded.mp3"

    def test_falls_back_to_legacy_when_auth_path_raises(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.download_artifact_sync",
            side_effect=RuntimeError("boom"),
        ), patch(
            "ee.voice.services.vapi_service.download_audio_from_url",
            return_value=b"legacy-bytes",
        ) as mock_legacy, patch(
            "ee.voice.services.vapi_service.upload_audio_to_s3",
            return_value="https://bucket.s3.amazonaws.com/uploaded.mp3",
        ):
            out = vapi_service.persist_audio_to_s3(
                self._input(audio_url="https://storage.vapi.ai/x.mp3")
            )
        mock_legacy.assert_called_once_with("https://storage.vapi.ai/x.mp3")
        assert out == "https://bucket.s3.amazonaws.com/uploaded.mp3"

    def test_falls_back_to_legacy_when_url_type_unknown(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.download_artifact_sync"
        ) as mock_auth, patch(
            "ee.voice.services.vapi_service.download_audio_from_url",
            return_value=b"legacy-bytes",
        ) as mock_legacy, patch(
            "ee.voice.services.vapi_service.upload_audio_to_s3",
            return_value="https://bucket.s3.amazonaws.com/uploaded.mp3",
        ):
            out = vapi_service.persist_audio_to_s3(
                self._input(
                    audio_url="https://storage.vapi.ai/x.mp3",
                    url_type="nonsense_type",
                )
            )
        mock_auth.assert_not_called()
        mock_legacy.assert_called_once_with("https://storage.vapi.ai/x.mp3")
        assert out == "https://bucket.s3.amazonaws.com/uploaded.mp3"

    def test_returns_original_url_on_upload_failure(self, vapi_service):
        with patch(
            "tracer.utils.vapi_recording.VapiRecordingService.download_artifact_sync",
            return_value=b"mp3-bytes",
        ), patch(
            "ee.voice.services.vapi_service.upload_audio_to_s3",
            side_effect=RuntimeError("s3 down"),
        ):
            out = vapi_service.persist_audio_to_s3(
                self._input(audio_url="https://storage.vapi.ai/x.mp3")
            )
        assert out == "https://storage.vapi.ai/x.mp3"


class TestExtractAndPersistRecordings:
    @pytest.mark.asyncio
    async def test_passes_call_project_id_for_every_recording(self, vapi_service):
        project_id = "project-123"
        call = SimpleNamespace(
            provider_call_data={
                "vapi": {
                    "id": "vapi-call-123",
                    "artifact": {
                        "recording": {
                            "mono": {
                                "combinedUrl": "https://r2/combined.mp3",
                                "customerUrl": "https://r2/customer.mp3",
                                "assistantUrl": "https://r2/assistant.mp3",
                            },
                            "stereoUrl": "https://r2/stereo.mp3",
                        }
                    },
                }
            },
            test_execution=SimpleNamespace(
                agent_definition=SimpleNamespace(
                    observability_provider=SimpleNamespace(project_id=project_id)
                ),
                run_test=SimpleNamespace(organization_id="org-123"),
            ),
        )
        recordings = {
            "stereo": "https://storage.vapi.ai/stereo.mp3",
            "assistant": "https://storage.vapi.ai/assistant.mp3",
            "customer": "https://storage.vapi.ai/customer.mp3",
        }
        normalized = SimpleNamespace(
            recording_url="https://storage.vapi.ai/mono.mp3",
            call_id="vapi-call-123",
        )

        with patch(
            "simulate.models.test_execution.CallExecution.objects.select_related"
        ) as select_related, patch.object(
            vapi_service, "_extract_recording_urls", return_value=recordings
        ), patch.object(
            vapi_service, "_normalize_to_fagi_call_data", return_value=normalized
        ), patch(
            "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
            new=AsyncMock(
                side_effect=lambda *args, **kwargs: (f"s3://{args[2]}", 10)
            ),
        ) as convert, patch("ee.usage.services.emitter.emit") as emit:
            select_related.return_value.aget = AsyncMock(return_value=call)
            result = await vapi_service.extract_and_persist_recordings("call-123")

        assert result.recording_url == "s3://recording"
        assert result.stereo_recording_url == "s3://stereo_recording"
        assert result.assistant_recording_url == "s3://assistant_recording"
        assert result.customer_recording_url == "s3://customer_recording"
        assert select_related.call_args.args == (
            "test_execution__agent_definition__observability_provider",
            "test_execution__run_test",
        )
        assert [call.kwargs["project_id"] for call in convert.await_args_list] == [
            project_id,
        ] * 4
        assert emit.call_count == 4
        assert call.provider_call_data["vapi"]["recording"] == {
            "combined": "s3://recording",
            "stereo": "s3://stereo_recording",
            "assistant": "s3://assistant_recording",
            "customer": "s3://customer_recording",
        }
        assert call.provider_call_data["vapi"]["artifact"]["recording"] == {
            "mono": {
                "combinedUrl": "s3://recording",
                "customerUrl": "s3://customer_recording",
                "assistantUrl": "s3://assistant_recording",
            },
            "stereoUrl": "s3://stereo_recording",
        }
        first_event = emit.call_args_list[0].args[0]
        assert first_event.event_id == str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "futureagi:simulate-recording:call-123:recording",
            )
        )

    @pytest.mark.asyncio
    async def test_rehosts_when_agent_has_no_api_key(self, vapi_service):
        call = SimpleNamespace(
            provider_call_data={"vapi": {"id": "vapi-call-123"}},
            test_execution=SimpleNamespace(
                agent_definition=SimpleNamespace(
                    api_key=None,
                    observability_provider=None,
                ),
                run_test=SimpleNamespace(organization_id="org-123"),
            ),
        )
        normalized = SimpleNamespace(
            recording_url="https://storage.vapi.ai/mono.mp3",
            call_id="vapi-call-123",
        )

        with patch(
            "simulate.models.test_execution.CallExecution.objects.select_related"
        ) as select_related, patch.object(
            vapi_service, "_extract_recording_urls", return_value={}
        ), patch.object(
            vapi_service, "_normalize_to_fagi_call_data", return_value=normalized
        ), patch(
            "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
            new=AsyncMock(return_value=("s3://recording", 10)),
        ) as convert, patch("ee.usage.services.emitter.emit"):
            select_related.return_value.aget = AsyncMock(return_value=call)
            result = await vapi_service.extract_and_persist_recordings("call-123")

        assert result.recording_url == "s3://recording"
        assert convert.await_args.kwargs["project_id"] is None
        assert convert.await_args.kwargs["api_key"] == vapi_service.api_key

    @pytest.mark.asyncio
    async def test_rehosts_when_agent_definition_is_missing(self, vapi_service):
        call = SimpleNamespace(
            provider_call_data={"vapi": {"id": "vapi-call-123"}},
            test_execution=SimpleNamespace(
                agent_definition=None,
                run_test=SimpleNamespace(organization_id="org-123"),
            ),
        )
        normalized = SimpleNamespace(
            recording_url="https://storage.vapi.ai/mono.mp3",
            call_id="vapi-call-123",
        )

        with patch(
            "simulate.models.test_execution.CallExecution.objects.select_related"
        ) as select_related, patch.object(
            vapi_service, "_extract_recording_urls", return_value={}
        ), patch.object(
            vapi_service, "_normalize_to_fagi_call_data", return_value=normalized
        ), patch(
            "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
            new=AsyncMock(return_value=("s3://recording", 10)),
        ) as convert, patch("ee.usage.services.emitter.emit"):
            select_related.return_value.aget = AsyncMock(return_value=call)
            result = await vapi_service.extract_and_persist_recordings("call-123")

        assert result.recording_url == "s3://recording"
        assert convert.await_args.kwargs["project_id"] is None

    def test_updates_stereo_artifact_without_mono_shape(self):
        provider_data = {
            "recording": "invalid",
            "artifact": {"recording": {"stereoUrl": "https://r2/stereo.mp3"}},
        }

        _update_recording_payload(
            provider_data,
            {
                "stereo": "s3://stereo.mp3",
                "assistant": "s3://assistant.mp3",
            },
        )

        assert provider_data["recording"] == {
            "stereo": "s3://stereo.mp3",
            "assistant": "s3://assistant.mp3",
        }
        assert provider_data["artifact"]["recording"] == {
            "stereoUrl": "s3://stereo.mp3"
        }
