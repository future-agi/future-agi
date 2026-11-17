// How a user shaped a gateway table (column order, width, visibility, sort),
// kept per table in this browser only, like the rest of the app's view
// preferences. Storage can be missing or throw (private windows, blocked site
// data): every read and write is guarded and the table works without it.

const PREFIX = "gateway-grid:";

export function loadColumnState(key) {
  if (!key) return null;
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    const state = raw ? JSON.parse(raw) : null;
    return Array.isArray(state) ? state : null;
  } catch {
    return null;
  }
}

export function saveColumnState(key, state) {
  if (!key || !Array.isArray(state)) return;
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(state));
  } catch {
    // A view preference that cannot be kept is not an error.
  }
}

export function clearColumnState(key) {
  if (!key) return;
  try {
    window.localStorage.removeItem(PREFIX + key);
  } catch {
    // Nothing to clear.
  }
}

// A table whose saved layout key moves (its columns changed enough that the
// old widths no longer fit) keeps the user's layout: what was saved under the
// old key is carried over once, through `transform`, unless the new key
// already has one, and the old key is removed.
export function migrateColumnState(fromKey, toKey, transform) {
  if (!fromKey || !toKey) return;
  const old = loadColumnState(fromKey);
  if (!old) return;
  if (!loadColumnState(toKey)) {
    try {
      saveColumnState(toKey, transform(old));
    } catch {
      // A layout that cannot be carried over leaves the table's defaults.
    }
  }
  clearColumnState(fromKey);
}
