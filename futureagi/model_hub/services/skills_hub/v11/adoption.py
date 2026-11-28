"""Observed version retrieval, not fleet inventory or proof of execution."""

from __future__ import annotations

import json
import re
from datetime import UTC, timedelta
from uuid import UUID

from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import (
    HubSkill,
    HubSkillLabel,
    HubSkillLabelEvent,
    HubSkillUsageDaily,
    HubSkillVersion,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import LABEL_RE, MAX_PAGE_SIZE
from model_hub.services.skills_hub.v11.types import SkillScope


def adoption_summary(
    *,
    scope: SkillScope,
    skill_id: UUID,
    label: str,
    days: int,
    page: int,
    page_size: int,
) -> dict:
    """Read pointer, promotion, aggregates and page in one Postgres snapshot.

    Callers authenticate scope access first (this read-only signature carries no
    principal). A single statement avoids READ COMMITTED tearing between pointer,
    audit event, summary and paginated daily observations, even inside a caller's
    existing transaction. Only the requested page is materialized in Python.
    """
    if (
        not isinstance(label, str)
        or not re.fullmatch(LABEL_RE, label)
        or label == "latest"
        or type(days) is not int
        or days not in (7, 30, 90)
        or type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        raise SkillsHubError(
            "invalid_selector",
            "Choose a label, a 7/30/90-day window and valid pagination.",
            status=400,
        )
    as_of = timezone.now().astimezone(UTC)
    start = as_of.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days - 1
    )
    tables = {
        name: connection.ops.quote_name(model._meta.db_table)
        for name, model in (
            ("skill", HubSkill),
            ("workspace", Workspace),
            ("label", HubSkillLabel),
            ("event", HubSkillLabelEvent),
            ("usage", HubSkillUsageDaily),
            ("version", HubSkillVersion),
        )
    }
    # Interpolation contains only quoted, server-owned table names. All request
    # values are bound parameters. Counts are observed identities AND channels.
    sql = f"""
        WITH scoped AS (
            SELECT s.id FROM {tables["skill"]} s
            JOIN {tables["workspace"]} w ON w.id = s.workspace_id
            WHERE s.id = %(skill_id)s AND s.organization_id = %(org)s
              AND s.workspace_id = %(workspace)s AND w.organization_id = %(org)s
              AND NOT s.deleted
        ), target AS (
            SELECT v.id AS version_id, v.number AS version, l.name AS label,
                   CASE WHEN e.to_version = v.number AND e.time_peers = 1 THEN e.id END AS promotion_event_id,
                   CASE WHEN e.to_version = v.number AND e.time_peers = 1 THEN e.created_at END AS promoted_at
            FROM scoped s JOIN {tables["label"]} l ON l.skill_id = s.id
            JOIN {tables["version"]} v ON v.id = l.version_id AND v.skill_id = s.id
            LEFT JOIN LATERAL (
                SELECT id, to_version, created_at,
                       COUNT(*) OVER (PARTITION BY created_at) AS time_peers
                FROM {tables["event"]}
                WHERE skill_id = s.id AND label = l.name
                ORDER BY created_at DESC, id DESC LIMIT 1
            ) e ON TRUE
            WHERE l.name = %(label)s
        ), observations AS (
            SELECT u.*, v.number,
                   t.version_id AS target_id, t.promoted_at,
                   (t.promoted_at IS NULL OR u.last_at = t.promoted_at
                    OR (u.last_at AT TIME ZONE 'UTC')::date <> u.day
                    OR u.last_at < %(start)s OR u.last_at > %(as_of)s) AS uncertain
            FROM scoped s JOIN {tables["usage"]} u ON u.skill_id = s.id
            JOIN {tables["version"]} v ON v.id = u.version_id AND v.skill_id = s.id
            CROSS JOIN target t
            WHERE u.day BETWEEN %(first_day)s AND %(last_day)s
        ), identities AS (
            SELECT consumer_key, channel, MAX(last_at) AS last_at,
                   (ARRAY_AGG(consumer_label ORDER BY last_at DESC, id DESC))[1] AS consumer_label,
                   BOOL_OR(NOT uncertain AND last_at > promoted_at AND version_id = target_id) AS current_seen,
                   BOOL_OR(NOT uncertain AND last_at > promoted_at AND version_id <> target_id) AS other_seen,
                   BOOL_OR(uncertain AND version_id = target_id) AS current_uncertain,
                   BOOL_OR(uncertain AND version_id <> target_id) AS other_uncertain
            FROM observations GROUP BY consumer_key, channel
        ), classified AS (
            SELECT *, CASE
                WHEN current_seen AND other_seen THEN 'mixed'
                WHEN (current_uncertain AND NOT current_seen) OR (other_uncertain AND NOT other_seen) THEN 'ambiguous'
                WHEN current_seen THEN 'current_only'
                WHEN other_seen THEN 'other_only'
                ELSE 'not_seen' END AS state
            FROM identities
        ), selected AS (
            SELECT * FROM classified ORDER BY last_at DESC, consumer_key, channel
            LIMIT %(limit)s OFFSET %(offset)s
        ), version_rows AS (
            SELECT o.consumer_key, o.channel, o.version_id, o.number, MAX(o.last_at) AS last_at
            FROM observations o JOIN selected p USING (consumer_key, channel)
            GROUP BY o.consumer_key, o.channel, o.version_id, o.number
        ), version_lists AS (
            SELECT consumer_key, channel,
                   JSONB_AGG(JSONB_BUILD_OBJECT('version', number, 'version_id', version_id, 'last_at', last_at)
                             ORDER BY number, version_id) AS versions
            FROM version_rows GROUP BY consumer_key, channel
        )
        SELECT EXISTS(SELECT 1 FROM scoped), JSONB_BUILD_OBJECT(
            'target', (SELECT TO_JSONB(t) FROM target t),
            'summary', (SELECT JSONB_BUILD_OBJECT(
                'current_only', COUNT(*) FILTER (WHERE state = 'current_only'),
                'other_only', COUNT(*) FILTER (WHERE state = 'other_only'),
                'mixed', COUNT(*) FILTER (WHERE state = 'mixed'),
                'not_seen', COUNT(*) FILTER (WHERE state = 'not_seen'),
                'ambiguous', COUNT(*) FILTER (WHERE state = 'ambiguous')
            ) FROM classified),
            'count', (SELECT COUNT(*) FROM classified),
            'results', COALESCE((
                SELECT JSONB_AGG(JSONB_BUILD_OBJECT(
                    'consumer_key', p.consumer_key, 'consumer_label', p.consumer_label,
                    'channel', p.channel, 'state', p.state, 'versions', v.versions, 'last_at', p.last_at
                ) ORDER BY p.last_at DESC, p.consumer_key, p.channel)
                FROM selected p JOIN version_lists v USING (consumer_key, channel)
            ), '[]'::jsonb)
        )
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            {
                "skill_id": skill_id,
                "org": scope.organization_id,
                "workspace": scope.workspace_id,
                "label": label,
                "start": start,
                "as_of": as_of,
                "first_day": start.date(),
                "last_day": as_of.date(),
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        exists, payload = cursor.fetchone()
    if not exists:
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    # Django's PostgreSQL JSON decoder is applied by ORM fields, not raw cursors.
    result: dict = json.loads(payload) if isinstance(payload, str) else payload
    for consumer in result["results"]:
        key = consumer.pop("consumer_key")
        consumer["consumer_id"] = salted_hmac(
            "skills.adoption.consumer.v1",
            f"{scope.organization_id}/{scope.workspace_id}/{key}",
            algorithm="sha256",
        ).hexdigest()
    result.update(
        {
            "window": {"start": start, "end": as_of, "as_of": as_of},
            "coverage": "observed_fetches_only",
            "historical_timestamp_quality": "best_effort",
            "page": page,
            "page_size": page_size,
            "total_pages": (result["count"] + page_size - 1) // page_size,
        }
    )
    return result
