"""Authorized shared runtime reads and query-bound cursor pagination."""

import hashlib
import json
from uuid import UUID

from django.core import signing
from django.db.models import Q

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.selectors.skills_hub_v12 import get_resource, resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import read_runtime_statement
from tracer.models.project import Project
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

# Bound the parameterized identity catalogue as well as returned picker rows.
# A thousand UUID/digest tuples fit below the normal CH query-size budget.
CATALOG_LIMIT = 1000
CURSOR_SALT = "skills-runtime-read-v1"
MAX_CURSOR_LENGTH = 2048


def projects_in_scope(*, principal, workspace):
    """Same workspace compatibility rule as Observe; no cross-tenant fallback.

    SkillsPermission has already required read access to this workspace. Observe
    projects inherit that workspace access; there is no project-specific ACL.
    """
    scope = Q(workspace_id=workspace.pk)
    if workspace.is_default:
        scope |= Q(
            workspace__is_default=True,
            workspace__organization_id=principal.scope.organization_id,
        ) | Q(workspace__isnull=True)
    return Project.no_workspace_objects.filter(
        scope,
        organization_id=principal.scope.organization_id,
        deleted=False,
        trace_type="observe",
    ).order_by("id")


def query_identity(*, principal, query, projection):
    payload = {
        "organization": str(principal.scope.organization_id),
        "workspace": str(principal.scope.workspace_id),
        "projection": projection,
        "query": {key: value for key, value in query.items() if key != "cursor"},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def cursor_after(*, query, identity):
    if not query.get("cursor"):
        return None
    try:
        payload = signing.loads(query["cursor"], salt=CURSOR_SALT, max_age=3600)
        if payload["query"] != identity or not isinstance(payload["after"], str):
            raise ValueError("Cursor query changed")
        return payload["after"]
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise SkillsHubError(
            "invalid_runtime_query",
            "This cursor is invalid or expired. Restart the read.",
            status=400,
        ) from exc


def next_cursor(*, identity, after):
    cursor = signing.dumps(
        {"query": identity, "after": after}, salt=CURSOR_SALT, compress=True
    )
    if len(cursor) > MAX_CURSOR_LENGTH:
        # Telemetry strings are not trusted to obey SDK limits. Never return a
        # cursor that the request serializer would reject on the next page.
        raise SkillsHubError(
            "runtime_read_too_large",
            "A filter value exceeds this read's limit.",
            status=503,
        )
    return cursor


def filter_values(*, principal, workspace, query):
    project = (
        projects_in_scope(principal=principal, workspace=workspace)
        .filter(pk=query["project_id"])
        .first()
    )
    if project is None:
        raise SkillsHubError(
            "project_not_readable", "A requested project cannot be opened.", status=403
        )
    resource = None
    if query.get("skill_id"):
        resource = get_resource(
            principal=principal, resource_id=query["skill_id"], kind="skill"
        )
    identity = query_identity(
        principal=principal, query=query, projection="filter-values"
    )
    after = cursor_after(query=query, identity=identity)
    field, failed = query["field"], query["load_result"] == "failed"
    resources = resources_for_scope(scope=principal.scope, kind="skill")
    if resource is not None:
        resources = resources.filter(pk=resource.pk)
    version_rows = HubSkillVersion.objects.filter(skill__in=resources)
    exact_version = None
    if field == "skill.version_id":
        try:
            candidate = UUID(query["q"])
            if str(candidate) == query["q"].lower():
                exact_version = candidate
        except ValueError:
            pass  # Ordinary picker searches match names, version numbers, and labels.
    if exact_version is not None:
        version_rows = version_rows.filter(pk=exact_version)
    versions = (
        list(
            version_rows.select_related("skill")
            .prefetch_related("labels")
            .order_by("id")[: CATALOG_LIMIT + 1]
        )
        if not failed
        else []
    )
    if len(versions) > CATALOG_LIMIT:
        raise SkillsHubError(
            "runtime_read_too_large", "Choose a skill to narrow this read.", status=503
        )
    values: dict[str, dict] = {}
    verified = []
    search = query["q"].casefold()
    for version in versions:
        skill = version.skill
        labels = sorted(label.name for label in version.labels.all())
        if field == "skill.name":
            if search not in skill.name.casefold():
                continue
            item = values.setdefault(
                str(skill.pk),
                {
                    "value": str(skill.pk),
                    "label": skill.name,
                    "skill_id": str(skill.pk),
                    "name": skill.name,
                },
            )
            if "production" in labels:
                item["production_version"] = version.number
        elif field == "skill.version_id":
            label = f"{skill.name} v{version.number}"
            if (
                exact_version is None
                and search
                and not any(search in value.casefold() for value in [label, *labels])
            ):
                continue
            values[str(version.pk)] = {
                "value": str(version.pk),
                "label": label,
                "version_id": str(version.pk),
                "version": version.number,
                "skill_name": skill.name,
                "labels_now": labels,
            }
        verified.append((str(skill.pk), str(version.pk), version.digest))
    contexts = {}
    if field == "skill.context_id":
        rows = resources_for_scope(
            scope=principal.scope, kind="agent_context"
        ).order_by("id")[: CATALOG_LIMIT + 1]
        contexts = {str(row.pk): row.name for row in rows}
        if len(contexts) > CATALOG_LIMIT:
            raise SkillsHubError(
                "runtime_read_too_large", "Too many contexts for this read.", status=503
            )
        contexts = {
            key: name for key, name in contexts.items() if search in name.casefold()
        }
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project.pk], start=query["from"], end=query["to"]
    )
    session = RuntimeReadSession(
        V2AnalyticsQueryService(), deadline=ReadDeadline.start(3000)
    )
    rows = read_runtime_statement(
        session,
        builder.build_filter_values(
            field=field,
            verified_identities=verified,
            context_ids=contexts,
            failed=failed,
            skill_name=resource.name if resource else None,
            search=""
            if (not failed and field in {"skill.name", "skill.version_id"})
            or field == "skill.context_id"
            else query["q"],
            after=after,
            limit=query["limit"],
        ),
        max_rows=query["limit"] + 1,
    )
    more = len(rows) > query["limit"]
    page = rows[: query["limit"]]
    result = []
    for row in page:
        value = str(row["value"])
        if not failed and field in {"skill.name", "skill.version_id"}:
            if value not in values:
                raise SkillsHubError(
                    "runtime_scope_mismatch", "Runtime read scope mismatch.", status=503
                )
            result.append(values[value])
        elif field == "skill.context_id":
            if value not in contexts:
                raise SkillsHubError(
                    "runtime_scope_mismatch", "Runtime read scope mismatch.", status=503
                )
            result.append({"value": value, "label": contexts[value]})
        else:
            result.append({"value": value, "label": value})
    return {
        "values": result,
        "next_cursor": next_cursor(identity=identity, after=str(page[-1]["value"]))
        if more
        else None,
        "truncated": False,
    }
