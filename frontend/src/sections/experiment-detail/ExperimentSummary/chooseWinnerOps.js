// L7c g6 (sum:choose-winner): latency and token weights rank runs by their
// recorded response time and token counts. Only prompt and HTTP runs
// record them for this ranking; agent, code and harness runs have none, so
// those weights would do nothing or invert the crown. The drawer offers
// them only when every run in the experiment records them, and sends 0 for
// them otherwise. Pure.
export const OPS_WEIGHT_KEYS = Object.freeze([
  "response_time",
  "completion_tokens",
  "total_tokens",
]);
const METERED = new Set(["prompt", "http"]);

/** True when every run of the experiment records latency and tokens. */
export function opsWeightsAvailable(runs = []) {
  return (runs ?? []).every(
    (r) => !r?.target_type || METERED.has(r.target_type),
  );
}

/** The weights to send: ops weights forced to 0 when not offered. */
export function weightsToSend(values, opsAvailable) {
  if (opsAvailable) return values;
  return {
    ...values,
    ...Object.fromEntries(OPS_WEIGHT_KEYS.map((k) => [k, 0])),
  };
}
