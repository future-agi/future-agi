// W5-7 (W5-SCOPE §7 C3): select every row of the filtered view, not only the
// rows one read loaded. The server half is R3 `ids_only=true` (every example
// id of the view, in order: fetchResultIds); this module decides when the
// offer shows and what it says. Pure, except selectAllMatching (one GET).
import { fetchResultIds } from "src/api/develop/run-results";

/**
 * The `Select all N matching rows` offer: shown only when every loaded row
 * is selected and the view has more rows than were loaded.
 * @returns {{total: number, text: string} | null}
 */
export function selectAllOffer({ selectedCount, loadedCount, total }) {
  if (!Number.isFinite(total) || !Number.isFinite(loadedCount)) return null;
  if (!loadedCount || selectedCount < loadedCount) return null;
  if (total <= loadedCount || selectedCount >= total) return null;
  return {
    total,
    text: `Select all ${total.toLocaleString()} matching rows`,
  };
}

/** `All 248 matching rows selected` once the selection is the whole view. */
export function allSelectedText(total) {
  return `All ${total.toLocaleString()} matching row${total === 1 ? "" : "s"} selected`;
}

/** Every example id of the view (the same filters, sort and runs). */
export function selectAllMatching(datasetId, view) {
  return fetchResultIds(datasetId, view);
}
