"""Shared Langfuse trace upsert logic.

Used by both the real-time ``POST /api/public/ingestion`` endpoint and
the Temporal integration sync activities to persist Langfuse-format
traces into the FutureAGI database.

Observation ids, the synthetic root id and score ids come from the sender,
and one table holds the spans of every project. Every row is therefore looked
up within the import's project. An id already stored in another project is
refused for that row and counted: the row is never moved into this project
or overwritten.

Span ids are looked up in ClickHouse too: the collector writes most spans
there and never to Postgres. When ClickHouse cannot be read, nothing of the
trace is stored (``SpanIdCheckUnavailable``). ClickHouse is asked only about
the ids the import's project does not already hold in Postgres, and the
operator can turn that lookup off with the environment variable
LANGFUSE_SPAN_ID_CLICKHOUSE_CHECK (see ``SPAN_ID_CLICKHOUSE_CHECK_SETTING``);
the Postgres lookup stays.
"""

import os
from dataclasses import dataclass
from datetime import datetime

import structlog
from django.db import transaction

from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.curated_writer import (
    CuratedEndUser,
    CuratedSession,
)
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_end_user_id,
    deterministic_trace_session_id,
)
from tracer.services.clickhouse.v2.span_writer import (
    mirror_spans_to_clickhouse,
    span_to_pg_row,
)

logger = structlog.get_logger(__name__)

# How long the upsert waits for ClickHouse to say which span ids another
# project holds; past it, nothing of the trace is stored.
CLICKHOUSE_ID_CHECK_TIMEOUT_MS = 5000
# A span id is at most 255 characters (ObservationSpan.id); a longer one can
# never be stored, so it is not asked about.
_SPAN_ID_MAX_LENGTH = 255
# ClickHouse parses at most 262,144 bytes of a statement, and clickhouse-driver
# writes every id into it, some characters twice. One read carries at most
# this many bytes of ids: the ids of a trace of 500 spans fit in one read.
_ID_CHECK_MAX_BYTES = 200_000
# One read also carries at most this many ids, so every read stays within the
# sizes the cost checks measure; a trace with more ids is read in several.
_ID_CHECK_MAX_IDS = 501
# A deleted row still holds its id, as in Postgres: there is no is_deleted
# filter, which on this table would also depend on when parts merge.
_HELD_IN_CLICKHOUSE = (
    "SELECT DISTINCT id FROM spans WHERE id IN %(ids)s AND project_id != %(project_id)s"
)
# The operator's setting for that read: an environment variable, read at each
# check and never cached. "off", in any case, skips the ClickHouse read and
# logs one line per skipped check; unset, "on" or any other value keeps the
# read, so a mistyped value still checks.
SPAN_ID_CLICKHOUSE_CHECK_SETTING = "LANGFUSE_SPAN_ID_CLICKHOUSE_CHECK"


class SpanIdCheckUnavailable(Exception):
    """ClickHouse could not be asked which span ids another project holds.

    Raised before anything of the trace is stored. The live endpoint answers
    so that the sender sends the batch again; the import stops the sync and
    keeps its window."""


@dataclass(frozen=True)
class RefusedRows:
    """The rows of one trace that were not stored: each id is already stored
    in another project. ``root`` is the trace's synthetic root span."""

    observation_ids: tuple = ()
    root: bool = False
    score_ids: tuple = ()

    @property
    def count(self):
        return len(self.observation_ids) + int(self.root) + len(self.score_ids)


def _stored_id(value):
    """An incoming id as the database stores it, or None when there is none.

    Span and score ids are text columns, and a batch can send an id as a JSON
    number: compare ids as text, so ``123`` and ``"123"`` are the same row.
    """
    if value is None or value == "":
        return None
    return str(value)


def get_v2_query_client():
    """The pooled ClickHouse read client of tracer/services/clickhouse/v2/
    query_service.py. Imported when first called, as attribute_reads does, so
    loading this module loads no ClickHouse query code. Tests answer the id
    check by replacing this name."""
    from tracer.services.clickhouse.v2 import query_service

    return query_service.get_v2_query_client()


def _id_check_batches(ids):
    """``ids`` split into reads that carry at most ``_ID_CHECK_MAX_IDS`` ids
    and at most ``_ID_CHECK_MAX_BYTES``."""
    batch, size = [], 0
    for span_id in ids:
        cost = 2 * len(span_id.encode()) + 4
        if batch and (
            len(batch) >= _ID_CHECK_MAX_IDS or size + cost > _ID_CHECK_MAX_BYTES
        ):
            yield batch
            batch, size = [], 0
        batch.append(span_id)
        size += cost
    if batch:
        yield batch


def _clickhouse_check_is_off():
    """Whether the operator turned the ClickHouse span id check off."""
    return os.environ.get(SPAN_ID_CLICKHOUSE_CHECK_SETTING, "").lower() == "off"


def _span_ids_in_other_projects_in_clickhouse(ids, project_id):
    """The span ids among ``ids`` that ClickHouse holds in a project other
    than ``project_id``. One read per trace; more only for a trace whose ids
    pass ``_ID_CHECK_MAX_IDS`` or ``_ID_CHECK_MAX_BYTES``.

    When the operator has turned the check off, nothing is read and no id is
    reported held: one log line gives the project and the count of ids, never
    the ids.

    Raises ``SpanIdCheckUnavailable`` when ClickHouse cannot be read (no
    client, a refused connection, a failed query or a timeout): an id that
    could not be checked is never stored."""
    ids = sorted({span_id for span_id in ids if len(span_id) <= _SPAN_ID_MAX_LENGTH})
    if not ids:
        return set()
    if _clickhouse_check_is_off():
        logger.warning(
            "langfuse_span_id_check_skipped",
            project_id=str(project_id),
            span_ids=len(ids),
        )
        return set()
    held = set()
    try:
        client = get_v2_query_client()
        for batch in _id_check_batches(ids):
            rows, _, _ = client.execute_read(
                _HELD_IN_CLICKHOUSE,
                {"ids": tuple(batch), "project_id": str(project_id)},
                timeout_ms=CLICKHOUSE_ID_CHECK_TIMEOUT_MS,
            )
            held.update(str(row[0]) for row in rows)
    except Exception as exc:
        # The count and the error's class only, never its text.
        logger.warning(
            "langfuse_span_id_check_unavailable",
            span_ids=len(ids),
            error_class=type(exc).__name__,
        )
        raise SpanIdCheckUnavailable() from exc
    return held


def _span_ids_in_other_projects(ids, project_id):
    """The span ids among ``ids`` already stored in a project other than
    ``project_id``, in Postgres or in ClickHouse. Postgres is read once for
    all of them, without the soft-delete filter: a deleted row still holds
    its id. ClickHouse is asked only about the ids no project holds in
    Postgres: an id ``project_id`` holds there is its own row, updated in
    place."""
    ids = [span_id for span_id in ids if span_id is not None]
    if not ids:
        return set()
    held, own = set(), set()
    rows = ObservationSpan._base_manager.filter(id__in=ids).values_list(
        "id", "project_id"
    )
    for span_id, holder in rows:
        if str(holder) == str(project_id):
            own.add(span_id)
        else:
            held.add(span_id)
    unheld = [span_id for span_id in ids if span_id not in held and span_id not in own]
    return held | _span_ids_in_other_projects_in_clickhouse(unheld, project_id)


def parse_langfuse_timestamp(ts):
    """Parse an ISO 8601 timestamp string to a datetime.

    Handles ``Z`` suffix, offset strings, and passthrough of existing
    ``datetime`` objects.
    """
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts)
        return ts
    except (ValueError, TypeError):
        return None


def merge_langfuse_observation(saved, patch):
    """Merge one live event without inventing values for absent fields.

    Usage aliases are normalized before merging so a later legacy ``usage``
    event can update a persisted ``usageDetails`` counter (including zero).
    """
    merged = {**saved, **patch}
    if "usageDetails" in patch or "usage" in patch:
        usage = patch.get("usageDetails", patch.get("usage"))
        if usage is None:
            merged["usageDetails"] = None
        else:
            counters = {}
            for key, alias in (
                ("input", "promptTokens"),
                ("output", "completionTokens"),
                ("total", "totalTokens"),
            ):
                if key in usage or alias in usage:
                    value = usage.get(key)
                    # Match transformer precedence: None falls back, zero does not.
                    counters[key] = value if value is not None else usage.get(alias)
            combined = dict(saved.get("usageDetails") or {})
            if "total" not in counters and (
                "input" in counters or "output" in counters
            ):
                combined.pop("total", None)
            merged["usageDetails"] = {**combined, **counters}
        merged.pop("usage", None)
    if "costDetails" in patch and "calculatedTotalCost" not in patch:
        merged.pop("calculatedTotalCost", None)
    start = parse_langfuse_timestamp(merged.get("startTime"))
    end = parse_langfuse_timestamp(merged.get("endTime"))
    merged["latency"] = (end - start).total_seconds() if start and end else 0
    return merged


def _saved_observation(span, root_span_id):
    """Reconstruct transformer input from persisted fields, not lossy defaults."""
    return {
        "id": span.id,
        "type": {"llm": "GENERATION", "chain": "SPAN"}.get(
            span.observation_type, "EVENT"
        ),
        "parentObservationId": (
            None if span.parent_span_id == root_span_id else span.parent_span_id
        ),
        "name": span.name,
        "model": span.model,
        "input": span.input,
        "output": span.output,
        "metadata": span.metadata,
        "modelParameters": span.model_parameters,
        "startTime": span.start_time.isoformat() if span.start_time else None,
        "endTime": span.end_time.isoformat() if span.end_time else None,
        "usageDetails": {
            "input": span.prompt_tokens,
            "output": span.completion_tokens,
            "total": span.total_tokens,
        },
        "calculatedTotalCost": span.cost,
        "level": {"OK": "DEFAULT", "ERROR": "ERROR"}.get(span.status, "UNSET"),
    }


def _merge_live_trace(assembled_trace, trace, saved_spans, root_span_id):
    """Hydrate only live patches; integration snapshots retain replacement semantics."""
    saved = {}
    if trace is not None:
        saved = {
            key: getattr(trace, key)
            for key in ("name", "input", "output", "metadata", "tags")
        }
        # Raw user/session strings are stored as attributes; PG FK ids are not
        # reversible. Prefer the synthetic root, then an existing observation.
        root = saved_spans.get(root_span_id)
        context_spans = ([root] if root is not None else []) + list(
            saved_spans.values()
        )
        for span in context_spans:
            attrs = span.span_attributes or {}
            for field, attr in (("userId", "user.id"), ("sessionId", "session.id")):
                if attr in attrs and field not in saved:
                    saved[field] = attrs[attr]
    combined = {**saved, **assembled_trace}
    observations = []
    for patch in assembled_trace.get("observations") or []:
        span = saved_spans.get(patch.get("id"))
        previous = _saved_observation(span, root_span_id) if span is not None else {}
        observations.append(merge_langfuse_observation(previous, patch))
    combined["observations"] = observations
    return combined


def upsert_langfuse_trace(
    assembled_trace,
    transformer,
    project_id,
    org,
    workspace,
    org_id,
    *,
    patch_mode=False,
):
    """Upsert a single assembled Langfuse trace with observations and scores.

    Creates a synthetic root span so the trace renders as a tree in the
    FutureAGI UI.  Langfuse treats the trace itself as the root node;
    our frontend expects a root ``ObservationSpan`` with children linked
    via ``parent_span_id``.

    ``patch_mode`` is reserved for the live JSON ingestion endpoint. Snapshot
    imports keep the transformer's original replacement/defaulting behavior.

    Every span, root and score is looked up within ``project_id``. An
    observation, root or score whose id is already stored in another project
    is not written: it is left where it is and reported in ``refused``. Span
    ids are looked up in Postgres and in ClickHouse.

    Returns:
        Tuple of ``(was_created, spans_count, scores_count, spans_created,
        scores_created, refused)``: the observations and scores written, then
        how many of them this call created, then the ``RefusedRows`` it did not
        store. A re-read of a stored trace creates none.

    Raises:
        SpanIdCheckUnavailable: ClickHouse could not be read. Nothing of the
        trace was stored.
    """
    external_id = assembled_trace["id"]
    root_span_id = f"root-{str(external_id)[:245]}"
    # Refuse the observations whose ids another project already holds, in
    # Postgres or in ClickHouse, before anything reads them: their children
    # are then treated like any child whose parent is not in the batch, and
    # their content is never used. The spans the batch's scores point at are
    # looked up in the same reads.
    observations = assembled_trace.get("observations") or []
    scores = assembled_trace.get("scores") or []
    taken_ids = _span_ids_in_other_projects(
        [root_span_id]
        + [_stored_id(obs.get("id")) for obs in observations]
        + [_stored_id(score.get("observationId")) for score in scores],
        project_id,
    )
    root_refused = root_span_id in taken_ids
    refused_observation_ids = tuple(
        dict.fromkeys(
            _stored_id(obs.get("id"))
            for obs in observations
            if _stored_id(obs.get("id")) in taken_ids
        )
    )
    if refused_observation_ids:
        assembled_trace = {
            **assembled_trace,
            "observations": [
                obs
                for obs in observations
                if _stored_id(obs.get("id")) not in taken_ids
            ],
        }
    refused_score_ids = []
    with transaction.atomic():
        # Tolerate historical duplicate trace rows, but serialize updates to
        # an existing trace so two partial batches cannot lose each other's fields.
        trace = (
            Trace.no_workspace_objects.select_for_update()
            .filter(
                project_id=project_id,
                external_id=external_id,
            )
            .order_by("created_at")
            .first()
        )
        changed_ids = [root_span_id] + [
            obs["id"]
            for obs in assembled_trace.get("observations") or []
            if obs.get("id")
        ]
        saved_spans = (
            {
                span.id: span
                for span in ObservationSpan.no_workspace_objects.filter(
                    trace=trace, id__in=changed_ids
                )
            }
            if trace is not None
            else {}
        )
        previous_rows = [
            span_to_pg_row(span, trace.session_id) for span in saved_spans.values()
        ]
        if patch_mode:
            assembled_trace = _merge_live_trace(
                assembled_trace, trace, saved_spans, root_span_id
            )
        trace_data = transformer.transform_trace(assembled_trace, project_id)
        if patch_mode:
            # Use the same canonical context on the first and later flushes:
            # persisted metadata/tags already contain the integration markers.
            assembled_trace = {
                **assembled_trace,
                "metadata": trace_data["metadata"],
                "tags": trace_data["tags"],
            }
        defaults = {
            "name": trace_data["name"],
            "input": trace_data.get("input"),
            "output": trace_data.get("output"),
            "metadata": trace_data.get("metadata", {}),
            "tags": trace_data.get("tags", []),
        }
        if trace:
            created = False
            for key, val in defaults.items():
                setattr(trace, key, val)
            trace.save(update_fields=[*defaults.keys(), "updated_at"])
        else:
            trace = Trace.no_workspace_objects.create(
                project_id=project_id,
                external_id=trace_data["external_id"],
                **defaults,
            )
            created = True

        trace_id = str(trace.id)

        # EndUser — CH-derived-dimensions P3b flip: NO PG ``EndUser.get_or_create``.
        # Compute the DETERMINISTIC end_user_id; Langfuse always uses
        # ``user_id_type="custom"`` (and ``user_id_hash=""``) — that EXACT hardcode
        # must feed the deterministic id so a net-new Langfuse user matches the
        # historical remap (§11.1). The id is stamped onto every span below; the
        # curated row goes to CH only.
        user_id_str = assembled_trace.get("userId")
        end_user_id = None
        ch_end_user = None
        if user_id_str:
            end_user_id = deterministic_end_user_id(
                project_id, org_id, user_id_str, "custom"
            )
            ch_end_user = CuratedEndUser(
                project_id=project_id,
                end_user_id=end_user_id,
                organization_id=org_id,
                user_id=str(user_id_str),
                user_id_type="custom",
                user_id_hash="",
            )

        # TraceSession — P3b flip: NO PG ``TraceSession.get_or_create``. STAMP the
        # DETERMINISTIC trace_session_id onto ``trace.session_id`` (db_constraint=
        # False, no PG row needed); the curated row goes to CH only.
        session_id = assembled_trace.get("sessionId")
        ch_session = None
        if session_id:
            session_uuid = deterministic_trace_session_id(project_id, session_id)
            if trace.session_id != session_uuid:
                trace.session_id = session_uuid
                trace.save(update_fields=["session"])
            ch_session = CuratedSession(
                project_id=project_id,
                trace_session_id=session_uuid,
                external_session_id=session_id,
            )

        # Transform observations
        obs_dicts = transformer.transform_observations(
            assembled_trace, trace_id, project_id
        )

        # Fix orphan parent_span_ids: Langfuse OTEL integration sometimes
        # absorbs the root span into the trace record, leaving child spans
        # pointing to a parent that doesn't exist in the observation set.
        obs_ids = {obs["id"] for obs in obs_dicts}
        if patch_mode:
            # A parent from an earlier flush is not an orphan.
            parent_ids = {
                obs["parent_span_id"] for obs in obs_dicts if obs.get("parent_span_id")
            }
            obs_ids.update(
                ObservationSpan.no_workspace_objects.filter(
                    trace=trace, id__in=parent_ids
                ).values_list("id", flat=True)
            )
        promoted_root_name = None
        for obs_data in obs_dicts:
            if (
                obs_data.get("parent_span_id")
                and obs_data["parent_span_id"] not in obs_ids
            ):
                obs_data["parent_span_id"] = None
                if obs_data.get("name"):
                    promoted_root_name = obs_data["name"]

        # If an orphan was promoted and the trace name was a fallback,
        # use the promoted root's name instead.
        raw_trace_name = assembled_trace.get("name") or ""
        if (
            promoted_root_name
            and not raw_trace_name
            and trace.name != promoted_root_name
        ):
            trace.name = promoted_root_name
            trace.save(update_fields=["name"])

        # Parent all top-level observations to the root span BEFORE
        # upserting them, so every child is linked from the start. A root
        # whose id another project holds is not this trace's: its top-level
        # observations stay top-level.
        for obs_data in obs_dicts:
            if not obs_data.get("parent_span_id") and not root_refused:
                obs_data["parent_span_id"] = root_span_id

        # Upsert observation spans first so timing query covers all of them.
        spans_count = 0
        spans_created = 0
        span_rows = []
        for obs_data in obs_dicts:
            obs_id = obs_data.pop("id")
            obs_data.pop("trace_id", None)
            obs_data.pop("project_id", None)

            if end_user_id is not None:
                obs_data["end_user_id"] = end_user_id

            span, new_span = ObservationSpan.no_workspace_objects.update_or_create(
                id=obs_id,
                project_id=project_id,
                defaults={
                    "trace": trace,
                    "project_id": project_id,
                    "org_id": org_id,
                    **obs_data,
                },
            )
            span_rows.append(span_to_pg_row(span, trace.session_id))
            spans_count += 1
            spans_created += new_span

        # Now compute root span timing from ALL existing observation spans
        # for this trace (not just the current batch).  Vapi sends events
        # in multiple batches, so we must query the DB to get the full range.
        from django.db.models import Max, Min

        # Read the writes in this PG transaction; the CH mirror runs only
        # after commit and cannot supply the root's complete timing here.
        timing = (
            ObservationSpan.no_workspace_objects.filter(trace=trace)
            .exclude(id=root_span_id)
            .aggregate(earliest=Min("start_time"), latest=Max("end_time"))
        )
        earliest_start = timing["earliest"]
        latest_end = timing["latest"]

        root_latency = 0
        if earliest_start and latest_end:
            root_latency = int((latest_end - earliest_start).total_seconds() * 1000)

        root_defaults = {
            "trace": trace,
            "project_id": project_id,
            "org_id": org_id,
            "parent_span_id": None,
            "observation_type": "chain",
            "name": trace.name or "Langfuse Trace",
            "start_time": earliest_start,
            "end_time": latest_end,
            "latency_ms": root_latency,
            "input": trace_data.get("input"),
            "output": trace_data.get("output"),
            "metadata": trace_data.get("metadata", {}),
            "model": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0,
            "status": "OK",
            "span_attributes": {
                "fi.span.kind": "CHAIN",
                "metadata": trace_data.get("metadata", {}),
            },
        }
        if end_user_id is not None:
            root_defaults["end_user_id"] = end_user_id
        # Keep the reversible context on the root for later observation-only
        # batches; deterministic dimension UUIDs alone cannot recover it.
        for field, attr in (("userId", "user.id"), ("sessionId", "session.id")):
            if assembled_trace.get(field):
                root_defaults["span_attributes"][attr] = assembled_trace[field]

        if not root_refused:
            root, _ = ObservationSpan.no_workspace_objects.update_or_create(
                id=root_span_id,
                project_id=project_id,
                defaults=root_defaults,
            )
            span_rows.append(span_to_pg_row(root, trace.session_id))

        # Scores
        score_dicts = transformer.transform_scores(assembled_trace, trace_id)
        scores_count = 0
        scores_created = 0

        for score_data in score_dicts:
            langfuse_score_id = score_data.pop("langfuse_score_id")
            observation_id = score_data.pop("observation_id", None)

            # A score id already stored on another project's trace is refused
            # and counted, like a span id: that row is never moved or rewritten.
            # Read without the soft-delete filter: a deleted row still holds
            # its id. A score on a span another project holds is refused the
            # same way: it is not this project's to attach.
            if _stored_id(observation_id) in taken_ids or (
                EvalLogger._base_manager.filter(eval_id=langfuse_score_id)
                .exclude(trace__project_id=project_id)
                .exists()
            ):
                refused_score_ids.append(_stored_id(langfuse_score_id))
                continue

            if observation_id:
                # CH25-TODO(read-after-write-inside-atomic; revisit after OTel
                # direct-to-CH cutover): same atomic block as the upserts at
                # lines 182/246. ObservationSpan is also a Django ForeignKey
                # on EvalLogger.observation_span (see EvalLogger.no_workspace_
                # objects.update_or_create call below), so the lookup must
                # return a Django model instance — CHSpan cannot stand in here.
                # CH-side lag mechanism: PeerDB CDC replication from PG.
                # Only this project's span: a score never attaches to a span
                # another project holds.
                try:
                    obs_span = ObservationSpan.no_workspace_objects.get(
                        id=observation_id, project_id=project_id
                    )
                except ObservationSpan.DoesNotExist:
                    logger.warning(
                        "langfuse_score_observation_not_found",
                        observation_id=observation_id,
                    )
                    continue
            else:
                # CH25-TODO(read-after-write-inside-atomic; revisit after OTel
                # direct-to-CH cutover): fallback pick of "earliest span on
                # the trace" — same read-after-write hazard as the timing
                # aggregate above, plus the EvalLogger FK requirement that
                # keeps obs_span as a Django ObservationSpan instance.
                obs_span = (
                    ObservationSpan.no_workspace_objects.filter(trace=trace)
                    .order_by("start_time")
                    .first()
                )
                if not obs_span:
                    logger.warning(
                        "langfuse_no_observations_for_score",
                        trace_id=trace_id,
                        score_name=score_data.get("eval_type_id"),
                    )
                    continue

            _, new_score = EvalLogger.no_workspace_objects.update_or_create(
                eval_id=langfuse_score_id,
                trace__project_id=project_id,
                defaults={
                    "trace": trace,
                    "observation_span": obs_span,
                    **score_data,
                },
            )
            scores_count += 1
            scores_created += new_score

    # Capture the saved revision, not a later wall-clock version, so callbacks
    # arriving out of order cannot resurrect an older observation/root payload.
    transaction.on_commit(
        lambda rows=tuple(span_rows), previous=tuple(previous_rows): (
            mirror_spans_to_clickhouse(rows, previous_spans=previous)
        )
    )

    # CH25: mirror this trace into the CH `traces` table — the app-level
    # replacement for the removed PeerDB CDC path feeding trace_dict. The
    # Langfuse path is where trace.name is promoted from the root span, so this
    # keeps named traces correct in CH. One mirror per upsert; best-effort.
    from tracer.services.clickhouse.v2.trace_writer import (
        mirror_traces_to_clickhouse,
    )

    transaction.on_commit(lambda tid=str(trace.id): mirror_traces_to_clickhouse([tid]))

    # CH25 (P3b flip): mirror the curated EndUser / TraceSession into CH
    # `end_users` / `trace_sessions`, keyed by the DETERMINISTIC ids stamped above.
    # The PG get_or_create is GONE — pass the CuratedEndUser/CuratedSession built
    # from the curated fields. One mirror per upsert (Langfuse is one trace per
    # call); post-commit + best-effort.
    if ch_end_user is not None or ch_session is not None:
        from tracer.services.clickhouse.v2.curated_writer import (
            mirror_curated_dimensions_to_clickhouse,
        )

        transaction.on_commit(
            lambda eu=ch_end_user, s=ch_session: (
                mirror_curated_dimensions_to_clickhouse(
                    [eu] if eu is not None else None,
                    [s] if s is not None else None,
                )
            )
        )

    refused = RefusedRows(
        observation_ids=refused_observation_ids,
        root=root_refused,
        score_ids=tuple(dict.fromkeys(refused_score_ids)),
    )
    if refused.count:
        logger.warning(
            "langfuse_rows_in_another_project_not_stored",
            project_id=str(project_id),
            observations=len(refused.observation_ids),
            root=refused.root,
            scores=len(refused.score_ids),
        )

    return created, spans_count, scores_count, spans_created, scores_created, refused
