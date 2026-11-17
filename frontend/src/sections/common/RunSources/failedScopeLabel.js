import { fmtCount } from "src/sections/common/format/fmt";

/**
 * The failed scope's label: `Failed rows (13)`, `Failed rows` before the
 * count is known, and `Failed rows (—)` for a run with nothing to count
 * (its rows stay in the scope; no number counts its errored tries).
 */
export function failedScopeLabel(failedRows, rowNoun, { noPassFail } = {}) {
  if (failedRows == null) return `Failed ${rowNoun}s`;
  return `Failed ${rowNoun}s (${noPassFail ? "—" : fmtCount(failedRows)})`;
}
