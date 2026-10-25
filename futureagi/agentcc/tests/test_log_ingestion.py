"""Log ingestion: Django's cost fallback, and the v2 webhook batch (log-entry-v2
and outcome-event-v1) written replay-safely."""

import copy
import hashlib
import json
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import connections

from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.request_attempt import AgentccRequestAttempt
from agentcc.models.request_outcome import AgentccRequestOutcome
from agentcc.services import log_ingestion
from agentcc.services.log_ingestion import _compute_cost, ingest_request_logs

EXAMPLES_DIR = (
    Path(__file__).resolve().parents[3] / "api_contracts" / "gateway" / "examples"
)
GOLDEN_REQUEST_ID = "req_01JD8Z3K4M5N6P7Q8R9S"
GOLDEN_KEY_ID = "key_7f3a"


def load_example(name):
    return json.loads((EXAMPLES_DIR / name).read_text())


def golden_entry(**changes):
    """The log-entry-v2 golden, with top-level keys replaced or (None) removed."""
    entry = load_example("log-entry-v2.json")
    for key, value in changes.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


def golden_outcome_event(org, **changes):
    event = load_example("outcome-event-v1.json")
    event["org_id"] = str(org.id)
    event.update(changes)
    return event


def v1_entry(request_id, **fields):
    """A v1 log entry: no v2 key at all."""
    return {
        "request_id": request_id,
        "timestamp": "2026-10-15T09:00:00Z",
        "model": "gpt-4o",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "status_code": 200,
        "latency_ms": 100,
        "auth_key_id": GOLDEN_KEY_ID,
        **fields,
    }


def cache_hit_entry(request_id="req_cachehit_1"):
    """A v2 cache hit as the flusher sends it: no attempts, so no attempt_count."""
    entry = golden_entry(
        request_id=request_id,
        generation_id=request_id,
        cache_hit=True,
        fallback_used=False,
        attempts=None,
        attempt_count=None,
    )
    entry["metadata"] = {"cost_source": "cache_hit"}
    return entry


def expected_usage_event_id(org, request_ids, prefix="agentcc:req:"):
    digest = hashlib.sha256("\n".join(sorted(request_ids)).encode()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}{org.id}:{digest}"))


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID, name="golden-key", organization=organization
    )


@pytest.fixture
def emit_mock():
    """EE metering on, with the emitter replaced by a mock."""
    with (
        patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=False),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        yield emit


def _log(request_id=GOLDEN_REQUEST_ID):
    return AgentccRequestLog.no_workspace_objects.get(request_id=request_id)


def test_compute_cost_uses_tts_input_characters():
    entry = {
        "model": "tts-1",
        "metadata": {
            "input_characters": "1000",
        },
    }

    assert _compute_cost(entry) == 0.015


def test_compute_cost_uses_stt_audio_seconds():
    entry = {
        "model": "whisper-1",
        "metadata": {
            "audio_seconds": "30",
        },
    }

    assert _compute_cost(entry) == 0.003


def test_compute_cost_does_not_charge_for_zero_generated_images(monkeypatch):
    captured = {}

    def fake_get_model_info(model):
        return {"mode": "image_generation"}

    def fake_calculate_total_cost(model, usage):
        captured.update(usage)
        return {"total_cost": 0}

    monkeypatch.setattr(
        "agentic_eval.core_evals.run_prompt.model_pricing.get_model_info",
        fake_get_model_info,
    )
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost",
        fake_calculate_total_cost,
    )

    entry = {
        "model": "gpt-image-1",
        "metadata": {
            "images_generated": 0,
        },
    }

    _compute_cost(entry)

    assert captured["images_generated"] == 0


@pytest.mark.integration
class TestIngestV2:
    def test_ingest_v2_golden_fills_columns_and_attempts(self, gateway_key):
        assert ingest_request_logs([golden_entry()]) == 1

        log = _log()
        assert log.organization_id == gateway_key.organization_id
        # Billing: the gateway's own numbers, never Django's estimate.
        assert log.cost == Decimal("0.011815")
        assert log.billing_mode == "managed"
        assert log.credential_type == "managed"
        assert log.cost_upstream == Decimal("0.011252")
        assert log.cost_markup == Decimal("0.000563")
        assert log.cost_landed == Decimal("0.012249")
        assert log.cost_source == "provider_reported"
        assert log.usage_estimated is False
        assert log.served_upstream_provider == "anthropic"
        assert log.upstream_key_id == "kh_3b9e"
        assert log.content_mode == "metadata"
        # Tokens, attempts and identity.
        assert (log.cached_tokens, log.cache_write_tokens, log.reasoning_tokens) == (
            1024,
            0,
            96,
        )
        assert log.attempt_count == 2
        assert log.generation_id == GOLDEN_REQUEST_ID
        # Agent.
        assert log.agent_framework == "omega"
        assert log.agent_id == "researcher"
        assert log.agent_version == "3"
        assert log.agent_run_id == "run_R2"
        assert log.agent_parent_run_id == "run_R1"
        assert log.agent_task_id == "task_17"
        assert log.agent_step == "act"
        assert log.agent_step_source == "header"
        assert log.agent_turn == 4
        assert log.agent_env == "production"
        assert log.agent_identity_source == "runtime_token"
        assert log.budget_scope == "run_R1"
        # Routing.
        assert log.policy_version == "v12"
        assert log.policy_variant == "stable"
        assert log.catalog_snapshot_id == "3f9a1c0b2d4e"
        assert log.auto_routed is True
        # App, user and the tool-call verdict, as sent.
        assert (log.app_title, log.app_referer) == (
            "Deep Research",
            "https://research.example.com",
        )
        assert log.end_user == "customer-1932"
        assert log.tags == ["research", "batch-7"]
        assert json.loads(log.metadata["guardrail_tool_calls"]) == {
            "checked": 2,
            "blocked": [],
            "stripped": [],
        }
        assert log.metadata["cost_markup_percent"] == "5"

        attempts = list(
            AgentccRequestAttempt.objects.filter(request_id=GOLDEN_REQUEST_ID).order_by(
                "index"
            )
        )
        assert [a.attempt_id for a in attempts] == [
            f"{GOLDEN_REQUEST_ID}.1",
            f"{GOLDEN_REQUEST_ID}.2",
        ]
        first, second = attempts
        assert all(a.request_log_id == log.id for a in attempts)
        assert all(a.organization_id == log.organization_id for a in attempts)
        assert (first.kind, first.reason, first.outcome) == ("primary", "", "error")
        assert (first.status_code, first.error_class, first.error_origin) == (
            429,
            "rate_limited",
            "upstream",
        )
        assert first.error_message == "Rate limit reached for requests"
        assert first.endpoint_id == "openrouter:openai:openai/gpt-6-sol"
        assert first.billable is False
        assert (second.kind, second.reason, second.outcome) == (
            "auto_fallback",
            "status_429",
            "success",
        )
        assert (second.latency_ms, second.ttfb_ms) == (2117, 640)
        assert (second.prompt_tokens, second.completion_tokens) == (1820, 412)
        assert (second.cached_tokens, second.reasoning_tokens) == (1024, 96)
        assert (
            second.upstream_cost_micros,
            second.billed_cost_micros,
            second.landed_cost_micros,
        ) == (11252, 11815, 12249)
        assert (second.upstream_fee_bps, second.processing_fee_bps) == (550, 320)
        assert second.cost_source == "provider_reported"
        assert second.credential_type == "managed"
        assert second.served_upstream_provider == "anthropic"
        assert second.upstream_key_id == "kh_3b9e"
        assert second.billable is True
        assert second.started_at.isoformat() == "2026-10-15T09:30:10.301000+00:00"

    def test_workflow_and_root_run_columns_filled(self, gateway_key):
        ingest_request_logs([golden_entry()])

        log = _log()
        assert log.agent_workflow == "deep-research"
        assert log.agent_root_run_id == "run_R1"

    def test_v1_entry_backfills_from_metadata(self, gateway_key, monkeypatch):
        monkeypatch.setattr(log_ingestion, "_compute_cost", lambda entry: 0.5)
        managed = v1_entry(
            "req_v1_managed",
            cost=0.0105,
            metadata={
                "billing_mode": "managed",
                "cost_base": "0.010000",
                "cost_markup_percent": "5",
                "cost_source": "catalog",
                "auto_decision": '{"model":"gpt-4o"}',
                "usage_estimated": "true",
            },
        )
        # A managed cache hit: the gateway billed nothing, so Django must not
        # price its tokens either.
        cache_hit = v1_entry(
            "req_v1_cached",
            cost=0,
            cache_hit=True,
            metadata={"billing_mode": "managed", "cost_source": "cache_hit"},
        )
        older = v1_entry("req_v1_older", cost=0)

        assert ingest_request_logs([managed, cache_hit, older]) == 3

        log = _log("req_v1_managed")
        assert log.billing_mode == "managed"
        assert log.cost_upstream == Decimal("0.01")
        assert log.cost_markup == Decimal("0.0005")
        assert log.cost_source == "catalog"
        assert log.auto_routed is True
        assert log.usage_estimated is True
        assert log.cost == Decimal("0.0105")
        assert log.attempt_count == 1
        assert log.generation_id is None
        assert not AgentccRequestAttempt.objects.filter(
            request_id="req_v1_managed"
        ).exists()

        cached = _log("req_v1_cached")
        assert cached.cost_source == "cache_hit"
        assert cached.cost == Decimal("0")
        assert cached.cost_upstream is None

        # Only an entry the gateway did not price falls back to Django's cost.
        older_log = _log("req_v1_older")
        assert older_log.cost == Decimal("0.5")
        assert older_log.billing_mode is None
        assert older_log.auto_routed is None
        assert older_log.usage_estimated is None

    def test_top_level_v2_fields_win_over_metadata(self, gateway_key):
        entry = golden_entry()
        entry["metadata"] = {
            **entry["metadata"],
            "billing_mode": "byok",
            "cost_source": "catalog",
            "cost_base": "9.0",
        }
        ingest_request_logs([entry])

        log = _log()
        assert log.billing_mode == "managed"
        assert log.cost_source == "provider_reported"
        assert log.cost_upstream == Decimal("0.011252")

    def test_v2_entry_omitted_usage_estimated_auto_routed_coerced_false(
        self, gateway_key
    ):
        entry = golden_entry(usage_estimated=None)
        entry["routing"].pop("auto")
        ingest_request_logs([entry, v1_entry("req_v1_plain")])

        log = _log()
        assert log.usage_estimated is False
        assert log.auto_routed is False
        # A v1 entry does not report them.
        v1 = _log("req_v1_plain")
        assert v1.usage_estimated is None
        assert v1.auto_routed is None

    def test_cache_hit_entry_attempt_count_zero(self, gateway_key):
        v1_cache_hit = v1_entry("req_v1_cachehit", cache_hit=True)
        assert ingest_request_logs([cache_hit_entry(), v1_cache_hit]) == 2

        hit = _log("req_cachehit_1")
        assert hit.cache_hit is True
        assert hit.attempt_count == 0
        assert not AgentccRequestAttempt.objects.filter(
            request_id="req_cachehit_1"
        ).exists()
        assert _log("req_v1_cachehit").attempt_count == 1

    def test_oversized_values_are_dropped_not_fatal(self, gateway_key):
        entry = golden_entry()
        entry["agent"] = {
            **entry["agent"],
            "env": "e" * 100,
            "run_id": "r" * 300,
        }
        entry["app"] = {"title": "t" * 300}
        entry["attempts"][0]["error_message"] = "x" * 2000
        entry["attempts"][1]["upstream_key_id"] = "k" * 500
        neighbour = v1_entry("req_neighbour")

        assert ingest_request_logs([entry, neighbour]) == 2

        log = _log()
        # Identifiers that do not fit are not stored (a cut id would join
        # the wrong rows); display text is shortened.
        assert log.agent_env is None
        assert log.agent_run_id is None
        assert log.app_title == "t" * 128
        assert log.agent_id == "researcher"
        attempts = AgentccRequestAttempt.objects.filter(
            request_id=GOLDEN_REQUEST_ID
        ).order_by("index")
        assert len(attempts[0].error_message) == 512
        assert attempts[1].upstream_key_id == ""
        assert _log("req_neighbour").model == "gpt-4o"


@pytest.mark.integration
class TestReplaySafety:
    def test_replayed_batch_no_duplicate_rows_attempts_or_events(
        self, gateway_key, emit_mock, organization
    ):
        batch = [
            golden_entry(),
            v1_entry("req_v1_replay"),
            golden_outcome_event(organization),
        ]

        assert ingest_request_logs(copy.deepcopy(batch)) == 2
        assert ingest_request_logs(copy.deepcopy(batch)) == 0

        assert AgentccRequestLog.no_workspace_objects.count() == 2
        assert AgentccRequestAttempt.objects.count() == 2
        assert AgentccRequestOutcome.objects.count() == 2  # classified + event
        emit_mock.assert_called_once()
        event = emit_mock.call_args.args[0]
        assert event.event_type == "gateway_request"
        assert event.amount == 2
        assert event.org_id == str(organization.id)
        assert event.event_id == expected_usage_event_id(
            organization, [GOLDEN_REQUEST_ID, "req_v1_replay"]
        )

        # Only the rows a batch adds are counted, under their own event id.
        emit_mock.reset_mock()
        assert ingest_request_logs([*copy.deepcopy(batch), v1_entry("req_v1_new")]) == 1
        event = emit_mock.call_args.args[0]
        assert event.amount == 1
        assert event.event_id == expected_usage_event_id(organization, ["req_v1_new"])

    def test_duplicate_entries_within_one_batch_store_one_row(
        self, gateway_key, emit_mock
    ):
        assert ingest_request_logs([golden_entry(), golden_entry()]) == 1

        assert AgentccRequestLog.no_workspace_objects.count() == 1
        assert AgentccRequestAttempt.objects.count() == 2
        assert emit_mock.call_args.args[0].amount == 1

    def test_cache_hits_get_their_own_event_and_id(
        self, gateway_key, emit_mock, organization
    ):
        ingest_request_logs([golden_entry(), cache_hit_entry()])

        events = {
            call.args[0].event_type: call.args[0] for call in emit_mock.call_args_list
        }
        assert set(events) == {"gateway_request", "gateway_cache_hit"}
        assert events["gateway_request"].amount == 1
        assert events["gateway_cache_hit"].amount == 1
        new_ids = [GOLDEN_REQUEST_ID, "req_cachehit_1"]
        assert events["gateway_request"].event_id == expected_usage_event_id(
            organization, new_ids
        )
        assert events["gateway_cache_hit"].event_id == expected_usage_event_id(
            organization, new_ids, prefix="agentcc:cache_hit:"
        )

    def test_one_org_failing_does_not_lose_another_orgs_rows(
        self, gateway_key, organization, monkeypatch
    ):
        from accounts.models import Organization

        other = Organization.objects.create(name="Other Org")
        AgentccAPIKey.objects.create(
            gateway_key_id="key-other", name="other", organization=other
        )
        real_attempt_rows = log_ingestion._attempt_rows

        def failing_for_first_org(org, log_row, entry):
            if org.id == organization.id:
                raise RuntimeError("boom")
            return real_attempt_rows(org, log_row, entry)

        monkeypatch.setattr(log_ingestion, "_attempt_rows", failing_for_first_org)

        with pytest.raises(log_ingestion.LogIngestionError):
            ingest_request_logs(
                [golden_entry(), v1_entry("req_other", auth_key_id="key-other")]
            )

        # The failed org's transaction rolled back whole; the other org's committed.
        assert not AgentccRequestLog.no_workspace_objects.filter(
            organization=organization
        ).exists()
        assert _log("req_other").organization_id == other.id

        # A replay of the batch then fills in only what is missing.
        monkeypatch.setattr(log_ingestion, "_attempt_rows", real_attempt_rows)
        assert (
            ingest_request_logs(
                [golden_entry(), v1_entry("req_other", auth_key_id="key-other")]
            )
            == 1
        )
        assert AgentccRequestLog.no_workspace_objects.count() == 2


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_concurrent_replays_insert_once(gateway_key, monkeypatch):
    """The flusher re-sends a batch whose first delivery is still running (its
    HTTP timeout is 15 s), so two deliveries can ingest the same batch at once.
    Both read the duplicate pre-query before either has written; the per-org
    lock must make the second wait for the first."""
    both_read = threading.Barrier(2, timeout=3)
    real_existing = log_ingestion._existing_request_ids

    def existing_then_wait(org, request_ids):
        found = real_existing(org, request_ids)
        try:
            both_read.wait()
        except threading.BrokenBarrierError:
            pass  # the other delivery is waiting on the lock
        return found

    monkeypatch.setattr(log_ingestion, "_existing_request_ids", existing_then_wait)
    counts, errors = [], []

    def deliver():
        try:
            counts.append(ingest_request_logs([golden_entry()]))
        except Exception as exc:  # surfaced below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(counts) == [0, 1]
    assert AgentccRequestLog.no_workspace_objects.count() == 1
    assert AgentccRequestAttempt.objects.count() == 2


@pytest.mark.integration
class TestOutcomes:
    def test_outcome_event_ingested_once(self, organization, emit_mock):
        event = golden_outcome_event(organization)

        assert ingest_request_logs([copy.deepcopy(event)]) == 0
        assert ingest_request_logs([copy.deepcopy(event)]) == 0

        row = AgentccRequestOutcome.objects.get()
        assert row.organization_id == organization.id
        assert row.request_id == GOLDEN_REQUEST_ID
        assert row.event_id == "evt_01JD8Z9V2W3X4Y5Z6A7B"
        assert (row.outcome, row.source) == ("verify_failed", "prev_outcome")
        assert row.score == pytest.approx(0.2)
        assert (row.agent_run_id, row.agent_id, row.agent_step) == (
            "run_R2",
            "researcher",
            "act",
        )
        assert row.model == "anthropic/claude-sonnet-5.5"
        assert int(row.occurred_at.timestamp() * 1000) == 1792056614201
        # An outcome is not a request: no row, no gateway_request event.
        assert not AgentccRequestLog.no_workspace_objects.exists()
        emit_mock.assert_not_called()

    def test_unusable_score_is_dropped_not_the_event(self, organization):
        ingest_request_logs(
            [
                golden_outcome_event(organization, event_id="evt_big", score=10**400),
                golden_outcome_event(organization, event_id="evt_str", score="0.9"),
            ]
        )

        assert dict(AgentccRequestOutcome.objects.values_list("event_id", "score")) == {
            "evt_big": None,
            "evt_str": None,
        }

    def test_outcome_before_its_request_is_kept(self, gateway_key, organization):
        ingest_request_logs([golden_outcome_event(organization)])
        ingest_request_logs([golden_entry()])

        assert (
            AgentccRequestOutcome.objects.filter(
                organization=organization, request_id=GOLDEN_REQUEST_ID
            ).count()
            == 2
        )

    def test_outcome_event_without_event_id_rejected(self, organization):
        missing = golden_outcome_event(organization)
        missing.pop("event_id")
        blank = golden_outcome_event(organization, event_id="")
        valid = golden_outcome_event(organization, event_id="evt_valid")

        ingest_request_logs([missing, blank, valid])

        assert list(
            AgentccRequestOutcome.objects.values_list("event_id", flat=True)
        ) == ["evt_valid"]

    @pytest.mark.parametrize(
        "changes",
        [
            {"org_id": "not-a-uuid"},
            {"org_id": str(uuid.uuid4())},
            {"generation_id": ""},
            {"outcome": "great"},
            {"source": "classified"},
            {"ts_ms": None},
            {"ts_ms": -1},
            {"event_id": "e" * 65},
            {"outcome": ["ok"]},
            {"source": {"feedback": 1}},
        ],
        ids=[
            "bad-org",
            "unknown-org",
            "no-generation",
            "bad-outcome",
            "classified-source",
            "no-ts",
            "negative-ts",
            "long-event-id",
            "list-outcome",
            "object-source",
        ],
    )
    def test_off_contract_outcome_events_rejected(self, organization, changes):
        off_contract = golden_outcome_event(organization, **changes)
        on_contract = golden_outcome_event(organization, event_id="evt_on_contract")

        ingest_request_logs([off_contract, on_contract])

        assert list(
            AgentccRequestOutcome.objects.values_list("event_id", flat=True)
        ) == ["evt_on_contract"]

    def test_outcome_class_row_from_log_entry(self, gateway_key, organization):
        ingest_request_logs([golden_entry()])
        ingest_request_logs([golden_entry()])  # replayed

        row = AgentccRequestOutcome.objects.get(source="classified")
        assert row.organization_id == organization.id
        assert row.request_id == GOLDEN_REQUEST_ID
        assert row.outcome == "ok"
        assert row.event_id is None
        assert (row.agent_run_id, row.agent_id, row.agent_step) == (
            "run_R2",
            "researcher",
            "act",
        )
        assert row.model == "anthropic/claude-sonnet-5.5"
        assert row.occurred_at == _log().started_at

    def test_unknown_record_kind_is_skipped(self, gateway_key):
        # A record with a kind is an event, never a request, even one that
        # names an API key.
        unknown = {
            "kind": "telemetry",
            "request_id": "evt-1",
            "auth_key_id": GOLDEN_KEY_ID,
        }

        assert ingest_request_logs([unknown, golden_entry()]) == 1
        assert list(
            AgentccRequestLog.no_workspace_objects.values_list("request_id", flat=True)
        ) == [GOLDEN_REQUEST_ID]
