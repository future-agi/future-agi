// Batch-only contract: a numeric subtotal is not a complete final charge.
export const batchChargeKnown = (amount, known) =>
  known === true && typeof amount === "number" && Number.isFinite(amount) && amount >= 0;

export const batchTotalKnown = (batch) =>
  batchChargeKnown(batch?.summary?.total_cost, batch?.summary?.cost_known) &&
  batch.summary.unpriced_requests === 0 &&
  Array.isArray(batch.results) &&
  batch.results.every((item) => batchChargeKnown(item?.cost, item?.cost_known));
