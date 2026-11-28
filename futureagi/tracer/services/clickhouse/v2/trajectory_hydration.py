"""Read-side hydration for Phase 0 sentinel compaction and Phase 1 Trajectory Store.

Mirrors the Go write-side contract in ``fi-collector/pkg/trajectory/``:

  - Phase 0 (intra-span compaction):
      ``__fi_promoted_input_key`` / ``__fi_promoted_output_key`` (plus optional
      ``__fi_promoted_*_json`` boolean flags) sit in ``attributes_extra`` when
      duplicate promoted payloads were stripped at ingest. Readers pop these
      sentinels and restore the exact attribute from ``spans.input`` /
      ``spans.output`` (after Phase 1 hydration, when applicable).

  - Phase 1 (content-addressed trajectory store):
      Spans with ``payload_encoding == 'items_v1'`` store compact previews in
      ``spans.input`` / ``spans.output`` alongside ordered hash lists
      (``input_item_hashes``, ``output_item_hashes``, ``tool_def_hashes``),
      ``item_scope_id``, and ``payload_envelope``. A single batched ClickHouse
      read against ``trajectory_items`` keyed by ``(project_id, scope_id)``
      fetches the items, and each extracted side is rebuilt by concatenating
      the envelope's literal gaps with the item contents in slot order. No
      JSON is parsed or re-serialised, so the result is the exact string the
      collector received, checked against the envelope's checksum.

      Envelope (version 2, written by ``pkg/trajectory/reconstruct.go``)::

          {"v": 2,
           "in":  {"g": [gap0, ..., gapN], "r": "smmt...", "sh": "p:<hash>",
                   "il": <code points>, "x": "<sha256 prefix>"},
           "out": {"g": [...], "r": "mm...", "il": ..., "x": ...}}

      ``r`` has one character per slot: ``m`` takes the next entry of the
      side's ``*_item_hashes``, ``t`` the next ``tool_def_hashes`` entry and
      ``s`` the side's ``sh``. ``g`` has ``len(r) + 1`` literals and the side
      is ``g0 + c(r0) + g1 + ... + gN``. ``x`` is the hex of the first 16
      bytes of SHA-256 over the side's UTF-8 bytes, the same digest prefix as
      ``item_hash``.

      Legacy ``payload_encoding == 'inline'`` spans trigger ZERO queries
      against ``trajectory_items``.

  - Read state (``trajectory_read_state``): NOT_READY, READY or KILLED, from
    a schema probe, so the pointer columns are never named before 040 is
    applied and a preview is never served as content once items exist.

  - Failure vocabulary: ``TrajectoryHydrationUnavailable`` (retryable: a
    probe or items query failed, or a fresh span's items are not yet
    visible) and ``TrajectoryContentUnavailable`` (the content is gone:
    missing item, checksum mismatch, kill switch). A side whose content is
    gone keeps its preview for display and is recorded as ``missing``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_SCOPE_ID = "__project__"
PROJECT_HASH_PREFIX = "p:"

ENCODING_INLINE = "inline"
ENCODING_ITEMS_V1 = "items_v1"

SENTINEL_PROMOTED_INPUT_KEY = "__fi_promoted_input_key"
SENTINEL_PROMOTED_INPUT_JSON = "__fi_promoted_input_json"
SENTINEL_PROMOTED_OUTPUT_KEY = "__fi_promoted_output_key"
SENTINEL_PROMOTED_OUTPUT_JSON = "__fi_promoted_output_json"

_SENTINEL_MARKER = "__fi_promoted_"

ENVELOPE_VERSION = 2
SLOT_MESSAGE = "m"
SLOT_TOOL = "t"
SLOT_SYSTEM = "s"


class TrajectoryContentUnavailable(Exception):
    """Stored trajectory content is not there or does not rebuild into the
    original payload (missing item, checksum mismatch, kill switch).

    Hydrators record the side as ``missing`` and keep the preview for
    display; consumers that must not use a preview (evals, datasets,
    Insights, RCA) raise this. Not retryable: the content is gone.
    """

    def __init__(
        self,
        message: str = "trajectory content unavailable",
        *,
        span_ids: tuple[str, ...] = (),
        surface: str = "",
    ):
        self.span_ids = span_ids
        self.surface = surface
        super().__init__(message)


def payload_checksum(text: str) -> str:
    """Hex of the first 16 bytes of SHA-256 over ``text`` (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).digest()[:16].hex()


TRAJECTORY_SPAN_COLUMNS: tuple[str, ...] = (
    "input_item_hashes",
    "output_item_hashes",
    "tool_def_hashes",
    "item_scope_id",
    "payload_encoding",
    "payload_envelope",
)


# --------------------------------------------------------------------------- #
# Read state: whether this process may project the pointer columns and fetch
# trajectory_items. One value per call, resolved from a schema probe.
# --------------------------------------------------------------------------- #


class TrajectoryReadState(StrEnum):
    """``NOT_READY``: 040 is not (fully) applied, or the flag is false while
    ``trajectory_items`` is empty; readers emit exactly the dev SQL.
    ``READY``: pointers are projected and items are fetched.
    ``KILLED``: pointers are projected and nothing is fetched; every
    extracted side is reported as content-unavailable, never as content."""

    NOT_READY = "not_ready"
    READY = "ready"
    KILLED = "killed"

    @property
    def projects_pointers(self) -> bool:
        return self is not TrajectoryReadState.NOT_READY

    @property
    def fetches_items(self) -> bool:
        return self is TrajectoryReadState.READY


class TrajectoryHydrationUnavailable(RuntimeError):
    """Trajectory content cannot be read right now (probe or items query
    failure, items not yet visible on this replica). Retryable: views answer
    503 ``service_unavailable`` and background consumers retry. Never turned
    into a preview."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# Per-side content states recorded by the hydrators.
CONTENT_INLINE = "inline"
CONTENT_HYDRATED = "hydrated"
CONTENT_MISSING = "missing"
# Dict rows carry ``row[CONTENT_STATE_KEY] = {"input": s, "output": s}``
# (items_v1 rows only); CHSpan carries ``content_state = (input, output)``.
CONTENT_STATE_KEY = "_content_state"
# The UI JSON key listing the sides whose full content is unavailable.
CONTENT_UNAVAILABLE_KEY = "content_unavailable"
CSV_UNAVAILABLE_PREFIX = "[preview — full content unavailable] "

# Indirection so tests drive the cache clock without patching ``time``.
_monotonic = time.monotonic

READ_STATE_NEGATIVE_TTL_S = 15.0
# A probe error reuses the last state a probe returned this recently.
READ_STATE_PROBE_ERROR_GRACE_S = 60.0
# A missing item on a span written this recently is replica lag, not loss.
FRESH_MISSING_WINDOW = timedelta(minutes=10)
DEFAULT_PROBE_ENDPOINT = "ch25"

ProbeRun = Callable[[str, dict[str, Any], tuple[str, ...]], list[tuple[Any, ...]]]


@dataclass(frozen=True)
class ReadStateProbe:
    """Where the schema probe runs. ``endpoint`` keys the state cache, so two
    databases never share a latch; ``run(sql, params, names)`` returns tuple
    rows in ``names`` order."""

    endpoint: str
    run: ProbeRun


def client_read_state_probe(client: Any, *, endpoint: str) -> ReadStateProbe:
    """A probe on a clickhouse-connect style client (``query().result_rows``)."""

    def run(sql: str, params: dict[str, Any], names: tuple[str, ...]):
        del names
        return [tuple(r) for r in client.query(sql, parameters=params).result_rows]

    return ReadStateProbe(endpoint, run)


def analytics_read_state_probe(analytics: Any, *, endpoint: str) -> ReadStateProbe:
    """A probe on an analytics executor (``execute_ch_query`` dict rows)."""

    def run(sql: str, params: dict[str, Any], names: tuple[str, ...]):
        result = analytics.execute_ch_query(
            sql, params, timeout_ms=5_000, settings={"max_threads": 1}
        )
        return [tuple(row.get(n) for n in names) for row in (result.data or [])]

    return ReadStateProbe(endpoint, run)


def _default_probe() -> ReadStateProbe:
    """The CH25 endpoint every span reader uses (``CLICKHOUSE_V2``)."""

    def run(sql: str, params: dict[str, Any], names: tuple[str, ...]):
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        probe = analytics_read_state_probe(
            V2AnalyticsQueryService(), endpoint=DEFAULT_PROBE_ENDPOINT
        )
        return probe.run(sql, params, names)

    return ReadStateProbe(DEFAULT_PROBE_ENDPOINT, run)


def _resolve_probe(source: Any) -> ReadStateProbe:
    if isinstance(source, ReadStateProbe):
        return source
    hook = getattr(source, "trajectory_read_probe", None) if source else None
    if isinstance(hook, ReadStateProbe):
        return hook
    if callable(hook):
        probe = hook()
        if isinstance(probe, ReadStateProbe):
            return probe
    return _default_probe()


def _setting(name: str, default: Any) -> Any:
    try:
        from django.conf import settings as django_settings
        from django.core.exceptions import ImproperlyConfigured
    except ImportError:
        return default
    try:
        return getattr(django_settings, name, default)
    except ImproperlyConfigured:
        return default


def _truthy(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def _read_flag() -> bool | None:
    """``FI_TRAJECTORY_STORE_READ_ENABLED``: True, False, or None (auto)."""
    return _truthy(_setting("FI_TRAJECTORY_STORE_READ_ENABLED", None))


def _kill_switch() -> bool:
    return bool(_truthy(_setting("FI_TRAJECTORY_HYDRATION_KILL", False)))


def _probe_cluster() -> str:
    return str(_setting("FI_TRAJECTORY_READ_PROBE_CLUSTER", "") or "").strip()


_PROBE_COLUMNS_SQL = (
    "SELECT name, type, default_kind, default_expression FROM system.columns "
    "WHERE database = currentDatabase() AND table = 'spans' "
    "AND name IN %(traj_probe_columns)s"
)
_PROBE_TABLE_SQL = (
    "SELECT total_rows FROM system.tables "
    "WHERE database = currentDatabase() AND name = 'trajectory_items'"
)
_PROBE_DATABASE_SQL = "SELECT currentDatabase() AS database"
_PROBE_CLUSTER_HOSTS_SQL = (
    "SELECT DISTINCT hostName() AS host "
    "FROM clusterAllReplicas(%(traj_probe_cluster)s, system.one)"
)
_PROBE_CLUSTER_COLUMNS_SQL = (
    "SELECT hostName() AS host, name, type, default_kind, default_expression "
    "FROM clusterAllReplicas(%(traj_probe_cluster)s, system.columns) "
    "WHERE database = %(traj_probe_database)s AND table = 'spans' "
    "AND name IN %(traj_probe_columns)s"
)
_COLUMN_NAMES = ("name", "type", "default_kind", "default_expression")


@dataclass(frozen=True)
class SchemaFacts:
    """What one probe saw. ``exact``: pointer columns with the 040 type and
    default; ``different``: pointer columns declared otherwise."""

    exact: frozenset[str]
    different: frozenset[str]
    table_present: bool
    total_rows: int | None
    lagging_replicas: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            self.exact == frozenset(TRAJECTORY_SPAN_COLUMNS)
            and not self.different
            and self.table_present
            and not self.lagging_replicas
        )

    @property
    def absent(self) -> bool:
        return not self.exact and not self.different and not self.table_present


def _exact_040_columns(rows: Iterable[Sequence[Any]]) -> tuple[set[str], set[str]]:
    """Split probed ``(name, type, default_kind, default_expression)`` rows
    into exact-040 and differently-declared pointer columns, with the
    comparison the OSS inspector makes (``oss_cdc_bootstrap._check_table``)."""
    from tracer.services.clickhouse import oss_cdc_bootstrap as core

    exact: set[str] = set()
    different: set[str] = set()
    for row in rows:
        if len(row) < 4:
            continue
        name, typ, kind, expr = (str(v) if v is not None else "" for v in row[:4])
        declared = core._TRAJECTORY_SPAN_COLUMNS.get(name)
        if declared is None:
            continue
        actual = (core._tokens(typ), kind, core._tokens(expr))
        if actual == core._column(core._tokens(declared)):
            exact.add(name)
        else:
            different.add(name)
    return exact, different


def probe_trajectory_schema(probe: ReadStateProbe, *, cluster: str = "") -> SchemaFacts:
    """Run the schema probe. Raises whatever the executor raises."""
    params = {"traj_probe_columns": TRAJECTORY_SPAN_COLUMNS}
    exact, different = _exact_040_columns(
        probe.run(_PROBE_COLUMNS_SQL, params, _COLUMN_NAMES)
    )
    tables = probe.run(_PROBE_TABLE_SQL, {}, ("total_rows",))
    table_present = bool(tables)
    total_rows: int | None = None
    if tables and tables[0] and tables[0][0] is not None:
        total_rows = int(tables[0][0])
    lagging: list[str] = []
    if cluster:
        database_rows = probe.run(_PROBE_DATABASE_SQL, {}, ("database",))
        database = str(database_rows[0][0]) if database_rows else ""
        cluster_params = {
            **params,
            "traj_probe_cluster": cluster,
            "traj_probe_database": database,
        }
        hosts = {
            str(r[0])
            for r in probe.run(
                _PROBE_CLUSTER_HOSTS_SQL, {"traj_probe_cluster": cluster}, ("host",)
            )
        }
        by_host: dict[str, list[tuple[Any, ...]]] = {h: [] for h in hosts}
        for row in probe.run(
            _PROBE_CLUSTER_COLUMNS_SQL, cluster_params, ("host", *_COLUMN_NAMES)
        ):
            by_host.setdefault(str(row[0]), []).append(tuple(row[1:]))
        for host, rows in sorted(by_host.items()):
            host_exact, host_different = _exact_040_columns(rows)
            if host_exact != set(TRAJECTORY_SPAN_COLUMNS) or host_different:
                lagging.append(host)
        if not hosts:
            raise RuntimeError(f"cluster {cluster!r} reported no replicas")
    return SchemaFacts(
        exact=frozenset(exact),
        different=frozenset(different),
        table_present=table_present,
        total_rows=total_rows,
        lagging_replicas=tuple(lagging),
    )


_state_lock = threading.Lock()
# (endpoint, flag, cluster) -> (state, expires_at monotonic; None = latched).
_state_cache: dict[
    tuple[str, bool | None, str], tuple[TrajectoryReadState, float | None]
] = {}
_last_known: dict[tuple[str, bool | None, str], tuple[TrajectoryReadState, float]] = {}
_logged_once: set[str] = set()
_pinned_state: ContextVar[TrajectoryReadState | None] = ContextVar(
    "trajectory_read_state_pinned", default=None
)


def _log_error_once(event: str, message: str, *args: Any) -> None:
    with _state_lock:
        if event in _logged_once:
            return
        _logged_once.add(event)
    logger.error(message, *args)


def _state_from_facts(
    facts: SchemaFacts, flag: bool | None, endpoint: str
) -> TrajectoryReadState:
    if facts.complete:
        if flag is False:
            if facts.total_rows == 0:
                return TrajectoryReadState.NOT_READY
            # Items exist: turning hydration off would serve previews as
            # content, so the flag is ignored.
            _log_error_once(
                "trajectory_read_flag_overridden",
                "trajectory_read_flag_overridden endpoint=%s total_rows=%s",
                endpoint,
                facts.total_rows,
            )
        return TrajectoryReadState.READY
    if not facts.absent:
        logger.error(
            "trajectory_schema_partial endpoint=%s exact=%s different=%s "
            "table_present=%s lagging_replicas=%s",
            endpoint,
            sorted(facts.exact),
            sorted(facts.different),
            facts.table_present,
            list(facts.lagging_replicas),
        )
    if flag is True:
        logger.error(
            "trajectory_read_flag_refused endpoint=%s reason=%s",
            endpoint,
            "schema_absent" if facts.absent else "schema_partial",
        )
    return TrajectoryReadState.NOT_READY


def _base_read_state(probe: ReadStateProbe) -> TrajectoryReadState:
    flag = _read_flag()
    cluster = _probe_cluster()
    key = (probe.endpoint, flag, cluster)
    now = _monotonic()
    with _state_lock:
        cached = _state_cache.get(key)
    if cached is not None and (cached[1] is None or cached[1] > now):
        return cached[0]
    try:
        facts = probe_trajectory_schema(probe, cluster=cluster)
    except Exception as exc:
        with _state_lock:
            last = _last_known.get(key)
        if last is not None and now - last[1] <= READ_STATE_PROBE_ERROR_GRACE_S:
            logger.warning(
                "trajectory_read_state_probe_failed endpoint=%s error=%s "
                "using_last_known=%s",
                probe.endpoint,
                type(exc).__name__,
                last[0].value,
            )
            return last[0]
        logger.error(
            "trajectory_read_state_probe_failed endpoint=%s error=%s",
            probe.endpoint,
            type(exc).__name__,
        )
        raise TrajectoryHydrationUnavailable("read_state_probe_failed") from exc
    state = _state_from_facts(facts, flag, probe.endpoint)
    with _state_lock:
        _state_cache[key] = (
            state,
            None
            if state is TrajectoryReadState.READY
            else now + READ_STATE_NEGATIVE_TTL_S,
        )
        _last_known[key] = (state, now)
    return state


def trajectory_read_state(source: Any = None) -> TrajectoryReadState:
    """The read state for one call.

    A state pinned by ``read_state_snapshot`` / ``pinned_read_state`` wins.
    Otherwise the schema is probed on ``source`` (a ``ReadStateProbe``, or an
    object whose ``trajectory_read_probe`` names one), by default the CH25
    endpoint the span readers use. READY latches for the process; a negative
    result is cached for 15 s; a probe error is never cached and raises
    ``TrajectoryHydrationUnavailable`` unless a probe succeeded within the
    last 60 s, whose state is reused.

    ``FI_TRAJECTORY_STORE_READ_ENABLED``: true still probes and stays
    NOT_READY when the columns are absent (logged); false is honoured only
    while ``trajectory_items`` has no rows; unset is auto.
    ``FI_TRAJECTORY_HYDRATION_KILL`` turns READY into KILLED.
    """
    pinned = _pinned_state.get()
    if pinned is not None:
        return pinned
    base = _base_read_state(_resolve_probe(source))
    if base is TrajectoryReadState.READY and _kill_switch():
        _log_error_once("trajectory_hydration_killed", "trajectory_hydration_killed")
        return TrajectoryReadState.KILLED
    return base


@contextmanager
def pinned_read_state(state: TrajectoryReadState) -> Iterator[TrajectoryReadState]:
    """Use ``state`` for every read in this context (one snapshot per call)."""
    token = _pinned_state.set(state)
    try:
        yield state
    finally:
        _pinned_state.reset(token)


@contextmanager
def read_state_snapshot(source: Any = None) -> Iterator[TrajectoryReadState]:
    """Resolve the state once and pin it, so SQL built and rows decoded in
    this context agree. A state already pinned by a caller is kept."""
    pinned = _pinned_state.get()
    if pinned is not None:
        yield pinned
        return
    with pinned_read_state(trajectory_read_state(source)) as state:
        yield state


def reset_read_state_cache() -> None:
    """Forget every probed state (tests, and an operator's reload hook)."""
    with _state_lock:
        _state_cache.clear()
        _last_known.clear()
        _logged_once.clear()


def _normalize_hash(value: bytes | bytearray | str) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("ascii", errors="ignore").rstrip("\x00")
    return str(value).rstrip("\x00")


def _resolve_scope_and_hash(prefixed_hash: str, default_scope: str) -> tuple[str, str]:
    if prefixed_hash.startswith(PROJECT_HASH_PREFIX):
        return PROJECT_SCOPE_ID, prefixed_hash[len(PROJECT_HASH_PREFIX) :]
    return default_scope, prefixed_hash


def _side_slot_refs(
    shape: Mapping[str, Any] | None,
    hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
) -> list[str] | None:
    """Return the ref of every slot of one side template, in slot order.

    ``None`` when the template is malformed or its slot counts disagree with
    the hash arrays (``count('m') != len(hashes)``, ``count('t') !=
    len(tool_hashes)``, or an ``s`` slot without ``sh``).
    """
    if not isinstance(shape, Mapping):
        return None
    gaps = shape.get("g")
    slots = shape.get("r")
    if (
        not isinstance(gaps, list)
        or not isinstance(slots, str)
        or len(gaps) != len(slots) + 1
        or not all(isinstance(g, str) for g in gaps)
    ):
        return None
    msgs = [str(h) for h in (hashes or ())]
    tools = [str(h) for h in (tool_hashes or ())]
    if slots.count(SLOT_MESSAGE) != len(msgs) or slots.count(SLOT_TOOL) != len(tools):
        return None
    sh = shape.get("sh")
    refs: list[str] = []
    msg_iter = iter(msgs)
    tool_iter = iter(tools)
    for slot in slots:
        if slot == SLOT_MESSAGE:
            refs.append(next(msg_iter))
        elif slot == SLOT_TOOL:
            refs.append(next(tool_iter))
        elif slot == SLOT_SYSTEM:
            if not isinstance(sh, str) or not sh:
                return None
            refs.append(sh)
        else:
            return None
    return refs


def _collect_required_refs(
    *,
    project_id: str,
    item_scope_id: str,
    input_hashes: Sequence[str] | None,
    output_hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    envelope: Mapping[str, Any] | None,
) -> list[tuple[str, str, str]]:
    """Return the ``(project_id, scope_id, raw_hash)`` tuples one span's slots reference."""
    if not project_id or not isinstance(envelope, Mapping):
        return []
    side_refs = (
        _side_slot_refs(envelope.get("in"), input_hashes, tool_hashes),
        _side_slot_refs(envelope.get("out"), output_hashes, None),
    )
    refs: list[tuple[str, str, str]] = []
    for slot_refs in side_refs:
        for ref in slot_refs or ():
            if ref:
                scope, raw = _resolve_scope_and_hash(ref, item_scope_id)
                if scope and raw:
                    refs.append((project_id, scope, raw))
    return refs


def _parse_json_item(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def reconstruct_payload(
    shape: Mapping[str, Any] | None,
    hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    resolve: Callable[[str], str | None],
) -> str | None:
    """Rebuild one side's original text from its envelope template.

    Pure concatenation of the literal gaps and the resolved item contents in
    slot order. Returns ``None`` when the template does not match the hash
    arrays or an item is missing, so callers keep the inline preview. Raises
    ``TrajectoryContentUnavailable`` when the rebuilt text fails the
    template's checksum.
    """
    slot_refs = _side_slot_refs(shape, hashes, tool_hashes)
    if slot_refs is None or not isinstance(shape, Mapping):
        return None
    gaps: list[str] = shape["g"]
    parts = [gaps[0]]
    for ref, gap in zip(slot_refs, gaps[1:], strict=True):
        content = resolve(ref)
        if content is None:
            return None
        parts.append(content)
        parts.append(gap)
    result = "".join(parts)
    if payload_checksum(result) != shape.get("x"):
        raise TrajectoryContentUnavailable("trajectory payload checksum mismatch")
    return result


def _parse_envelope(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("trajectory_envelope_unparseable")
            return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("v") != ENVELOPE_VERSION:
        logger.warning(
            "trajectory_envelope_version_rejected version=%r", parsed.get("v")
        )
        return None
    return parsed


_MAX_SCOPES_PER_QUERY = 500
_MAX_PAIRS_PER_QUERY = 1500
# Kept for callers and tests that bound a batch by hash count.
_MAX_HASHES_PER_QUERY = _MAX_PAIRS_PER_QUERY
ITEMS_QUERY_MAX_EXECUTION_S = 10
ITEMS_QUERY_LOG_COMMENT = "trajectory_hydration"


def _chunk_project_refs(
    refs: Iterable[tuple[str, str, str]],
) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]:
    """Group refs by project into bounded ``(project, scopes, pairs)`` chunks.

    Each chunk holds at most 1500 ``(scope_id, item_hash)`` pairs over at
    most 500 scopes, which keeps a query far below max_query_size (256 KiB)
    and lets callers cap ``max_result_rows`` at ``len(pairs) + 1``.
    """
    by_project: dict[str, list[tuple[str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for project_id, scope_id, item_hash in refs:
        if not project_id or not scope_id or not item_hash:
            continue
        key = (str(project_id), str(scope_id), str(item_hash))
        if key in seen:
            continue
        seen.add(key)
        by_project.setdefault(key[0], []).append((key[1], key[2]))

    chunks: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = []
    for project_id, pairs in by_project.items():
        pairs.sort()
        cur: list[tuple[str, str]] = []
        cur_scopes: set[str] = set()
        for pair in pairs:
            if cur and (
                len(cur) >= _MAX_PAIRS_PER_QUERY
                or (
                    pair[0] not in cur_scopes
                    and len(cur_scopes) >= _MAX_SCOPES_PER_QUERY
                )
            ):
                chunks.append((project_id, tuple(sorted(cur_scopes)), tuple(cur)))
                cur, cur_scopes = [], set()
            cur.append(pair)
            cur_scopes.add(pair[0])
        if cur:
            chunks.append((project_id, tuple(sorted(cur_scopes)), tuple(cur)))
    return chunks


def build_trajectory_items_query(
    *, project_id: str, pairs: Iterable[tuple[str, str]]
) -> tuple[str, dict[str, Any]]:
    """One PK-pruned query for exactly ``pairs`` of one project.

    ``scope_id IN scopes`` prunes on the primary key; ``(scope_id, item_hash)
    IN pairs`` returns only the requested pairs, never the cross product of
    the batch's scopes and hashes. ``argMax`` collapses a refreshed key's
    unmerged copies to one row.
    """
    exact = tuple(dict.fromkeys((str(s), str(h)) for s, h in pairs if s and h))
    if not project_id or not exact:
        return "", {}
    # A one-element tuple renders as ``(('s', 'h'))``, which ClickHouse reads
    # as a single tuple, not a set of tuples. Repeating the pair keeps the
    # literal a set on both drivers; the set has the same members.
    bound = exact if len(exact) > 1 else exact * 2
    sql = """
        SELECT
            scope_id,
            item_hash,
            argMax(content, persisted_at) AS content
        FROM trajectory_items
        PREWHERE project_id = %(traj_project_id)s
          AND scope_id IN %(traj_scope_ids)s
          AND (scope_id, item_hash) IN %(traj_pairs)s
        GROUP BY scope_id, item_hash
    """
    params = {
        "traj_project_id": str(project_id),
        "traj_scope_ids": tuple(sorted({s for s, _h in exact})),
        "traj_pairs": bound,
    }
    return sql, params


def items_query_row_cap(params: Mapping[str, Any]) -> int:
    """``max_result_rows`` for one items query: its distinct pairs + 1."""
    return len(set(params.get("traj_pairs") or ())) + 1


def items_query_settings(
    params: Mapping[str, Any], base: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Settings for one items query: one thread, a 10 s server timeout and a
    row cap of ``len(pairs) + 1`` that throws. Applied after the application
    read policy, which otherwise zeroes every statement cap."""
    from tracer.services.clickhouse.v2.query_settings import (
        application_read_settings,
    )

    settings = application_read_settings({**(base or {}), "max_threads": 1})
    settings.update(
        max_threads=1,
        max_execution_time=ITEMS_QUERY_MAX_EXECUTION_S,
        timeout_overflow_mode="throw",
        max_result_rows=items_query_row_cap(params),
        result_overflow_mode="throw",
        log_comment=ITEMS_QUERY_LOG_COMMENT,
    )
    return settings


def _fetch_items(
    query_fn: Callable[[str, dict[str, Any]], Iterable[Any]],
    refs: Iterable[tuple[str, str, str]],
    decode: Callable[[Any], tuple[str, str, Any] | None],
) -> dict[tuple[str, str, str], str]:
    resolved: dict[tuple[str, str, str], str] = {}
    for project_id, _scopes, pairs in _chunk_project_refs(refs):
        sql, params = build_trajectory_items_query(project_id=project_id, pairs=pairs)
        if not sql:
            continue
        try:
            rows = list(query_fn(sql, params))
        except TrajectoryHydrationUnavailable:
            raise
        except Exception as exc:
            logger.error(
                "trajectory_hydration_failed project_id=%s pairs=%s error=%s",
                project_id,
                len(pairs),
                type(exc).__name__,
            )
            raise TrajectoryHydrationUnavailable("items_query_failed") from exc
        wanted = set(pairs)
        # An executor may drop the server row cap; the client enforces it too.
        if len(rows) > len(wanted):
            raise TrajectoryHydrationUnavailable("items_query_row_cap")
        for row in rows:
            decoded = decode(row)
            if decoded is None:
                continue
            scope, item_hash, content = decoded
            if (scope, item_hash) not in wanted:
                raise TrajectoryHydrationUnavailable("items_query_unrequested_row")
            if content is not None:
                resolved[(project_id, scope, item_hash)] = str(content)
    return resolved


def _decode_dict_item(row: Any) -> tuple[str, str, Any] | None:
    scope = str(row.get("scope_id") or "")
    item_hash = _normalize_hash(row.get("item_hash") or "")
    if not scope or not item_hash:
        return None
    return scope, item_hash, row.get("content")


def _decode_tuple_item(row: Any) -> tuple[str, str, Any] | None:
    if len(row) < 3:
        return None
    scope = str(row[0] or "")
    item_hash = _normalize_hash(row[1] or "")
    if not scope or not item_hash:
        return None
    return scope, item_hash, row[2]


def fetch_trajectory_items_dict_rows(
    query_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    refs: Iterable[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Fetch referenced items with a dict-row query callback (analytics executor).

    Raises ``TrajectoryHydrationUnavailable`` when a query fails or returns a
    row that was not asked for; never returns a partial map silently.
    """
    return _fetch_items(query_fn, refs, _decode_dict_item)


def fetch_trajectory_items_tuple_rows(
    query_fn: Callable[[str, dict[str, Any]], Sequence[tuple[Any, ...]]],
    refs: Iterable[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Fetch referenced items with a tuple-row query callback (CHSpanReader)."""
    return _fetch_items(query_fn, refs, _decode_tuple_item)


def _restore_enabled(enabled: bool | None) -> bool:
    if enabled is not None:
        return enabled
    try:
        return trajectory_read_state().projects_pointers
    except TrajectoryHydrationUnavailable:
        # The read itself already resolved (or failed on) the state; leave
        # attributes as stored rather than guessing.
        return False


def restore_promoted_attributes(
    attrs: dict[str, Any],
    *,
    span_input: str | None = None,
    span_output: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Pop Phase 0 compaction sentinels and re-inject original promoted attributes.

    While the read state is NOT_READY the attributes are returned untouched:
    the collector writes no sentinels then, and a customer key that merely
    shares a sentinel name must keep its value. ``enabled`` overrides the
    state (the hydrators pass True). A side passed as None is never restored:
    its sentinel is still removed.
    """
    if not _restore_enabled(enabled):
        return attrs
    in_key = attrs.pop(SENTINEL_PROMOTED_INPUT_KEY, None)
    in_was_json = bool(attrs.pop(SENTINEL_PROMOTED_INPUT_JSON, False))
    out_key = attrs.pop(SENTINEL_PROMOTED_OUTPUT_KEY, None)
    out_was_json = bool(attrs.pop(SENTINEL_PROMOTED_OUTPUT_JSON, False))

    if (
        isinstance(in_key, str)
        and in_key
        and in_key not in attrs
        and span_input is not None
    ):
        attrs[in_key] = _parse_json_item(span_input) if in_was_json else span_input

    if (
        isinstance(out_key, str)
        and out_key
        and out_key not in attrs
        and span_output is not None
    ):
        attrs[out_key] = _parse_json_item(span_output) if out_was_json else span_output

    return attrs


def restore_promoted_in_attributes_extra(
    raw_extra: str | dict[str, Any] | None,
    *,
    span_input: str | None = None,
    span_output: str | None = None,
    enabled: bool | None = None,
) -> str | dict[str, Any] | None:
    """Restore Phase 0 promoted attributes inside a raw ``attributes_extra`` JSON string or dict.

    Fast-paths in O(1) when no sentinel marker is present so legacy spans incur
    zero JSON parsing overhead.
    """
    if isinstance(raw_extra, dict):
        if not any(
            k in raw_extra
            for k in (SENTINEL_PROMOTED_INPUT_KEY, SENTINEL_PROMOTED_OUTPUT_KEY)
        ):
            return raw_extra
        if not _restore_enabled(enabled):
            return raw_extra
        copy = dict(raw_extra)
        restore_promoted_attributes(
            copy, span_input=span_input, span_output=span_output, enabled=True
        )
        return copy

    if isinstance(raw_extra, str):
        if _SENTINEL_MARKER not in raw_extra:
            return raw_extra
        if not _restore_enabled(enabled):
            return raw_extra
        try:
            parsed = json.loads(raw_extra)
        except json.JSONDecodeError:
            return raw_extra
        if not isinstance(parsed, dict):
            return raw_extra
        restore_promoted_attributes(
            parsed, span_input=span_input, span_output=span_output, enabled=True
        )
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    return raw_extra


# One warning per side that keeps its preview, named by the reason.
_SIDE_FAILURE_EVENTS = {
    "invalid_template": (
        "trajectory_hydration_invalid_template side=%s project_id=%s span_id=%s"
        " scope_id=%s surface=%s"
    ),
    "missing_items": (
        "trajectory_hydration_missing_items side=%s project_id=%s span_id=%s"
        " scope_id=%s surface=%s"
    ),
    "checksum_mismatch": (
        "trajectory_hydration_checksum_mismatch side=%s project_id=%s span_id=%s"
        " scope_id=%s surface=%s"
    ),
}

_SIDES = ("in", "out")
_SIDE_FIELDS = {"in": "input", "out": "output"}

_SENTRY_INTERVAL_S = 60.0
_sentry_last: dict[str, float] = {}


def _report_content_unavailable(
    reason: str, *, surface: str, project_id: str, span_id: Any, side: str
) -> None:
    """A rate-limited Sentry event (one per reason and surface a minute)."""
    key = f"{reason}:{surface}"
    now = _monotonic()
    with _state_lock:
        if now - _sentry_last.get(key, -_SENTRY_INTERVAL_S) < _SENTRY_INTERVAL_S:
            return
        _sentry_last[key] = now
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("trajectory_reason", reason)
            scope.set_tag("trajectory_surface", surface)
            scope.set_extra("project_id", project_id)
            scope.set_extra("span_id", str(span_id or ""))
            scope.set_extra("side", side)
            sentry_sdk.capture_message(f"trajectory_hydration_{reason}", level="error")
    except Exception:  # noqa: BLE001 - reporting must never fail a read
        logger.debug("trajectory_hydration_sentry_report_failed", exc_info=True)


def _hydrate_side(
    shape: Any,
    hashes: Sequence[str] | None,
    tools: Sequence[str] | None,
    resolve: Callable[[str], str | None],
) -> tuple[str | None, str | None]:
    """``(text, None)`` when the side rebuilds, else ``(None, reason)``."""
    if _side_slot_refs(shape, hashes, tools) is None:
        return None, "invalid_template"
    try:
        text = reconstruct_payload(shape, hashes, tools, resolve)
    except TrajectoryContentUnavailable:
        return None, "checksum_mismatch"
    if text is None:
        return None, "missing_items"
    return text, None


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return None


def _is_fresh(created_at: Any, now: datetime) -> bool:
    """Written within ``FRESH_MISSING_WINDOW``. A row without ``created_at``
    is treated as old: its missing items are reported, not retried."""
    written = _as_utc(created_at)
    return written is not None and now - written <= FRESH_MISSING_WINDOW


@dataclass
class _RowView:
    """The fields one span row contributes to hydration."""

    project_id: str
    item_scope_id: str
    span_id: Any
    input_hashes: Sequence[str] | None
    output_hashes: Sequence[str] | None
    tool_hashes: Sequence[str] | None
    created_at: Any
    envelope: dict[str, Any] | None
    readable: bool


@dataclass
class _RowResult:
    texts: dict[str, str]
    states: dict[str, str]


def _plan(
    views: Sequence[_RowView | None],
    state: TrajectoryReadState,
    fetch: Callable[[list[tuple[str, str, str]]], dict[tuple[str, str, str], str]],
    surface: str,
) -> list[_RowResult | None]:
    """Hydrate every items_v1 row (``None`` entries are inline rows).

    Raises ``TrajectoryHydrationUnavailable`` before anything is applied when
    the items query fails or an item of a freshly written span is not yet
    visible (replica lag); otherwise every side is inline, hydrated or
    missing.
    """
    refs: list[tuple[str, str, str]] = []
    if state.fetches_items:
        for view in views:
            if view is not None and view.envelope is not None:
                refs.extend(
                    _collect_required_refs(
                        project_id=view.project_id,
                        item_scope_id=view.item_scope_id,
                        input_hashes=view.input_hashes,
                        output_hashes=view.output_hashes,
                        tool_hashes=view.tool_hashes,
                        envelope=view.envelope,
                    )
                )
    resolved = fetch(refs) if refs else {}
    now = datetime.now(UTC)
    results: list[_RowResult | None] = []
    pending: list[tuple[str, Any, str]] = []
    reports: list[tuple[str, str, Any, str, str]] = []
    killed = 0
    for view in views:
        if view is None:
            results.append(None)
            continue
        result = _RowResult(texts={}, states={})
        results.append(result)
        if not view.readable or view.envelope is None:
            result.states = {"in": CONTENT_MISSING, "out": CONTENT_MISSING}
            reports.append(
                ("unreadable_envelope", view.project_id, view.span_id, "in", "")
            )
            continue

        def _resolve(prefixed_hash: str, _view: _RowView = view) -> str | None:
            s, raw = _resolve_scope_and_hash(prefixed_hash, _view.item_scope_id)
            return resolved.get((_view.project_id, s, raw))

        for side, hashes, tools in (
            ("in", view.input_hashes, view.tool_hashes),
            ("out", view.output_hashes, None),
        ):
            shape = view.envelope.get(side)
            if shape is None:
                result.states[side] = CONTENT_INLINE
                continue
            if not state.fetches_items:
                result.states[side] = CONTENT_MISSING
                killed += 1
                continue
            text, reason = _hydrate_side(shape, hashes, tools, _resolve)
            if reason is None and text is not None:
                result.texts[side] = text
                result.states[side] = CONTENT_HYDRATED
                continue
            if reason == "missing_items" and _is_fresh(view.created_at, now):
                pending.append((view.project_id, view.span_id, side))
                continue
            result.states[side] = CONTENT_MISSING
            reason = reason or "missing_items"
            logger.warning(
                _SIDE_FAILURE_EVENTS[reason],
                side,
                view.project_id,
                view.span_id or "",
                view.item_scope_id,
                surface,
            )
            reports.append((reason, view.project_id, view.span_id, side, surface))
    if pending:
        project_id, span_id, side = pending[0]
        logger.warning(
            "trajectory_hydration_items_not_visible spans=%s project_id=%s "
            "span_id=%s side=%s surface=%s",
            len(pending),
            project_id,
            span_id or "",
            side,
            surface,
        )
        raise TrajectoryHydrationUnavailable("items_not_yet_visible")
    if killed:
        logger.warning(
            "trajectory_content_unavailable reason=kill_switch sides=%s surface=%s",
            killed,
            surface,
        )
    for reason, project_id, span_id, side, report_surface in reports:
        _report_content_unavailable(
            reason,
            surface=report_surface or surface,
            project_id=project_id,
            span_id=span_id,
            side=side,
        )
    return results


def _sequence(value: Any) -> Sequence[str] | None:
    if value is None:
        return None
    return [str(v) for v in value]


def hydrate_span_dicts(
    rows: list[dict[str, Any]],
    query_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    *,
    project_id_fallback: str | None = None,
    attr_key: str = "span_attributes",
    state: TrajectoryReadState = TrajectoryReadState.READY,
    surface: str = "unknown",
) -> list[dict[str, Any]]:
    """Hydrate ``input``, ``output`` and Phase 0 sentinels on dict rows in place.

    ``state`` is the caller's snapshot; the caller projected the pointer
    columns, so it is READY or KILLED (KILLED fetches nothing). Each items_v1
    row gets ``row[CONTENT_STATE_KEY] = {"input": s, "output": s}`` with s in
    inline / hydrated / missing. A missing side keeps its preview, and its
    promoted attribute is never rebuilt from that preview. Executes ZERO
    queries when no row has ``payload_encoding == 'items_v1'``.
    """
    if not rows:
        return rows

    views: list[_RowView | None] = []
    for row in rows:
        if row.get("payload_encoding") != ENCODING_ITEMS_V1:
            views.append(None)
            continue
        env = _parse_envelope(row.get("payload_envelope"))
        views.append(
            _RowView(
                project_id=str(row.get("project_id") or project_id_fallback or ""),
                item_scope_id=str(row.get("item_scope_id") or ""),
                span_id=row.get("id"),
                input_hashes=_sequence(row.get("input_item_hashes")),
                output_hashes=_sequence(row.get("output_item_hashes")),
                tool_hashes=_sequence(row.get("tool_def_hashes")),
                created_at=row.get("created_at"),
                envelope=env,
                readable=env is not None,
            )
        )

    results = _plan(
        views,
        state,
        lambda refs: fetch_trajectory_items_dict_rows(query_fn, refs),
        surface,
    )

    for row, result in zip(rows, results, strict=True):
        missing: set[str] = set()
        if result is not None:
            for side, text in result.texts.items():
                row[_SIDE_FIELDS[side]] = text
            missing = {s for s, v in result.states.items() if v == CONTENT_MISSING}
            row[CONTENT_STATE_KEY] = {
                _SIDE_FIELDS[s]: result.states.get(s, CONTENT_INLINE) for s in _SIDES
            }
        if attr_key in row:
            # A promoted attribute is never restored from a preview: the
            # sentinel is still removed, and the attribute stays absent.
            row[attr_key] = restore_promoted_in_attributes_extra(
                row[attr_key],
                span_input=None if "in" in missing else row.get("input"),
                span_output=None if "out" in missing else row.get("output"),
                enabled=True,
            )

    return rows


def hydrate_chspans(
    spans: list[Any],
    query_fn: Callable[[str, dict[str, Any]], Sequence[tuple[Any, ...]]],
    *,
    state: TrajectoryReadState = TrajectoryReadState.READY,
    surface: str = "span_reader",
) -> list[Any]:
    """Hydrate ``CHSpan`` instances (frozen dataclasses) via one batched query.

    Sets ``content_state`` on items_v1 spans. Executes ZERO queries when all
    spans have ``payload_encoding != 'items_v1'``.
    """
    if not spans:
        return spans

    views: list[_RowView | None] = []
    for span in spans:
        if getattr(span, "payload_encoding", ENCODING_INLINE) != ENCODING_ITEMS_V1:
            views.append(None)
            continue
        env = _parse_envelope(getattr(span, "payload_envelope", ""))
        views.append(
            _RowView(
                project_id=str(getattr(span, "project_id", "") or ""),
                item_scope_id=str(getattr(span, "item_scope_id", "") or ""),
                span_id=getattr(span, "id", ""),
                input_hashes=_sequence(getattr(span, "input_item_hashes", ())),
                output_hashes=_sequence(getattr(span, "output_item_hashes", ())),
                tool_hashes=_sequence(getattr(span, "tool_def_hashes", ())),
                created_at=getattr(span, "created_at", None),
                envelope=env,
                readable=env is not None,
            )
        )

    results = _plan(
        views,
        state,
        lambda refs: fetch_trajectory_items_tuple_rows(query_fn, refs),
        surface,
    )

    out = list(spans)
    for idx, (span, result) in enumerate(zip(spans, results, strict=True)):
        updates: dict[str, Any] = {}
        missing: set[str] = set()
        if result is not None:
            for side, text in result.texts.items():
                updates[_SIDE_FIELDS[side]] = text
            missing = {s for s, v in result.states.items() if v == CONTENT_MISSING}
            if hasattr(span, "content_state"):
                updates["content_state"] = tuple(
                    result.states.get(s, CONTENT_INLINE) for s in _SIDES
                )

        raw_extra = getattr(span, "attributes_extra", None)
        if isinstance(raw_extra, str) and _SENTINEL_MARKER in raw_extra:
            # A promoted attribute is never restored from a preview: the
            # sentinel is still removed, and the attribute stays absent.
            eff_in = (
                None
                if "in" in missing
                else updates.get("input", getattr(span, "input", None))
            )
            eff_out = (
                None
                if "out" in missing
                else updates.get("output", getattr(span, "output", None))
            )
            restored_extra = restore_promoted_in_attributes_extra(
                raw_extra,
                span_input=eff_in,
                span_output=eff_out,
                enabled=True,
            )
            if restored_extra != raw_extra:
                updates["attributes_extra"] = restored_extra

        if updates:
            out[idx] = replace(span, **updates)

    return out


def content_unavailable_sides(obj: Any) -> list[str]:
    """``["input"]``, ``["output"]``, both or none: the sides of a hydrated
    row (dict or CHSpan) whose full content could not be read."""
    if isinstance(obj, Mapping):
        states = obj.get(CONTENT_STATE_KEY) or {}
        return [f for f in ("input", "output") if states.get(f) == CONTENT_MISSING]
    states = getattr(obj, "content_state", None) or ()
    return [
        f
        for f, s in zip(("input", "output"), states, strict=False)
        if s == CONTENT_MISSING
    ]


def mark_csv_unavailable_previews(rows: Iterable[dict[str, Any]]) -> None:
    """Prefix a missing side's preview with the fixed CSV marker, in place."""
    for row in rows:
        for field_name in content_unavailable_sides(row):
            value = row.get(field_name)
            text = (
                value
                if isinstance(value, str)
                else ("" if value is None else str(value))
            )
            if not text.startswith(CSV_UNAVAILABLE_PREFIX):
                row[field_name] = CSV_UNAVAILABLE_PREFIX + text


def raise_if_content_unavailable(objs: Iterable[Any], *, surface: str) -> None:
    """Fail closed for consumers that must never use a preview as content
    (evals, datasets, Insights, RCA)."""
    unavailable = [
        (getattr(o, "id", None) if not isinstance(o, Mapping) else o.get("id"), sides)
        for o in objs
        if (sides := content_unavailable_sides(o))
    ]
    if unavailable:
        raise TrajectoryContentUnavailable(
            f"trajectory content unavailable for {len(unavailable)} span(s)",
            span_ids=tuple(str(s or "") for s, _sides in unavailable),
            surface=surface,
        )
