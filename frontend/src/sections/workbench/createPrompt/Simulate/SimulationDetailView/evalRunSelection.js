export function resolveEvalsToRun(evals = [], selectedIds) {
  if (!selectedIds || selectedIds.size === 0) {
    return evals;
  }
  const chosen = evals.filter((item) => selectedIds.has(item.id));
  return chosen.length > 0 ? chosen : evals;
}
