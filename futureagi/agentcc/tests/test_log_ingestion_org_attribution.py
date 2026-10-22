"""A log webhook batch mixes orgs; every entry is stored under its own key's org.

The gateway's LogFlusher keeps one buffer for every org it serves and posts it
as one batch, so these tests send batches that mix two orgs' keys (and keys
Django does not know) and check where each entry, and what it triggers, lands.
"""

import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
import structlog
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccSession
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.log_ingestion import ingest_request_logs

KEY_A = "gk-log-org-a"
KEY_B = "gk-log-org-b"


@pytest.fixture
def org_a(organization):
    return organization


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Log Batch Org B")


@pytest.fixture
def keys(org_a, org_b):
    return (
        AgentccAPIKey.objects.create(
            gateway_key_id=KEY_A, name="org-a-key", organization=org_a
        ),
        AgentccAPIKey.objects.create(
            gateway_key_id=KEY_B, name="org-b-key", organization=org_b
        ),
    )


def entry(request_id, key_id=None, **extra):
    record = {
        "request_id": request_id,
        "model": "gpt-4o",
        "provider": "openai",
        "status_code": 200,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost": 0.001,
    }
    if key_id is not None:
        record["auth_key_id"] = key_id
    record.update(extra)
    return record


def stored_orgs():
    """request_id -> organization_id of every stored request-log row."""
    return dict(
        AgentccRequestLog._base_manager.values_list("request_id", "organization_id")
    )


@pytest.mark.integration
class TestMixedOrgBatch:
    @pytest.mark.parametrize("b_first", [False, True], ids=["a-first", "b-first"])
    def test_each_entry_is_stored_under_its_own_keys_org(
        self, keys, org_a, org_b, b_first
    ):
        batch = [
            entry("req-a", KEY_A, request_body={"messages": ["org a's prompt"]}),
            entry("req-b", KEY_B, request_body={"messages": ["org b's prompt"]}),
        ]
        if b_first:
            batch.reverse()

        assert ingest_request_logs(batch) == 2

        assert stored_orgs() == {"req-a": org_a.id, "req-b": org_b.id}
        row_b = AgentccRequestLog._base_manager.get(request_id="req-b")
        assert row_b.request_body == {"messages": ["org b's prompt"]}
        assert not AgentccRequestLog.no_workspace_objects.filter(
            organization=org_a, request_id="req-b"
        ).exists()

    def test_the_api_key_id_field_attributes_too(self, keys, org_a, org_b):
        ingest_request_logs(
            [entry("req-a", api_key_id=KEY_A), entry("req-b", api_key_id=KEY_B)]
        )

        assert stored_orgs() == {"req-a": org_a.id, "req-b": org_b.id}


@pytest.mark.integration
class TestUnattributedEntries:
    def test_an_unknown_or_missing_key_is_never_stored_under_another_org(
        self, keys, org_a
    ):
        batch = [
            entry("req-a", KEY_A),
            entry("req-unknown-key", "gk-nobody-knows"),
            entry("req-no-key"),
            "not an object",
        ]

        with structlog.testing.capture_logs() as logs:
            assert ingest_request_logs(batch) == 1

        assert stored_orgs() == {"req-a": org_a.id}
        skipped = [log for log in logs if log["event"] == "log_entry_unattributed"]
        assert len(skipped) == 1
        assert skipped[0]["log_level"] == "warning"
        assert skipped[0]["skipped"] == 3
        assert skipped[0]["reasons"] == {
            "unknown_key": 1,
            "no_key": 1,
            "not_an_object": 1,
        }
        assert set(skipped[0]["request_ids"]) == {"req-unknown-key", "req-no-key"}

    def test_a_batch_no_entry_of_which_has_a_known_key_stores_nothing(self, keys):
        assert (
            ingest_request_logs([entry("req-unknown", "gk-unknown"), entry("req-none")])
            == 0
        )

        assert stored_orgs() == {}

    def test_a_key_whose_rows_name_two_orgs_is_not_guessed(self, org_a, org_b):
        for org in (org_a, org_b):
            AgentccAPIKey.objects.create(
                gateway_key_id="gk-shared",
                name="deleted",
                organization=org,
                deleted=True,
            )

        assert ingest_request_logs([entry("req-shared", "gk-shared")]) == 0

        assert stored_orgs() == {}


@pytest.mark.integration
class TestMetadataOrgIdIsNotAuthority:
    """metadata.org_id is not evidence of an entry's org: ingestion takes the
    org from the key the entry was authenticated with. An entry for a revoked
    or expired key can carry org_id but no auth_key_id.
    """

    def test_a_known_key_wins_over_metadata_org_id(self, keys, org_a, org_b):
        batch = [
            # Unknown key first: the batch's org never comes from it.
            entry("req-unknown", "gk-unknown", metadata={"org_id": str(org_b.id)}),
            entry("req-a", KEY_A, metadata={"org_id": str(org_b.id)}),
        ]

        assert ingest_request_logs(batch) == 1

        assert stored_orgs() == {"req-a": org_a.id}

    @pytest.mark.parametrize(
        "key_id", ["gk-unknown", None], ids=["unknown-key", "no-key"]
    )
    def test_metadata_org_id_alone_stores_nothing(self, keys, org_b, key_id):
        batch = [entry("req-x", key_id, metadata={"org_id": str(org_b.id)})]

        assert ingest_request_logs(batch) == 0

        assert stored_orgs() == {}


@pytest.mark.integration
class TestDeletedKeys:
    """Entries for a key Django has soft-deleted are stored and billed in the
    key's own org."""

    def test_a_deleted_keys_entries_stay_in_its_own_org(self, keys, org_a, org_b):
        key_a, _ = keys
        key_a.deleted = True
        key_a.save(update_fields=["deleted"])

        batch = [
            entry("req-b", KEY_B),
            entry("req-a", KEY_A, metadata={"org_id": str(org_b.id)}),
        ]

        assert ingest_request_logs(batch) == 2

        assert stored_orgs() == {"req-a": org_a.id, "req-b": org_b.id}

    def test_a_live_row_decides_over_a_deleted_one(self, org_a, org_b):
        AgentccAPIKey.objects.create(
            gateway_key_id="gk-reused", name="old", organization=org_a, deleted=True
        )
        AgentccAPIKey.objects.create(
            gateway_key_id="gk-reused", name="current", organization=org_b
        )

        ingest_request_logs([entry("req-reused", "gk-reused")])

        assert stored_orgs() == {"req-reused": org_b.id}


@pytest.mark.integration
@pytest.mark.requires_ee
class TestUsageEventsPerOrg:
    def test_usage_events_follow_each_entrys_org(self, keys, org_a, org_b):
        from ee.usage.deployment import DeploymentMode

        emitted = []
        batch = [
            entry("req-a", KEY_A),
            entry("req-b1", KEY_B),
            entry("req-b2", KEY_B, cache_hit=True),
        ]
        with (
            patch.object(DeploymentMode, "is_oss", return_value=False),
            patch("ee.usage.services.emitter.emit", side_effect=emitted.append),
        ):
            assert ingest_request_logs(batch) == 3

        assert sorted(
            (event.org_id, str(event.event_type), event.amount) for event in emitted
        ) == sorted(
            [
                (str(org_a.id), "gateway_request", 1),
                (str(org_b.id), "gateway_request", 1),
                (str(org_b.id), "gateway_cache_hit", 1),
            ]
        )


@pytest.mark.integration
class TestSideEffectsPerOrg:
    @patch("agentcc.services.webhook_delivery.deliver_webhook_events")
    def test_an_orgs_webhooks_get_only_its_own_entries(
        self, deliver, keys, org_a, org_b
    ):
        hooks = {
            org.id: AgentccWebhook.objects.create(
                organization=org,
                name=f"hook-{org.name}",
                url="https://example.com/hook",
                events=["request.completed"],
                is_active=True,
            )
            for org in (org_a, org_b)
        }

        ingest_request_logs([entry("req-a", KEY_A), entry("req-b", KEY_B)])

        def delivered(org):
            return {
                (event.organization_id, event.payload["request_id"])
                for event in AgentccWebhookEvent.no_workspace_objects.filter(
                    webhook=hooks[org.id]
                )
            }

        assert delivered(org_a) == {(org_a.id, "req-a")}
        assert delivered(org_b) == {(org_b.id, "req-b")}
        delivered_orgs = [call.kwargs["org_id"] for call in deliver.call_args_list]
        assert sorted(map(str, delivered_orgs)) == sorted(
            [str(org_a.id), str(org_b.id)]
        )

    def test_sessions_are_created_in_each_entrys_org(self, keys, org_a, org_b):
        ingest_request_logs(
            [
                entry("req-a", KEY_A, session_id="session-a"),
                entry("req-b", KEY_B, session_id="session-b"),
            ]
        )

        assert set(
            AgentccSession.no_workspace_objects.values_list(
                "organization_id", "session_id"
            )
        ) == {(org_a.id, "session-a"), (org_b.id, "session-b")}

    def test_each_orgs_keys_record_their_last_use(self, keys):
        ingest_request_logs([entry("req-a", KEY_A), entry("req-b", KEY_B)])

        for key in keys:
            key.refresh_from_db()
            assert key.last_used_at is not None


@pytest.mark.integration
class TestOneOrgFailing:
    def test_another_orgs_entries_are_still_stored(self, keys, org_a, org_b):
        real_bulk_create = AgentccRequestLog.no_workspace_objects.bulk_create

        def fail_for_org_b(objs, **kwargs):
            if any(obj.organization_id == org_b.id for obj in objs):
                raise RuntimeError("org b's insert failed")
            return real_bulk_create(objs, **kwargs)

        with (
            patch.object(
                AgentccRequestLog.no_workspace_objects,
                "bulk_create",
                side_effect=fail_for_org_b,
            ),
            pytest.raises(Exception, match="1 org"),
        ):
            ingest_request_logs([entry("req-b", KEY_B), entry("req-a", KEY_A)])

        assert stored_orgs() == {"req-a": org_a.id}


STARTED = "2026-10-16T10:00:00.123456789Z"


def started_entry(request_id, key_id, timestamp=STARTED, **extra):
    """An entry as the gateway sends it: its ULID request_id and start time."""
    return entry(request_id, key_id, timestamp=timestamp, **extra)


def rows_per_request():
    return Counter(
        AgentccRequestLog._base_manager.values_list("request_id", "organization_id")
    )


@pytest.fixture
def hooks(org_a, org_b):
    return {
        org.id: AgentccWebhook.objects.create(
            organization=org,
            name=f"hook-{org.name}",
            url=f"https://{org.id}.example.com/hook",
            events=["request.completed"],
            is_active=True,
        )
        for org in (org_a, org_b)
    }


@pytest.mark.integration
class TestResentBatch:
    """The flusher resends the whole batch after a 5xx or its 15 s timeout,
    and the first delivery may have stored it already."""

    @pytest.mark.parametrize(
        "timestamp", [STARTED, None], ids=["with-start-time", "no-start-time"]
    )
    @patch("agentcc.services.webhook_delivery.deliver_webhook_events")
    def test_a_resent_batch_is_stored_and_notified_once(
        self, deliver, keys, hooks, org_a, org_b, timestamp
    ):
        batch = [
            started_entry("req-a", KEY_A, timestamp, session_id="session-a"),
            started_entry("req-b", KEY_B, timestamp, session_id="session-b"),
        ]

        assert ingest_request_logs(batch) == 2
        assert ingest_request_logs(batch) == 0

        assert rows_per_request() == {("req-a", org_a.id): 1, ("req-b", org_b.id): 1}
        assert sorted(
            AgentccWebhookEvent.no_workspace_objects.values_list(
                "organization_id", "payload__request_id"
            )
        ) == sorted([(org_a.id, "req-a"), (org_b.id, "req-b")])
        assert deliver.call_count == 2  # once per org, on the first delivery only
        assert AgentccSession.no_workspace_objects.count() == 2

    def test_a_resend_with_new_entries_stores_only_the_new_ones(
        self, keys, org_a, org_b
    ):
        ingest_request_logs([started_entry("req-a1", KEY_A)])

        added = ingest_request_logs(
            [
                started_entry("req-a1", KEY_A),
                started_entry("req-a2", KEY_A),
                started_entry("req-b1", KEY_B),
                started_entry("req-b1", KEY_B),  # twice in one batch
            ]
        )

        assert added == 2
        assert rows_per_request() == {
            ("req-a1", org_a.id): 1,
            ("req-a2", org_a.id): 1,
            ("req-b1", org_b.id): 1,
        }

    def test_the_same_request_id_in_another_org_is_not_a_copy(self, keys, org_a, org_b):
        ingest_request_logs([started_entry("req-same", KEY_A)])

        assert ingest_request_logs([started_entry("req-same", KEY_B)]) == 1

        assert rows_per_request() == {
            ("req-same", org_a.id): 1,
            ("req-same", org_b.id): 1,
        }

    def test_a_dated_batch_does_not_read_the_orgs_undated_rows(self, keys):
        with CaptureQueriesContext(connection) as queries:
            ingest_request_logs([started_entry("req-a", KEY_A)])

        lookups = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and 'FROM "agentcc_request_log"' in query["sql"]
        ]
        assert lookups
        assert not any('"started_at" IS NULL' in sql for sql in lookups)

    def test_another_start_time_is_another_request(self, keys, org_a):
        ingest_request_logs([started_entry("req-a", KEY_A)])

        added = ingest_request_logs(
            [started_entry("req-a", KEY_A, timestamp="2026-10-16T10:00:01Z")]
        )

        assert added == 1
        assert rows_per_request() == {("req-a", org_a.id): 2}


@pytest.mark.integration
@pytest.mark.requires_ee
class TestResentBatchUsage:
    def test_a_resent_batch_is_metered_once(self, keys, org_a, org_b):
        from ee.usage.deployment import DeploymentMode

        emitted = []
        batch = [started_entry("req-a", KEY_A), started_entry("req-b", KEY_B)]
        with (
            patch.object(DeploymentMode, "is_oss", return_value=False),
            patch("ee.usage.services.emitter.emit", side_effect=emitted.append),
        ):
            ingest_request_logs(batch)
            ingest_request_logs(batch)

        assert sorted((event.org_id, event.amount) for event in emitted) == sorted(
            [(str(org_a.id), 1), (str(org_b.id), 1)]
        )


@pytest.mark.integration
class TestSlowWebhook:
    """One org's webhook endpoint that never answers (a tarpit)."""

    BUDGET_S = 1.5

    def test_it_holds_up_no_other_orgs_part_and_a_resend_adds_nothing(
        self, keys, hooks, org_a, org_b
    ):
        tarpit = hooks[org_a.id].url
        calls = []

        def post(url, timeout, **kwargs):
            calls.append(
                {
                    "url": url,
                    "timeout": timeout,
                    "b_stored": AgentccRequestLog._base_manager.filter(
                        organization=org_b, request_id="req-b"
                    ).exists(),
                }
            )
            if url == tarpit:
                time.sleep(timeout)
                raise requests.Timeout("tarpit")
            return MagicMock(status_code=200, text="ok")

        session = MagicMock()
        session.post.side_effect = post
        # Org A's entries first, so org A's slow delivery comes before org B's
        # part of the batch.
        batch = [started_entry(f"req-a{i}", KEY_A) for i in range(8)]
        batch.append(started_entry("req-b", KEY_B))

        with (
            patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
            patch(
                "agentcc.services.webhook_delivery.build_ssrf_safe_session",
                return_value=session,
            ),
            patch(
                "agentcc.services.log_ingestion.WEBHOOK_DELIVERY_BUDGET_S",
                self.BUDGET_S,
            ),
        ):
            started = time.monotonic()
            assert ingest_request_logs(batch) == 9
            elapsed = time.monotonic() - started

            # The delivery budget bounds the whole batch's webhook time.
            assert elapsed < self.BUDGET_S + 2.0
            assert [call["url"] for call in calls] == [tarpit]
            assert calls[0]["timeout"] <= self.BUDGET_S
            assert calls[0]["b_stored"]  # org B was stored before any delivery
            assert sum(rows_per_request().values()) == 9
            assert stored_orgs()["req-b"] == org_b.id

            # The events the budget did not reach wait, untouched.
            events = AgentccWebhookEvent.no_workspace_objects
            assert events.filter(organization=org_a).count() == 8
            assert events.filter(organization=org_a, attempts=1).count() == 1
            assert events.filter(organization=org_b, attempts=0).count() == 1
            assert not events.exclude(status=AgentccWebhookEvent.PENDING).exists()

            # The flusher's resend stores, meters and sends nothing again.
            assert ingest_request_logs(batch) == 0
            assert sum(rows_per_request().values()) == 9
            assert events.count() == 9
            assert len(calls) == 1

            # Org B's next batch delivers its waiting event with the new one.
            ingest_request_logs([started_entry("req-b2", KEY_B)])
            delivered = events.filter(
                organization=org_b, status=AgentccWebhookEvent.DELIVERED
            )
            assert sorted(delivered.values_list("payload__request_id", flat=True)) == [
                "req-b",
                "req-b2",
            ]


@pytest.mark.integration
class TestDeliveryDeadline:
    def _events(self, org, count):
        webhook = AgentccWebhook.objects.create(
            organization=org,
            name="deadline-hook",
            url="https://example.com/deadline",
            events=["request.completed"],
            is_active=True,
        )
        return [
            AgentccWebhookEvent.objects.create(
                organization=org,
                webhook=webhook,
                event_type="request.completed",
                payload={"event": "request.completed", "request_id": f"r{i}"},
                status=AgentccWebhookEvent.PENDING,
            )
            for i in range(count)
        ]

    def test_events_past_the_deadline_stay_pending_and_untouched(self, org_a):
        from agentcc.services import webhook_delivery

        self._events(org_a, 3)
        now = [100.0]
        timeouts = []

        def post(url, timeout, **kwargs):
            timeouts.append(timeout)
            now[0] += 0.8
            return MagicMock(status_code=200, text="ok")

        session = MagicMock()
        session.post.side_effect = post
        with (
            patch.object(
                webhook_delivery, "time", SimpleNamespace(monotonic=lambda: now[0])
            ),
            patch.object(webhook_delivery, "ensure_public_http_url"),
            patch.object(
                webhook_delivery, "build_ssrf_safe_session", return_value=session
            ),
        ):
            result = webhook_delivery.deliver_webhook_events(
                org_id=org_a.id, deadline=102.0
            )

        assert result == {"delivered": 2, "failed": 0, "deferred": 1}
        assert timeouts == [pytest.approx(2.0), pytest.approx(1.2)]
        waiting = AgentccWebhookEvent.no_workspace_objects.exclude(
            status=AgentccWebhookEvent.DELIVERED
        )
        assert list(waiting.values_list("status", "attempts")) == [
            (AgentccWebhookEvent.PENDING, 0)
        ]

    def test_without_a_deadline_each_request_may_take_ten_seconds(self, org_a):
        from agentcc.services import webhook_delivery

        self._events(org_a, 1)
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="ok")
        with (
            patch.object(webhook_delivery, "ensure_public_http_url"),
            patch.object(
                webhook_delivery, "build_ssrf_safe_session", return_value=session
            ),
        ):
            result = webhook_delivery.deliver_webhook_events(org_id=org_a.id)

        assert result == {"delivered": 1, "failed": 0, "deferred": 0}
        assert session.post.call_args.kwargs["timeout"] == 10


@pytest.mark.integration
class TestCallerSetFields:
    def test_an_over_long_user_field_is_stored_truncated_not_dropped(
        self, keys, org_a, org_b
    ):
        # The gateway logs the request body's OpenAI ``user`` field as user_id.
        batch = [
            started_entry("req-a", KEY_A, user_id="u" * 300),
            started_entry("req-b", KEY_B),
        ]

        assert ingest_request_logs(batch) == 2

        assert stored_orgs() == {"req-a": org_a.id, "req-b": org_b.id}
        row = AgentccRequestLog._base_manager.get(request_id="req-a")
        assert row.user_id == "u" * 255
