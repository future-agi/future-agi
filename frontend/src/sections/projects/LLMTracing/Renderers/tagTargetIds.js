export const getTagCellTargetIds = (data = {}) => {
  const explicitSpanId =
    data?.span_id ?? data?.spanId ?? data?.observation_span_id;
  const traceId = data?.trace_id ?? data?.traceId;
  const rowId = data?.id;
  const spanId =
    explicitSpanId ?? (traceId && rowId && rowId !== traceId ? rowId : null);

  return {
    traceId: traceId ?? (!spanId ? rowId : null) ?? undefined,
    spanId: spanId ?? undefined,
  };
};
