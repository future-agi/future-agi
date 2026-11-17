export const matchesEvalSearch = (evalRow, query) => {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return true;
  if (String(evalRow?.eval_name || "").toLowerCase().includes(needle)) return true;
  return (evalRow?.spans || []).some((span) =>
    String(span?.span_name || "").toLowerCase().includes(needle),
  );
};

export const hasScorableAggregate = (aggregate) =>
  aggregate &&
  typeof aggregate === "object" &&
  (Number(aggregate.pass) > 0 || Number(aggregate.fail) > 0);

export const summaryText = (aggregate) => {
  if (typeof aggregate === "number" && Number.isFinite(aggregate)) {
    return `${aggregate}%`;
  }
  if (!hasScorableAggregate(aggregate)) return null;
  return `${aggregate.pass || 0}/${(aggregate.pass || 0) + (aggregate.fail || 0)} passed`;
};
