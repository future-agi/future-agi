// The one cost a customer screen shows for an attempt: what the org pays on
// its route. On Future AGI credits that is the amount the ledger debits
// (billed_cost_micros), even when it is zero. On the org's own key it is the
// provider's price (upstream_cost_micros) and no credit is drawn: a chat
// attempt on an own key records billed equal to upstream
// (agentcc-gateway internal/plugins/cost/micros.go), but a native capability
// attempt records the provider's price as upstream and bills 0
// (internal/capability/attempts.go). This is the gateway's own rule for what
// an attempt cost the org (internal/plugins/agentbudget/settle.go,
// internal/plugins/budget/cap_actual.go): billed when managed, else upstream.
// The provider's part of a credits charge, and the difference, are never
// shown.

const micros = (value) => {
  const n = Number(value);
  return value != null && Number.isFinite(n) && n > 0 ? n : 0;
};

/** Micro-dollars the org pays for `attempt` on its route. */
export function paidCostMicros(attempt) {
  if (!attempt) return 0;
  return attempt.credential_type === "managed"
    ? micros(attempt.billed_cost_micros)
    : micros(attempt.upstream_cost_micros);
}
