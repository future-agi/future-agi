const nonEmptyString = (value) => typeof value === "string" && value.length > 0;

/**
 * ClickHouse spans are physically identified by this complete tuple. A bare
 * OpenTelemetry span_id is not globally unique and can repeat in other traces.
 */
export const getSpanPhysicalIdentity = (row) => {
  const projectId = row?.project_id;
  const traceId = row?.trace_id;
  const spanId = row?.span_id;
  const startTime = row?.start_time;

  if (
    !nonEmptyString(projectId) ||
    !nonEmptyString(traceId) ||
    !nonEmptyString(spanId) ||
    !nonEmptyString(startTime)
  ) {
    return null;
  }

  return { projectId, traceId, spanId, startTime };
};

export const getSpanPhysicalRowId = (row) => {
  const identity = getSpanPhysicalIdentity(row);
  return identity
    ? JSON.stringify([
        identity.projectId,
        identity.traceId,
        identity.spanId,
        identity.startTime,
      ])
    : null;
};

export const parseSpanPhysicalRowId = (rowId) => {
  if (!nonEmptyString(rowId)) return null;

  try {
    const parts = JSON.parse(rowId);
    if (
      !Array.isArray(parts) ||
      parts.length !== 4 ||
      !parts.every(nonEmptyString)
    ) {
      return null;
    }
    return {
      projectId: parts[0],
      traceId: parts[1],
      spanId: parts[2],
      startTime: parts[3],
    };
  } catch {
    return null;
  }
};

export const AMBIGUOUS_SPAN_SELECTION_MESSAGE =
  "Selected spans cannot be submitted safely because a span ID is reused across multiple traces. Narrow the selection and retry.";

export const INVALID_SPAN_SELECTION_MESSAGE =
  "Selected spans are missing their canonical identity. Refresh the data and retry.";

/**
 * Existing mutation APIs accept only a bare source_id. Decode canonical grid
 * identities at that boundary and fail closed if two physical rows would alias
 * to the same source_id. This deliberately does not invent a new API shape.
 */
export const spanSourceIdsFromPhysicalRowIds = (rowIds = []) => {
  const sourceIdToRowId = new Map();

  rowIds.forEach((rowId) => {
    const identity = parseSpanPhysicalRowId(rowId);
    if (!identity) throw new Error(INVALID_SPAN_SELECTION_MESSAGE);

    const previousRowId = sourceIdToRowId.get(identity.spanId);
    if (previousRowId && previousRowId !== rowId) {
      throw new Error(AMBIGUOUS_SPAN_SELECTION_MESSAGE);
    }
    sourceIdToRowId.set(identity.spanId, rowId);
  });

  return Array.from(sourceIdToRowId.keys());
};
