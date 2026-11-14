"""Keep trace-level mapped inputs bound to the spans that proved row filters."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from tracer.selectors.eval_tasks.row_resolver import (
    EvalTaskSelectionRejected,
    TraceFilterWitness,
    _annotation_label_ids_for_filters,
    _eval_config_ids_for_filters,
    _eval_filter_metadata_for_filters,
    _task_ui_filters,
    _trace_any_span_filter_descriptors,
    _validated_trace_filter_witnesses,
)
from tracer.services.eval_tasks.sampling.query import BATCH_SIZE, stream_query

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask


class TraceWitnessReader:
    def __init__(self, task: EvalTask) -> None:
        self.project_id = str(task.project_id)
        self.builder = None
        self.filters = []
        self.columns = []
        if task.row_type != "traces":
            return
        self.filters = _task_ui_filters(
            task.filters or {}, row_type="traces", bounded_trace_root=True
        )
        descriptors = _trace_any_span_filter_descriptors(self.filters)
        if not descriptors:
            return
        from tracer.services.clickhouse.v2.dispatch import get_v2_class

        options = {
            "project_id": self.project_id,
            "filters": self.filters,
            "bounded_internal_scan": True,
            "bounded_identity_only": True,
            "bounded_bulk_scan": True,
            "annotation_label_ids": list(
                _annotation_label_ids_for_filters(self.project_id, self.filters)
            ),
        }
        eval_ids = _eval_config_ids_for_filters(self.project_id, self.filters)
        if eval_ids is not None:
            options["eval_config_ids"] = list(eval_ids)
        metadata = _eval_filter_metadata_for_filters(self.project_id, self.filters)
        if metadata is not None:
            options["eval_filter_metadata"] = metadata
        self.builder = get_v2_class("TRACE_LIST")(**options)
        if not self.builder.supports_bounded_filter_scan():
            raise EvalTaskSelectionRejected(
                "Trace sampling requires bounded, supported row filters."
            )
        self.columns = [
            "trace_id",
            *[f"filter_witness_{index}" for index in range(len(descriptors))],
        ]

    def build_query(self, ids: Iterable[str]) -> tuple[str, dict] | None:
        identities = list(ids)
        if not identities or self.builder is None:
            return None
        if len(identities) > BATCH_SIZE:
            raise ValueError(
                "Trace witness batches must not exceed the sampling batch size."
            )
        query, params = self.builder.build_filter_match_query(
            identities, candidate_full_state=True
        )
        if not query:
            raise EvalTaskSelectionRejected("Trace row filters could not be proven.")
        return f"SELECT {', '.join(self.columns)} FROM ({query})", params

    def read(self, ids: Iterable[str]) -> tuple[TraceFilterWitness, ...]:
        identities = list(ids)
        query = self.build_query(identities)
        if query is None:
            return ()
        rows = []
        for block in stream_query(*query):
            rows.extend(dict(zip(self.columns, row, strict=True)) for row in block)
            if len(rows) > len(identities):
                raise EvalTaskSelectionRejected(
                    "Trace witness result exceeded its candidate scope."
                )
        if {str(row["trace_id"]) for row in rows} != set(identities):
            raise EvalTaskSelectionRejected(
                "Trace filters changed during sampling. Retry the bounded window."
            )
        return _validated_trace_filter_witnesses(
            rows, ui_filters=self.filters, project_id=self.project_id
        )
