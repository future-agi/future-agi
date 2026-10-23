export function contributorLink(projectId, row) {
  const column = row.trace_id
    ? "trace_id"
    : row.session_id
      ? "session_id"
      : "user_id";
  const id = row[column];
  if (!id) return null;
  const session = column === "session_id";
  const params = new URLSearchParams({
    [session ? "sessionFilter" : "primaryTraceFilter"]: JSON.stringify([
      {
        column_id: column,
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: id,
        },
        id: `contributor-${column}`,
      },
    ]),
  });
  return `/dashboard/observe/${encodeURIComponent(projectId)}/${session ? "sessions" : "llm-tracing"}?${params}`;
}
