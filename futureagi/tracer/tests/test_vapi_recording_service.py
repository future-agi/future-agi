"""Tests for tracer.utils.vapi_recording.VapiRecordingService."""

import gzip
import io
import json
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from tracer.utils.vapi_recording import (
    VapiArtifactNotReadyError,
    VapiArtifactType,
    VapiAuthError,
    VapiRateLimitError,
    VapiRecordingService,
    _URL_TYPE_TO_ARTIFACT,
)


class TestBuildArtifactUrl:
    def test_builds_expected_url(self):
        url = VapiRecordingService.build_artifact_url("abc-123", "mono-recording")
        assert url == "https://api.vapi.ai/call/abc-123/mono-recording"

    def test_uses_artifact_type_verbatim(self):
        url = VapiRecordingService.build_artifact_url("x", "call-logs")
        assert url.endswith("/call-logs")


class TestArtifactForUrlType:
    @pytest.mark.parametrize(
        "url_type,expected",
        [
            ("mono_combined", VapiArtifactType.MONO),
            ("mono_customer", VapiArtifactType.CUSTOMER),
            ("mono_assistant", VapiArtifactType.ASSISTANT),
            ("stereo", VapiArtifactType.STEREO),
            ("recording", VapiArtifactType.MONO),
            ("stereo_recording", VapiArtifactType.STEREO),
            ("customer_recording", VapiArtifactType.CUSTOMER),
            ("assistant_recording", VapiArtifactType.ASSISTANT),
        ],
    )
    def test_maps_known_types(self, url_type, expected):
        assert VapiRecordingService.artifact_for_url_type(url_type) == expected

    def test_returns_none_for_unknown(self):
        assert VapiRecordingService.artifact_for_url_type("nonsense") is None
        assert VapiRecordingService.artifact_for_url_type("") is None

    def test_map_covers_rehost_and_legacy_url_types(self):
        assert set(_URL_TYPE_TO_ARTIFACT.keys()) == {
            "mono_combined",
            "mono_customer",
            "mono_assistant",
            "stereo",
            "recording",
            "stereo_recording",
            "customer_recording",
            "assistant_recording",
        }


class TestIsAuthenticatedDownload:
    def test_all_args_and_vapi_provider_returns_true(self):
        assert VapiRecordingService.is_authenticated_download(
            "vapi", "k", "cid", "mono-recording"
        ) is True

    @pytest.mark.parametrize(
        "provider,api_key,call_id,artifact",
        [
            (None, "k", "cid", "mono-recording"),
            ("retell", "k", "cid", "mono-recording"),
            ("vapi", None, "cid", "mono-recording"),
            ("vapi", "k", None, "mono-recording"),
            ("vapi", "k", "cid", None),
            ("vapi", "", "cid", "mono-recording"),
        ],
    )
    def test_missing_or_wrong_returns_false(self, provider, api_key, call_id, artifact):
        assert VapiRecordingService.is_authenticated_download(
            provider, api_key, call_id, artifact
        ) is False


class TestIsFagiS3Url:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://fi-content-dev.s3.ap-south-1.amazonaws.com/y.mp3", True),
            ("https://fi-content.s3.amazonaws.com/x.mp3", True),
            ("https://fi-customer-data.s3.us-east-1.amazonaws.com/z.mp3", True),
            ("https://storage.vapi.ai/x.mp3", False),
            ("https://other-bucket.s3.amazonaws.com/x.mp3", False),
            ("", False),
            (None, False),
        ],
    )
    def test_matches_fagi_buckets_only(self, url, expected):
        assert VapiRecordingService.is_fagi_s3_url(url) is expected


class _FakeHttpResponse:
    def __init__(self, status_code, content=b"", headers=None, history=None, url=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.history = history or []
        self.url = url

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise httpx.HTTPStatusError("http error", request=Mock(), response=Mock(status_code=self.status_code))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def iter_bytes(self, chunk_size=None):
        yield self.content

    async def aiter_bytes(self, chunk_size=None):
        yield self.content


class _FakeAsyncClient:
    def __init__(self, response, follow_redirects=True, timeout=None):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        return self._response

    def stream(self, method, url, headers=None):
        return self._response


class _FakeSyncClient:
    def __init__(self, response, follow_redirects=True, timeout=None):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None):
        return self._response

    def stream(self, method, url, headers=None):
        return self._response


class TestDownloadArtifactAsync:
    @pytest.mark.asyncio
    async def test_returns_bytes_on_200(self):
        response = _FakeHttpResponse(200, content=b"mp3-bytes")
        with patch("tracer.utils.vapi_recording.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            out = await VapiRecordingService.download_artifact_async(
                "cid", "mono-recording", "key"
            )
        assert out == b"mp3-bytes"

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.asyncio
    async def test_raises_auth_error(self, status):
        response = _FakeHttpResponse(status)
        with patch("tracer.utils.vapi_recording.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            with pytest.raises(VapiAuthError):
                await VapiRecordingService.download_artifact_async(
                    "cid", "mono-recording", "key"
                )

    @pytest.mark.asyncio
    async def test_raises_not_ready_on_404(self):
        response = _FakeHttpResponse(404)
        with patch("tracer.utils.vapi_recording.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            with pytest.raises(VapiArtifactNotReadyError):
                await VapiRecordingService.download_artifact_async(
                    "cid", "mono-recording", "key"
                )

    @pytest.mark.asyncio
    async def test_raises_rate_limit_on_429(self):
        response = _FakeHttpResponse(429)
        with patch("tracer.utils.vapi_recording.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            with pytest.raises(VapiRateLimitError):
                await VapiRecordingService.download_artifact_async(
                    "cid", "mono-recording", "key"
                )

    @pytest.mark.asyncio
    async def test_missing_call_id_raises(self):
        with pytest.raises(ValueError):
            await VapiRecordingService.download_artifact_async("", "mono-recording", "key")

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        with pytest.raises(ValueError):
            await VapiRecordingService.download_artifact_async("cid", "mono-recording", "")

    @pytest.mark.asyncio
    async def test_stops_streaming_when_authenticated_artifact_exceeds_limit(
        self, monkeypatch
    ):
        response = _FakeHttpResponse(200)

        async def oversized_chunks(chunk_size=None):
            yield b"1234"
            yield b"5"

        response.aiter_bytes = oversized_chunks
        monkeypatch.setattr("tracer.utils.vapi_recording.MAX_AUDIO_FILE_SIZE", 4)
        with patch("tracer.utils.vapi_recording.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            with pytest.raises(ValueError, match="maximum size"):
                await VapiRecordingService.download_artifact_async(
                    "cid", "mono-recording", "key"
                )


class TestDownloadArtifactSync:
    def test_returns_bytes_on_200(self):
        response = _FakeHttpResponse(200, content=b"mp3-bytes")
        with patch("tracer.utils.vapi_recording.httpx.Client", return_value=_FakeSyncClient(response)):
            out = VapiRecordingService.download_artifact_sync("cid", "mono-recording", "key")
        assert out == b"mp3-bytes"

    @pytest.mark.parametrize("status,exc_type", [(401, VapiAuthError), (403, VapiAuthError), (404, VapiArtifactNotReadyError), (429, VapiRateLimitError)])
    def test_raises_typed_error(self, status, exc_type):
        response = _FakeHttpResponse(status)
        with patch("tracer.utils.vapi_recording.httpx.Client", return_value=_FakeSyncClient(response)):
            with pytest.raises(exc_type):
                VapiRecordingService.download_artifact_sync("cid", "mono-recording", "key")

    def test_stops_streaming_when_authenticated_artifact_exceeds_limit(
        self, monkeypatch
    ):
        response = _FakeHttpResponse(200)
        response.iter_bytes = lambda chunk_size=None: iter([b"1234", b"5"])
        monkeypatch.setattr("tracer.utils.vapi_recording.MAX_AUDIO_FILE_SIZE", 4)
        with patch("tracer.utils.vapi_recording.httpx.Client", return_value=_FakeSyncClient(response)):
            with pytest.raises(ValueError, match="maximum size"):
                VapiRecordingService.download_artifact_sync("cid", "mono-recording", "key")


class TestParseCallLogContent:
    def _gzip(self, lines):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for line in lines:
                gz.write((line + "\n").encode("utf-8"))
        return buf.getvalue()

    def test_parses_gzipped_jsonl(self):
        content = self._gzip([
            json.dumps({"severity": "info", "body": "hi"}),
            json.dumps({"severity": "warn", "body": "yo"}),
        ])
        entries = VapiRecordingService.parse_call_log_content(content)
        assert len(entries) == 2

    def test_wraps_invalid_json_lines(self):
        content = self._gzip(["not-json", json.dumps({"severity": "info"})])
        entries = VapiRecordingService.parse_call_log_content(content)
        assert len(entries) == 2
        assert all("id" in e and "payload" in e for e in entries)
        assert entries[0]["payload"] == {"raw_line": "not-json"}

    def test_skips_empty_lines(self):
        content = self._gzip(["", json.dumps({"severity": "info"}), ""])
        entries = VapiRecordingService.parse_call_log_content(content)
        assert len(entries) == 1


class TestFetchCallLogsContentTiers:
    def _gzip(self, payload=None):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write((payload or json.dumps({"severity": "info"})).encode("utf-8"))
        return buf.getvalue()

    def test_tier1_success_returns_bytes_without_touching_tier2(self):
        gz_bytes = self._gzip()
        response = _FakeHttpResponse(200, content=gz_bytes, headers={"content-type": "application/gzip"})
        with patch("tracer.utils.vapi_recording.requests.get", return_value=response) as mock_get:
            out = VapiRecordingService._fetch_call_logs_content(
                call_id="cid",
                api_key="key",
                legacy_url="https://calllogs.vapi.ai/legacy",
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out == gz_bytes
        assert mock_get.call_count == 1
        assert "api.vapi.ai/call/cid/call-logs" in mock_get.call_args.args[0]

    def test_tier1_auth_failure_falls_back_to_tier2(self):
        gz_bytes = self._gzip()
        tier1 = _FakeHttpResponse(401)
        tier2 = _FakeHttpResponse(200, content=gz_bytes)
        with patch("tracer.utils.vapi_recording.requests.get", side_effect=[tier1, tier2]) as mock_get:
            out = VapiRecordingService._fetch_call_logs_content(
                call_id="cid",
                api_key="key",
                legacy_url="https://calllogs.vapi.ai/legacy",
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out == gz_bytes
        assert mock_get.call_count == 2

    def test_tier1_exception_falls_back_to_tier2(self):
        gz_bytes = self._gzip()
        tier2 = _FakeHttpResponse(200, content=gz_bytes)
        with patch(
            "tracer.utils.vapi_recording.requests.get",
            side_effect=[RuntimeError("boom"), tier2],
        ) as mock_get:
            out = VapiRecordingService._fetch_call_logs_content(
                call_id="cid",
                api_key="key",
                legacy_url="https://calllogs.vapi.ai/legacy",
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out == gz_bytes
        assert mock_get.call_count == 2

    def test_no_tier2_when_legacy_url_missing(self):
        tier1 = _FakeHttpResponse(401)
        with patch("tracer.utils.vapi_recording.requests.get", return_value=tier1) as mock_get:
            out = VapiRecordingService._fetch_call_logs_content(
                call_id="cid",
                api_key="key",
                legacy_url=None,
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out is None
        assert mock_get.call_count == 1

    def test_skips_tier1_when_missing_args_and_uses_tier2(self):
        gz_bytes = self._gzip()
        tier2 = _FakeHttpResponse(200, content=gz_bytes)
        with patch("tracer.utils.vapi_recording.requests.get", return_value=tier2) as mock_get:
            out = VapiRecordingService._fetch_call_logs_content(
                call_id=None,
                api_key=None,
                legacy_url="https://calllogs.vapi.ai/legacy",
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out == gz_bytes
        assert mock_get.call_count == 1

    def test_returns_none_when_both_tiers_fail(self):
        with patch(
            "tracer.utils.vapi_recording.requests.get",
            side_effect=[RuntimeError("t1"), RuntimeError("t2")],
        ):
            out = VapiRecordingService._fetch_call_logs_content(
                call_id="cid",
                api_key="key",
                legacy_url="https://calllogs.vapi.ai/legacy",
                timeout_seconds=5.0,
                verify_ssl=True,
            )
        assert out is None


class TestFetchAndParseCallLogs:
    def _gzip(self, entries):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for e in entries:
                gz.write((json.dumps(e) + "\n").encode("utf-8"))
        return buf.getvalue()

    def test_returns_parsed_entries_on_tier1_hit(self):
        gz = self._gzip([{"severity": "info", "body": "hi"}])
        response = _FakeHttpResponse(200, content=gz)
        with patch("tracer.utils.vapi_recording.requests.get", return_value=response):
            entries = VapiRecordingService.fetch_and_parse_call_logs(
                call_id="cid", api_key="key", legacy_url=None
            )
        assert len(entries) == 1

    def test_returns_none_when_both_tiers_absent(self):
        entries = VapiRecordingService.fetch_and_parse_call_logs(
            call_id=None, api_key=None, legacy_url=None
        )
        assert entries is None

    def test_info_logs_do_not_include_signed_legacy_urls(self):
        signed_url = "https://provider.example/logs?token=secret-token"
        response = _FakeHttpResponse(200, content=self._gzip([]))
        events = []

        def capture(event, **kwargs):
            events.append((event, kwargs))

        with patch("tracer.utils.vapi_recording.logger.info", side_effect=capture), patch(
            "tracer.utils.vapi_recording.requests.get", return_value=response
        ):
            VapiRecordingService.fetch_and_parse_call_logs(
                call_id=None, api_key=None, legacy_url=signed_url
            )

        assert signed_url not in str(events)


class TestMirrorS3UrlToConsumerFields:
    """DB-mirror side-effects are patched away; behaviour tested is the returned attrs dict."""

    def _patch_db_mirrors(self):
        return patch.multiple(
            VapiRecordingService,
            _mirror_to_call_execution=Mock(),
            _mirror_to_call_execution_snapshot=Mock(),
        )

    def test_writes_flat_aliases_when_missing(self):
        with self._patch_db_mirrors():
            attrs = {}
            out = VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=attrs,
                call_id="cid",
                s3_url_by_url_type={
                    "mono_combined": "https://x.s3.amazonaws.com/mono.mp3",
                    "stereo": "https://x.s3.amazonaws.com/stereo.mp3",
                },
            )
        assert out["recording_url"] == "https://x.s3.amazonaws.com/mono.mp3"
        assert out["stereo_recording_url"] == "https://x.s3.amazonaws.com/stereo.mp3"

    def test_does_not_clobber_existing_s3(self):
        with self._patch_db_mirrors():
            attrs = {"recording_url": "https://fi-customer-data.s3.amazonaws.com/prev.mp3"}
            out = VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=attrs,
                call_id="cid",
                s3_url_by_url_type={"mono_combined": "https://new.s3.amazonaws.com/new.mp3"},
            )
        assert out["recording_url"] == "https://fi-customer-data.s3.amazonaws.com/prev.mp3"

    def test_overwrites_raw_vapi_alias(self):
        with self._patch_db_mirrors():
            attrs = {"recording_url": "https://storage.vapi.ai/raw.mp3"}
            out = VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=attrs,
                call_id="cid",
                s3_url_by_url_type={"mono_combined": "https://new.s3.amazonaws.com/new.mp3"},
            )
        assert out["recording_url"] == "https://new.s3.amazonaws.com/new.mp3"

    def test_returns_fresh_dict(self):
        with self._patch_db_mirrors():
            attrs = {"other": "keep"}
            out = VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=attrs,
                call_id="cid",
                s3_url_by_url_type={"mono_combined": "https://x.s3.amazonaws.com/m.mp3"},
            )
        assert out is not attrs
        assert attrs == {"other": "keep"}

    def test_none_attrs_becomes_empty_dict(self):
        with self._patch_db_mirrors():
            out = VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=None,
                call_id="cid",
                s3_url_by_url_type={},
            )
        assert isinstance(out, dict)

    def test_invokes_db_mirror_methods_when_call_id_present(self):
        with patch.object(VapiRecordingService, "_mirror_to_call_execution") as ce, \
             patch.object(VapiRecordingService, "_mirror_to_call_execution_snapshot") as snap:
            VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs={},
                call_id="cid",
                s3_url_by_url_type={"mono_combined": "https://x.s3.amazonaws.com/m.mp3"},
            )
        ce.assert_called_once()
        snap.assert_called_once()

    def test_does_not_invoke_db_mirror_when_no_urls(self):
        with patch.object(VapiRecordingService, "_mirror_to_call_execution") as ce, \
             patch.object(VapiRecordingService, "_mirror_to_call_execution_snapshot") as snap:
            VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs={},
                call_id="cid",
                s3_url_by_url_type={},
            )
        ce.assert_not_called()
        snap.assert_not_called()

    def test_updates_every_snapshot_with_the_same_provider_call_id(self):
        first = Mock(recording_url=None, stereo_recording_url=None)
        second = Mock(recording_url=None, stereo_recording_url=None)
        queryset = Mock()
        queryset.only.return_value = [first, second]
        snapshot_model = SimpleNamespace(objects=Mock())
        snapshot_model.objects.filter.return_value = queryset

        with patch.dict(
            sys.modules,
            {"simulate.models.test_execution": SimpleNamespace(CallExecutionSnapshot=snapshot_model)},
        ):
            VapiRecordingService._mirror_to_call_execution_snapshot(
                call_id="cid",
                mono_s3="https://fi-customer-data.s3.amazonaws.com/mono.mp3",
                stereo_s3=None,
            )

        snapshot_model.objects.filter.assert_called_once_with(
            service_provider_call_id="cid"
        )
        assert first.recording_url.endswith("mono.mp3")
        assert second.recording_url.endswith("mono.mp3")
        first.save.assert_called_once_with(update_fields=["recording_url"])
        second.save.assert_called_once_with(update_fields=["recording_url"])


class TestNormaliseCallLogEntry:
    """Only verify shape stability — the exact fields depend on Vapi's payload."""

    def test_returns_dict(self):
        out = VapiRecordingService._normalise_call_log_entry({"severity": "info", "body": "hi"})
        assert isinstance(out, dict)

    def test_handles_raw_line_wrapper(self):
        out = VapiRecordingService._normalise_call_log_entry({"raw_line": "not-json"})
        assert isinstance(out, dict)


class TestApiKeyFromAgentDefinition:
    """Table-driven test of the versioned-snapshot vs plaintext-column preference."""

    def _agent(self, snapshot=None, plaintext=None, has_latest_version=True):
        latest_version = None
        if has_latest_version:
            latest_version = MagicMock(configuration_snapshot=snapshot or {})
        agent = MagicMock(latest_version=latest_version, api_key=plaintext)
        return agent

    def test_prefers_versioned_snapshot(self):
        agent = self._agent(snapshot={"api_key": "snap"}, plaintext="plain")
        with patch("simulate.services.agent_definition.resolve_api_key_for_version", return_value="snap"):
            assert VapiRecordingService._api_key_from_agent_definition(agent) == "snap"

    def test_falls_back_to_plaintext_when_snapshot_missing(self):
        agent = self._agent(snapshot={}, plaintext="plain")
        with patch("simulate.services.agent_definition.resolve_api_key_for_version", return_value="plain"):
            assert VapiRecordingService._api_key_from_agent_definition(agent) == "plain"

    def test_falls_back_when_no_latest_version(self):
        agent = self._agent(has_latest_version=False, plaintext="plain")
        with patch("simulate.services.agent_definition.resolve_api_key_for_version", return_value="plain"):
            assert VapiRecordingService._api_key_from_agent_definition(agent) == "plain"

    def test_returns_none_when_all_absent(self):
        agent = self._agent(snapshot={}, plaintext=None)
        with patch("simulate.services.agent_definition.resolve_api_key_for_version", return_value=None):
            assert VapiRecordingService._api_key_from_agent_definition(agent) is None


class TestGetApiKeyForProject:
    """The DB-touching resolution wrappers — mocked at the ORM boundary."""

    def test_returns_none_for_none_project(self):
        assert VapiRecordingService.get_api_key_for_project(None) is None

    def test_returns_provider_row_key_when_present(self):
        agent = MagicMock(latest_version=MagicMock(configuration_snapshot={"api_key": "abc"}), api_key="ignored")
        provider = MagicMock(agent_definition=agent)
        with patch.object(
            VapiRecordingService, "_get_vapi_provider_for_project", return_value=provider
        ), patch(
            "simulate.services.agent_definition.resolve_api_key_for_version", return_value="abc"
        ):
            assert VapiRecordingService.get_api_key_for_project("proj-id") == "abc"

    def test_falls_through_to_any_agent_on_project(self):
        with patch.object(
            VapiRecordingService, "_get_vapi_provider_for_project", return_value=None
        ), patch.object(
            VapiRecordingService, "_api_key_from_any_agent_on_project", return_value="xyz"
        ):
            assert VapiRecordingService.get_api_key_for_project("proj-id") == "xyz"

    def test_returns_none_when_lookup_raises(self):
        with patch.object(
            VapiRecordingService,
            "_get_vapi_provider_for_project",
            side_effect=RuntimeError("boom"),
        ):
            assert VapiRecordingService.get_api_key_for_project("proj-id") is None


class TestGetApiKeyForAgentDefinition:
    def test_returns_none_for_none_id(self):
        assert VapiRecordingService.get_api_key_for_agent_definition(None) is None

    def test_returns_none_for_invalid_uuid_string(self):
        assert VapiRecordingService.get_api_key_for_agent_definition("not-a-uuid") is None
