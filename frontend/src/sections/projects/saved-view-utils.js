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
    // column_id is the native execution field, not a globally unique
    // property identity. A system field and a custom attribute may share it
    // (for example `model`), so an identity-only edit must still mark the
    // saved view dirty. Missing IDs remain equal for legacy saved views.
    if ((aArr[i]?.property_id ?? null) !== (bArr[i]?.property_id ?? null)) {
      return false;
    }
    if (
      JSON.stringify(aArr[i]?.filter_config ?? null) !==
      JSON.stringify(bArr[i]?.filter_config ?? null)
    ) {
      return false;
    }
  }
  return true;
};

// A Custom date range keeps dateOption "Custom" while its [start, end]
// bounds change, so comparing the option name alone misses edited dates.
// Bounds are persisted as "yyyy-MM-dd HH:mm:ss" strings; compare them as
// strings so a hydrated saved view equals its own stored pair. A missing
// pair compares equal to an empty one for legacy views without bounds.
export const dateBoundsEqual = (a, b) => {
  const aArr = Array.isArray(a) ? a : [];
  const bArr = Array.isArray(b) ? b : [];
  if (aArr.length !== bArr.length) return false;
  for (let i = 0; i < aArr.length; i += 1) {
    if (String(aArr[i] ?? "") !== String(bArr[i] ?? "")) return false;
  }
  return true;
};

// Date filter equality for saved-view dirty checks: option name first, then
// the custom bounds when the option is "Custom".
export const dateFilterEqual = (current, baseline) => {
  const currentOption = current?.dateOption ?? null;
  const baselineOption = baseline?.dateOption ?? null;
  if (currentOption !== baselineOption) return false;
  if (baselineOption !== "Custom") return true;
  return dateBoundsEqual(current?.dateFilter, baseline?.dateFilter);
};
