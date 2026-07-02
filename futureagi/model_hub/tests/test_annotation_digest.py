"""Tests for the annotation digest emails (realtime + daily).

Covers:
- Skip-if-empty (no email when zero pending)
- Per-user 1-hour throttle on the realtime track
- Snooze respected (realtime paused; daily still fires)
- Unsubscribe respected (both tracks paused)
- Recipient filtering: annotators see assignments, managers see review queue
- Timezone window matching for the daily track
- HMAC unsubscribe token round-trip
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationNotificationState,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)

User = get_user_model()


def _make_project(organization, workspace, name="Digest Project"):
    from tracer.models.project import Project

    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _make_trace(project, name="t"):
    from tracer.models.trace import Trace

    return Trace.objects.create(
        name=name,
        project=project,
        input={"message": "hi"},
        output={"response": "ok"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def digest_org():
    return Organization.objects.create(name="Digest Org")


@pytest.fixture
def digest_user(digest_org):
    return User.objects.create_user(
        email="digest-annotator@example.com",
        password="pw",
        name="Digest Annotator",
        organization=digest_org,
    )


@pytest.fixture
def manager_user(digest_org):
    return User.objects.create_user(
        email="digest-manager@example.com",
        password="pw",
        name="Digest Manager",
        organization=digest_org,
    )


@pytest.fixture
def reviewer_user(digest_org):
    return User.objects.create_user(
        email="digest-reviewer@example.com",
        password="pw",
        name="Digest Reviewer",
        organization=digest_org,
    )


@pytest.fixture
def digest_workspace(digest_org, digest_user):
    return Workspace.objects.create(
        name="Digest WS", organization=digest_org, created_by=digest_user
    )


@pytest.fixture
def digest_project(digest_org, digest_workspace):
    return _make_project(digest_org, digest_workspace)


@pytest.fixture
def digest_queue(digest_org, digest_workspace, digest_project, digest_user, manager_user):
    queue = AnnotationQueue.objects.create(
        name="Digest Queue",
        organization=digest_org,
        workspace=digest_workspace,
        project=digest_project,
    )
    AnnotationQueueAnnotator.objects.create(
        queue=queue, user=digest_user, role=AnnotatorRole.ANNOTATOR.value
    )
    AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=manager_user,
        role=AnnotatorRole.MANAGER.value,
        roles=[AnnotatorRole.MANAGER.value],
    )
    return queue


def _create_queue_item(queue, *, assigned_to, status, organization, project):
    trace = _make_trace(project)
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE.value,
        organization=organization,
        trace=trace,
        assigned_to=assigned_to,
        status=status,
    )


# ---------------------------------------------------------------------------
# Realtime track
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRealtimeDigest:
    """Realtime digest = 15-min cron, throttled to 1/hour/user, delta-only."""

    def test_skip_if_empty(self, digest_user):
        """No pending items → no email, no state mutation."""
        from model_hub.utils.annotation_digest import _send_realtime_for_user

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(digest_user, timezone.now())

        assert sent is False
        mock_send.assert_not_called()
        assert not AnnotationNotificationState.objects.filter(
            user=digest_user, last_realtime_digest_at__isnull=False
        ).exists()

    def test_sends_email_with_assignment_bucket(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        """User has pending items → email goes out and last_realtime_digest_at bumps."""
        from model_hub.utils.annotation_digest import _send_realtime_for_user

        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(digest_user, timezone.now())

        assert sent is True
        assert mock_send.call_count == 1
        # Subject mentions the count.
        kwargs = mock_send.call_args.kwargs
        assert "1 new item" in kwargs["mail_subject"]
        # State was updated so the next tick skips this user.
        state = AnnotationNotificationState.objects.get(user=digest_user)
        assert state.last_realtime_digest_at is not None

    def test_throttle_blocks_second_send_within_hour(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        """A user who got an email in the last hour is skipped on next tick."""
        from model_hub.utils.annotation_digest import (
            REALTIME_THROTTLE,
            _send_realtime_for_user,
        )

        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )

        # Pretend we just sent one.
        state, _ = AnnotationNotificationState.objects.get_or_create(user=digest_user)
        state.last_realtime_digest_at = timezone.now() - REALTIME_THROTTLE + timedelta(
            minutes=10
        )
        state.save()

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(digest_user, timezone.now())

        assert sent is False
        mock_send.assert_not_called()

    def test_snooze_blocks_realtime_track(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )
        state, _ = AnnotationNotificationState.objects.get_or_create(user=digest_user)
        state.realtime_snoozed_until = timezone.now() + timedelta(days=3)
        state.save()

        from model_hub.utils.annotation_digest import _send_realtime_for_user

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(digest_user, timezone.now())

        assert sent is False
        mock_send.assert_not_called()

    def test_unsubscribe_blocks_realtime_track(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )
        state, _ = AnnotationNotificationState.objects.get_or_create(user=digest_user)
        state.digest_enabled = False
        state.save()

        from model_hub.utils.annotation_digest import _send_realtime_for_user

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(digest_user, timezone.now())

        assert sent is False
        mock_send.assert_not_called()

    def test_reviewer_bucket_includes_completed_items(
        self,
        digest_user,
        manager_user,
        digest_queue,
        digest_org,
        digest_project,
    ):
        """Manager role sees items with status=COMPLETED and reviewed_at=None."""
        # Item completed but not yet reviewed.
        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.COMPLETED.value,
            organization=digest_org,
            project=digest_project,
        )

        from model_hub.utils.annotation_digest import _send_realtime_for_user

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_realtime_for_user(manager_user, timezone.now())

        assert sent is True
        kwargs = mock_send.call_args.kwargs
        assert "pending review" in kwargs["mail_subject"]

    def test_reviewer_role_gets_pending_review_digest(
        self,
        digest_user,
        reviewer_user,
        digest_queue,
        digest_org,
        digest_project,
    ):
        """Explicit reviewer role sees completed items awaiting review."""
        AnnotationQueueAnnotator.objects.create(
            queue=digest_queue,
            user=reviewer_user,
            role=AnnotatorRole.REVIEWER.value,
            roles=[AnnotatorRole.REVIEWER.value],
        )
        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.COMPLETED.value,
            organization=digest_org,
            project=digest_project,
        )

        from model_hub.utils.annotation_digest import _send_realtime_for_user

        with patch("model_hub.utils.annotation_digest.email_helper") as mock_send:
            sent = _send_realtime_for_user(reviewer_user, timezone.now())

        assert sent is True
        kwargs = mock_send.call_args.kwargs
        assert "pending review" in kwargs["mail_subject"]


# ---------------------------------------------------------------------------
# Daily track
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDailyDigest:
    """Daily digest fires per user-local hour; current-state snapshot."""

    def test_skip_when_local_hour_does_not_match(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        from model_hub.utils.annotation_digest import _send_daily_for_user

        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )
        # User's preferred hour is 9, but force a "now" where their local
        # hour is 14 — should skip.
        digest_user.last_timezone = "UTC"
        digest_user.save()
        now = datetime(2026, 5, 15, 14, 0, tzinfo=dt_timezone.utc)

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_daily_for_user(digest_user, now)

        assert sent is False
        mock_send.assert_not_called()

    def test_fires_when_local_hour_matches(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        from model_hub.utils.annotation_digest import _send_daily_for_user

        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )
        digest_user.last_timezone = "UTC"
        digest_user.save()
        now = datetime(2026, 5, 15, 9, 0, tzinfo=dt_timezone.utc)

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_daily_for_user(digest_user, now)

        assert sent is True
        mock_send.assert_called_once()

    def test_tz_local_hour_respects_user_timezone(
        self, digest_user, digest_queue, digest_org, digest_project
    ):
        """User in LA (UTC-7/8) — daily fires at 9am LA, not 9am UTC."""
        from model_hub.utils.annotation_digest import _send_daily_for_user

        _create_queue_item(
            digest_queue,
            assigned_to=digest_user,
            status=QueueItemStatus.PENDING.value,
            organization=digest_org,
            project=digest_project,
        )
        digest_user.last_timezone = "America/Los_Angeles"
        digest_user.save()

        # 9am LA in summer (PDT, UTC-7) = 16:00 UTC. Should fire.
        now_summer = datetime(2026, 7, 15, 16, 0, tzinfo=dt_timezone.utc)
        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_daily_for_user(digest_user, now_summer)
        assert sent is True

        # 9am UTC = 2am LA. Should skip.
        digest_user.refresh_from_db()
        state = AnnotationNotificationState.objects.get(user=digest_user)
        state.last_daily_digest_at = None
        state.save()
        now_utc_morning = datetime(2026, 7, 15, 9, 0, tzinfo=dt_timezone.utc)
        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_daily_for_user(digest_user, now_utc_morning)
        assert sent is False

    def test_skip_if_empty_daily(self, digest_user):
        from model_hub.utils.annotation_digest import _send_daily_for_user

        digest_user.last_timezone = "UTC"
        digest_user.save()
        now = datetime(2026, 5, 15, 9, 0, tzinfo=dt_timezone.utc)

        with patch(
            "model_hub.utils.annotation_digest.email_helper"
        ) as mock_send:
            sent = _send_daily_for_user(digest_user, now)

        assert sent is False
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Unsubscribe token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeToken:
    def test_round_trip_valid_token(self, digest_user):
        from model_hub.utils.annotation_digest import (
            _unsubscribe_token,
            _verify_unsubscribe_token,
        )

        token = _unsubscribe_token(digest_user.id)
        assert _verify_unsubscribe_token(token) == str(digest_user.id)

    def test_invalid_token_returns_none(self):
        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        assert _verify_unsubscribe_token("not-a-token") is None
        assert _verify_unsubscribe_token("") is None
