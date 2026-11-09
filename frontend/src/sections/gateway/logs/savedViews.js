// Saved filters for the Logs page, kept in this browser only (like the grid's
// column widths): a name and the URL query of the filters. Storage can be
// missing or throw (private windows, blocked site data), so every read and
// write is guarded and the page works without it.

export const SAVED_VIEWS_KEY = "gateway-logs:saved-filters";
const MAX_VIEWS = 30;
// Where a page of results is, or which request is open: not what the
// filters are.
const NOT_A_FILTER = new Set(["page", "pageSize", "page_size", "log"]);

export function cleanQuery(query) {
  const params = new URLSearchParams(query || "");
  for (const key of [...params.keys()]) {
    if (NOT_A_FILTER.has(key)) params.delete(key);
  }
  return params.toString();
}

export function loadSavedViews() {
  try {
    const raw = window.localStorage.getItem(SAVED_VIEWS_KEY);
    const views = raw ? JSON.parse(raw) : [];
    return Array.isArray(views)
      ? views.filter(
          (v) => v && typeof v.name === "string" && typeof v.query === "string",
        )
      : [];
  } catch {
    return [];
  }
}

function write(views) {
  try {
    window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(views));
  } catch {
    // A preference that cannot be kept is not an error.
  }
}

export function saveView(name, query) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return loadSavedViews();
  const views = loadSavedViews();
  const entry = { name: trimmed, query: cleanQuery(query) };
  const at = views.findIndex((v) => v.name === trimmed);
  if (at >= 0) views[at] = entry;
  else views.push(entry);
  const next = views.slice(-MAX_VIEWS);
  write(next);
  return next;
}

export function deleteSavedView(name) {
  const next = loadSavedViews().filter((v) => v.name !== name);
  write(next);
  return next;
}
