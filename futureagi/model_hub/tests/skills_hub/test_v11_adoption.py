"""Synthetic retrieval history; no provider or GitHub traffic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import IntegrityError
from django.db.models import QuerySet

from model_hub.models.skills_hub import (
    HubSkillLabel,
    HubSkillLabelEvent,
    HubSkillUsageDaily,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.usage import record_fetch
from model_hub.services.skills_hub.v11.adoption import adoption_summary
from model_hub.services.skills_hub.v11.types import SkillScope

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 6, 18, tzinfo=UTC)
PROMOTED = NOW - timedelta(hours=6)


@pytest.fixture
def adoption(hub_skill, publish_skill, make_skill_files, organization, workspace):
    first = publish_skill(hub_skill).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="Different instructions.")
    ).version
    # Roll back to the LOWER version: number ordering must not imply freshness.
    HubSkillLabel.objects.create(skill=hub_skill, name="production", version=first)
    event = HubSkillLabelEvent.objects.create(
        skill=hub_skill, label="production", from_version=2, to_version=1
    )
    HubSkillLabelEvent.objects.filter(pk=event.pk).update(created_at=PROMOTED)
    return {
        "skill": hub_skill,
        "first": first,
        "second": second,
        "event": event,
        "scope": SkillScope(organization.id, workspace.id),
    }


def observe(context, consumer, version, when, channel="api", day=None):
    return HubSkillUsageDaily.objects.create(
        skill=context["skill"],
        version=context[version],
        day=day or when.date(),
        consumer_key=f"key:{consumer}",
        consumer_label=f"Consumer {consumer}",
        channel=channel,
        count=100,
        last_at=when,
    )


def summary(context, **overrides):
    args = {
        "scope": context["scope"],
        "skill_id": context["skill"].id,
        "label": "production",
        "days": 7,
        "page": 1,
        "page_size": 25,
    }
    args.update(overrides)
    with patch(
        "model_hub.services.skills_hub.v11.adoption.timezone.now", return_value=NOW
    ):
        return adoption_summary(**args)


def states(result):
    return {
        (row["consumer_label"], row["channel"]): row["state"]
        for row in result["results"]
    }


def test_intraday_rollback_mixed_tie_not_seen_and_channel_identity(adoption):
    observe(adoption, "current", "first", PROMOTED + timedelta(minutes=1))
    observe(adoption, "old", "second", PROMOTED + timedelta(minutes=2))
    observe(adoption, "mixed", "first", NOW)
    observe(adoption, "mixed", "second", PROMOTED + timedelta(minutes=1))
    observe(adoption, "prior", "second", PROMOTED - timedelta(seconds=1))
    observe(adoption, "tie", "first", PROMOTED)
    observe(adoption, "current", "second", NOW, channel="mcp")
    result = summary(adoption)
    assert result["target"]["version"] == 1
    assert result["target"]["version_id"] == str(adoption["first"].id)
    assert result["target"]["promotion_event_id"] == str(adoption["event"].id)
    assert result["summary"] == {
        "current_only": 1,
        "other_only": 2,
        "mixed": 1,
        "not_seen": 1,
        "ambiguous": 1,
    }
    assert states(result)[("Consumer mixed", "api")] == "mixed"
    mixed = next(
        row for row in result["results"] if row["consumer_label"] == "Consumer mixed"
    )
    assert {v["version_id"] for v in mixed["versions"]} == {
        str(adoption["first"].id),
        str(adoption["second"].id),
    }
    assert result["count"] == 6  # not 700 fetches, and not a fleet denominator
    assert result["coverage"] == "observed_fetches_only"
    assert result["historical_timestamp_quality"] == "best_effort"
    assert all(
        "consumer_key" not in row and len(row["consumer_id"]) == 64
        for row in result["results"]
    )
    keys = [
        row["consumer_id"]
        for row in result["results"]
        if row["consumer_label"] == "Consumer current"
    ]
    assert keys[0] == keys[1]  # channel grouping does not create a global tracking ID


def test_tie_only_uncertain_when_it_can_change_classification(adoption):
    observe(adoption, "same", "first", PROMOTED, channel="api")
    observe(
        adoption,
        "same",
        "first",
        NOW - timedelta(days=1),
        day=(NOW - timedelta(days=1)).date(),
    )
    assert states(summary(adoption))[("Consumer same", "api")] == "ambiguous"
    # A certain other-version read plus target tie could be other-only or mixed.
    observe(adoption, "same", "second", NOW)
    assert states(summary(adoption))[("Consumer same", "api")] == "ambiguous"


def test_tied_promotion_events_do_not_guess_history_order(adoption):
    event = HubSkillLabelEvent.objects.create(
        skill=adoption["skill"], label="production", from_version=1, to_version=2
    )
    HubSkillLabelEvent.objects.filter(pk=event.pk).update(created_at=PROMOTED)
    observe(adoption, "tie", "first", NOW)
    result = summary(adoption)
    assert result["target"]["promoted_at"] is None
    assert result["summary"]["ambiguous"] == 1


def test_redundant_tie_does_not_override_certain_current_observation(adoption):
    earlier = PROMOTED - timedelta(days=1)
    HubSkillLabelEvent.objects.filter(pk=adoption["event"].pk).update(
        created_at=earlier
    )
    observe(adoption, "same", "first", earlier)
    observe(adoption, "same", "first", NOW)
    assert summary(adoption)["summary"]["current_only"] == 1


def test_calendar_window_truncates_observations_not_promotion(adoption):
    HubSkillLabelEvent.objects.filter(pk=adoption["event"].pk).update(
        created_at=NOW - timedelta(days=20)
    )
    observe(adoption, "outside", "second", NOW - timedelta(days=7))
    observe(adoption, "inside", "first", NOW - timedelta(days=6, hours=17))
    result = summary(adoption)
    assert result["count"] == 1
    assert result["summary"]["current_only"] == 1
    assert result["window"]["start"] == datetime(2026, 10, 31, tzinfo=UTC)
    assert result["window"]["end"] == result["window"]["as_of"] == NOW


def test_missing_history_and_malformed_historical_order_are_ambiguous(adoption):
    observe(adoption, "bad", "first", NOW - timedelta(days=1), day=NOW.date())
    assert summary(adoption)["summary"]["ambiguous"] == 1
    HubSkillLabelEvent.objects.filter(pk=adoption["event"].pk).delete()
    observe(adoption, "good", "first", NOW)
    result = summary(adoption)
    assert result["target"]["promoted_at"] is None
    assert result["summary"]["ambiguous"] == 2


def test_absent_label_and_no_observations_are_not_fleet_claims(adoption):
    assert summary(adoption)["results"] == []
    observe(adoption, "one", "first", NOW)
    result = summary(adoption, label="staging")
    assert result["target"] is None and result["count"] == 0 and result["results"] == []
    assert all(value == 0 for value in result["summary"].values())


def test_scope_archived_and_unknown_skill(
    adoption, hub_other_workspace, hub_other_organization
):
    for overrides in (
        {
            "scope": SkillScope(
                adoption["scope"].organization_id, hub_other_workspace.id
            )
        },
        {
            "scope": SkillScope(
                hub_other_organization.id, adoption["scope"].workspace_id
            )
        },
        {"skill_id": uuid4()},
    ):
        with pytest.raises(SkillsHubError, match="Skill not found") as error:
            summary(adoption, **overrides)
        assert error.value.status == 404
    adoption["skill"].deleted = True
    adoption["skill"].save(update_fields=["deleted"])
    with pytest.raises(SkillsHubError):
        summary(adoption)


@pytest.mark.parametrize(
    "overrides",
    [
        {"days": 8},
        {"days": True},
        {"page": 0},
        {"page_size": 101},
        {"label": "latest"},
        {"label": "../production"},
    ],
)
def test_invalid_options(adoption, overrides):
    with pytest.raises(SkillsHubError) as error:
        summary(adoption, **overrides)
    assert error.value.status == 400


def test_sql_snapshot_and_bounded_page_at_volume(adoption, django_assert_num_queries):
    HubSkillUsageDaily.objects.bulk_create(
        [
            HubSkillUsageDaily(
                skill=adoption["skill"],
                version=adoption["first"],
                day=NOW.date(),
                channel="api",
                consumer_key=f"user:{index}",
                consumer_label=f"User {index}",
                count=1,
                last_at=NOW,
            )
            for index in range(350)
        ]
    )
    with django_assert_num_queries(1):
        result = summary(adoption, page=3, page_size=100)
    assert (
        len(result["results"]) == 100
        and result["count"] == 350
        and result["total_pages"] == 4
    )
    assert result["summary"]["current_only"] == 350
    next_page = summary(adoption, page=4, page_size=100)
    assert len(next_page["results"]) == 50
    assert not (
        {r["consumer_id"] for r in result["results"]}
        & {r["consumer_id"] for r in next_page["results"]}
    )


def test_record_fetch_older_observation_does_not_regress_last_at(adoption):
    observe(adoption, "delayed", "first", NOW)
    with patch(
        "model_hub.services.skills_hub.usage.timezone.now", return_value=PROMOTED
    ):
        record_fetch(
            version=adoption["first"],
            channel="api",
            consumer_key="key:delayed",
            consumer_label="Consumer delayed",
        )
    row = HubSkillUsageDaily.objects.get(consumer_key="key:delayed")
    assert row.last_at == NOW and row.count == 101


def test_record_fetch_create_race_preserves_newer_timestamp(adoption):
    original_create = HubSkillUsageDaily.objects.create

    def racing_create(**kwargs):
        # Simulate another transaction's committed row before the failed insert.
        raise IntegrityError("synthetic unique collision")

    original_update = QuerySet.update
    calls = 0

    def race_update(queryset, **kwargs):
        nonlocal calls
        if queryset.model is HubSkillUsageDaily:
            calls += 1
            if calls == 1:
                original_create(
                    skill=adoption["skill"],
                    version=adoption["first"],
                    day=NOW.date(),
                    channel="api",
                    consumer_key="key:race",
                    consumer_label="Race",
                    count=1,
                    last_at=NOW,
                )
                return 0
        return original_update(queryset, **kwargs)

    with (
        patch(
            "model_hub.services.skills_hub.usage.timezone.now", return_value=PROMOTED
        ),
        patch("django.db.models.query.QuerySet.update", race_update),
        patch.object(HubSkillUsageDaily.objects, "create", side_effect=racing_create),
    ):
        record_fetch(
            version=adoption["first"],
            channel="api",
            consumer_key="key:race",
            consumer_label="Race",
        )
    row = HubSkillUsageDaily.objects.get(consumer_key="key:race")
    assert row.count == 2 and row.last_at == NOW


def test_fetch_accounting_failure_stays_best_effort(adoption, caplog):
    with patch(
        "model_hub.services.skills_hub.usage.scoped_skill",
        side_effect=RuntimeError("synthetic failure"),
    ):
        record_fetch(
            version=adoption["first"],
            channel="api",
            consumer_key="user:synthetic",
            consumer_label="Synthetic",
        )
    assert "Could not record a Skills fetch" in caplog.text
