import { buildAddEvalsDraft } from "src/sections/projects/LLMTracing/buildAddEvalsDraft";

const pickString = (...values) =>
  values.find((value) => typeof value === "string" && value.trim());

export function resolveErrorFeedAddEvalsContext(error) {
  if (!error) return null;

  const projectId = pickString(error.projectId, error.project_id);
  const traceId = pickString(
    error.representativeTrace?.traceId,
    error.representativeTrace?.trace_id,
    error.traceId,
    error.trace_id,
  );

  if (!projectId || !traceId) return null;

  return {
    clusterId: pickString(error.clusterId, error.cluster_id) || null,
    projectId,
    traceId,
  };
}

export function buildErrorFeedTraceFilter(traceId) {
  return {
    column_id: "trace_id",
    display_name: "Trace ID",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: traceId,
      col_type: "SYSTEM_METRIC",
    },
  };
}

export function buildErrorFeedAddEvalsPath({ error, returnTo }) {
  const context = resolveErrorFeedAddEvalsContext(error);
  if (!context) return null;

  return buildAddEvalsDraft({
    observeId: context.projectId,
    rowType: "traces",
    mainFilters: [buildErrorFeedTraceFilter(context.traceId)],
    returnTo,
  });
}
