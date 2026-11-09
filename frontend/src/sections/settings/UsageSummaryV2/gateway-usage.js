import { OWN_KEY_LABEL } from "src/sections/gateway/utils/providerLabel";

const micros = (value) => Math.round(Number(value || 0) * 1e6);

/**
 * Credits come from the ledger; own-key cost comes only from the analytics
 * billing_mode group. Statement lines intentionally contain no upstream cost.
 * A missing breakdown is unknown, not zero and not the total of all requests.
 */
export function splitGatewayUsage(statement, requestCosts) {
  const lines = statement?.spend_by_model || [];
  const spend = lines.reduce((sum, line) => sum + micros(line.spend_usd), 0);
  const requests = lines.reduce((sum, line) => sum + (line.count || 0), 0);
  const ownKey =
    requestCosts?.group_by === "billing_mode" &&
    Array.isArray(requestCosts.breakdown)
      ? requestCosts.breakdown
          .filter((group) => group.name === OWN_KEY_LABEL)
          .reduce((sum, group) => sum + micros(group.total_cost), 0)
      : null;
  return {
    managedSpend: spend / 1e6,
    managedRequests: requests,
    ownKeyCost: ownKey === null ? null : ownKey / 1e6,
  };
}
