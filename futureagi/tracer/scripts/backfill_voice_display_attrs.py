"""Backfill the materialized ``call.*`` display attrs onto historical voice
conversation spans in ClickHouse.

Not a management command — run it from a Django shell (e.g. inside a backend
pod, so ``get_clickhouse_client()`` targets that cluster's ClickHouse):

    python manage.py shell
    >>> from tracer.scripts.backfill_voice_display_attrs import backfill_voice_display_attrs
    >>> backfill_voice_display_attrs(dry_run=True)                    # count only
    >>> backfill_voice_display_attrs(project_ids=["<uuid>"], limit=50)
    >>> backfill_voice_display_attrs(since="2026-01-01", until="2026-07-01")

Design (prod-shaped — safe on a ``ReplicatedReplacingMergeTree`` cluster with
200M+ rows):

  • **Server-side row copy.** ``attributes_extra`` / ``resource_attrs`` /
    ``metadata`` are CH ``JSON`` columns that do not round-trip cleanly through
    a Python client, so the enriched row is rebuilt with ``INSERT … SELECT`` —
    ClickHouse copies every untouched column (incl. JSON) natively and only the
    three attr Maps get ``mapUpdate``-ed with the new ``call.*`` delta.

  • **Batched, not per-row.** One ``INSERT … SELECT`` per page (not 300k
    single-row inserts, which would blow past ``parts_to_throw_insert``). Each
    row's delta is looked up by span id from an inlined ``Map(String, Map(…))``
    literal — ``mapUpdate(attrs_string, JS_STR[id])``.

  • **No FINAL.** The latest physical version of each span is picked with
    ``ORDER BY _version DESC LIMIT 1 BY id`` (FINAL on a 200M table is the
    expensive path we avoid). The re-inserted row omits ``_version`` so its
    DEFAULT (fresh ns timestamp) wins ReplacingMergeTree dedup; collapse happens
    on the next background merge.

  • **Day-windowed + resumable.** Work is chunked by ``toDate(start_time)`` (the
    partition key) so each pass prunes to one partition instead of scanning the
    whole table. The ``call.message_count`` marker makes the run idempotent —
    already-enriched spans are never re-selected, so a killed run just resumes.

``raw_log`` is read from ``attributes_extra`` (post-PR #1693) with a fallback to
``attrs_string['raw_log']`` (pre-fix rows). Spans without it are skipped.
"""

import uuid
from datetime import date, datetime, timezone

import json

import structlog

from tracer.models.observability_provider import ProviderChoices
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS, split_attributes
from tracer.utils.otel import CallAttributes
from tracer.utils.twilio_calls import normalize_twilio_data

logger = structlog.get_logger(__name__)

# The "already enriched" marker: call.message_count in attrs_number. Every
# provider sets it, and the driver ALSO force-defaults it onto every inserted
# span (see nn.setdefault below), so a span with an all-string/bool delta still
# gets marked and never re-scans. Same key Phase-1 ingest sets, so freshly
# ingested spans are correctly excluded from the candidate set too.
_MARKER_KEY = CallAttributes.MESSAGE_COUNT  # "call.message_count"

# Only these providers can be enriched by extract_display_attrs. Excluding the
# rest (livekit/others/openai/empty-provider) keeps un-enrichable conversation
# spans out of the candidate set so they don't re-scan every run. The .value
# strings must match what CH stores (verified: 'vapi'/'retell'/… lowercase).
_SUPPORTED_PROVIDERS = (
    ProviderChoices.VAPI,
    ProviderChoices.RETELL,
    ProviderChoices.ELEVEN_LABS,
    ProviderChoices.BLAND,
    ProviderChoices.TWILIO,
)
_PROVIDER_FILTER = "provider IN (" + ", ".join(
    f"'{p.value}'" for p in _SUPPORTED_PROVIDERS
) + ")"

# The materialized voice-list display keys — the set the backfill adds to
# historical spans. Mirrors the CallAttributes display block.
CALL_DISPLAY_ATTR_KEYS = frozenset(
    {
        CallAttributes.CUSTOMER_NAME,
        CallAttributes.STATUS_DISPLAY,
        CallAttributes.CALL_TYPE,
        CallAttributes.SUMMARY,
        CallAttributes.OVERALL_SCORE,
        CallAttributes.ASSISTANT_ID,
        CallAttributes.ASSISTANT_PHONE_NUMBER,
        CallAttributes.ERROR_MESSAGE,
        CallAttributes.COST_CENTS,
        CallAttributes.MESSAGE_COUNT,
        CallAttributes.TRANSCRIPT_AVAILABLE,
        CallAttributes.RECORDING_AVAILABLE,
        CallAttributes.STARTED_AT,
        CallAttributes.ENDED_AT,
        CallAttributes.CREATED_AT,
        CallAttributes.RESPONSE_TIME_MS,
        CallAttributes.RESPONSE_TIME_SECONDS,
        CallAttributes.TALK_SECONDS_USER,
        CallAttributes.TALK_SECONDS_BOT,
        CallAttributes.TALK_PCT_USER,
        CallAttributes.TALK_PCT_BOT,
    }
)

# Not-yet-enriched + has a raw_log. Applied in the per-page read's OUTER query
# (after dedup to the latest version). Day-discovery deliberately does NOT use
# this — the negated mapContains / toString(JSON) defeat the bloom index and
# would scan every conversation partition; discovery uses a light provider/day
# filter instead and lets the per-day read do the real filtering.
_ENRICHABLE_PREDICATE = (
    f"NOT mapContains(attrs_number, '{_MARKER_KEY}') "
    "AND (rl != '' OR length(ex) > 2)"
)


def extract_display_attrs(raw_log: dict, provider: str) -> dict:
    """Voice-list display attrs (the CALL_DISPLAY_ATTR_KEYS subset) derived from a
    stored raw_log, dispatched per provider. Runs ONLY the display-producing
    extract helpers (same code as ingest, so values match), skipping the heavy /
    side-effecting ones (metrics_calculator, token parsing, call-log fetch, S3
    rehost). Output is only the new call.* keys. Never raises."""
    if not isinstance(raw_log, dict) or not raw_log:
        return {}
    attrs: dict = {}
    try:
        if provider == ProviderChoices.VAPI:
            from tracer.utils import vapi as _v

            _v._extract_conversation(raw_log, attrs)
            _v._extract_recording_urls(raw_log, attrs)
            _v._extract_metadata(raw_log, attrs)
            _v._extract_common_call_fields(raw_log, attrs)
        elif provider == ProviderChoices.RETELL:
            from tracer.utils import retell as _r

            _r._process_transcript(raw_log, attrs)
            _r._extract_recording_urls(raw_log, attrs)
            _r._extract_metadata(raw_log, attrs)
            _r._extract_common_call_fields(raw_log, attrs)
        elif provider == ProviderChoices.ELEVEN_LABS:
            from tracer.utils import eleven_labs as _e

            _e._extract_common_call_fields(raw_log, attrs)
        elif provider == ProviderChoices.BLAND:
            from tracer.utils import bland as _b

            _b._extract_common_call_fields(raw_log, attrs)
        elif provider == ProviderChoices.TWILIO:
            attrs = normalize_twilio_data(raw_log).get("span_attributes", {})
        else:
            return {}
    except Exception:
        logger.warning("extract_display_attrs_failed", provider=provider, exc_info=True)
        return {}
    return {k: v for k, v in attrs.items() if k in CALL_DISPLAY_ATTR_KEYS}


def _ch_str_lit(value) -> str:
    """A CH single-quoted string literal with backslash/quote escaping.

    We build map literals ourselves rather than pass dicts as driver params —
    clickhouse-driver formats dict values with Python quoting, which uses
    double-quotes when a value contains an apostrophe (invalid CH)."""
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def _inner_map_literal(delta: dict, kind: str) -> str:
    """A single row's delta as a CH ``map(...)`` literal. kind ∈ {'str','num','bool'}.
    Callers only pass non-empty deltas, so type inference on the outer map is
    unambiguous."""
    if kind == "str":
        pairs = ", ".join(f"{_ch_str_lit(k)}, {_ch_str_lit(v)}" for k, v in delta.items())
    elif kind == "num":
        pairs = ", ".join(f"{_ch_str_lit(k)}, {float(v)}" for k, v in delta.items())
    else:  # bool
        pairs = ", ".join(f"{_ch_str_lit(k)}, {int(v)}" for k, v in delta.items())
    return f"map({pairs})"


def _id_keyed_map_literal(rows, kind: str) -> str | None:
    """Build a ``Map(String, Map(String, T))`` literal keyed by span id, from the
    subset of rows that have a non-empty delta of this kind. Returns None when no
    row in the batch has this kind (caller then leaves the column untouched).

    In the INSERT this is indexed by the row's ``id`` — ``JS[id]`` — so each span
    gets its own delta merged, and any id absent from the map gets the value
    type's default (empty map) → mapUpdate no-op."""
    if not rows:
        return None
    body = "map(" + ", ".join(
        f"{_ch_str_lit(sid)}, {_inner_map_literal(delta, kind)}" for sid, delta in rows
    ) + ")"
    if kind == "str":
        return body  # Map(String, Map(String, String)) — inferred from non-empty inners
    if kind == "num":
        return f"CAST({body} AS Map(String, Map(String, Float64)))"
    return f"CAST({body} AS Map(String, Map(String, UInt8)))"


def _build_batch_insert_sql(batch, project_ids=None) -> str:
    """One ``INSERT … SELECT`` that enriches a whole page of spans server-side.

    batch: list of (span_id, ns, nn, nb) — the split display delta per span.
    Each of the three attr maps is merged via ``mapUpdate(col, JS[id])`` where
    JS is an id-keyed literal; ``updated_at`` is bumped; ``_version`` is omitted
    (DEFAULT wins dedup). Latest version per id via ORDER BY _version DESC +
    LIMIT 1 BY id (no FINAL). ids are bound as %(ids)s for idx_id pruning.
    Span ids are project-salted so id alone is safe; when project_ids is passed
    we still scope the write to match the read (defense-in-depth)."""
    js_str = _id_keyed_map_literal([(sid, ns) for sid, ns, nn, nb in batch if ns], "str")
    js_num = _id_keyed_map_literal([(sid, nn) for sid, ns, nn, nb in batch if nn], "num")
    js_bool = _id_keyed_map_literal([(sid, nb) for sid, ns, nn, nb in batch if nb], "bool")
    merges = {}
    if js_str is not None:
        merges["attrs_string"] = f"mapUpdate(attrs_string, {js_str}[id])"
    if js_num is not None:
        merges["attrs_number"] = f"mapUpdate(attrs_number, {js_num}[id])"
    if js_bool is not None:
        merges["attrs_bool"] = f"mapUpdate(attrs_bool, {js_bool}[id])"
    select_exprs = [
        merges.get(col, "now64(6)" if col == "updated_at" else col)
        for col in CH_INSERT_COLUMNS
    ]
    return (
        f"INSERT INTO spans ({', '.join(CH_INSERT_COLUMNS)}) "
        f"SELECT {', '.join(select_exprs)} "
        "FROM spans "
        "WHERE observation_type = 'conversation' AND id IN %(ids)s "
        f"{_build_project_filter(project_ids)} "
        "ORDER BY _version DESC "
        "LIMIT 1 BY id"
    )


def _split_delta(new: dict):
    """Split a display delta into (attrs_string, attrs_number, attrs_bool) maps
    and FORCE the marker into the numeric map. This is what guarantees C2
    convergence: even a delta with only string/bool keys still lands
    call.message_count in attrs_number, so the span is marked and never re-scans.
    setdefault keeps the real message count when the provider already set it."""
    ns, nn, nb, _ = split_attributes(new)
    nn.setdefault(_MARKER_KEY, 0.0)
    return ns, nn, nb


def _parse_raw_log(raw_log_json) -> dict:
    """A raw_log JSON string → dict. Tolerant of junk/empty/dict."""
    if isinstance(raw_log_json, dict):
        return raw_log_json
    if not isinstance(raw_log_json, str) or not raw_log_json:
        return {}
    try:
        parsed = json.loads(raw_log_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_raw_log(extra_json, attrs_string_raw_log) -> dict:
    """Resolve raw_log from either column: attributes_extra first (the post-
    PR #1693 location, an object under the top-level 'raw_log' key), else
    attrs_string['raw_log'] (pre-fix rows, a JSON string). Parsed once."""
    if extra_json:
        try:
            extra = json.loads(extra_json) if isinstance(extra_json, str) else extra_json
        except (json.JSONDecodeError, TypeError):
            extra = None
        if isinstance(extra, dict) and extra.get("raw_log"):
            rl = _parse_raw_log(extra["raw_log"])
            if rl:
                return rl
    return _parse_raw_log(attrs_string_raw_log)


def _coerce_ts(value):
    """ISO8601 string or datetime → aware UTC datetime, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _build_project_filter(project_ids) -> str:
    if not project_ids:
        return ""
    pid_list = ", ".join(f"toUUID('{p}')" for p in project_ids)
    return f"AND project_id IN ({pid_list})"


def _discover_days(client, project_ids, since, until) -> list[date]:
    """Distinct partition days (toDate(start_time)) for supported-provider
    conversation spans, oldest first. Deliberately LIGHT — no marker / raw_log
    predicate (those negate the bloom index and would scan every conversation
    partition on 200M rows). It may over-include fully-drained days; the per-day
    read then returns 0 for those. Bound with since/until on a big first run."""
    since_filter = "AND start_time >= %(since)s" if since else ""
    until_filter = "AND start_time < %(until)s" if until else ""
    sql = (
        "SELECT DISTINCT toDate(start_time) AS d FROM spans "
        f"WHERE observation_type = 'conversation' AND {_PROVIDER_FILTER} "
        f"{_build_project_filter(project_ids)} {since_filter} {until_filter} "
        "ORDER BY d"
    )
    params = {}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    rows, _cols, _qt = client.execute_read(sql, params, timeout_ms=120000)
    return [r[0] for r in rows]


def _build_read_sql(project_ids) -> str:
    """Candidate page for one day. The inner query dedups to each span's LATEST
    version (ORDER BY _version DESC + LIMIT 1 BY id) BEFORE the marker filter, so
    an already-enriched span is excluded immediately — even before its stale
    pre-merge version is collapsed by a background merge. Without this the marker
    on the winning version is invisible while an older unmarked part still exists,
    and every re-run / resume would re-process the whole set. No FINAL.

    Keyset predicate lives in the inner query: start_time/id are immutable across
    versions, so `(start_time, id) > cursor` keeps either all or none of a span's
    versions — LIMIT 1 BY id still sees them all and picks the latest."""
    return (
        "SELECT id, provider, start_time, rl, ex FROM ("
        " SELECT toString(id) AS id, provider, start_time, "
        " attrs_string['raw_log'] AS rl, toString(attributes_extra) AS ex, attrs_number "
        " FROM spans "
        " WHERE observation_type = 'conversation' "
        f" AND {_PROVIDER_FILTER} "
        " AND toDate(start_time) = %(day)s "
        f" {_build_project_filter(project_ids)} "
        " AND (start_time, id) > (%(last_ts)s, %(last_id)s) "
        " ORDER BY _version DESC "
        " LIMIT 1 BY id"
        ") "
        f"WHERE {_ENRICHABLE_PREDICATE} "
        "ORDER BY start_time, id "
        "LIMIT %(batch)s"
    )


def backfill_voice_display_attrs(
    *,
    project_ids=None,
    since=None,
    until=None,
    batch_size=300,
    limit=None,
    dry_run=False,
) -> dict:
    """Backfill call.* display attrs onto historical voice conversation spans.

    Args:
        project_ids: optional list of project UUIDs to limit to.
        since / until: ISO8601 strings or datetimes bounding start_time.
        batch_size: rows read (and enriched) per INSERT. Kept modest so the
            inlined delta literal stays well under max_query_size.
        limit: cap total spans processed (smoke testing).
        dry_run: read + compute; report counts without writing.

    Returns a summary dict and prints a one-line result.
    """
    project_ids = [str(p) for p in (project_ids or [])]
    for pid in project_ids:
        uuid.UUID(pid)  # validate; raises ValueError on a bad UUID
    since = _coerce_ts(since)
    until = _coerce_ts(until)

    client = get_clickhouse_client()
    read_sql = _build_read_sql(project_ids)
    days = _discover_days(client, project_ids, since, until)

    processed = enriched = skipped = failed = 0
    print(f"voice display backfill: {len(days)} candidate day-partition(s)")

    for day in days:
        last_ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
        last_id = ""
        while True:
            if limit and processed >= limit:
                break
            params = {"day": day, "last_ts": last_ts, "last_id": last_id, "batch": batch_size}
            rows, _cols, _qt = client.execute_read(read_sql, params, timeout_ms=120000)
            if not rows:
                break
            batch = []
            for span_id, provider, start_time, raw_log_str, extra_json in rows:
                if limit and processed >= limit:
                    break
                last_ts, last_id = start_time, span_id
                processed += 1
                new = extract_display_attrs(
                    _extract_raw_log(extra_json, raw_log_str), provider
                )
                if not new:
                    skipped += 1
                    continue
                ns, nn, nb = _split_delta(new)
                batch.append((span_id, ns, nn, nb))
            if batch:
                if dry_run:
                    enriched += len(batch)
                else:
                    try:
                        client.execute(
                            _build_batch_insert_sql(batch, project_ids),
                            {"ids": tuple(sid for sid, *_ in batch)},
                            settings={"max_query_size": 20_000_000, "max_execution_time": 300},
                        )
                        enriched += len(batch)
                    except Exception as e:
                        failed += len(batch)
                        print(f"  ! {day} batch of {len(batch)}: {str(e)[:200]}")
            if len(rows) < batch_size:
                break
        if limit and processed >= limit:
            break

    # Completion signal is enriched==0, NOT processed==0: a small residual of
    # supported-provider spans whose raw_log yields no attrs (skipped_no_attrs)
    # can't be marked and are re-read every run — that's expected, not a bug.
    verb = "would enrich" if dry_run else "enriched"
    print(
        f"voice display backfill: processed={processed} {verb}={enriched} "
        f"skipped_no_attrs={skipped} failed={failed} "
        f"(done when {verb}=0)"
    )
    return {
        "processed": processed,
        "enriched": enriched,
        "skipped_no_attrs": skipped,
        "failed": failed,
        "days": len(days),
    }
