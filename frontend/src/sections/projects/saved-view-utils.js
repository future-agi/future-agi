// Helpers shared by canSaveView memos across LLMTracingView, SessionsView,
// and UsersView. The naive length check used previously missed value-only
// edits (same column, new filter value), so the Save view button stayed
// hidden after legitimate changes.

const readColumnId = (f) => f?.column_id ?? f?.columnId ?? null;
const readFilterConfig = (f) => f?.filter_config ?? f?.filterConfig ?? null;

// Deep equality for an extraFilters / structural-filter array. Tolerates the
// snake_case ↔ camelCase split between the saved-view backend payload and the
// live in-memory shape (Sessions normalizes on apply, but mixed inputs still
// turn up via the popover and direct setExtraFilters callers).
export const filtersContentEqual = (a, b) => {
  const aArr = Array.isArray(a) ? a : [];
  const bArr = Array.isArray(b) ? b : [];
  if (aArr.length !== bArr.length) return false;
  if (aArr.length === 0) return true;
  for (let i = 0; i < aArr.length; i += 1) {
    if (readColumnId(aArr[i]) !== readColumnId(bArr[i])) return false;
    if (
      JSON.stringify(readFilterConfig(aArr[i])) !==
      JSON.stringify(readFilterConfig(bArr[i]))
    ) {
      return false;
    }
  }
  return true;
};
