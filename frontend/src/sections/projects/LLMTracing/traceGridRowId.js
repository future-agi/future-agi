const nonEmptyString = (value) => typeof value === "string" && value.length > 0;

/**
 * A trace id is unique only within its project. A trace grid that is not
 * pinned to one project (/dashboard/users/:userId) lists one row per
 * (project_id, trace_id), so its AG Grid row ids must carry the project;
 * a bare trace id makes AG Grid reject the whole page (warning #205).
 * A project-pinned grid keeps the bare trace id.
 */
export const getTraceGridRowId = (row, { crossProject = false } = {}) => {
  const traceId = row?.trace_id;
  if (
    crossProject &&
    nonEmptyString(row?.project_id) &&
    nonEmptyString(traceId)
  )
    return JSON.stringify([row.project_id, traceId]);
  return traceId;
};

const traceIdFromGridRowId = (rowId) => {
  try {
    const parts = JSON.parse(rowId);
    if (
      Array.isArray(parts) &&
      parts.length === 2 &&
      parts.every(nonEmptyString)
    ) {
      return parts[1];
    }
  } catch {
    // A bare trace id.
  }
  return rowId;
};

/**
 * Selection APIs take bare trace ids. Decode grid row ids (either shape) at
 * that boundary; copies of one trace id from several projects collapse to one.
 */
export const traceIdsFromGridRowIds = (rowIds = []) => [
  ...new Set((rowIds || []).map(traceIdFromGridRowId).filter(nonEmptyString)),
];
