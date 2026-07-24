from types import SimpleNamespace

import pytest
import requests

from tfc.temporal.backfill.minimal_backfill import (
    VapiMinimalBackfillWorkflow,
    _artifacts,
    _canonical_call_id,
    _ch_call_id,
    _ch_extra_predicate,
    _ch_map_predicate,
    _ch_predicate,
    _ch_scan_settings,
    _ch_shape,
    _download,
    _extra_artifacts,
    _is_retryable,
    _is_vapi_url,
    _json_replace,
    _mapping_table,
    _row_artifacts,
    _stored,
    _update_pg,
    _validate_storage_stat,
    _valid_direct_audio,
    _verify_proof_gate,
    get_activities,
    get_workflows,
)


def payload(url="https://storage.vapi.ai/call.wav"):
    return {
        "other": {"keep": True},
        "vapi": {
            "id": "call-1",
            "artifact": {
                "recording": {
                    "mono": {
                        "combinedUrl": url,
                        "customerUrl": url + "?customer",
                        "assistantUrl": url + "?assistant",
                    },
                    "stereoUrl": url + "?stereo",
                }
            },
            "recording": {"combined": url},
        },
    }


def test_extracts_all_four_artifacts_and_call_id():
    call_id, found = _artifacts(payload())
    assert call_id == "call-1"
    assert set(found) == {"mono_combined", "mono_customer", "mono_assistant", "stereo"}


def test_row_artifacts_includes_top_level_only_urls():
    row = SimpleNamespace(
        provider_call_data=None,
        recording_url="https://storage.vapi.ai/only-top.wav",
        stereo_recording_url=None,
        service_provider_call_id="full-call-id-from-ce",
        __class__=SimpleNamespace(__name__="CallExecution"),
    )

    # Patch class name check via real type-like object
    class CallExecution:
        pass

    row = CallExecution()
    row.provider_call_data = None
    row.recording_url = "https://storage.vapi.ai/only-top.wav"
    row.stereo_recording_url = None
    row.service_provider_call_id = "full-call-id-from-ce"
    call_id, found = _row_artifacts(row)
    assert call_id == "full-call-id-from-ce"
    assert found["mono_combined"].endswith("only-top.wav")


def test_snapshot_never_uses_truncated_service_provider_call_id():
    class CallExecution:
        service_provider_call_id = "full-parent-call-id-abcdefghijklmnopqrstuvwxyz"

    class CallExecutionSnapshot:
        pass

    snap = CallExecutionSnapshot()
    snap.provider_call_data = {"vapi": {}}  # no nested id
    snap.service_provider_call_id = "truncated-id"
    snap.call_execution = CallExecution()
    snap.recording_url = None
    snap.stereo_recording_url = None
    call_id = _canonical_call_id(snap, None)
    assert call_id == "full-parent-call-id-abcdefghijklmnopqrstuvwxyz"
    assert call_id != "truncated-id"


def test_update_pg_replaces_all_exact_paths_and_preserves_unrelated_data():
    old, new = (
        "https://storage.vapi.ai/call.wav",
        "https://fi-content.s3.amazonaws.com/call.mp3",
    )
    row = SimpleNamespace(
        provider_call_data=payload(old), recording_url=old, stereo_recording_url=None
    )
    saved = []
    row.save = lambda update_fields: saved.extend(update_fields)
    assert _update_pg(row, "mono_combined", old, new)
    assert row.recording_url == new
    assert (
        row.provider_call_data["vapi"]["artifact"]["recording"]["mono"]["combinedUrl"]
        == new
    )
    assert row.provider_call_data["vapi"]["recording"]["combined"] == new
    assert row.provider_call_data["other"] == {"keep": True}
    assert set(saved) == {"recording_url", "provider_call_data"}


def test_json_replace_only_changes_exact_urls():
    old, new = "https://storage.vapi.ai/a.wav", "https://s3/a.wav"
    value = _json_replace(
        '{"nested":["https://storage.vapi.ai/a.wav","keep"]}', {old: new}
    )
    assert new in value and "keep" in value



def test_json_replace_errors_on_invalid_json_when_rewrite_required():
    import pytest
    from tfc.temporal.backfill.minimal_backfill import _json_replace

    with pytest.raises(ValueError, match="not valid JSON"):
        _json_replace('not-json https://storage.vapi.ai/x.wav', {
            "https://storage.vapi.ai/x.wav": "https://s3.example/x.wav"
        })

def test_url_classification_and_registration_factories():
    assert _is_vapi_url("https://storage.vapi.ai/a.wav")
    assert not _is_vapi_url("https://example.com/a.wav")
    assert len(get_workflows()) == 1
    assert len(get_activities()) == 4


def test_ch_call_id_prefers_vapi_normalizer_attribute():
    assert _ch_call_id({"vapi.call_id": "call-from-vapi"}) == "call-from-vapi"
    assert _ch_call_id({}, "call-from-extra") == "call-from-extra"


def test_extracts_artifacts_from_attributes_extra():
    call_id, found = _extra_artifacts(
        '{"raw_log":{"id":"call-2","artifact":{"recording":{"mono":{"customerUrl":"https://storage.vapi.ai/customer.wav"}}}}}'
    )
    assert call_id == "call-2"
    assert found == {"mono_customer": "https://storage.vapi.ai/customer.wav"}


def test_destination_head_validation_rejects_empty_or_non_audio():
    with pytest.raises(RuntimeError):
        _validate_storage_stat(SimpleNamespace(size=0, content_type="audio/mpeg"))
    with pytest.raises(RuntimeError):
        _validate_storage_stat(SimpleNamespace(size=12, content_type="text/plain"))
    _validate_storage_stat(SimpleNamespace(size=12, content_type="audio/wav"))


def test_retry_classification_typed_vapi_errors():
    from tracer.utils.vapi_recording import (
        VapiArtifactNotReadyError,
        VapiAuthError,
        VapiRateLimitError,
    )

    assert _is_retryable(requests.Timeout())
    assert _is_retryable(VapiRateLimitError("slow"))
    assert _is_retryable(VapiArtifactNotReadyError("pending"))
    assert not _is_retryable(VapiAuthError("nope"))
    assert not _is_retryable(ValueError("bad source data"))
    predicate = _ch_predicate("attrs_string", "attributes_extra")
    assert "storage.vapi.ai" in predicate and "api.vapi.ai" in predicate
    assert "position(attrs_string['recording_url'], 'vapi')" not in predicate
    map_only = _ch_map_predicate("attrs_string")
    assert "attributes_extra" not in map_only
    assert "storage.vapi.ai" in map_only
    extra_only = _ch_extra_predicate("attributes_extra")
    assert "attrs_string" not in extra_only
    assert "position(attributes_extra, 'storage.vapi.ai')" in extra_only
    # Full predicate is map OR extra, never a bare cold full-table requirement.
    assert map_only in predicate and "OR" in predicate
    settings = _ch_scan_settings(max_memory_bytes=1024)
    assert settings["max_memory_usage"] == 1024
    assert settings["max_threads"] >= 1


def test_mapping_table_is_shard_local():
    assert _mapping_table("run1", 0) == "backfill_mapping_run1_0"
    assert _mapping_table("run1", 3) == "backfill_mapping_run1_3"
    assert _mapping_table("run1", 0) != _mapping_table("run1", 1)
    with pytest.raises(ValueError):
        _mapping_table("bad-id!", 0)


@pytest.mark.asyncio
async def test_workflow_signal_state_transitions():
    flow = VapiMinimalBackfillWorkflow()
    await flow.pause()
    assert flow.paused
    await flow.resume()
    assert not flow.paused
    await flow.cancel()
    assert flow.cancelled
    assert flow.status()["cancelled"] is True


def test_proof_gate_detects_unrelated_column_change_and_missing_spans():
    # light row: id, attrs_string, _version, attributes_extra, other_hash
    stored = ["id", "attrs_string", "attributes_extra", "_version", "name", "input"]
    mapping = {
        "span_id": "s1",
        "patch_map": {"recording_url": "s3"},
        "extra": "{}",
        "extra_changed": 0,
        "version": 2,
    }

    class FakeCH:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, query, params=None, settings=None):
            if "system.mutations" in query:
                return [(0,)]
            if "system.merges" in query:
                return [(0, 0)]
            return self.rows

    before = {
        "s1": (
            "s1",
            {"recording_url": "old", "keep_me": "yes"},
            1,
            "{}",
            111,  # other_hash
        )
    }
    with pytest.raises(RuntimeError, match="unrelated wide columns"):
        _verify_proof_gate(
            ch=FakeCH(
                [
                    (
                        "s1",
                        {"recording_url": "s3", "keep_me": "yes"},
                        2,
                        "{}",
                        999,  # hash changed => wide col clobber
                    )
                ]
            ),
            stored=stored,
            active="attrs_string",
            extra="attributes_extra",
            project_id="p1",
            before=before,
            mappings=[mapping],
        )
    with pytest.raises(RuntimeError, match="lost spans"):
        _verify_proof_gate(
            ch=FakeCH([]),
            stored=stored,
            active="attrs_string",
            extra="attributes_extra",
            project_id="p1",
            before=before,
            mappings=[mapping],
        )


def test_proof_gate_accepts_mapupdate_patch_and_preserves_other_keys():
    stored = ["id", "attrs_string", "attributes_extra", "_version", "name", "input"]
    mapping = {
        "span_id": "s1",
        "patch_map": {"recording_url": "s3"},
        "extra": '{"nested":"s3"}',
        "extra_changed": 1,
        "version": 2,
    }

    class FakeCH:
        def execute(self, query, params=None, settings=None):
            if "system.mutations" in query:
                return [(0,)]
            if "system.merges" in query:
                return [(0, 0)]
            # id, map, version, extra, other_hash
            return [
                (
                    "s1",
                    {"recording_url": "s3", "keep_me": "yes"},
                    2,
                    '{"nested":"s3"}',
                    111,
                )
            ]

    before = {
        "s1": (
            "s1",
            {"recording_url": "old", "keep_me": "yes"},
            1,
            '{"nested":"old"}',
            111,
        )
    }
    _verify_proof_gate(
        ch=FakeCH(),
        stored=stored,
        active="attrs_string",
        extra="attributes_extra",
        project_id="p1",
        before=before,
        mappings=[mapping],
    )


def test_proof_gate_detects_clobbered_unrelated_map_key():
    stored = ["id", "attrs_string", "attributes_extra", "_version", "name", "input"]
    mapping = {
        "span_id": "s1",
        "patch_map": {"recording_url": "s3"},
        "extra": "",
        "extra_changed": 0,
        "version": 2,
    }

    class FakeCH:
        def execute(self, query, params=None, settings=None):
            if "system.mutations" in query:
                return [(0,)]
            if "system.merges" in query:
                return [(0, 0)]
            return [
                (
                    "s1",
                    {"recording_url": "s3", "keep_me": "NOPE"},
                    2,
                    "{}",
                    111,
                )
            ]

    before = {
        "s1": (
            "s1",
            {"recording_url": "old", "keep_me": "yes"},
            1,
            "{}",
            111,
        )
    }
    with pytest.raises(RuntimeError, match="clobbered unrelated key keep_me"):
        _verify_proof_gate(
            ch=FakeCH(),
            stored=stored,
            active="attrs_string",
            extra="attributes_extra",
            project_id="p1",
            before=before,
            mappings=[mapping],
        )


def test_proof_light_select_never_includes_wide_columns():
    from tfc.temporal.backfill.minimal_backfill import _proof_light_select

    light, hash_expr = _proof_light_select(
        ["id", "attrs_string", "attributes_extra", "_version", "input", "output", "name"],
        "attrs_string",
        "attributes_extra",
        "_version",
    )
    assert light == ["id", "attrs_string", "_version", "attributes_extra"]
    assert "input" not in light and "output" not in light
    assert "input" in hash_expr and "output" in hash_expr
    assert "cityHash64" in hash_expr


def test_dedicated_worker_rejects_multiple_activity_slots(monkeypatch):
    from tfc.temporal.backfill.start_backfill_worker import main

    monkeypatch.setenv("BACKFILL_MAX_CONCURRENT_ACTIVITIES", "2")
    with pytest.raises(ValueError, match="must be 1"):
        main()


def test_clickhouse_shape_selects_v1_and_v2_columns(monkeypatch):
    import tracer.services.clickhouse.schema as schema

    class FakeCH:
        def __init__(self, columns):
            self.columns = columns

        def execute(self, query, params=None, settings=None):
            return list(self.columns.items())

    monkeypatch.setattr(schema, "detect_spans_table_shape", lambda execute: "v1")
    assert _ch_shape(
        FakeCH({"span_attr_str": "Map(String,String)", "span_attributes_raw": "String"})
    )[0:2] == ("span_attr_str", "span_attributes_raw")
    monkeypatch.setattr(schema, "detect_spans_table_shape", lambda execute: "v2")
    assert _ch_shape(
        FakeCH({"attrs_string": "Map(String,String)", "attributes_extra": "String"})
    )[0:2] == ("attrs_string", "attributes_extra")


def test_reconcile_map_only_all_projects_and_day_chunked_extra(monkeypatch):
    """Reconcile must never issue a project-wide attributes_extra cold scan."""
    from tfc.temporal.backfill import minimal_backfill as mb

    queries: list[str] = []

    class FakeCH:
        def execute(self, query, params=None, settings=None):
            queries.append(query)
            q = " ".join(query.split())
            if "GROUP BY project_id" in q:
                return [("p1", 10), ("p2", 5)]
            if "toString(toDate(start_time))" in q or "GROUP BY day" in q:
                return [("2026-07-01",), ("2026-06-30",)]
            if "count()" in q and "toDate(start_time)" in q:
                # day-chunked extra-only
                return [(3 if params and params.get("day") == "2026-07-01" else 2,)]
            if "count()" in q and "project_id=%(project)s" in q:
                return [(7,)]
            if "system.columns" in q:
                return [
                    ("attrs_string", "Map(String, String)"),
                    ("attributes_extra", "String"),
                    ("_version", "UInt64"),
                    ("is_deleted", "UInt8"),
                ]
            return []

    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: FakeCH(),
    )
    # Force shape without real CH.
    monkeypatch.setattr(
        mb,
        "_ch_shape",
        lambda ch: ("attrs_string", "attributes_extra", "Map(String, String)", "_version", "is_deleted"),
    )

    all_projects = mb.reconcile_backfill_sample(
        mb.VapiBackfillInput(source="observability", project_id=None)
    )
    assert all_projects["mode"] == "map_only_all_projects"
    assert all_projects["map_pending_total"] == 15
    assert all_projects["projects"] == 2
    # No query should scan attributes_extra without a day bound in all-project mode.
    for q in queries:
        if "attributes_extra" in q:
            assert "toDate(start_time)" in q

    queries.clear()
    one = mb.reconcile_backfill_sample(
        mb.VapiBackfillInput(
            source="observability", project_id="11111111-1111-1111-1111-111111111111"
        )
    )
    assert one["map_pending"] == 7
    assert one["extra_pending"] == 5  # 3 + 2 day chunks
    assert one["spans_pending"] == 12
    # Extra counts must be day-scoped.
    extra_queries = [q for q in queries if "attributes_extra" in q and "count()" in q]
    assert extra_queries
    assert all("toDate(start_time)" in q for q in extra_queries)


def test_valid_direct_audio_uses_existing_detector(monkeypatch):
    from simulate.temporal.utils import async_storage as storage_helpers

    class HtmlResp:
        content = b"<!doctype html><html>expired</html>"
        headers = {"Content-Type": "text/html"}

    class EmptyResp:
        content = b""
        headers = {"Content-Type": "audio/wav"}

    class GoodResp:
        content = b"fake-aac-or-wav-bytes-here"
        headers = {"Content-Type": "audio/aac"}

    assert _valid_direct_audio(HtmlResp()) is None
    assert _valid_direct_audio(EmptyResp()) is None

    # Detector accepts this body as a supported extension (aac/wav/etc.).
    monkeypatch.setattr(
        storage_helpers, "_detected_audio_extension", lambda content: "aac"
    )
    assert _valid_direct_audio(GoodResp()) == (GoodResp.content, "aac")

    # Detector rejection (unsupported / non-audio) falls through.
    monkeypatch.setattr(
        storage_helpers,
        "_detected_audio_extension",
        lambda content: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert _valid_direct_audio(GoodResp()) is None


def test_download_valid_direct_audio_never_resolves_api_key(monkeypatch):
    import requests as requests_mod
    from simulate.temporal.utils import async_storage as storage_helpers

    class Resp:
        content = b"ID3-or-aac-payload-bytes"
        headers = {"Content-Type": "audio/mpeg"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests_mod, "get", lambda *args, **kwargs: Resp())
    monkeypatch.setattr(
        storage_helpers, "_detected_audio_extension", lambda content: "mp3"
    )
    called = {"n": 0}

    def provider():
        called["n"] += 1
        raise AssertionError("api key must not be resolved on valid direct audio")

    data, ext = _download(
        "https://storage.vapi.ai/call.wav", "call-1", "mono_combined", provider
    )
    assert data == b"ID3-or-aac-payload-bytes"
    assert ext == "mp3"
    assert called["n"] == 0


def test_download_invalid_storage_vapi_html_uses_api_fallback(monkeypatch):
    """storage.vapi.ai 200 HTML/error body must not count as audio."""
    import requests as requests_mod
    from tracer.utils.vapi_recording import VapiRecordingService

    class HtmlResp:
        content = b"<!doctype html><html>expired</html>"
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests_mod, "get", lambda *args, **kwargs: HtmlResp())
    monkeypatch.setenv("VAPI_API_RATE_LIMIT_PER_SECOND", "5")
    monkeypatch.setattr(
        VapiRecordingService,
        "artifact_for_url_type",
        staticmethod(lambda kind: "recording"),
    )
    monkeypatch.setattr(
        VapiRecordingService,
        "download_artifact_sync",
        staticmethod(lambda call_id, artifact, api_key: b"from-api"),
    )
    monkeypatch.setattr(
        "tfc.temporal.backfill.minimal_backfill._wait_for_api_slot", lambda rate: None
    )
    called = {"n": 0}

    def provider():
        called["n"] += 1
        return "secret-key"

    data, ext = _download(
        "https://storage.vapi.ai/call.wav", "call-1", "mono_combined", provider
    )
    assert data == b"from-api"
    assert ext is None
    assert called["n"] == 1


def test_download_empty_storage_vapi_body_uses_api_fallback(monkeypatch):
    import requests as requests_mod
    from tracer.utils.vapi_recording import VapiRecordingService

    class EmptyResp:
        content = b""
        headers = {"Content-Type": "audio/wav"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests_mod, "get", lambda *args, **kwargs: EmptyResp())
    monkeypatch.setenv("VAPI_API_RATE_LIMIT_PER_SECOND", "5")
    monkeypatch.setattr(
        VapiRecordingService,
        "artifact_for_url_type",
        staticmethod(lambda kind: "recording"),
    )
    monkeypatch.setattr(
        VapiRecordingService,
        "download_artifact_sync",
        staticmethod(lambda call_id, artifact, api_key: b"from-api"),
    )
    monkeypatch.setattr(
        "tfc.temporal.backfill.minimal_backfill._wait_for_api_slot", lambda rate: None
    )
    data, ext = _download(
        "https://storage.vapi.ai/call.wav",
        "call-1",
        "mono_combined",
        lambda: "secret",
    )
    assert data == b"from-api"
    assert ext is None


def test_download_private_r2_failure_uses_api_fallback_when_configured(monkeypatch):
    """r2.dev is private; failed direct GET may use authenticated Vapi API only."""
    import requests as requests_mod
    from tracer.utils.vapi_recording import VapiRecordingService

    def boom(*args, **kwargs):
        err = requests_mod.HTTPError("forbidden")
        err.response = SimpleNamespace(status_code=403)
        raise err

    monkeypatch.setattr(requests_mod, "get", boom)
    monkeypatch.setenv("VAPI_API_RATE_LIMIT_PER_SECOND", "5")
    monkeypatch.setattr(
        VapiRecordingService,
        "artifact_for_url_type",
        staticmethod(lambda kind: "recording"),
    )
    monkeypatch.setattr(
        VapiRecordingService,
        "download_artifact_sync",
        staticmethod(lambda call_id, artifact, api_key: b"from-api"),
    )
    monkeypatch.setattr(
        "tfc.temporal.backfill.minimal_backfill._wait_for_api_slot", lambda rate: None
    )
    called = {"n": 0}

    def provider():
        called["n"] += 1
        return "secret-key"

    data, ext = _download(
        "https://pub-abc.r2.dev/call.wav", "call-1", "mono_combined", provider
    )
    assert data == b"from-api"
    assert ext is None
    assert called["n"] == 1


def test_download_cdn_failure_skips_key_when_rate_disabled(monkeypatch):
    import requests as requests_mod

    def boom(*args, **kwargs):
        raise requests_mod.HTTPError("gone")

    monkeypatch.setattr(requests_mod, "get", boom)
    monkeypatch.delenv("VAPI_API_RATE_LIMIT_PER_SECOND", raising=False)
    called = {"n": 0}

    def provider():
        called["n"] += 1
        raise AssertionError("must not resolve key when rate limit unset")

    with pytest.raises(RuntimeError, match="authenticated fallback is disabled"):
        _download(
            "https://storage.vapi.ai/call.wav", "call-1", "mono_combined", provider
        )
    assert called["n"] == 0


def test_download_cdn_failure_uses_api_fallback_when_configured(monkeypatch):
    import requests as requests_mod
    from tracer.utils.vapi_recording import VapiRecordingService

    def boom(*args, **kwargs):
        err = requests_mod.HTTPError("expired")
        err.response = SimpleNamespace(status_code=403)
        raise err

    monkeypatch.setattr(requests_mod, "get", boom)
    monkeypatch.setenv("VAPI_API_RATE_LIMIT_PER_SECOND", "5")
    monkeypatch.setattr(
        VapiRecordingService,
        "artifact_for_url_type",
        staticmethod(lambda kind: "recording"),
    )
    monkeypatch.setattr(
        VapiRecordingService,
        "download_artifact_sync",
        staticmethod(lambda call_id, artifact, api_key: b"from-api"),
    )
    monkeypatch.setattr(
        "tfc.temporal.backfill.minimal_backfill._wait_for_api_slot", lambda rate: None
    )
    called = {"n": 0}

    def provider():
        called["n"] += 1
        return "secret-key"

    data, ext = _download(
        "https://storage.vapi.ai/call.wav", "call-1", "mono_combined", provider
    )
    assert data == b"from-api"
    assert ext is None
    assert called["n"] == 1


def test_stored_resolves_existing_webm_without_restat(monkeypatch):
    from simulate.temporal.utils import async_storage as storage_helpers
    from tracer.utils.vapi_recording import VapiRecordingService

    base = "call-recordings/p/vapi/call-1/mono_combined"
    webm_url = "https://fi-content-dev.s3.amazonaws.com/" + base + ".webm"

    monkeypatch.setattr(
        storage_helpers,
        "_existing_rehosted_audio",
        lambda object_key_base: (webm_url, 12),
    )
    monkeypatch.setattr(
        storage_helpers,
        "_rehost_object_key_base",
        lambda call_id, kind, project_id, provider: base,
    )
    monkeypatch.setattr(
        storage_helpers,
        "_rehost_object_key",
        lambda object_key_base, extension: f"{object_key_base}.{extension}",
    )

    class Storage:
        def stat_object(self, bucket, key):
            # Final HEAD validation only — no extension probe loop.
            assert key.endswith(".webm")
            return SimpleNamespace(size=12, content_type="audio/webm")

    monkeypatch.setattr(
        "tfc.utils.storage_client.get_storage_client", lambda: Storage()
    )
    monkeypatch.setattr(
        "tfc.settings.settings.UPLOAD_BUCKET_NAME", "fi-content-dev", raising=False
    )
    monkeypatch.setattr(
        VapiRecordingService, "is_fagi_s3_url", staticmethod(lambda url: True)
    )

    url = _stored("p", "call-1", "mono_combined", "https://storage.vapi.ai/old.wav", None)
    assert url == webm_url


def test_stored_reuses_carried_audio_ext_without_redetection(monkeypatch):
    from simulate.temporal.utils import async_storage as storage_helpers
    from tracer.utils.vapi_recording import VapiRecordingService

    base = "call-recordings/p/vapi/call-1/mono_combined"
    detected = {"n": 0}

    monkeypatch.setattr(
        storage_helpers, "_existing_rehosted_audio", lambda object_key_base: None
    )
    monkeypatch.setattr(
        storage_helpers,
        "_rehost_object_key_base",
        lambda call_id, kind, project_id, provider: base,
    )
    monkeypatch.setattr(
        storage_helpers,
        "_rehost_object_key",
        lambda object_key_base, extension: f"{object_key_base}.{extension}",
    )

    def boom_detect(content):
        detected["n"] += 1
        raise AssertionError("must not re-detect when audio_ext is carried")

    monkeypatch.setattr(storage_helpers, "_detected_audio_extension", boom_detect)
    monkeypatch.setattr(
        "tfc.utils.storage.upload_audio_to_s3",
        lambda payload, object_key=None: f"https://fi-content-dev.s3.amazonaws.com/{object_key}",
    )

    class Storage:
        def stat_object(self, bucket, key):
            return SimpleNamespace(size=12, content_type="audio/aac")

    monkeypatch.setattr(
        "tfc.utils.storage_client.get_storage_client", lambda: Storage()
    )
    monkeypatch.setattr(
        "tfc.settings.settings.UPLOAD_BUCKET_NAME", "fi-content-dev", raising=False
    )
    monkeypatch.setattr(
        VapiRecordingService, "is_fagi_s3_url", staticmethod(lambda url: True)
    )

    url = _stored(
        "p",
        "call-1",
        "mono_combined",
        "https://storage.vapi.ai/old.wav",
        b"aac-bytes",
        audio_ext="aac",
    )
    assert url.endswith(".aac")
    assert detected["n"] == 0


def test_run_worker_rejects_backfill_multi_slot():
    import asyncio

    from tfc.temporal.common.worker import run_worker

    with pytest.raises(ValueError, match="max_concurrent_activities=1"):
        asyncio.run(run_worker("backfill", max_concurrent_activities=2, skip_otel_init=True))
