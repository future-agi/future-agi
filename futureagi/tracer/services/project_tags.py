"""Project-scoped tag definitions and ClickHouse span assignments.

Definitions (name + colour) live in Postgres. Assignments stay in the
ClickHouse ``spans.tags`` column as a JSON array of names, which is what
filters, dashboards, and SDK ingest already use.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q

from tracer.models.project import Project, ProjectTagDefinition
from tracer.services.clickhouse.v2 import get_v2_config

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TAG_NAME_MAX = 255
# A tag write must not scan every partition. Ninety days matches the spans TTL.
_LOOKUP_WINDOW = timedelta(days=90)


class TagStoreUnavailable(RuntimeError):
    """ClickHouse could not be read or written. Not the same as "not found"."""


class TagNotFound(LookupError):
    pass


def parse_tag_items(raw_tags: Any) -> list[dict[str, str]]:
    """Accept ``["name"]`` or ``[{name, color}]`` and return unique items.

    Colour is optional. A missing or invalid colour is stored as empty so the
    UI can fall back to its hash colour. Names are stripped and de-duplicated
    in request order.
    """
    if not isinstance(raw_tags, list):
        raise ValueError("tags must be a list")
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw_tags:
        if isinstance(entry, str):
            name, color = entry, ""
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            name = entry["name"]
            color = entry.get("color") or ""
            if color and not _HEX_COLOR.match(str(color)):
                raise ValueError(f"invalid tag color: {color}")
            color = str(color)
        else:
            raise ValueError("each tag must be a name or {name, color}")
        name = name.strip()
        if not name or len(name) > _TAG_NAME_MAX:
            raise ValueError("tag name is empty or too long")
        if name in seen:
            continue
        seen.add(name)
        items.append({"name": name, "color": color})
    return items


def _names(items: list[dict[str, str]]) -> list[str]:
    return [item["name"] for item in items]


def _client():
    try:
        import clickhouse_connect

        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
        )
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse client unavailable") from exc


def _query(client, sql: str, params: dict) -> list[tuple]:
    try:
        result = client.query(sql, parameters=params)
        return list(result.result_rows)
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse query failed") from exc


def upsert_definitions(project: Project, items: list[dict[str, str]]) -> None:
    """Create or update definitions. An empty colour does not clear an existing one."""
    for item in items:
        definition, created = ProjectTagDefinition.objects.get_or_create(
            project=project,
            name=item["name"],
            defaults={"color": item["color"]},
        )
        if not created and item["color"] and definition.color != item["color"]:
            definition.color = item["color"]
            definition.save(update_fields=["color", "updated_at"])


def find_root_span(
    *,
    project_id: str,
    trace_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Locate the root span for a trace inside one project.

    Identity is ``project_id`` plus ``trace_id``. The span's own ``start_time``
    is returned so the mutation can prune to that partition day. A fixed recent
    window is not used here: SDK traces can be older than 90 days, and missing
    them would report "Trace not found" for a row that exists.
    """
    del now
    client = _client()
    try:
        rows = _query(
            client,
            """
            SELECT id, start_time, tags
            FROM spans FINAL
            WHERE project_id = {project_id:UUID}
              AND trace_id = {trace_id:String}
              AND parent_span_id = ''
              AND is_deleted = 0
            ORDER BY start_time ASC
            LIMIT 1
            """,
            {
                "project_id": project_id,
                "trace_id": trace_id,
            },
        )
    finally:
        client.close()
    if not rows:
        raise TagNotFound("trace")
    span_id, start_time, tags = rows[0]
    return {
        "id": str(span_id),
        "start_time": start_time,
        "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
    }


def find_span(
    *,
    project_id: str,
    span_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Locate one span inside one project. Never by span id alone."""
    del now
    client = _client()
    try:
        rows = _query(
            client,
            """
            SELECT id, start_time, tags, trace_id
            FROM spans FINAL
            WHERE project_id = {project_id:UUID}
              AND id = {span_id:String}
              AND is_deleted = 0
            ORDER BY start_time DESC
            LIMIT 1
            """,
            {
                "project_id": project_id,
                "span_id": span_id,
            },
        )
    finally:
        client.close()
    if not rows:
        raise TagNotFound("span")
    found_id, start_time, tags, trace_id = rows[0]
    return {
        "id": str(found_id),
        "start_time": start_time,
        "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
        "trace_id": str(trace_id),
    }


def find_span_in_projects(*, project_ids: list[str], span_id: str) -> dict[str, Any]:
    """Locate one span inside the caller's projects. Never by span id alone."""
    if not project_ids or not span_id:
        raise TagNotFound("span")
    client = _client()
    try:
        rows = _query(
            client,
            """
            SELECT id, start_time, tags, trace_id, project_id
            FROM spans FINAL
            WHERE project_id IN {project_ids:Array(UUID)}
              AND id = {span_id:String}
              AND is_deleted = 0
            LIMIT 2
            """,
            {"project_ids": [str(item) for item in project_ids], "span_id": str(span_id)},
        )
    finally:
        client.close()
    if len(rows) != 1:
        raise TagNotFound("span")
    found_id, start_time, tags, trace_id, project_id = rows[0]
    return {
        "id": str(found_id),
        "start_time": start_time,
        "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
        "trace_id": str(trace_id),
        "project_id": str(project_id),
    }


def find_root_in_projects(*, project_ids: list[str], trace_id: str) -> dict[str, Any]:
    """Locate one trace root inside the caller's projects."""
    if not project_ids or not trace_id:
        raise TagNotFound("trace")
    client = _client()
    try:
        rows = _query(
            client,
            """
            SELECT id, start_time, tags, project_id
            FROM spans FINAL
            WHERE project_id IN {project_ids:Array(UUID)}
              AND trace_id = {trace_id:String}
              AND parent_span_id = ''
              AND is_deleted = 0
            ORDER BY start_time ASC
            LIMIT 2
            """,
            {"project_ids": [str(item) for item in project_ids], "trace_id": str(trace_id)},
        )
    finally:
        client.close()
    if len(rows) != 1:
        raise TagNotFound("trace")
    found_id, start_time, tags, project_id = rows[0]
    return {
        "id": str(found_id),
        "start_time": start_time,
        "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
        "project_id": str(project_id),
    }


def write_assignment(
    *,
    project_id: str,
    span_id: str,
    start_time: datetime,
    names: list[str],
) -> None:
    """Replace one span's tag names. Scoped by project, id, and its partition day."""
    day_start = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    payload = json.dumps(names, ensure_ascii=False)
    client = _client()
    try:
        client.command(
            """
            ALTER TABLE spans UPDATE tags = {tags:String}
            WHERE project_id = {project_id:UUID}
              AND id = {span_id:String}
              AND start_time >= {day_start:DateTime64(6)}
              AND start_time < {day_end:DateTime64(6)}
            SETTINGS mutations_sync = 2
            """,
            parameters={
                "tags": payload,
                "project_id": project_id,
                "span_id": span_id,
                "day_start": day_start,
                "day_end": day_end,
            },
        )
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse tag update failed") from exc
    finally:
        client.close()


def set_trace_tags(*, project: Project, trace_id: str, raw_tags: Any) -> list[str]:
    items = parse_tag_items(raw_tags)
    span = find_root_span(project_id=str(project.id), trace_id=str(trace_id))
    with transaction.atomic():
        upsert_definitions(project, items)
        write_assignment(
            project_id=str(project.id),
            span_id=span["id"],
            start_time=span["start_time"],
            names=_names(items),
        )
    return _names(items)


def rewrite_tag_name(*, project_id: str, old_name: str, new_name: str | None) -> None:
    """Rename or remove one tag name across a project's span assignments.

    One mutation, scoped by project_id. ``new_name`` None removes the name.
    A rename onto a name already present collapses to one entry.
    """
    if new_name is None:
        expression = (
            "arrayFilter(x -> x != {old_name:String}, "
            "JSONExtract(ifNull(tags, '[]'), 'Array(String)'))"
        )
    else:
        expression = (
            "arrayDistinct(arrayMap(x -> if(x = {old_name:String}, {new_name:String}, x), "
            "JSONExtract(ifNull(tags, '[]'), 'Array(String)')))"
        )
    client = _client()
    try:
        client.command(
            f"""
            ALTER TABLE spans UPDATE tags = toJSONString({expression})
            WHERE project_id = {{project_id:UUID}}
              AND has(JSONExtract(ifNull(tags, '[]'), 'Array(String)'), {{old_name:String}})
            SETTINGS mutations_sync = 2
            """,
            parameters={
                "project_id": project_id,
                "old_name": old_name,
                "new_name": new_name or "",
            },
        )
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse tag rewrite failed") from exc
    finally:
        client.close()


def list_definitions(*, project: Project) -> list[dict[str, Any]]:
    """Union of stored definitions and names currently assigned in ClickHouse."""
    stored = {
        row.name: row
        for row in ProjectTagDefinition.objects.filter(project=project)
    }
    client = _client()
    try:
        rows = _query(
            client,
            """
            SELECT tag, count()
            FROM (
                SELECT arrayJoin(JSONExtract(ifNull(tags, '[]'), 'Array(String)')) AS tag
                FROM spans FINAL
                WHERE project_id = {project_id:UUID}
                  AND is_deleted = 0
            )
            WHERE tag != ''
            GROUP BY tag
            """,
            {"project_id": str(project.id)},
        )
    finally:
        client.close()
    counts = {name: int(count) for name, count in rows}
    names = list(stored.keys())
    for name in counts:
        if name not in stored:
            names.append(name)
    names.sort()
    return [
        {
            "name": name,
            "color": stored[name].color if name in stored else "",
            "usage_count": counts.get(name, 0),
        }
        for name in names
    ]


def update_definition(
    *,
    project: Project,
    name: str,
    new_name: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if color is not None and color and not _HEX_COLOR.match(color):
        raise ValueError(f"invalid tag color: {color}")
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name or len(new_name) > _TAG_NAME_MAX:
            raise ValueError("tag name is empty or too long")
    definition = ProjectTagDefinition.objects.filter(project=project, name=name).first()
    current = list_definitions(project=project)
    assigned = name in {item["name"] for item in current}
    if definition is None and not assigned:
        raise TagNotFound("definition")
    if new_name and new_name != name:
        target = ProjectTagDefinition.objects.filter(project=project, name=new_name).first()
        stored_color = color if color is not None else ""
        with transaction.atomic():
            if definition is not None:
                if target is not None:
                    if color is not None:
                        target.color = stored_color
                        target.save(update_fields=["color", "updated_at"])
                    definition.delete()
                    definition = target
                else:
                    definition.name = new_name
                    if color is not None:
                        definition.color = stored_color
                    definition.save()
            elif color is not None:
                definition = ProjectTagDefinition.objects.create(
                    project=project, name=new_name, color=stored_color
                )
            try:
                rewrite_tag_name(
                    project_id=str(project.id), old_name=name, new_name=new_name
                )
            except TagStoreUnavailable:
                transaction.set_rollback(True)
                raise
        name = new_name
    elif definition is None:
        definition = ProjectTagDefinition.objects.create(
            project=project, name=name, color="" if color is None else color
        )
    elif color is not None:
        definition.color = color
        definition.save(update_fields=["color", "updated_at"])
    return {
        "name": name,
        "color": definition.color if definition else (color or ""),
    }


def delete_definition(*, project: Project, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    with transaction.atomic():
        ProjectTagDefinition.objects.filter(project=project, name=name).delete()
        try:
            rewrite_tag_name(project_id=str(project.id), old_name=name, new_name=None)
        except TagStoreUnavailable:
            transaction.set_rollback(True)
            raise


def set_span_tags(*, project: Project, span_id: str, raw_tags: Any) -> list[str]:
    items = parse_tag_items(raw_tags)
    span = find_span(project_id=str(project.id), span_id=str(span_id))
    with transaction.atomic():
        upsert_definitions(project, items)
        write_assignment(
            project_id=str(project.id),
            span_id=span["id"],
            start_time=span["start_time"],
            names=_names(items),
        )
    return _names(items)
