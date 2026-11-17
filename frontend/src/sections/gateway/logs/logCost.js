// How a request's cost reads in the Logs row, the drawer and a session's
// request list, by one rule. The CSV and JSON export keep the stored cost,
// 0 for such a request; its cost_source column tells the two apart.
//
// - A request the gateway found no price for reads "—", titled "No price
//   found" (isUnpriced).
// - "$0.00" only when a charge was computed and came to zero: the gateway
//   priced the request (a cache hit, a free model) and says how with a cost
//   source.
// - "—" when no charge was computed: a request refused, or failed before
//   anything was served, is never priced, so it carries a cost of 0 and no
//   cost source (plugins/cost/finalize.go). Its billing mode does not tell
//   the two apart: the route sets it before such a refusal.
//
// An amount other than zero always reads as it is.

import { isUnpriced } from "../utils/formatters";

/** What "—" means on a request no charge was computed for. */
export const NO_CHARGE = "No charge computed";

/** The request's cost source, from the row, the detail's billing block or v1 metadata. */
export function costSourceOf(log) {
  const sources = [
    log?.cost_source,
    log?.billing?.cost_source,
    log?.metadata?.cost_source,
  ];
  for (const source of sources) {
    if (typeof source === "string" && source.trim()) return source;
  }
  return null;
}

/** Whether no charge was computed for the request: a cost of 0 with no cost source. */
export function noChargeComputed(log) {
  if (!log || isUnpriced(log)) return false;
  const cost = Number(log.cost ?? 0);
  if (Number.isFinite(cost) && cost !== 0) return false;
  return costSourceOf(log) == null;
}
