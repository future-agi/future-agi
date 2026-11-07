"""Atomic daily increments, window aggregation and fail-open telemetry."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.usage import (
    fetches_7d_map,
    record_fetch,
    usage_summary,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_usage_increments_and_aggregates(
    hub_skill, publish_skill, user, make_skill_files
):
    first = publish_skill(hub_skill).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="New release")
    ).version
    consumer = {"consumer_key": f"user:{user.pk}", "consumer_label": "Synthetic author"}
    record_fetch(version=first, channel="api", **consumer)
    record_fetch(version=first, channel="api", **consumer)
    record_fetch(version=second, channel="download", **consumer)
    record_fetch(version=second, channel="mcp", **consumer)
    assert hub_skill.usage_days.count() == 3
    assert hub_skill.usage_days.get(version=first, channel="api").count == 2
    summary = usage_summary(hub_skill, 7)
    assert summary["total"] == 4
    assert summary["by_channel"] == {"api": 2, "download": 1, "mcp": 1}
    assert [(row["version"], row["count"]) for row in summary["by_version"]] == [
        (1, 2),
        (2, 2),
    ]
    assert [(row["version"], row["count"]) for row in summary["by_day"]] == [
        (1, 2),
        (2, 2),
    ]
    assert summary["last_fetched_at"] is not None
    assert sum(row["count"] for row in summary["consumers"]) == 4
    assert summary["consumers"][0]["label"] == "Synthetic author"
    assert fetches_7d_map([hub_skill.pk]) == {hub_skill.pk: 4}
    assert fetches_7d_map([]) == {}


def test_window_includes_today_and_six_prior_utc_days(hub_skill, publish_skill, user):
    version = publish_skill(hub_skill).version
    now = timezone.now()
    for days_ago in [0, 6, 7, 29, 30, 89, 90]:
        hub_skill.usage_days.create(
            version=version,
            day=(now - timedelta(days=days_ago)).date(),
            channel="api",
            consumer_key=f"user:{user.pk}",
            consumer_label="Synthetic author",
            count=1,
            last_at=now - timedelta(days=days_ago),
        )
    assert usage_summary(hub_skill, 7)["total"] == 2
    assert usage_summary(hub_skill, 30)["total"] == 4
    assert usage_summary(hub_skill, 90)["total"] == 6
    assert fetches_7d_map([hub_skill.pk])[hub_skill.pk] == 2
    with pytest.raises(SkillsHubError) as exc:
        usage_summary(hub_skill, 8)
    assert exc.value.code == "invalid_selector"


def test_empty_usage(hub_skill):
    assert usage_summary(hub_skill, 7) == {
        "days": 7,
        "total": 0,
        "by_day": [],
        "by_channel": {"api": 0, "download": 0, "mcp": 0},
        "by_version": [],
        "consumers": [],
        "last_fetched_at": None,
    }
    assert fetches_7d_map([hub_skill.pk]) == {hub_skill.pk: 0}


def test_database_failure_never_raises_or_poison_outer_transaction(
    hub_skill, publish_skill, user, caplog
):
    version = publish_skill(hub_skill).version
    with transaction.atomic():
        with patch.object(
            QuerySet, "update", side_effect=DatabaseError("synthetic database failure")
        ):
            record_fetch(
                version=version,
                channel="api",
                consumer_key=f"user:{user.pk}",
                consumer_label="Synthetic author",
            )
        assert hub_skill.versions.count() == 1
    assert "Could not record a Skills fetch" in caplog.text
    assert not hub_skill.usage_days.exists()


def test_actual_integrity_error_does_not_poison_outer_transaction(
    hub_skill, publish_skill, user, caplog
):
    version = publish_skill(hub_skill).version
    # A database NOT NULL violation exercises savepoint recovery, not just a mock.
    with transaction.atomic():
        record_fetch(
            version=version,
            channel="api",
            consumer_key=f"user:{user.pk}",
            consumer_label=None,
        )
        assert hub_skill.versions.count() == 1
    assert "Could not record a Skills fetch" in caplog.text
    assert not hub_skill.usage_days.exists()


def test_create_race_retries_increment_once(hub_skill, publish_skill, user):
    version = publish_skill(hub_skill).version
    kwargs = {
        "version": version,
        "channel": "api",
        "consumer_key": f"user:{user.pk}",
        "consumer_label": "Synthetic author",
    }
    record_fetch(**kwargs)
    actual_update = QuerySet.update
    updates = []

    def miss_once(queryset, **values):
        updates.append(values)
        return 0 if len(updates) == 1 else actual_update(queryset, **values)

    with (
        patch.object(QuerySet, "update", new=miss_once),
        patch.object(
            HubSkillUsageDaily.objects,
            "create",
            side_effect=IntegrityError("concurrent insert"),
        ),
    ):
        record_fetch(**kwargs)
    assert len(updates) == 2
    assert hub_skill.usage_days.get().count == 2


def test_non_database_failure_never_raises(hub_skill, publish_skill, user, caplog):
    version = publish_skill(hub_skill).version
    record_fetch(
        version=version,
        channel="invalid",
        consumer_key=f"user:{user.pk}",
        consumer_label="Synthetic author",
    )
    assert "Could not record a Skills fetch" in caplog.text
    assert not hub_skill.usage_days.exists()
