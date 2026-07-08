"""trace_session_dict_reader — batch ``external_session_id`` lookups from the CH
``trace_sessions_dict`` (DESIGN §5.2, the Session-name reads cutover).

Why this exists
---------------
Before the CH-derived-dimensions migration the session list/detail builders
emitted ``session_name=None`` and the view back-filled it from PG
``TraceSession.name``. Once TraceSession's *external identity* moves to CH
(``trace_sessions`` RMT / ``trace_sessions_dict``), the session's external id —
the OTel ``session.id`` string the user passed — lives in the dict keyed by
``trace_session_id`` (the span's OWN soft-id column), NOT in PG.

The session display name is then
``COALESCE(overlay.display_name, trace_sessions_dict.external_session_id)``
(DESIGN §5.2): this module resolves the CH half (``external_session_id``); the
PG ``TraceSessionOverlay.display_name`` override is layered on top by the caller.

A CH ``dictGet`` cannot run inside a PG queryset, so the read path resolves the
per-session ``trace_session_id`` in PG/CH-spans first (a plain column read, no FK
join), then calls this module to batch-resolve the
``{trace_session_id -> external_session_id}`` labels from CH and merges in Python.
This module is the CH half of that restructure — a sibling of
``end_user_dict_reader`` for the Session dimension.

Faithfulness to the old back-fill semantics (the parity contract)
-----------------------------------------------------------------
The old back-fill produced ``None`` whenever no PG ``TraceSession`` row matched
the span's ``trace_session_id``. We reproduce it with ``dictGetOrNull``: a key
MISSING from the dict returns NULL (NOT the column's ``''`` default that a plain
``dictGet`` would give), so a session id with no curated row resolves to ``None``
exactly like the old PG miss did.

NOTE on the empty-string coercion: ``trace_sessions.external_session_id`` is a
non-null String (schema 018) populated from PG ``TraceSession.name`` (which is
``null=True``); the backfill/collector coerce PG NULL → ``''``. So a session
whose PG ``name`` was NULL surfaces ``''`` here, whereas the old back-fill (which
read ``name`` straight off the row) would surface ``None``. We normalize ``''`` →
``None`` so a name-less session renders identically OLD vs NEW. (A genuine
empty-string external id — none observed on the box — would also collapse to
``None``; accepted, the column is a display label.)

This module is read-only: a failure here is a real read error (parity reads must
surface problems, unlike the best-effort ingest dual-write), so it does NOT
swallow exceptions.

EXISTENCE + FIELDS (P3b step2 — the Slice C/D/E/F building block)
----------------------------------------------------------------
``resolve_external_session_ids`` above is *forward id → display label* only and
reads the dict (a 60–120s-stale label cache is fine for a name back-fill). Step2
needs two MORE resolutions that the dict cannot serve:

  • ``session_exists(project_id, trace_session_id)`` — does this id name a known
    session? Used by the annotation-queue / eval-dispatch validation branches
    (Slices C/E) that today do a PG ``TraceSession`` ``.get``/``.first`` — which
    404s a *net-new* session (no PG row post-flip) and a *straddler* queried by
    its NEW deterministic id.
  • ``resolve_session_fields(trace_session_ids)`` — the curated identity
    (``external_session_id``, ``first_seen``) PLUS the PG overlay
    (``bookmarked``, ``display_name``) for a batch of ids (Slices C/D).

Both read the ``trace_sessions`` TABLE (``FINAL``), **not** ``trace_sessions_dict``,
for two reasons the dict cannot satisfy: (a) the dict (schema 018) exposes only
``external_session_id`` — it has **no ``first_seen``**, which the fields read must
return; (b) the dict's ``LIFETIME(60,120)`` means a just-written net-new row is
invisible for up to 120 s, which would make an eval-dispatch existence check
flap. Reading the RMT with ``FINAL`` gives ``first_seen`` AND immediate
visibility of the collector's dual-write row. Both resolutions are **remap-aware**
(``id_remap_sql``): a straddler answers true / resolves to ONE unified entity
whether queried by its OLD curated id or its NEW deterministic id.

``resolve_session_fields`` is therefore NOT CH-only — it overlays PG
``TraceSessionOverlay`` (the UI-sourced ``bookmarked``/``display_name``, DESIGN
§5) by the **resolved** (old/survivor) ``trace_session_id``, exactly the
Score/annotation soft-id overlay pattern. (Keying the overlay by the resolved id
— not the input id — is load-bearing: a bookmark is written on the OLD PG id, so
a straddler queried by its NEW id must resolve to the old id BEFORE the overlay
lookup or the bookmark is silently missed.)
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

import structlog

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)
from tracer.services.clickhouse.v2.query_settings import current_settings

log = structlog.get_logger("ch25.trace_session_dict_reader")

# The curated-identity TABLE (schema 018) + the id-remap TABLE (schema 019) that
# the existence/fields reads resolve against. Unqualified names: resolved in the
# connection's configured database (CH25_DATABASE) — the single dev/test/prod
# switch, same rule as the dict name above and ``end_user_dict_reader``.
_SESSIONS_TABLE = "trace_sessions"
_SESSION_REMAP = "trace_session_id_remap"

# Dictionary + attribute the external session id is read from. Unqualified dict
# name: the query runs against the connection's configured database
# (CH25_DATABASE), so the SAME code resolves ``trace_sessions_dict`` in dev /
# test (ch_test) / prod — never hard-codes ``default`` (the apply_schema
# DB-switch rule, mirrored from ``end_user_dict_reader``).
_DICT_NAME = "trace_sessions_dict"
_LABEL_ATTR = "external_session_id"

_client = None
_client_lock = threading.Lock()
# Cached client for non-empty settings contexts. At most one live client per
# distinct settings dict; replaced (old closed) when the key changes.
_settings_client = None
_settings_client_key = None


def _get_client():
    """Lazily build + cache a clickhouse-connect client (mirrors
    ``end_user_dict_reader._get_client``; kept separate so a reset here can't
    disturb the enduser reader's or writer's cached handle).

    When ``ch_query_settings`` is active, returns a cached client keyed by the
    merged settings dict. Reuses it while the key matches; closes and replaces
    the cached client when the key changes. At most one live settings-client at
    any time, no per-call leak. The empty-settings path is unchanged."""
    overrides = current_settings()
    if overrides:
        global _settings_client, _settings_client_key
        key = tuple(sorted(overrides.items()))
        with _client_lock:
            if _settings_client_key == key and _settings_client is not None:
                return _settings_client
            # Key changed or no cached settings-client: close old, build new.
            if _settings_client is not None:
                try:
                    _settings_client.close()
                except Exception:
                    pass
            import clickhouse_connect

            cfg = get_v2_config()
            _settings_client = clickhouse_connect.get_client(
                host=cfg["host"],
                port=cfg["http_port"],
                username=cfg["user"],
                password=cfg["password"] or "",
                database=cfg["database"],
                send_receive_timeout=15,
                settings=overrides,
            )
            _settings_client_key = key
        return _settings_client
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import clickhouse_connect

            cfg = get_v2_config()
            _client = clickhouse_connect.get_client(
                host=cfg["host"],
                port=cfg["http_port"],
                username=cfg["user"],
                password=cfg["password"] or "",
                database=cfg["database"],
                send_receive_timeout=15,
            )
    return _client


def _reset_client() -> None:
    global _client, _settings_client, _settings_client_key
    with _client_lock:
        try:
            if _client is not None:
                _client.close()
        except Exception:
            pass
        _client = None
        try:
            if _settings_client is not None:
                _settings_client.close()
        except Exception:
            pass
        _settings_client = None
        _settings_client_key = None


def resolve_external_session_ids(
    trace_session_ids: Iterable[object],
) -> dict[str, str | None]:
    """Batch-resolve ``{trace_session_id (str) -> external_session_id}`` from the
    CH ``trace_sessions_dict``.

    • Input ids are coerced to ``str`` and de-duplicated; ``None``/empty are
      dropped (the caller maps those to a ``None`` label without a lookup).
    • A key MISSING from the dict maps to ``None`` (faithful to the old
      PG-name-miss → NULL), via ``dictGetOrNull``.
    • A present-but-empty ``external_session_id`` (PG NULL ``name`` coerced to
      ``''`` on write) is normalized back to ``None`` so a name-less session
      renders identically OLD vs NEW.
    • Returns ``{}`` for empty input (no CH round-trip).

    The returned dict only contains keys that were looked up; callers must treat
    an absent key the same as a ``None`` value (both mean "no external id").
    """
    ids = {str(s) for s in trace_session_ids if s}
    if not ids:
        return {}

    client = _get_client()
    try:
        # arrayJoin over the literal id list resolves the whole batch in ONE
        # round-trip. dictGetOrNull keeps the missing-key → NULL semantics.
        result = client.query(
            (
                f"SELECT toString(sid), "
                f"dictGetOrNull('{_DICT_NAME}', '{_LABEL_ATTR}', sid) "
                f"FROM (SELECT arrayJoin(%(ids)s::Array(UUID)) AS sid)"
            ),
            parameters={"ids": list(ids)},
        )
    except Exception:
        # A read error is real (parity must not silently degrade). Reset the
        # cached handle so a transient CH blip doesn't wedge it, then re-raise.
        _reset_client()
        raise

    out: dict[str, str | None] = {}
    for row in result.result_rows:
        # Normalize the non-null-String '' (PG NULL name coerced on write) back
        # to None — matches the old back-fill that read NULL straight off PG.
        out[row[0]] = row[1] or None
    return out


def _resolve_existing_ids(trace_session_ids: Iterable[object]) -> dict[str, str]:
    """Core CH resolution shared by ``session_exists`` + ``resolve_session_fields``:
    map each input ``trace_session_id`` to the ``trace_session_id`` of the
    ``trace_sessions`` row it identifies (its OLD/survivor id), or omit it if no
    such row exists.

    Resolution (DESIGN §3 / ``id_remap_sql``) — ONE backbone for all three states:

      • historical (old id): no ``trace_session_id_remap`` match → resolves to
        itself → found as the ``trace_sessions`` row keyed by that old id.
      • straddler queried by NEW id: matches ``remap.new_id`` → resolves to its
        ``old_id`` (the still-primary curated key) → found, UNIFIED with the
        historical rows. Queried by the OLD id: no match → itself → same row.
      • net-new (deterministic id, collector dual-write): no remap row → resolves
        to itself → found as the ``trace_sessions`` row the dual-write keyed by
        that deterministic id.

    Returns ``{input_id (str) -> resolved_id (str)}`` containing ONLY ids that
    name a live (``is_deleted = 0``) session. The ``resolved_id`` is what the
    overlay must be keyed by (a straddler's bookmark lives on the OLD id).

    Reads ``trace_sessions FINAL`` (NOT the dict): immediate visibility of a
    just-dual-written net-new row + access to the table's own key. Project scope
    is applied by the caller (``session_exists``) or left to the caller's id set
    (``resolve_session_fields`` ids are already project-derived); the resolution
    itself is project-agnostic because the surrogate id is globally unique.
    """
    ids = {str(s) for s in trace_session_ids if s}
    if not ids:
        return {}

    client = _get_client()
    # Resolve new→old in an INNER subquery that yields (input_id, resolved_id) as
    # plain columns, THEN join the curated table on the resolved id as a plain
    # column equality — keeping the ``if(...)`` resolved expression OUT of the
    # JOIN ON (CH is finicky about expression-keyed joins; this is robust).
    resolved = resolved_id_expr("ids.sid")
    remap_join = remap_left_join("ids.sid", _SESSION_REMAP)
    try:
        result = client.query(
            (
                f"SELECT toString(r.input_id), toString(r.resolved_id) "
                f"FROM ("
                f"  SELECT ids.sid AS input_id, {resolved} AS resolved_id "
                f"  FROM (SELECT arrayJoin(%(ids)s::Array(UUID)) AS sid) AS ids "
                f"  {remap_join}"
                f") AS r "
                # `AS <alias> FINAL` (alias BEFORE FINAL) — the only order CH
                # accepts; `FINAL AS` is a syntax error. Matches remap_left_join.
                f"INNER JOIN {_SESSIONS_TABLE} AS ts FINAL "
                f"  ON ts.trace_session_id = r.resolved_id "
                f"WHERE ts.is_deleted = 0"
            ),
            parameters={"ids": list(ids)},
        )
    except Exception:
        # A read error is real (parity must not silently degrade). Reset the
        # cached handle so a transient CH blip doesn't wedge it, then re-raise.
        _reset_client()
        raise

    return {row[0]: row[1] for row in result.result_rows}


def session_exists(project_id: object, trace_session_id: object) -> bool:
    """Return ``True`` iff ``trace_session_id`` names a known, live session in
    ``project_id`` — straddler-safe and net-new-aware (P3b step2, the Slice C/E
    validation building block).

    Replaces the PG ``TraceSession.objects.filter(id=…, project=…).exists()`` /
    ``.get`` existence checks that 404 a session with no PG row (every net-new
    session post-flip) or one queried by its NEW deterministic id (a straddler).

    ``True`` for: a historical session by its old id, a straddler by EITHER its
    old or its new id (both resolve to the one curated row via
    ``trace_session_id_remap``), and a net-new session by its deterministic id
    (the collector's ``trace_sessions`` dual-write row). ``False`` for an unknown
    id, a tombstoned (``is_deleted=1``) session, or a session in a DIFFERENT
    project (the surrogate id is globally unique, so this read is project-scoped
    to stop a cross-tenant existence leak).
    """
    if not trace_session_id or not project_id:
        return False

    resolved = _resolve_existing_ids([trace_session_id])
    if not resolved:
        return False

    # _resolve_existing_ids is project-agnostic (the id is globally unique); pin
    # the project here so the check can't answer True for another tenant's
    # session. Re-read the single resolved id under the project filter (one cheap
    # point lookup; FINAL for the same just-dual-written visibility).
    resolved_id = next(iter(resolved.values()))
    client = _get_client()
    try:
        result = client.query(
            (
                f"SELECT 1 FROM {_SESSIONS_TABLE} FINAL "
                f"WHERE trace_session_id = %(sid)s AND project_id = %(pid)s "
                f"  AND is_deleted = 0 LIMIT 1"
            ),
            parameters={"sid": resolved_id, "pid": str(project_id)},
        )
    except Exception:
        _reset_client()
        raise
    return bool(result.result_rows)


def resolve_session_fields(
    trace_session_ids: Iterable[object],
    *,
    project_id: str | None = None,
) -> dict[str, dict[str, object]]:
    """Batch-resolve ``{trace_session_id (str) -> {external_session_id,
    first_seen, project_id, bookmarked, display_name}}`` — the curated CH identity
    overlaid with the PG user fields (P3b step2, the Slice C/D building block).

    Replaces the PG ``TraceSession.objects.get(...)`` field reads that 404 a
    net-new / straddler-by-new-id session. The record unifies:

      • ``external_session_id`` / ``first_seen`` / ``project_id`` — from the CH
        ``trace_sessions`` RMT (``FINAL``), resolved through
        ``trace_session_id_remap`` so a straddler returns its (old) survivor row
        whether queried by old or new id, and a net-new session returns its
        dual-write row. ``project_id`` is the session's owning tenant (Slice D:
        the eval-context detail org-scopes on it and feeds it to
        ``session_trace_ids``, which has no other way to learn a net-new session's
        project).
      • ``bookmarked`` / ``display_name`` — overlaid from PG ``TraceSessionOverlay``
        (DESIGN §5), one cheap soft-id query keyed by the **resolved** id. A
        session with no overlay row → ``bookmarked=False`` / ``display_name=None``
        (the un-bookmarked, un-renamed default). The overlay is keyed by the
        OLD/survivor id, so a straddler's UI bookmark (written on the old PG id)
        is still found when the session is queried by its NEW id.

    Semantics:
      • Input ids are coerced to ``str`` + de-duplicated; ``None``/empty dropped.
      • A MISSING id (no live ``trace_sessions`` row) is **absent** from the
        result — the caller decides 404 (mirrors the old ``.get`` raising).
      • ``external_session_id`` ``''`` (PG NULL ``name`` coerced on write) is
        normalized back to ``None`` — same as ``resolve_external_session_ids``.
      • ``project_id`` is returned as a ``str``.
      • When several input ids resolve to the SAME survivor (straddler old+new
        both passed), each input id maps to its own copy of the one entity.
      • ``project_id`` (optional kwarg): scope the WHERE to one tenant, pruning on
        the ``trace_sessions`` ORDER BY ``(project_id, trace_session_id)``
        sort-key prefix so an eval-path caller reads ~its own sessions instead
        of the whole table.
      • Returns ``{}`` for empty input (no CH round-trip).
    """
    ids = {str(s) for s in trace_session_ids if s}
    if not ids:
        return {}

    client = _get_client()
    resolved = resolved_id_expr("ids.sid")
    remap_join = remap_left_join("ids.sid", _SESSION_REMAP)
    params: dict[str, object] = {"ids": list(ids)}
    project_clause = ""
    if project_id:
        params["pid"] = str(project_id)
        project_clause = " AND ts.project_id = %(pid)s"
    try:
        # Resolve new→old in the inner subquery (plain (input_id, resolved_id)
        # columns), join the curated table on the resolved id as a plain column,
        # and pull external_session_id/first_seen/project_id off the survivor row.
        # FINAL for immediate visibility of the net-new dual-write + RMT
        # latest-wins.
        result = client.query(
            (
                f"SELECT toString(r.input_id), toString(r.resolved_id), "
                f"ts.external_session_id, ts.first_seen, toString(ts.project_id) "
                f"FROM ("
                f"  SELECT ids.sid AS input_id, {resolved} AS resolved_id "
                f"  FROM (SELECT arrayJoin(%(ids)s::Array(UUID)) AS sid) AS ids "
                f"  {remap_join}"
                f") AS r "
                # alias BEFORE FINAL (CH syntax); see _resolve_existing_ids.
                f"INNER JOIN {_SESSIONS_TABLE} AS ts FINAL "
                f"  ON ts.trace_session_id = r.resolved_id "
                f"WHERE ts.is_deleted = 0{project_clause}"
            ),
            parameters=params,
        )
    except Exception:
        _reset_client()
        raise

    out: dict[str, dict[str, object]] = {}
    resolved_by_input: dict[str, str] = {}
    for row in result.result_rows:
        input_id, resolved_id, external, first_seen, proj_id = row
        resolved_by_input[input_id] = resolved_id
        out[input_id] = {
            # '' (PG NULL name coerced on write) → None, parity with the old
            # PG-name read. Overlay defaults filled below.
            "external_session_id": external or None,
            "first_seen": first_seen,
            "project_id": proj_id,
            "bookmarked": False,
            "display_name": None,
        }

    if not out:
        return out

    # Overlay the PG user fields by the RESOLVED (old/survivor) id — one query.
    # Lazy import: this module is otherwise CH-only and import-cycle-sensitive.
    from tracer.models.trace_session import TraceSessionOverlay

    survivor_ids = set(resolved_by_input.values())
    overlay_by_resolved: dict[str, dict[str, object]] = {}
    for tsid, bookmarked, display_name in TraceSessionOverlay.objects.filter(
        trace_session_id__in=survivor_ids
    ).values_list("trace_session_id", "bookmarked", "display_name"):
        overlay_by_resolved[str(tsid)] = {
            "bookmarked": bool(bookmarked),
            "display_name": display_name,
        }

    for input_id, record in out.items():
        ov = overlay_by_resolved.get(resolved_by_input[input_id])
        if ov is not None:
            record["bookmarked"] = ov["bookmarked"]
            record["display_name"] = ov["display_name"]
    return out
