import { BATCH_TERMINAL_STATUSES } from "../hooks/useBatchJobs";
import { batchChargeKnown, batchTotalKnown } from "./batchCharge";

// Export only the response already loaded. A terminal status does not prove
// every result was retained, so every format explicitly names its scope.
export function batchResultsExport(batch, batchId, format) {
  if (!Array.isArray(batch?.results) || !batch.results.length) return null;
  const { results: loadedResults, ...metadata } = batch;
  // Match the screen even for an older response lacking completeness fields.
  const results = loadedResults.map((item) => ({
    ...item,
    cost: batchChargeKnown(item?.cost, item?.cost_known) ? item.cost : null,
    cost_known: batchChargeKnown(item?.cost, item?.cost_known),
  }));
  const snapshot = {
    ...metadata,
    ...(metadata.summary ? { summary: {
      ...metadata.summary,
      total_cost: batchTotalKnown(batch) ? metadata.summary.total_cost : null,
      cost_known: batchTotalKnown(batch),
    } } : {}),
    batch_id: batchId,
    export_scope: "loaded-results-only",
    snapshot_kind: BATCH_TERMINAL_STATUSES.has(batch.status) && batch.outcome !== "unknown" ? "terminal" : "partial",
  };
  if (format === "jsonl") {
    return [
      JSON.stringify({ record_type: "batch_snapshot", ...snapshot }),
      ...results.map((result) => JSON.stringify(result)),
    ].join("\n") + "\n";
  }
  return JSON.stringify({ ...snapshot, results }, null, 2);
}
