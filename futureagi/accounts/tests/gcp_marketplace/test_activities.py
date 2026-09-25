"""The Pub/Sub consumer activity and its schedules.

The subscriber client is faked so the ack decisions can be asserted per
message: handled and unparseable messages are acked, a failing one is left
for redelivery until the poison window passes.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from accounts.tests.gcp_marketplace.support import ENTITLEMENT_ID, event
from tfc.temporal.marketplace.activities import (
    ACK_DEADLINE_SECONDS,
    POISON_AFTER,
    _clear_failures,
    _drain_sync,
    _failure_key,
    _is_configured,
    _is_poison,
    drain_gcp_marketplace_events_activity,
)
from tfc.temporal.marketplace.types import DrainResult

pytestmark = [pytest.mark.unit]

CONFIGURED = override_settings(
    GCP_MARKETPLACE_PROJECT_ID="futureagiprimary",
    GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION="marketplace-events",
)
UNCONFIGURED = override_settings(
    GCP_MARKETPLACE_PROJECT_ID="", GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION=""
)
SUBSCRIPTION_PATH = "projects/futureagiprimary/subscriptions/marketplace-events"


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def message(ack_id: str, body) -> SimpleNamespace:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return SimpleNamespace(ack_id=ack_id, message=SimpleNamespace(data=data))


@pytest.fixture
def subscriber():
    fake = MagicMock()
    fake.subscription_path.return_value = SUBSCRIPTION_PATH
    fake.pull.return_value = SimpleNamespace(received_messages=[])
    with patch("google.cloud.pubsub_v1.SubscriberClient", return_value=fake):
        yield fake


@pytest.fixture
def process_event():
    with patch("accounts.gcp_marketplace_events.process_event") as mocked:
        yield mocked


def creation(event_id="CREATE_ENTITLEMENT-1"):
    return event(
        "ENTITLEMENT_CREATION_REQUESTED",
        entitlement_id=ENTITLEMENT_ID,
        event_id=event_id,
    )


class TestConfiguration:
    @CONFIGURED
    def test_configured_when_project_and_subscription_are_set(self):
        assert _is_configured() is True

    @UNCONFIGURED
    def test_not_configured_without_them(self):
        assert _is_configured() is False

    @override_settings(
        GCP_MARKETPLACE_PROJECT_ID="p", GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION=""
    )
    def test_not_configured_with_only_a_project(self):
        assert _is_configured() is False

    async def test_activity_is_a_no_op_outside_cloud(self, settings):
        settings.GCP_MARKETPLACE_PROJECT_ID = ""
        settings.GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION = ""

        assert await drain_gcp_marketplace_events_activity() == DrainResult(
            events_processed=0, had_events=False
        )


class TestPoisonGuard:
    def test_failure_key_needs_an_event_id(self):
        assert _failure_key({"eventType": "ENTITLEMENT_ACTIVE"}) is None
        assert _failure_key(creation("E-1")).endswith("E-1")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Google reuses one eventId across the events of a purchase, so one "
            "failing handler counts against the others sharing the id. Fixed on "
            "fix/th-7731-marketplace-event-dedupe."
        ),
    )
    def test_failure_key_tells_event_types_apart(self):
        payload = creation("SHARED")
        sibling = event(
            "ENTITLEMENT_OFFER_ACCEPTED",
            entitlement_id=ENTITLEMENT_ID,
            event_id="SHARED",
        )

        assert _failure_key(payload) != _failure_key(sibling)

    def test_first_failures_are_retried(self):
        payload = creation()

        assert _is_poison(payload) is False
        assert _is_poison(payload) is False
        assert cache.get(_failure_key(payload))["count"] == 2

    def test_dropped_after_failing_for_the_whole_window(self):
        payload = creation()
        first = timezone.now() - POISON_AFTER - timedelta(minutes=1)
        cache.set(_failure_key(payload), {"first": first.isoformat(), "count": 40})

        assert _is_poison(payload) is True
        assert cache.get(_failure_key(payload)) is None

    def test_success_clears_the_record(self):
        payload = creation()
        _is_poison(payload)

        _clear_failures(payload)

        assert cache.get(_failure_key(payload)) is None

    def test_message_without_an_id_is_dropped_at_once(self):
        assert _is_poison({"eventType": "ENTITLEMENT_ACTIVE"}) is True


@pytest.mark.django_db
class TestDrain:
    @pytest.fixture(autouse=True)
    def configured(self, settings):
        settings.GCP_MARKETPLACE_PROJECT_ID = "futureagiprimary"
        settings.GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION = "marketplace-events"

    def test_empty_pull_acks_nothing(self, subscriber, process_event):
        assert _drain_sync(MagicMock()) == {"events_processed": 0, "had_events": False}

        subscriber.acknowledge.assert_not_called()
        process_event.assert_not_called()

    def test_acks_handled_and_unparseable_and_keeps_the_failed_one(
        self, subscriber, process_event
    ):
        subscriber.pull.return_value = SimpleNamespace(
            received_messages=[
                message("ack-ok", creation("E-ok")),
                message("ack-bad", creation("E-bad")),
                message("ack-garbage", b"not json"),
            ]
        )
        process_event.side_effect = lambda payload: (
            (_ for _ in ()).throw(RuntimeError("procurement down"))
            if payload["eventId"] == "E-bad"
            else True
        )
        heartbeat = MagicMock()

        result = _drain_sync(heartbeat)

        assert result == {"events_processed": 2, "had_events": True}
        subscriber.modify_ack_deadline.assert_called_once_with(
            request={
                "subscription": SUBSCRIPTION_PATH,
                "ack_ids": ["ack-ok", "ack-bad", "ack-garbage"],
                "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
            },
            timeout=30,
        )
        subscriber.acknowledge.assert_called_once()
        assert subscriber.acknowledge.call_args.kwargs["request"]["ack_ids"] == [
            "ack-ok",
            "ack-garbage",
        ]
        assert heartbeat.call_count == 3
        assert cache.get(_failure_key(creation("E-bad")))["count"] == 1
        assert cache.get(_failure_key(creation("E-ok"))) is None

    def test_a_message_failing_for_a_day_is_finally_acked(
        self, subscriber, process_event
    ):
        payload = creation("E-poison")
        first = timezone.now() - POISON_AFTER - timedelta(minutes=1)
        cache.set(_failure_key(payload), {"first": first.isoformat(), "count": 280})
        subscriber.pull.return_value = SimpleNamespace(
            received_messages=[message("ack-poison", payload)]
        )
        process_event.side_effect = RuntimeError("still broken")

        result = _drain_sync(MagicMock())

        assert result == {"events_processed": 1, "had_events": True}
        assert subscriber.acknowledge.call_args.kwargs["request"]["ack_ids"] == [
            "ack-poison"
        ]


class TestSchedules:
    def test_four_marketplace_schedules_on_the_default_queue(self):
        from tfc.temporal.schedules.marketplace import MARKETPLACE_SCHEDULES

        by_id = {config.schedule_id: config for config in MARKETPLACE_SCHEDULES}
        assert set(by_id) == {
            "gcp-marketplace-consumer",
            "gcp-marketplace-usage-report",
            "gcp-marketplace-plan-reconcile",
            "gcp-marketplace-usage-reconcile",
        }
        assert {config.queue for config in MARKETPLACE_SCHEDULES} == {"default"}
        assert by_id["gcp-marketplace-consumer"].interval_seconds == 300

    def test_marketplace_schedules_are_part_of_the_registered_set(self):
        from tfc.temporal.schedules import ALL_SCHEDULES
        from tfc.temporal.schedules.marketplace import MARKETPLACE_SCHEDULES

        registered = {config.schedule_id for config in ALL_SCHEDULES}
        assert {c.schedule_id for c in MARKETPLACE_SCHEDULES} <= registered
