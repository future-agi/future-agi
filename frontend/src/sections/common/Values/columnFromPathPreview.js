// Column from path: what the live preview found (L2 fix round 1). Pure.

/**
 * What the preview found. Dev's preview/extract_json/ answers
 * `{preview_results: [{row_id, input, output}], sample_size}` for the first
 * rows of the dataset; prototype mode's mock adds `found`, `total`,
 * `data_type` and a per-row `found`. A row counts as found when its output
 * is a value (dev returns null for a missing key).
 */
export function previewSummary(result) {
  if (!result || typeof result !== "object") return null;
  const rows = (result.preview_results ?? []).map((r) => ({
    ...r,
    found:
      typeof r?.found === "boolean"
        ? r.found
        : r?.output !== null && r?.output !== undefined && r?.output !== "",
  }));
  const counted = typeof result.found === "number";
  const found = counted ? result.found : rows.filter((r) => r.found).length;
  const total =
    typeof result.total === "number"
      ? result.total
      : Number(result.sample_size ?? rows.length) || rows.length;
  const label = counted
    ? `Found in ${found.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} rows`
    : `Found in ${found} of the first ${total} rows`;
  return { found, total, rows, label, dataType: result.data_type ?? null };
}
