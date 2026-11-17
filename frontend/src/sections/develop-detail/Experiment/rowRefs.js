// options-truth L3 G8 P1 (entry:deep-link): `rows=` in a drawer deep link
// names rows by id or by label (`T5`, `Row 5`, `5`). Pure: resolve the refs
// against the dataset's rows, drop the ones that name no row and say so.
import { rowNumberOf } from "./tryOnOneRowModel";

const LABEL = /^(?:t|row)?\s*(\d+)$/i;

/**
 * @param {string[]} refs  the `rows=` values, in link order
 * @param {object[]} rows  table rows (`row_id`, 0-based `order`)
 * @param {{complete?: boolean}} [opts]  `complete: false` when `rows` is
 *   only the first page: an id-like value that is not on the page is kept
 *   (it may be further down), a label that names no listed row is not.
 * @returns {{ids: string[], labels: string[], missing: string[],
 *   found: number, asked: number}}
 */
export function resolveRowRefs(refs, rows, { complete = true } = {}) {
  const list = rows ?? [];
  const byId = new Map(list.map((r) => [String(r?.row_id), r]));
  const byNumber = new Map(
    list
      .map((r) => [rowNumberOf(r), r])
      .filter(([n]) => n !== null)
      .map(([n, r]) => [String(n), r]),
  );
  const ids = [];
  const labels = [];
  const missing = [];
  const asked = [];
  (refs ?? [])
    .map((s) => String(s ?? "").trim())
    .filter(Boolean)
    .forEach((ref) => {
      if (asked.includes(ref)) return;
      asked.push(ref);
      const m = ref.match(LABEL);
      const row =
        byId.get(ref) ?? (m ? byNumber.get(String(Number(m[1]))) : null);
      if (!row) {
        if (!complete && !m && !ids.includes(ref)) {
          ids.push(ref);
          labels.push(ref);
          return;
        }
        missing.push(ref);
        return;
      }
      if (ids.includes(row.row_id)) return;
      ids.push(row.row_id);
      labels.push(ref);
    });
  return { ids, labels, missing, found: ids.length, asked: asked.length };
}

/**
 * The line the Data step shows under a deep link's rows, or null when
 * every value named a row: `2 of 3 rows found · not in this dataset: zzz`.
 */
export function rowRefsNote(res, { noun = "row" } = {}) {
  if (!res || !res.asked || !res.missing.length) return null;
  const nouns = res.asked === 1 ? noun : `${noun}s`;
  const head = `${res.found} of ${res.asked} ${nouns} found`;
  const tail = `not in this dataset: ${res.missing.join(", ")}`;
  const fallback = res.found ? "" : ` · running on all ${noun}s`;
  return `${head} · ${tail}${fallback}`;
}
