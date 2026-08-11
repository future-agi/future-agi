"""end_user_dict_reader — batch ``user_id`` label lookups from the CH
``end_users_dict`` (DESIGN §4.3, the EndUser reads cutover).

Why this exists
---------------
Before the CH-derived-dimensions migration, callers resolved an EndUser's
external ``user_id`` label by traversing the PG ``ObservationSpan.end_user`` FK
into ``tracer_enduser`` (``end_user__user_id``). Once EndUser moves to CH, the PG
``tracer_enduser`` table is gone, so the label now lives in the CH
``end_users_dict`` keyed by ``end_user_id`` (the span's OWN soft-id column).

A CH ``dictGet`` cannot run inside a PG queryset, so the read paths that used a
correlated ``Subquery``/``OuterRef`` must RESTRUCTURE: resolve the per-entity
``end_user_id`` in PG (a plain column read, no FK join), then call this module to
batch-resolve the ``{end_user_id -> user_id}`` labels from CH, and merge in
Python. This module is the CH half of that restructure.

Faithfulness to the old FK semantics (the parity contract)
----------------------------------------------------------
The old ``Subquery(...values("end_user__user_id"))`` yields **NULL** in three
cases: the span's ``end_user_id`` is NULL, the FK points at a row that does not
exist (``db_constraint=False`` allows orphans), or the joined ``user_id`` is
NULL. We reproduce exactly:

  • Callers never pass a NULL ``end_user_id`` here (they map it to ``None``
    label without calling this module), covering the first case.
  • For a present ``end_user_id`` we use ``dictGetOrNull`` — a MISSING dict key
    returns NULL (NOT the column's ``''`` default that plain ``dictGet`` would
    give), so an orphan id resolves to ``None`` exactly like the FK miss did.

This module is read-only and best-effort-free: a failure here is a real read
error (unlike the ingest dual-write, parity reads must surface problems), so it
does NOT swallow exceptions.
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

log = structlog.get_logger("ch25.end_user_dict_reader")

# Dictionary + attribute the label is read from. Unqualified dict name: the
# query runs against the connection's configured database (CH25_DATABASE), so
# the SAME code resolves ``end_users_dict`` in dev / test (ch_test) / prod —
# never hard-codes ``default`` (the schema/apply_schema DB-switch rule).
_DICT_NAME = "end_users_dict"
_LABEL_ATTR = "user_id"

# Extra curated attributes the session-detail read (`_fetch_end_user_info`)
# needs beyond the bare `user_id` label. Both are exposed by `end_users_dict`
# (schema 017): `user_id_type` is Nullable(String) — round-trips None/'' faith-
# fully; `user_id_hash` is a non-null String (PG NULL coerced to '' on write).
_TYPE_ATTR = "user_id_type"
_HASH_ATTR = "user_id_hash"

# Sentinel distinguishing "user_id_type filter omitted" (match on user_id alone)
# from an explicit ``user_id_type=None`` (match the NULL/'' type). A bare default
# of ``None`` could not tell the two apart — and they mean different SQL.
_UNSET = object()

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Lazily build + cache a clickhouse-connect client (mirrors
    ``curated_writer._get_client``; kept separate so a reset here can't disturb
    the writer's cached handle)."""
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
    global _client
    with _client_lock:
        try:
            if _client is not None:
                _client.close()
        except Exception:
            pass
        _client = None


def resolve_user_ids(end_user_ids: Iterable[object]) -> dict[str, str | None]:
    """Batch-resolve ``{end_user_id (str) -> user_id label}`` from the CH
    ``end_users_dict``.

    • Input ids are coerced to ``str`` and de-duplicated; ``None``/empty are
      dropped (the caller maps those to a ``None`` label without a lookup).
    • A key MISSING from the dict maps to ``None`` (faithful to the old FK-miss
      → NULL), via ``dictGetOrNull``.
    • Returns ``{}`` for empty input (no CH round-trip).

    The returned dict only contains keys that were looked up; callers must treat
    an absent key the same as a ``None`` value (both mean "no label").
    """
    ids = {str(e) for e in end_user_ids if e}
    if not ids:
        return {}

    client = _get_client()
    resolved = resolved_id_expr("ids.eid")
    remap_join = remap_left_join("ids.eid", "end_user_id_remap")
    try:
        # arrayJoin over the literal id list resolves the whole batch in ONE
        # round-trip. dictGetOrNull keeps the missing-key → NULL semantics.
        #
        # P3b step1.5 (DESIGN §3 / id_remap_sql): a NEW (deterministic) span id is
        # NOT a key in end_users_dict (the dict is OLD-keyed). LEFT JOIN
        # end_user_id_remap to resolve the lookup id new→old, then dictGet on the
        # RESOLVED id. The returned KEY is the ORIGINAL input id (`eid`) — callers
        # key their result by the id they passed (a span's stored end_user_id),
        # so a new-id span looks up its label by its new id and still gets the
        # curated label. Pre-flip NO id matches a `new_id`, so the resolved id ==
        # the input id and this is byte-identical (gate B).
        result = client.query(
            (
                f"SELECT toString(ids.eid), "
                f"dictGetOrNull('{_DICT_NAME}', '{_LABEL_ATTR}', {resolved}) "
                f"FROM (SELECT arrayJoin(%(ids)s::Array(UUID)) AS eid) AS ids "
                f"{remap_join}"
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
        out[row[0]] = row[1]
    return out


def resolve_end_user_fields(
    end_user_ids: Iterable[object],
) -> dict[str, dict[str, str | None]]:
    """Batch-resolve ``{end_user_id (str) -> {user_id, user_id_type,
    user_id_hash}}`` from the CH ``end_users_dict`` — the curated fields the
    session-detail read (``_fetch_end_user_info``) used to traverse the PG
    ``ObservationSpan.end_user`` FK for (DESIGN §4.3, §5.2).

    Faithfulness to the old ``end_user__user_id``/``__user_id_type``/
    ``__user_id_hash`` FK reads (the parity contract):

    • A key MISSING from the dict (orphan / no curated row) → every field
      ``None``, via ``dictGetOrNull`` — exactly like the old FK miss.
    • ``user_id`` and ``user_id_hash`` are non-null String columns: the writer
      coerces PG NULL → ``''`` (schema 017 / ``curated_writer``). The old FK read
      surfaced those NULLs as ``None``, so we normalize ``''`` → ``None`` here to
      match. (A genuine empty-string value would also collapse to ``None`` —
      accepted; both are display labels and the case is unobserved on the box.)
    • ``user_id_type`` is ``Nullable(String)`` in BOTH the column and the dict,
      so ``dictGetOrNull`` round-trips None-vs-``''`` faithfully — it is **NOT**
      normalized (a row with a genuine ``''`` type must stay ``''`` to match the
      old FK value, NOT collapse to ``None``).

    • Input ids are coerced to ``str`` and de-duplicated; ``None``/empty are
      dropped (the caller maps those to an all-``None`` record without a lookup).
    • Returns ``{}`` for empty input (no CH round-trip). Callers must treat an
      absent key the same as an all-``None`` record.
    """
    ids = {str(e) for e in end_user_ids if e}
    if not ids:
        return {}

    client = _get_client()
    resolved = resolved_id_expr("ids.eid")
    remap_join = remap_left_join("ids.eid", "end_user_id_remap")
    try:
        # arrayJoin over the literal id list resolves the whole batch in ONE
        # round-trip. dictGetOrNull keeps the missing-key → NULL semantics for
        # every attribute.
        #
        # P3b step1.5 (DESIGN §3 / id_remap_sql): resolve the lookup id new→old
        # through end_user_id_remap, then dictGet every attribute on the RESOLVED
        # id (a new-id span is not a key in the OLD-keyed dict). The returned KEY
        # stays the ORIGINAL input id (`eid`) so callers key by the id they
        # passed. Pre-flip a no-op (no id matches a `new_id`) → gate B.
        result = client.query(
            (
                f"SELECT toString(ids.eid), "
                f"dictGetOrNull('{_DICT_NAME}', '{_LABEL_ATTR}', {resolved}), "
                f"dictGetOrNull('{_DICT_NAME}', '{_TYPE_ATTR}', {resolved}), "
                f"dictGetOrNull('{_DICT_NAME}', '{_HASH_ATTR}', {resolved}) "
                f"FROM (SELECT arrayJoin(%(ids)s::Array(UUID)) AS eid) AS ids "
                f"{remap_join}"
            ),
            parameters={"ids": list(ids)},
        )
    except Exception:
        _reset_client()
        raise

    out: dict[str, dict[str, str | None]] = {}
    for row in result.result_rows:
        out[row[0]] = {
            # NULL hash/user_id coerced to '' on write → back to None (parity
            # with the old FK-NULL). user_id_type is Nullable end-to-end → keep
            # the dict value verbatim (None stays None, '' stays '').
            "user_id": row[1] or None,
            "user_id_type": row[2],
            "user_id_hash": row[3] or None,
        }
    return out


def resolve_end_user_ids_by_user_id(
    user_id: object,
    *,
    project_id: object | None = None,
    organization_id: object | None = None,
    user_id_type: object | None = _UNSET,
) -> list[str]:
    """Reverse-resolve a human ``user_id`` label → the curated ``end_user_id``
    SET from the CH ``end_users`` RMT (the state-robust reverse lookup,
    PG_ORM_READ_MIGRATION §"The state-robust reverse-resolve pattern").

    This is the CH replacement for the PG ``EndUser.objects.get/filter(
    user_id=…, project_id=…/organization=…)`` natural-key→surrogate lookups that
    go STALE for a NET-NEW user post-step2 (no PG row is ever written for an
    identity first seen after the ingest ``get_or_create`` is dropped). Reading
    the curated ``end_users`` dimension instead catches all three post-flip
    cases in ONE query, with NO ``deterministic_id`` compute (so it works for a
    ``user_id``-only filter where ``user_id_type`` is unavailable):

      • historical user — its OLD-id ``end_users`` row,
      • net-new user — its post-flip deterministic-id ``end_users`` row (rides
        the best-effort ingest dual-write; filterable a few seconds late),
      • straddler — BOTH rows pre-consolidation-sweep, the survivor after.

    The returned ids are the CURATED keys (OLD pre-sweep, survivor post-sweep).
    Callers feed them to a spans filter that resolves each span's stored
    ``end_user_id`` new→old via ``end_user_id_remap`` and matches THIS set
    (``resolved_id_expr(end_user_id) IN <set>``): a historical/net-new span
    (no remap entry) resolves to itself and matches its own curated id; a
    straddler's new-id span resolves to the old id and matches. This is exactly
    the id-set the committed ``filters._build_enduser_string_condition`` builds
    inline — exposed here so a VIEW-level resolve (which cannot embed a CH
    subquery in a PG queryset) gets the same state-robust semantics. NO remap
    is applied HERE: ``end_users`` already holds the curated keys; the remap
    belongs on the SPANS side at filter time.

    Scope (mirrors the PG queries' ``project_id`` / ``organization`` scoping —
    ``end_users`` carries BOTH columns, schema 017): pass ``project_id`` for the
    project-scoped lookup (1265/2768), or ``organization_id`` for the org-wide
    session view's cross-project user filter (2014, org-scope mode), or both
    (2014, project mode). At least one MUST be given — an unscoped reverse
    lookup would leak ids across tenants; a missing scope raises ``ValueError``.

    Parameters
    ----------
    user_id : the external user-id string (coerced to ``str``).
    project_id : optional project UUID scope.
    organization_id : optional organization UUID scope.
    user_id_type : when supplied (including ``None``), adds an exact
        ``user_id_type`` match — ``None``/``''`` map to the SQL ``''`` the
        writer stores for a NULL type (schema 017 coerces NULL→'' on write), so
        a typed filter round-trips faithfully. OMIT it (the default) to match
        on ``user_id`` ALONE across every type — the common product filter,
        and the form that avoids the ``user_id_type``-availability trap.

    Returns a de-duplicated ``list[str]`` of ``end_user_id`` values (``[]`` when
    nothing matches — the caller treats that as "no such user", an EMPTY id-set
    that filters to zero rows). Read-only and NOT best-effort: a CH failure is a
    real read error and re-raises (parity reads must surface problems).
    """
    if project_id is None and organization_id is None:
        raise ValueError(
            "resolve_end_user_ids_by_user_id requires project_id and/or "
            "organization_id (an unscoped reverse lookup would cross tenants)"
        )
    if user_id is None or str(user_id) == "":
        return []

    client = _get_client()
    conds = ["user_id = %(uid)s", "is_deleted = 0"]
    params: dict[str, object] = {"uid": str(user_id)}
    if project_id is not None:
        conds.append("project_id = %(pid)s")
        params["pid"] = str(project_id)
    if organization_id is not None:
        conds.append("organization_id = %(oid)s")
        params["oid"] = str(organization_id)
    if user_id_type is not _UNSET:
        # NULL/'' type → the '' the writer stores for a PG-NULL type (017).
        conds.append("user_id_type = %(utype)s")
        params["utype"] = "" if user_id_type in (None, "") else str(user_id_type)
    where = " AND ".join(conds)
    try:
        result = client.query(
            f"SELECT DISTINCT toString(end_user_id) FROM end_users FINAL WHERE {where}",
            parameters=params,
        )
    except Exception:
        _reset_client()
        raise

    return [row[0] for row in result.result_rows if row[0]]
