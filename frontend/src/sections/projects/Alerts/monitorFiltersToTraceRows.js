import { getRandomId } from "src/utils/utils";
import { FILTER_FOR_ERRORS } from "src/sections/projects/LLMTracing/common";

// The monitor stores span type as `observation_type`; the trace list's property
// registry knows the same field as `node_type` (filters.py maps both to the
// observation_type column, so the query is identical either way). Only
// `node_type` resolves to a registry property, and an unresolved row renders
// with its raw column id and a fallback operator — which a user editing the row
// could then write back over the real one.
const TRACE_LIST_SPAN_TYPE_FIELD = "node_type";

// The condition each metric appends after the monitor's own filters, mirroring
// build_metric_value_query in tracer/services/clickhouse/query_builders/
// monitor_metrics.py. It is not persisted on the monitor, so it has to be
// restated here; keep the two in step. Without it a "73 errors" alert lands on
// 89 traces, the extra 16 being traffic the rule watches that did not fail.
const METRIC_EXTRA_ROWS = {
  count_of_errors: FILTER_FOR_ERRORS,
};

// The monitor already stores span attribute rows in the shape the trace list
// accepts — filter_config.col_type = "SPAN_ATTRIBUTE" and all — so they pass
// through untouched apart from the row id the grid assigns its own rows. Span
// type is stored as a bare list and has to become a row of its own.
//
// filter_type is "text", not the "string" SPAN_TYPE_PROPERTY uses for the alert
// panel: "string" is absent from the filter schema's enum, so a row carrying it
// is discarded on arrival with no error and no chip, which is indistinguishable
// from having sent no filter at all.
export const monitorFiltersToTraceRows = (filters, { metricType } = {}) => {
  const rows = [];

  const metricRow = METRIC_EXTRA_ROWS[metricType];
  if (metricRow) {
    rows.push({ ...metricRow, id: getRandomId() });
  }

  if (!filters) return rows;

  const spanTypes = filters.observation_type;
  if (Array.isArray(spanTypes) && spanTypes.length > 0) {
    rows.push({
      id: getRandomId(),
      column_id: TRACE_LIST_SPAN_TYPE_FIELD,
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: spanTypes,
      },
    });
  }

  for (const row of filters.span_attributes_filters || []) {
    if (!row?.column_id || !row?.filter_config) continue;
    rows.push({ ...row, id: getRandomId() });
  }

  return rows;
};
