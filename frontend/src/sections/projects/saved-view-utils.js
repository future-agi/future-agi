// Helpers shared by canSaveView memos across LLMTracingView, SessionsView,
// and UsersView. The naive length check used previously missed value-only
// edits (same column, new filter value), so the Save view button stayed
// hidden after legitimate changes.

// Deep equality for an extraFilters / structural-filter array. Saved-view
// filters are stored and compared in the canonical API shape.
export const filtersContentEqual = (a, b) => {
  const aArr = Array.isArray(a) ? a : [];
  const bArr = Array.isArray(b) ? b : [];
  if (aArr.length !== bArr.length) return false;
  if (aArr.length === 0) return true;
  for (let i = 0; i < aArr.length; i += 1) {
    if (aArr[i]?.column_id !== bArr[i]?.column_id) return false;
    if (
      JSON.stringify(aArr[i]?.filter_config ?? null) !==
      JSON.stringify(bArr[i]?.filter_config ?? null)
    ) {
      return false;
    }
  }
  return true;
};
