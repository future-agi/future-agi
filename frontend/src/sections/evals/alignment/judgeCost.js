import { formatExactMoney } from "src/sections/common/format/money";
// W7: what the judge calls of an Agreement-tab action
// cost, from the ONE estimator (estimateInputs.judgeCallUsd: the default
// judge's call price scaled by the model's list price, the rule the run
// ledger bills). Compare, Improve judge and the tuned-judge check price
// their confirm here, and the mock answers its `estimate_usd` here too, so
// a confirm and its response carry one number. Pure.
//
// A Custom model is priced by its own stored prices (integration H-B1: the
// estimator reads the org's custom model prices, as the run engine does).
import { judgeCallUsd } from "src/sections/develop-detail/Experiment/estimateInputs";

/**
 * USD of one judge call at `model` (null: the default judge); a Custom model
 * at its own prices. Never null once H-B1 is in (the type stays nullable for
 * callers that format a missing price).
 * @param {string|null} model
 * @param {Object<string, number[]>|null} customPrices  estimateInputs.customPricesOf
 */
export function judgeCallPrice(model, customPrices = null) {
  return judgeCallUsd(model, { customPrices });
}

/**
 * Scoring `n` rows once with each of `models`: `{calls, usd, unpriced}`;
 * `usd` is null when a model has no price the estimator reads.
 */
export function judgeCallsCost(n, models, customPrices = null) {
  const rows = Math.max(0, Number(n) || 0);
  const prices = (models ?? []).map((m) => [
    m,
    judgeCallPrice(m, customPrices),
  ]);
  const unpriced = prices.filter(([, usd]) => usd == null).map(([m]) => m);
  return {
    calls: rows * prices.length,
    usd: unpriced.length
      ? null
      : rows * prices.reduce((a, [, usd]) => a + usd, 0),
    unpriced,
  };
}

/** $1.02 / $0.058 / $0.0059 (two significant figures under a cent). */
export const usdText = (usd) =>
  formatExactMoney(usd, {
    minimumFractionDigits: Number(usd) === 0 ? 0 : 2,
    missing: "–",
  });

/** The confirm's cost: `about $1.02`, or which model the estimate cannot price. */
export const costClause = (cost) =>
  cost.usd != null
    ? `about ${usdText(cost.usd)}`
    : `${cost.unpriced.join(", ")} has no price in the estimate`;
