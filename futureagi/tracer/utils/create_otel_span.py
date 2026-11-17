"""OTel span ingestion for the REST span route.

Each span and its trace are written to Postgres, the system of record. After
those writes commit, the app's mirrors copy the span and the trace row into
ClickHouse, where the Observe lists read them. A REST request sends its copy
once its whole transaction commits, and waits seconds at most for ClickHouse.
A mirror never fails the request: a batch ClickHouse does not accept is
logged with its row count and left for the operator backfill to copy.
"""

import structlog
from django.db import transaction

from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import trace_writer
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
from tracer.utils.helper import get_default_project_session_config
from tracer.utils.otel import convert_otel_span_to_observation_span

logger = structlog.get_logger(__name__)


def _with_trace_session(row, trace_pk):
    """The span row with the session its trace carries once the write commits.

    The spans of one REST request are written on separate worker threads, so
    a sibling span can stamp the trace's session after this span loaded the
    trace. One read of the session column. If the trace has no session, or
    the read fails, the row keeps the session this thread saw: the copy is
    best-effort and never fails the request.
    """
    try:
        session_id = (
            Trace.all_objects.filter(pk=trace_pk)
            .values_list("session_id", flat=True)
            .first()
        )
    except Exception:
        logger.warning("rest_span_session_read_failed", exc_info=True)
        return row
    if session_id is None:
        return row
    return {**row, "trace_session_id": session_id}


class _CopyAtCommit:
    """Copies each row into ClickHouse as soon as its write commits.

    For a span written outside a REST request, with no request to send it.
    """

    def add_span(self, row):
        mirror_spans_to_clickhouse((row,))

    def add_trace(self, trace_id):
        trace_writer.mirror_traces_to_clickhouse([trace_id])


class RestClickHouseCopy:
    """The ClickHouse copy of one REST request, sent once the request commits.

    Each span's commit callback adds its row, and a root span's its trace id;
    a request that rolls back runs none of them. ``send`` is registered as the
    request's last commit callback, so it runs after every add. The trace
    rows go first, then every span in one insert, through the writers'
    short-wait client: a ClickHouse that accepts and never answers costs the
    request one short wait, not a long wait per span. If the trace rows fail,
    the spans are skipped, with one log line that counts them. Postgres keeps
    every span either way, for the operator backfill.
    """

    def __init__(self):
        self._trace_ids = []
        self._span_rows = []

    def add_span(self, row):
        self._span_rows.append(row)

    def add_trace(self, trace_id):
        self._trace_ids.append(trace_id)

    def send(self):
        trace_ids, self._trace_ids = self._trace_ids, []
        span_rows, self._span_rows = self._span_rows, []
        with trace_writer.short_waits():
            if trace_ids and not trace_writer.mirror_traces_to_clickhouse(trace_ids):
                if span_rows:
                    logger.warning("rest_span_copy_skipped", n_spans=len(span_rows))
                return
            if span_rows:
                mirror_spans_to_clickhouse(span_rows)


def create_single_otel_span(
    data, organization_id, user_id, workspace_id=None, clickhouse_copy=None
):
    """Write one OTel span from a REST request to Postgres.

    ``clickhouse_copy`` is the request's ``RestClickHouseCopy``; without one,
    each ClickHouse copy goes as soon as its write commits.
    """
    if clickhouse_copy is None:
        clickhouse_copy = _CopyAtCommit()
    parsed_data = convert_otel_span_to_observation_span(
        data, organization_id, user_id, workspace_id
    )

    trace_manager = getattr(Trace, "no_workspace_objects", Trace.objects)
    existing_trace = trace_manager.filter(id=parsed_data["trace"]).first()
    if existing_trace and existing_trace.project_id != parsed_data["project"].id:
        raise ValueError("Trace does not belong to the resolved project")

    try:
        trace = Trace.objects.get(
            id=parsed_data["trace"], project=parsed_data["project"]
        )
    except Trace.DoesNotExist:
        try:
            trace, created = Trace.objects.get_or_create(
                id=parsed_data["trace"],
                defaults={
                    "project": parsed_data["project"],
                    **(
                        {"project_version": parsed_data["project_version"]}
                        if parsed_data["project_type"] == "experiment"
                        else {}
                    ),
                },
            )

        except Exception as e:
            logger.exception(f"{e}")
            raise Exception(  # noqa: B904
                "Error creating trace while creating observation span from otel"
            )

    val = parsed_data["observation_span"]
    val["trace"] = trace

    # CH-derived-dimensions P3b flip: NO PG ``EndUser.get_or_create`` (the hot-path
    # round-trip is removed; DESIGN §8 drops the PG write last). Instead STAMP the
    # DETERMINISTIC end_user_id (DESIGN §3) into the span's FK COLUMN. The column is
    # ``db_constraint=False`` (migration 0080), so a bare id needs no PG ``EndUser``
    # row; it carries to the CH span via the ``s.end_user_id`` materialization.
    # ``user_id_type`` is the normalized value (``get_user_id_type``) — the SAME
    # value fed to ``deterministic_end_user_id`` and to the CH ``end_users`` row, so
    # the row's key matches its id and the §11.1a NULL→'' sentinel lines up with the
    # committed read-side remap.
    _ch_end_user = None
    if parsed_data.get("end_user"):
        end_user_data = parsed_data["end_user"]
        if (
            end_user_data.get("user_id") is not None
            and parsed_data["project"].trace_type == "observe"
        ):
            eu_id = deterministic_end_user_id(
                parsed_data["project"].id,
                organization_id,
                end_user_data["user_id"],
                end_user_data.get("user_id_type"),
            )
            val["end_user_id"] = eu_id
            _ch_end_user = CuratedEndUser(
                project_id=parsed_data["project"].id,
                end_user_id=eu_id,
                organization_id=organization_id,
                user_id=str(end_user_data["user_id"]),
                user_id_type=end_user_data.get("user_id_type"),
                user_id_hash=end_user_data.get("user_id_hash"),
                metadata=end_user_data.get("metadata", {}),
            )

    if (
        parsed_data["prompt_details"] is not None
        and val.get("observation_type", None) == "llm"
    ):
        prompt_details = parsed_data["prompt_details"]
        prompt_template_name = prompt_details.get("prompt_template_name", None)
        prompt_template_version = prompt_details.get("prompt_template_version", None)
        prompt_template_label = prompt_details.get("prompt_template_label", None)

        if prompt_template_name and prompt_template_label:
            filters = {
                "original_template__name": prompt_template_name,
                "original_template__organization": organization_id,
                "labels__name": prompt_template_label,
            }

            if prompt_template_version:
                filters["template_version"] = prompt_template_version

            prompt_version = PromptVersion.objects.filter(**filters).first()

            if prompt_version:
                prompt_labels_ids = prompt_version.labels.through.objects.filter(
                    promptversion_id=prompt_version
                ).values_list("promptlabel_id", flat=True)
                req_label = None
                if prompt_labels_ids:
                    req_label = PromptLabel.no_workspace_objects.filter(
                        id__in=prompt_labels_ids, name=prompt_template_label
                    ).first()

                if req_label:
                    val["prompt_version"] = prompt_version
                    val["prompt_label_id"] = str(req_label.id)

    observation_span = ObservationSpan.objects.create(**val)

    if not observation_span:
        raise ValueError("observation_span is None")

    if not observation_span.project:
        raise ValueError(f"Project is None for observation_span {observation_span}")

    if not observation_span.trace:
        raise ValueError(f"Trace is None for observation_span {observation_span}")

    # Update trace with input/output from root span
    if observation_span.parent_span_id is None:
        attrs = val.get("span_attributes") or val.get("eval_attributes") or {}
        input_val = getattr(observation_span, "input", None) or attrs.get("input.value")
        output_val = getattr(observation_span, "output", None) or attrs.get(
            "output.value"
        )
        if input_val:
            trace.input = input_val
        if output_val:
            trace.output = output_val
    trace.save()

    # CH-derived-dimensions P3b flip: NO PG ``TraceSession.get/create``. STAMP the
    # DETERMINISTIC trace_session_id (DESIGN §3) onto ``trace.session_id`` (the
    # COLUMN). ``Trace.session`` is ``db_constraint=False`` (migration 0082), so a
    # bare id needs no PG ``trace_session`` row; it carries to the CH span via the
    # ``t.session_id`` materialization join AND to CH ``traces.session_id`` via
    # ``trace_writer``. The first-session-seeds-the-project's-session_config side
    # effect is preserved, but now idempotently (no PG row to signal "created"):
    # seed only when the project still has no config — fires once per project, then
    # the truthy config skips it.
    _ch_session = None
    if parsed_data["session_name"] is not None:
        ts_id = deterministic_trace_session_id(
            observation_span.project.id, parsed_data["session_name"]
        )
        trace.session_id = ts_id
        trace.save()
        _ch_session = CuratedSession(
            project_id=observation_span.project.id,
            trace_session_id=ts_id,
            external_session_id=parsed_data["session_name"],
        )

        project = observation_span.project
        if not project.session_config:
            project.session_config = get_default_project_session_config()
            project.save(update_fields=["session_config"])

    # Mirror every span, root or child, into ClickHouse ``spans``: the Observe
    # trace list reads that table. The row is captured here, so a later change
    # to the span cannot alter what the callback sends. Only the trace's
    # session is read again, in the callback once the write has committed: a
    # sibling span on another thread may have stamped it since this span
    # loaded the trace.
    span_row = span_to_pg_row(observation_span, trace.session_id)
    # Same trace id text as the trace row, whatever form the sender used: the
    # trace id field reads every form the trace row itself accepted.
    span_row["trace_id"] = str(Trace._meta.pk.to_python(span_row["trace_id"]))
    transaction.on_commit(
        lambda row=span_row, trace_pk=trace.pk: clickhouse_copy.add_span(
            _with_trace_session(row, trace_pk)
        )
    )

    # CH25: mirror the trace into the CH `traces` table (the app-level
    # replacement for the removed PeerDB CDC path that fed trace_dict). Gate on
    # the ROOT span — it carries the trace's identity + input/output, so this
    # fires ~once per trace instead of once per span. Post-commit + best-effort
    # so a CH hiccup never breaks PG ingestion. A REST request sends it ahead
    # of its spans (``RestClickHouseCopy``).
    if observation_span.parent_span_id is None:
        transaction.on_commit(lambda tid=str(trace.id): clickhouse_copy.add_trace(tid))

        # CH25 (P3b flip): mirror the curated EndUser / TraceSession into CH
        # `end_users` / `trace_sessions`, keyed by the DETERMINISTIC id stamped
        # above (_ch_end_user ~L80, _ch_session ~L165). The PG get_or_create is
        # GONE, so we pass the curated fields directly — no PG object, no PG read.
        # Gated on the ROOT span like the trace mirror so it fires ~once per trace,
        # not per span; post-commit + best-effort so a CH hiccup never breaks
        # ingestion.
        if _ch_end_user is not None or _ch_session is not None:
            from tracer.services.clickhouse.v2.curated_writer import (
                mirror_curated_dimensions_to_clickhouse,
            )

            transaction.on_commit(
                lambda eu=_ch_end_user, s=_ch_session: (
                    mirror_curated_dimensions_to_clickhouse(
                        [eu] if eu is not None else None,
                        [s] if s is not None else None,
                    )
                )
            )

    return observation_span
