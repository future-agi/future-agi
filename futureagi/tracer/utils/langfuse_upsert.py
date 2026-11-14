"""Shared Langfuse trace upsert logic.

Used by both the real-time ``POST /api/public/ingestion`` endpoint and
the Temporal integration sync activities to persist Langfuse-format
traces into the FutureAGI database.
"""

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

logger = structlog.get_logger(__name__)


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


def upsert_langfuse_trace(
    assembled_trace,
    transformer,
    project_id,
    org,
    workspace,
    org_id,
):
    """Upsert a single assembled Langfuse trace with observations and scores.

    Creates a synthetic root span so the trace renders as a tree in the
    FutureAGI UI.  Langfuse treats the trace itself as the root node;
    our frontend expects a root ``ObservationSpan`` with children linked
    via ``parent_span_id``.

    Returns:
        Tuple of ``(was_created, spans_count, scores_count)``.
    """
    trace_data = transformer.transform_trace(assembled_trace, project_id)

    with transaction.atomic():
        # Use filter().first() instead of update_or_create to tolerate
        # duplicate Trace rows.  Vapi sends concurrent batches that can
        # race past update_or_create's internal get(), creating duplicates
        # (no DB-level unique constraint on project_id + external_id).
        defaults = {
            "name": trace_data["name"],
            "input": trace_data.get("input"),
            "output": trace_data.get("output"),
            "metadata": trace_data.get("metadata", {}),
            "tags": trace_data.get("tags", []),
        }
        trace = (
            Trace.no_workspace_objects.filter(
                project_id=project_id,
                external_id=trace_data["external_id"],
            )
            .order_by("created_at")
            .first()
        )
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

        # Create a synthetic root span so the trace renders as a tree.
        # Use a deterministic ID so re-ingestion is idempotent.
        # Truncate external_id to stay within CharField(max_length=255).
        external_id = trace_data["external_id"]
        root_span_id = f"root-{external_id[:245]}"

        # Parent all top-level observations to the root span BEFORE
        # upserting them, so every child is linked from the start.
        for obs_data in obs_dicts:
            if not obs_data.get("parent_span_id"):
                obs_data["parent_span_id"] = root_span_id

        # Upsert observation spans first so timing query covers all of them.
        spans_count = 0
        for obs_data in obs_dicts:
            obs_id = obs_data.pop("id")
            obs_data.pop("trace_id", None)
            obs_data.pop("project_id", None)

            if end_user_id is not None:
                obs_data["end_user_id"] = end_user_id

            ObservationSpan.no_workspace_objects.update_or_create(
                id=obs_id,
                defaults={
                    "trace": trace,
                    "project_id": project_id,
                    "org_id": org_id,
                    **obs_data,
                },
            )
            spans_count += 1

        # Now compute root span timing from ALL existing observation spans
        # for this trace (not just the current batch).  Vapi sends events
        # in multiple batches, so we must query the DB to get the full range.
        from django.db.models import Max, Min

        # CH25-TODO(read-after-write-inside-atomic; revisit after OTel
        # direct-to-CH cutover): this aggregate reads spans that were JUST
        # update_or_create()'d at line 182 inside the same transaction.atomic()
        # block. CH analytics today are populated via PeerDB CDC from PG
        # to CH (see tracer/services/clickhouse/__init__.py:4) — there is
        # an inherent replication lag, so a CHSpanReader.trace_aggregate(
        # trace.id) call here would NOT see the spans we just wrote and
        # would silently under-count earliest_start/latest_end (resulting
        # in a 0-latency root span). After the OTel direct-to-CH cutover
        # spans are written directly to CH at request time and the lag
        # disappears; only then can this be safely migrated.
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
            "name": trace_data["name"] or "Langfuse Trace",
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

        ObservationSpan.no_workspace_objects.update_or_create(
            id=root_span_id,
            defaults=root_defaults,
        )

        # Scores
        score_dicts = transformer.transform_scores(assembled_trace, trace_id)
        scores_count = 0

        for score_data in score_dicts:
            langfuse_score_id = score_data.pop("langfuse_score_id")
            observation_id = score_data.pop("observation_id", None)

            if observation_id:
                # CH25-TODO(read-after-write-inside-atomic; revisit after OTel
                # direct-to-CH cutover): same atomic block as the upserts at
                # lines 182/246. ObservationSpan is also a Django ForeignKey
                # on EvalLogger.observation_span (see EvalLogger.no_workspace_
                # objects.update_or_create call below), so the lookup must
                # return a Django model instance — CHSpan cannot stand in here.
                # CH-side lag mechanism: PeerDB CDC replication from PG.
                try:
                    obs_span = ObservationSpan.no_workspace_objects.get(
                        id=observation_id
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

            EvalLogger.no_workspace_objects.update_or_create(
                eval_id=langfuse_score_id,
                defaults={
                    "trace": trace,
                    "observation_span": obs_span,
                    **score_data,
                },
            )
            scores_count += 1

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

    return created, spans_count, scores_count
