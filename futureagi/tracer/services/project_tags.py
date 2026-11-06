"""Project-scoped tag definitions and their assignments.

A definition (name + colour) lives in Postgres, once per project. An
assignment is a JSON array of names, written where current readers look:

* ``spans.tags`` on the tagged span. Span detail and the dashboard tag
  dimension read it. A trace's tags also go on its root span, which trace
  detail reads when the trace has no Postgres row.
* ``traces.tags`` for a trace. The trace grid and the native tag filter read
  the latest ``traces`` version.
* ``tracer_trace.tags`` when that Postgres row exists. Trace detail prefers
  it, and the ingestion mirror copies it back into ``traces``, so it must not
  disagree with ClickHouse.

Colour never goes into an assignment. A span id is never an identity on its
own: every lookup is scoped to the caller's authorized projects, and every
write names one exact physical row.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from tracer.models.project import Project, ProjectTagDefinition
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import get_v2_config

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_TAG_NAME_MAX = 255
# Same ceiling trace detail uses before it refuses an implicit project scope.
_MAX_LOOKUP_PROJECTS = 4096
_MAX_LISTED_TAGS = 10_000
# The shared read lane drops row/byte abort caps by policy; what bounds these
# reads is the explicit per-statement execution cap plus memory and threads.
_LOOKUP_CAP_MS = 9_500
_LIST_CAP_MS = 20_000
_LOOKUP_SETTINGS = {"max_threads": 1, "max_memory_usage": 4 * 1024**3}
_LIST_SETTINGS = {"max_threads": 4, "max_memory_usage": 8 * 1024**3}
_ASSIGNMENT_TABLES = ("spans", "traces")


@dataclass(frozen=True)
class SpanKey:
    """One stored span: the ``spans`` ReplacingMergeTree key plus its current time.

    Versions of a span replace each other on ``(project_id, observation_type,
    service_name, toStartOfHour(start_time), trace_id, id)``; ``start_time`` is
    the latest version's, which can differ within the hour from older ones.
    """

    project_id: str
    observation_type: str
    service_name: str
    trace_id: str
    span_id: str
    start_time: datetime

    @property
    def start_us(self) -> int:
        start = self.start_time
        start = (
            start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        )
        delta = start - datetime(1970, 1, 1, tzinfo=UTC)
        return (
            delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        )

    @property
    def partition_id(self) -> str:
        start = self.start_time
        start = (
            start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        )
        return start.strftime("%Y%m%d")


class TagStoreUnavailable(RuntimeError):
    """ClickHouse could not be read or written. Not the same as "not found"."""


class TagNotFound(LookupError):
    pass


class TagTargetAmbiguous(LookupError):
    """The id matches rows in more than one of the caller's projects."""


class TagListTooLarge(ValueError):
    """More distinct tag names than one response may list."""

    def __init__(self, limit: int):
        super().__init__(
            f"This project has more than {limit} distinct tag names, so they "
            "cannot be listed in one response."
        )


def is_valid_tag_color(color: Any) -> bool:
    return isinstance(color, str) and bool(_HEX_COLOR.fullmatch(color))


def _existing_name(name: Any) -> str:
    """An existing tag's name, matched exactly (an SDK may have padded it)."""
    if not isinstance(name, str) or not name or len(name) > _TAG_NAME_MAX:
        raise ValueError(f"tag name must be 1 to {_TAG_NAME_MAX} characters")
    return name


def _clean_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError("tag name must be a string")
    name = name.strip()
    if not name or len(name) > _TAG_NAME_MAX:
        raise ValueError(
            f"tag name must be 1 to {_TAG_NAME_MAX} characters after trimming"
        )
    return name


def parse_tag_items(raw_tags: Any) -> list[dict[str, str]]:
    """Accept ``["name"]`` or ``[{name, color}]`` and return unique items.

    Colour is optional. An empty colour leaves an existing definition's colour
    alone. Names are stripped and de-duplicated in request order; the first
    occurrence of a name wins.
    """
    if not isinstance(raw_tags, list):
        raise ValueError('tags must be a list, e.g. ["prod"] or [{"name": "prod"}]')
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw_tags:
        if isinstance(entry, str):
            name, color = entry, ""
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            name, color = entry["name"], entry.get("color") or ""
            if color and not is_valid_tag_color(color):
                raise ValueError(f"invalid tag color {color!r}; use #RRGGBB")
        else:
            raise ValueError(
                'each tag must be a name or {"name": ..., "color": "#RRGGBB"}'
            )
        name = _clean_name(name)
        if name in seen:
            continue
        seen.add(name)
        items.append({"name": name, "color": color})
    return items


def _names(items: list[dict[str, str]]) -> list[str]:
    return [item["name"] for item in items]


def _canonical_trace_uuid(trace_id: Any) -> str | None:
    """Return the dashed UUID that ``traces.id``/``tracer_trace.id`` can match."""
    text = str(trace_id or "").strip().lower()
    try:
        canonical = str(uuid.UUID(text))
    except ValueError:
        return None
    return canonical if canonical == text else None


def authorized_project_ids(projects: Iterable[Any]) -> list[str]:
    """Project ids the caller may tag in, as strings, bounded like trace detail."""
    ids = list(dict.fromkeys(str(value) for value in projects if value))
    if not ids:
        raise TagNotFound("project")
    if len(ids) > _MAX_LOOKUP_PROJECTS:
        raise TagTargetAmbiguous("project_scope_too_large")
    return ids


# --- ClickHouse access -------------------------------------------------------


def _read(sql: str, params: dict, *, cap_ms: int, settings: dict) -> list[dict]:
    """Bounded read through the shared CH25 analytics lane."""
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    try:
        result = V2AnalyticsQueryService().execute_ch_query(
            sql,
            params,
            timeout_ms=cap_ms,
            settings=dict(settings),
            server_execution_cap_ms=cap_ms,
        )
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse read failed") from exc
    return list(result.data)


def _write_client():
    """A write-capable client. The shared analytics lane is read-only."""
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


def _command(sql: str, parameters: dict) -> None:
    client = _write_client()
    try:
        client.command(sql, parameters=parameters)
    except Exception as exc:
        raise TagStoreUnavailable("clickhouse tag write failed") from exc
    finally:
        client.close()


def _span_key(row: dict) -> SpanKey:
    return SpanKey(
        project_id=str(row["project_id"]),
        observation_type=str(row["observation_type"]),
        service_name=str(row["service_name"]),
        trace_id=str(row["trace_id"]),
        span_id=str(row["span_id"]),
        start_time=row["latest_start_time"],
    )


def _only_project(rows: list[dict]) -> dict:
    """The one candidate, when the id matched in exactly one project.

    Lookups return at most one row per project (``LIMIT 1 BY project_id``), so
    a second row always means a second project, however many rows the first
    project holds.
    """
    if len(rows) > 1:
        raise TagTargetAmbiguous("id_in_several_projects")
    return rows[0]


def find_trace_root(*, project_ids: list[str], trace_id: str) -> SpanKey | None:
    """The earliest live root span of a trace inside the authorized projects.

    Latest state per stored span (the table's replacing key), never ``FINAL``
    or a bare id, so a root whose time was corrected within the hour is one
    span, not two.
    """
    rows = _read(
        """
        SELECT
            toString(project_id) AS project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time) AS start_hour,
            trace_id,
            id AS span_id,
            argMax(start_time, _version) AS latest_start_time,
            argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
            argMax(is_deleted, _version) AS latest_is_deleted
        FROM spans
        PREWHERE project_id IN %(project_ids)s
          AND trace_id = %(trace_id)s
        GROUP BY project_id, observation_type, service_name, start_hour, trace_id, id
        HAVING latest_is_deleted = 0 AND ifNull(latest_parent_span_id, '') = ''
        ORDER BY latest_start_time ASC, span_id ASC
        LIMIT 1 BY project_id
        LIMIT 2
        """,
        {"project_ids": tuple(project_ids), "trace_id": str(trace_id)},
        cap_ms=_LOOKUP_CAP_MS,
        settings=_LOOKUP_SETTINGS,
    )
    if not rows:
        return None
    return _span_key(_only_project(rows))


def find_span(*, project_ids: list[str], span_id: str) -> SpanKey:
    """One live stored span inside the authorized projects.

    Within one project a replay can leave the same span id on several stored
    spans. Pick the most recently written one (as span detail's anchor query
    does) and write only that one.
    """
    rows = _read(
        """
        SELECT
            toString(project_id) AS project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time) AS start_hour,
            trace_id,
            id AS span_id,
            argMax(start_time, _version) AS latest_start_time,
            argMax(is_deleted, _version) AS latest_is_deleted,
            max(_version) AS latest_version
        FROM spans
        PREWHERE project_id IN %(project_ids)s
          AND id = %(span_id)s
        GROUP BY project_id, observation_type, service_name, start_hour, trace_id, id
        HAVING latest_is_deleted = 0
        ORDER BY latest_version DESC, project_id DESC, trace_id DESC,
                 latest_start_time DESC
        LIMIT 1 BY project_id
        LIMIT 2
        """,
        {"project_ids": tuple(project_ids), "span_id": str(span_id)},
        cap_ms=_LOOKUP_CAP_MS,
        settings=_LOOKUP_SETTINGS,
    )
    if not rows:
        raise TagNotFound("span")
    return _span_key(_only_project(rows))


_SPAN_KEY_WHERE = """
        project_id = {project_id:UUID}
          AND observation_type = {observation_type:String}
          AND service_name = {service_name:String}
          AND toStartOfHour(start_time)
              = toStartOfHour(fromUnixTimestamp64Micro({start_us:Int64}, 'UTC'))
          AND trace_id = {trace_id:String}
          AND id = {span_id:String}"""


def _span_key_params(key: SpanKey) -> dict:
    return {
        "project_id": key.project_id,
        "observation_type": key.observation_type,
        "service_name": key.service_name,
        "start_us": key.start_us,
        "trace_id": key.trace_id,
        "span_id": key.span_id,
    }


def write_span_assignment(key: SpanKey, names: list[str]) -> None:
    """Replace one stored span's tag names in place, on every version of it.

    In place, not a new ``_version``: dashboard aggregates read ``spans``
    without ``FINAL`` and would count a re-inserted copy twice until a merge.
    The partition and the whole replacing key are pinned, so no other span is
    rewritten, and whichever version wins a merge carries the new names.
    """
    partition = key.partition_id
    if not re.fullmatch(r"\d{8}", partition):
        raise TagStoreUnavailable("span partition could not be derived")
    _command(
        f"""
        ALTER TABLE spans UPDATE tags = {{tags:String}}
        IN PARTITION ID '{partition}'
        WHERE {_SPAN_KEY_WHERE}
        SETTINGS mutations_sync = 2
        """,
        {"tags": json.dumps(names, ensure_ascii=False), **_span_key_params(key)},
    )


def write_trace_assignment(
    *,
    project_id: str,
    trace_uuid: str,
    names: list[str],
    root: SpanKey | None = None,
) -> None:
    """Give the trace's ``traces`` row these tags, creating it if it is missing.

    An existing live row gets a newer version that differs only in its tags,
    which is the trace mirror's own update pattern. When no row exists (the
    collector writes it best-effort, after the spans), one is created from the
    root span the way the collector derives it. A tombstoned trace is left
    alone.
    """
    state = _read(
        """
        SELECT count() AS versions, argMax(is_deleted, _version) AS latest_is_deleted
        FROM traces
        WHERE project_id = %(project_id)s AND id = %(trace_id)s
        """,
        {"project_id": project_id, "trace_id": trace_uuid},
        cap_ms=_LOOKUP_CAP_MS,
        settings=_LOOKUP_SETTINGS,
    )
    versions = int(state[0]["versions"]) if state else 0
    tags = json.dumps(names, ensure_ascii=False)
    if versions:
        if int(state[0]["latest_is_deleted"]):
            return
        _command(
            """
            INSERT INTO traces
            SELECT * REPLACE (
                {tags:String} AS tags,
                now64(6, 'UTC') AS updated_at,
                greatest(
                    _version + 1,
                    toUInt64(toUnixTimestamp64Nano(now64(9, 'UTC')))
                ) AS _version
            )
            FROM traces FINAL
            WHERE project_id = {project_id:UUID} AND id = {trace_id:UUID}
            """,
            {"tags": tags, "project_id": project_id, "trace_id": trace_uuid},
        )
        return
    if root is None:
        return
    _command(
        f"""
        INSERT INTO traces
            (id, project_id, name, session_id, tags, input, output,
             created_at, updated_at, is_deleted, _version)
        SELECT
            toUUID(trace_id), project_id, name, trace_session_id, {{tags:String}},
            input, output, start_time, now64(6, 'UTC'), 0,
            toUInt64(toUnixTimestamp64Nano(now64(9, 'UTC')))
        FROM spans
        WHERE {_SPAN_KEY_WHERE}
        ORDER BY _version DESC
        LIMIT 1
        """,
        {"tags": tags, **_span_key_params(root)},
    )


def _rewrite_expression(new_name: str | None) -> str:
    current = "JSONExtract(tags, 'Array(String)')"
    if new_name is None:
        return f"arrayFilter(x -> x != {{old_name:String}} AND x != '', {current})"
    return (
        "arrayFilter(x -> x != '', arrayDistinct(arrayMap("
        f"x -> if(x = {{old_name:String}}, {{new_name:String}}, x), {current})))"
    )


def rewrite_tag_name(*, project_id: str, old_name: str, new_name: str | None) -> None:
    """Rename or remove one name across a project's assignments.

    One scoped mutation per assignment table, not a row-by-row cascade. A
    rename onto a name already present collapses to one entry; ``new_name``
    None removes the name.
    """
    expression = _rewrite_expression(new_name)
    for table in _ASSIGNMENT_TABLES:
        _command(
            f"""
            ALTER TABLE {table} UPDATE tags = toJSONString({expression})
            WHERE project_id = {{project_id:UUID}}
              AND has(JSONExtract(tags, 'Array(String)'), {{old_name:String}})
            SETTINGS mutations_sync = 2
            """,
            {
                "project_id": project_id,
                "old_name": old_name,
                "new_name": new_name or "",
            },
        )


def _rewrite_postgres_trace_tags(
    *, project_id: str, old_name: str, new_name: str | None
) -> None:
    """The same rename/removal on legacy ``tracer_trace`` rows, one statement."""
    table = connection.ops.quote_name(Trace._meta.db_table)
    if new_name is None:
        replacement = "tags - %(old)s"
    else:
        replacement = """
            CASE WHEN tags ? %(new)s THEN tags - %(old)s ELSE (
                SELECT COALESCE(
                    jsonb_agg(
                        CASE WHEN e.value = to_jsonb(%(old)s::text)
                             THEN to_jsonb(%(new)s::text)
                             ELSE e.value END
                        ORDER BY e.ord
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(tags) WITH ORDINALITY AS e(value, ord)
            ) END
        """
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table}
            SET tags = {replacement}, updated_at = now()
            WHERE project_id = %(project_id)s
              AND jsonb_typeof(tags) = 'array'
              AND tags ? %(old)s
            """,
            {"project_id": project_id, "old": old_name, "new": new_name or ""},
        )


# --- Definitions -------------------------------------------------------------


def upsert_definitions(project: Project, items: list[dict[str, str]]) -> None:
    """Create a definition for each new name; never recolour an existing one.

    An assignment's colour only seeds a tag the project does not have yet, or
    one that has no colour. The current editors resend a colour for every tag
    they show, usually one derived from the name, so honouring it here would
    overwrite real colours project-wide. Recolour with tag-definitions PATCH.
    """
    for item in items:
        definition, created = ProjectTagDefinition.objects.get_or_create(
            project=project,
            name=item["name"],
            defaults={"color": item["color"]},
        )
        if not created and item["color"] and not definition.color:
            definition.color = item["color"]
            definition.save(update_fields=["color", "updated_at"])


def _assigned_names(project_id: str) -> tuple[dict[str, int], set[str]]:
    """Names assigned in the project: span counts, plus names seen on trace rows.

    ``usage_count`` is the number of live spans whose tags contain the name,
    once per span even if an SDK repeated it in one list. Trace rows only add
    names (a legacy trace-level tag with no span carrying it counts 0).
    """
    found: dict[str, dict[str, int]] = {}
    for table, live in (("spans", "AND is_deleted = 0"), ("traces", "")):
        rows = _read(
            f"""
            SELECT tag, count() AS row_count
            FROM (
                SELECT arrayDistinct(JSONExtract(tags, 'Array(String)')) AS names
                FROM {table} FINAL
                WHERE project_id = %(project_id)s
                  AND tags NOT IN ('', '[]')
                  {live}
            )
            ARRAY JOIN names AS tag
            WHERE tag != ''
            GROUP BY tag
            ORDER BY tag
            LIMIT {_MAX_LISTED_TAGS + 1}
            """,
            {"project_id": project_id},
            cap_ms=_LIST_CAP_MS,
            settings=_LIST_SETTINGS,
        )
        if len(rows) > _MAX_LISTED_TAGS:
            raise TagListTooLarge(_MAX_LISTED_TAGS)
        found[table] = {str(row["tag"]): int(row["row_count"]) for row in rows}
    return found["spans"], set(found["traces"])


def list_definitions(*, project: Project) -> list[dict[str, Any]]:
    """Union of stored definitions and names assigned in ClickHouse.

    Names set only by an SDK appear with an empty colour.
    """
    stored = {
        row.name: row for row in ProjectTagDefinition.objects.filter(project=project)
    }
    span_counts, trace_names = _assigned_names(str(project.id))
    names = sorted(set(stored) | set(span_counts) | trace_names)
    if len(names) > _MAX_LISTED_TAGS:
        raise TagListTooLarge(_MAX_LISTED_TAGS)
    return [
        {
            "name": name,
            "color": stored[name].color if name in stored else "",
            "usage_count": span_counts.get(name, 0),
        }
        for name in names
    ]


def _name_assigned(project: Project, name: str) -> bool:
    """Whether any assignment in the project carries ``name``. Cheap probes."""
    if Trace.no_workspace_objects.filter(
        project=project, tags__contains=[name]
    ).exists():
        return True
    for table in _ASSIGNMENT_TABLES:
        rows = _read(
            f"""
            SELECT 1 AS present
            FROM {table}
            WHERE project_id = %(project_id)s
              AND has(JSONExtract(tags, 'Array(String)'), %(name)s)
            LIMIT 1
            """,
            {"project_id": str(project.id), "name": name},
            cap_ms=_LOOKUP_CAP_MS,
            settings=_LOOKUP_SETTINGS,
        )
        if rows:
            return True
    return False


def _check_color(color: Any) -> str | None:
    if color is None:
        return None
    if not isinstance(color, str) or (color and not is_valid_tag_color(color)):
        raise ValueError(f"invalid tag color {color!r}; use #RRGGBB or an empty string")
    return color


def _lock_project_tags(project: Project) -> None:
    """Serialize rename/recolour/delete of one project's tags (transaction-scoped)."""
    key = uuid.UUID(str(project.id)).int & 0x7FFFFFFF
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [7275, key])


def update_definition(
    *,
    project: Project,
    name: str,
    new_name: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Recolour and/or rename one project tag.

    Recolouring changes the definition only. A rename also rewrites every
    assignment in the project; renaming onto an existing tag merges into it.
    ``color`` None keeps the current colour, ``""`` clears it.

    Repeating a rename that already happened (the old name is gone and the new
    one exists) completes it instead of failing, so a retry after a partial
    ClickHouse failure converges.
    """
    name = _existing_name(name)
    color = _check_color(color)
    new_name = None if new_name is None else _clean_name(new_name)
    renaming = new_name is not None and new_name != name
    with transaction.atomic():
        _lock_project_tags(project)
        definition = (
            ProjectTagDefinition.objects.select_for_update()
            .filter(project=project, name=name)
            .first()
        )
        if definition is None and not _name_assigned(project, name):
            already_renamed = renaming and (
                ProjectTagDefinition.objects.filter(
                    project=project, name=new_name
                ).exists()
                or _name_assigned(project, new_name)
            )
            if not already_renamed:
                raise TagNotFound("definition")
            name, renaming = new_name, False

        if not renaming:
            if definition is None:
                definition, _ = ProjectTagDefinition.objects.get_or_create(
                    project=project, name=name, defaults={"color": color or ""}
                )
            if color is not None and definition.color != color:
                definition.color = color
                definition.save(update_fields=["color", "updated_at"])
            return {"name": name, "color": definition.color}

        target = (
            ProjectTagDefinition.objects.select_for_update()
            .filter(project=project, name=new_name)
            .first()
        )
        if target is not None:
            # Merge: the existing tag survives and keeps its colour unless a
            # new one was given or it had none.
            if color is not None:
                merged = color
            else:
                merged = target.color or (definition.color if definition else "")
            if merged != target.color:
                target.color = merged
                target.save(update_fields=["color", "updated_at"])
            if definition is not None:
                definition.delete()
            survivor = target
        elif definition is not None:
            definition.name = new_name
            if color is not None:
                definition.color = color
            definition.save(update_fields=["name", "color", "updated_at"])
            survivor = definition
        else:
            survivor = ProjectTagDefinition.objects.create(
                project=project, name=new_name, color=color or ""
            )
        _rewrite_postgres_trace_tags(
            project_id=str(project.id), old_name=name, new_name=new_name
        )
        # Last, so a ClickHouse failure rolls the Postgres side back. The
        # statements are idempotent, and a repeated rename completes (see
        # above), so retrying after a partial failure converges.
        rewrite_tag_name(project_id=str(project.id), old_name=name, new_name=new_name)
    return {"name": new_name, "color": survivor.color}


def delete_definition(*, project: Project, name: str) -> None:
    """Remove a tag from the project and from every assignment in it."""
    name = _existing_name(name)
    with transaction.atomic():
        _lock_project_tags(project)
        ProjectTagDefinition.objects.filter(project=project, name=name).update(
            deleted=True, deleted_at=timezone.now()
        )
        _rewrite_postgres_trace_tags(
            project_id=str(project.id), old_name=name, new_name=None
        )
        rewrite_tag_name(project_id=str(project.id), old_name=name, new_name=None)


# --- Assignments -------------------------------------------------------------


def set_trace_tags(
    *, project_ids: list[str], trace_id: str, raw_tags: Any
) -> dict[str, Any]:
    """Replace a trace's tags on every surface that reads them.

    ``project_ids`` is the caller's authorized scope: one project when the
    request named it, otherwise every project the caller can see. A Postgres
    trace row, when present, fixes the project (its id is unique).
    """
    items = parse_tag_items(raw_tags)
    names = _names(items)
    trace_uuid = _canonical_trace_uuid(trace_id)
    pg_trace = None
    if trace_uuid is not None:
        pg_trace = (
            Trace.no_workspace_objects.filter(project_id__in=project_ids, id=trace_uuid)
            .only("id", "project_id")
            .first()
        )
    scope = [str(pg_trace.project_id)] if pg_trace is not None else project_ids
    root = find_trace_root(project_ids=scope, trace_id=str(trace_id))
    if root is None and pg_trace is None:
        raise TagNotFound("trace")
    project_id = root.project_id if root is not None else str(pg_trace.project_id)
    project = Project.no_workspace_objects.get(id=project_id)

    with transaction.atomic():
        # Same lock as rename/delete: concurrent writes to one trace cannot
        # leave its root span and its traces row disagreeing.
        _lock_project_tags(project)
        upsert_definitions(project, items)
        if pg_trace is not None:
            Trace.no_workspace_objects.filter(id=pg_trace.id).update(
                tags=names, updated_at=timezone.now()
            )
        # ClickHouse last, so a failure rolls the Postgres side back. Both
        # writes replace the whole list, so retrying after a partial failure
        # converges.
        if root is not None:
            write_span_assignment(root, names)
        if trace_uuid is not None:
            write_trace_assignment(
                project_id=project_id, trace_uuid=trace_uuid, names=names, root=root
            )
    return {"project_id": project_id, "tags": names}


def set_span_tags(
    *, project_ids: list[str], span_id: str, raw_tags: Any
) -> dict[str, Any]:
    """Replace one span's tags. Only that span's ClickHouse row changes."""
    items = parse_tag_items(raw_tags)
    names = _names(items)
    identity = find_span(project_ids=project_ids, span_id=str(span_id))
    project = Project.no_workspace_objects.get(id=identity.project_id)
    with transaction.atomic():
        _lock_project_tags(project)
        upsert_definitions(project, items)
        write_span_assignment(identity, names)
    return {"project_id": identity.project_id, "tags": names}
