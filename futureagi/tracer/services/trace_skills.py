"""Authorized trace projections. Delivery evidence is never proof of use."""

import json
from datetime import UTC, datetime
from uuid import UUID

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.selectors.skills_hub_v12 import resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_reads import (
    cursor_after,
    next_cursor,
    projects_in_scope,
    query_identity,
)
from model_hub.services.skills_hub.runtime_usage import read_runtime_statement
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
)
from tracer.services.clickhouse.v2.query_builders.trace_skills import (
    TraceSkillsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _catalog(principal, ids, kind):
    resources = resources_for_scope(scope=principal.scope, kind=kind)
    valid_ids = {valid for value in ids if (valid := _uuid(value))}
    return {
        str(row.pk): row
        for row in HubSkillVersion.objects.filter(
            skill__in=resources,
            pk__in=valid_ids,
        ).select_related("skill")
    }


def _skill_rows(rows, catalog, project_id):
    groups = {}
    for row in rows:
        version = catalog.get(row["skill_version_id"])
        if version and str(version.skill_id) != row["skill_id"]:
            version = None
        digest = (
            "verified"
            if version and version.digest == row["skill_digest"]
            else "mismatch"
        )
        key = (
            row["skill_id"],
            row["skill_version_id"],
            row["service_name"],
            row["agent_name"],
            row["requested_label"],
            row["requested_version"],
            row["evidence"],
            digest,
        )
        item = groups.setdefault(
            key,
            {
                "skill_id": str(version.skill_id) if version else None,
                "name": version.skill.name if version else "Unknown skill",
                "version_id": str(version.pk) if version else None,
                "version": version.number if version else None,
                "requested_label": row["requested_label"],
                "requested_version": row["requested_version"] or None,
                "agent": row["agent_name"] or row["service_name"],
                "agent_key": [str(project_id), row["service_name"], row["agent_name"]],
                "evidence": row["evidence"],
                "digest_status": digest,
                "loads": 0,
                "span_ids": [],
            },
        )
        item["loads"] += 1
        item["span_ids"].append(row["span_id"])
    return list(groups.values())


def trace_skills_projection(*, principal, workspace, query):
    project = (
        projects_in_scope(principal=principal, workspace=workspace)
        .filter(pk=query["project_id"])
        .first()
    )
    if project is None:
        raise SkillsHubError(
            "project_not_readable", "A requested project cannot be opened.", status=403
        )
    identity = query_identity(
        principal=principal, query=query, projection="trace-skills"
    )
    after = cursor_after(query=query, identity=identity)
    try:
        offset = int(after) if after is not None else 0
        if offset < 0 or offset > 10000:
            raise ValueError
    except ValueError as exc:
        raise SkillsHubError(
            "invalid_runtime_query", "Restart this trace read.", status=400
        ) from exc
    session = RuntimeReadSession(
        V2AnalyticsQueryService(), deadline=ReadDeadline.start(3000)
    )
    if query["projection"] == "cohort":
        builder = TraceSkillsQueryBuilder(
            project_ids=[project.pk], start=query["from"], end=query["to"]
        )
        versions = list(
            HubSkillVersion.objects.filter(
                skill__in=resources_for_scope(scope=principal.scope, kind="skill"),
            ).values_list("skill_id", "id", "digest")[:10001]
        )
        if len(versions) > 10000:
            raise SkillsHubError(
                "runtime_read_too_large", "Too many versions for this read.", status=503
            )
        verified = tuple(
            (str(skill), str(version), digest) for skill, version, digest in versions
        )
        try:
            row = read_runtime_statement(
                session,
                builder.build_summary(
                    filters=query["filters"],
                    verified_identities=verified,
                ),
                max_rows=1,
            )[0]
        except UnsupportedFilterShapeError as exc:
            raise SkillsHubError(
                "invalid_runtime_query",
                "This filter combination is not supported.",
                status=400,
            ) from exc
        return {
            "summary": {
                "traces": row["traces"],
                "incomplete": row["incomplete"],
                "failed_requests": {
                    "count": row["failed_count"],
                    "rate": row["failed_rate"],
                },
                "latency_ms": {"p95": row["latency_p95"]},
                "cost": {"per_trace_mean": row["cost_mean"]},
            },
            "next_cursor": None,
            "truncated": False,
        }
    ids = list(dict.fromkeys(query.get("trace_ids", [query.get("trace_id")])))
    page = (
        ids[offset : offset + query["limit"]] if query["projection"] == "page" else ids
    )
    if not page:
        return {"traces": {}, "next_cursor": None, "truncated": False}
    # Trace identity reads cover all retained history. The builder pushes the
    # bounded trace set into every source, rather than scanning this time range.
    builder = TraceSkillsQueryBuilder(
        project_ids=[project.pk],
        start=datetime(1970, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
    )
    builder.params["trace_ids"] = tuple(page)
    rows = read_runtime_statement(
        session, builder.build_trace_loads(trace_ids=page), max_rows=10000
    )
    failed = read_runtime_statement(
        session, builder.build_trace_loads(trace_ids=page, failed=True), max_rows=10000
    )
    # Successful load MVs also retain ERROR rows with resolved IDs. They must
    # not be presented as delivered bytes or contribute scope coverage.
    failed_ids = {(row["trace_id"], row["span_id"]) for row in failed}
    rows = [row for row in rows if (row["trace_id"], row["span_id"]) not in failed_ids]
    catalog = _catalog(principal, [row["skill_version_id"] for row in rows], "skill")
    entries = {}
    for trace_id in page:
        skills = _skill_rows(
            [row for row in rows if row["trace_id"] == trace_id], catalog, project.pk
        )
        entries[trace_id] = {
            "skills": skills,
            "failed": [
                {
                    "skill_name": row["skill_name"],
                    "error_type": row["error_type"],
                    "span_id": row["span_id"],
                }
                for row in failed
                if row["trace_id"] == trace_id
            ],
            "more": max(0, len(skills) - 8),
        }
    if query["projection"] == "page":
        for entry in entries.values():
            entry["skills"] = entry["skills"][:8]
        return {
            "traces": entries,
            "next_cursor": next_cursor(identity=identity, after=str(offset + len(page)))
            if offset + len(page) < len(ids)
            else None,
            "truncated": False,
        }
    spans = read_runtime_statement(session, builder.build_spans(), max_rows=10000)
    context_spans = [
        row
        for row in spans
        if row["name"] == "context.load" and row["status"] != "ERROR"
    ]
    contexts_catalog = _catalog(
        principal,
        [row["attrs"].get("fi.context.version_id") for row in context_spans],
        "agent_context",
    )
    contexts, linked_by_context = {}, {}
    for row in context_spans:
        attrs = row["attrs"]
        context = contexts_catalog.get(attrs.get("fi.context.version_id"))
        if (
            not context
            or str(context.skill_id) != attrs.get("fi.context.id")
            or context.digest != attrs.get("fi.context.digest")
        ):
            continue
        contexts[str(context.pk)] = {
            "context_id": str(context.skill_id),
            "name": context.skill.name,
            "version": context.number,
        }
        try:
            attributes = json.loads(row["attributes"])
            values = attributes.get("fi.context.linked_skill_version_ids", [])
            if isinstance(values, list):
                linked_by_context.setdefault(str(context.pk), set()).update(
                    value for value in values if isinstance(value, str)
                )
        except (ValueError, TypeError, AttributeError):
            pass
    linked_catalog = _catalog(
        principal, set().union(*linked_by_context.values()), "skill"
    )
    entry = entries[page[0]]
    # Compare resource identity first, then versions. A failed read or a span
    # with mismatched bytes must not establish that the skill was loaded.
    loaded_by_skill = {}
    loaded_spans = set()
    for item in entry["skills"]:
        if item["digest_status"] == "verified":
            loaded_by_skill.setdefault(item["skill_id"], set()).add(item["version"])
            loaded_spans.update(item["span_ids"])
    linked_names, differences = set(), []
    for context_key, linked_ids in sorted(linked_by_context.items()):
        for linked_id in sorted(linked_ids):
            version = linked_catalog.get(linked_id)
            if version is None:
                continue
            observed = loaded_by_skill.get(str(version.skill_id))
            if not observed:
                linked_names.add(version.skill.name)
            elif version.number not in observed:
                differences.append(
                    {
                        "context_name": contexts[context_key]["name"],
                        "context_version": contexts[context_key]["version"],
                        "skill_name": version.skill.name,
                        "linked_version": version.number,
                        "loaded_versions": sorted(observed),
                    }
                )
    first_load = min(
        (row["start_time"] for row in rows if row["span_id"] in loaded_spans),
        default=None,
    )
    stamped = sum(bool(row["version_ids"]) for row in spans)
    collections = {
        "skills": entry["skills"],
        "failed": entry["failed"],
        "contexts": list(contexts.values()),
        "linked_not_loaded": sorted(linked_names),
        "linked_version_differences": differences,
    }
    more = any(len(items) > offset + query["limit"] for items in collections.values())
    return {
        **{
            key: items[offset : offset + query["limit"]]
            for key, items in collections.items()
        },
        "coverage": {
            "spans": len(spans),
            "stamped_spans": stamped,
            "rate": stamped / len(spans) if spans else None,
            "span_ids_before_first_load": [
                row["id"]
                for row in spans
                if first_load
                and not row["version_ids"]
                and row["start_time"] < first_load
            ],
        },
        "next_cursor": next_cursor(
            identity=identity, after=str(offset + query["limit"])
        )
        if more
        else None,
        "truncated": False,
    }
