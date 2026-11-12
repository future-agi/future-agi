// Grid links contain presentation state, never permission or dataset overrides.
export const GRID_VIEW_PARAM = "grid_view";
const object = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const densities = ["compact", "comfortable", "tall", "full"];

export function readGridViewLink(encoded) {
  if (!encoded || encoded.length > 65536) return null;
  try {
    const value = JSON.parse(encoded);
    if (!object(value) || !densities.includes(value.density)) return null;
    if (
      typeof value.q !== "string" ||
      !Array.isArray(value.filters) ||
      !Array.isArray(value.sort)
    )
      return null;
    if (
      !value.filters.every(
        (f) =>
          object(f) &&
          typeof f.columnId === "string" &&
          object(f.filterConfig) &&
          typeof f.filterConfig.filterType === "string" &&
          typeof f.filterConfig.filterOp === "string" &&
          "filterValue" in f.filterConfig,
      )
    )
      return null;
    if (
      !value.sort.every(
        (s) =>
          object(s) &&
          typeof s.colId === "string" &&
          ["asc", "desc"].includes(s.sort),
      )
    )
      return null;
    // Split names as in the URL (`splits=hard,none`), applied only with their
    // own capability (useDataToolbarView).
    if ("splits" in value && typeof value.splits !== "string") return null;
    return {
      q: value.q,
      density: value.density,
      filters: value.filters,
      sort: value.sort,
      failed: value.failed === true,
      fevals: Array.isArray(value.fevals)
        ? value.fevals.filter((id) => typeof id === "string")
        : [],
      ...(value.splits ? { splits: value.splits } : {}),
      layout: {
        columns: object(value.layout?.columns) ? value.layout.columns : {},
      },
    };
  } catch {
    return null;
  }
}

export function gridViewHref(state, layout, currentHref) {
  const url = new URL(currentHref);
  // Drop legacy query-state aliases so they cannot overwrite the linked state.
  ["q", "filters", "failed", "fevals", "splits", "expr", "density"].forEach(
    (key) => url.searchParams.delete(key),
  );
  url.searchParams.set(
    GRID_VIEW_PARAM,
    JSON.stringify({
      q: state.q ?? "",
      density: state.density ?? "compact",
      filters: state.filters ?? [],
      sort: state.sort ?? [],
      failed: state.failed === true,
      fevals: state.fevals ?? [],
      ...(typeof state.splits === "string" && state.splits
        ? { splits: state.splits }
        : {}),
      layout: { columns: layout?.columns ?? {} },
    }),
  );
  return url.toString();
}
