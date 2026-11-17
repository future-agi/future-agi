// One request on Future AGI credits and one on the org's own key, as the
// gateway logs, meters and bills them, for the price reconciliation tests.
//
// The price rule: every price or cost the console shows is ONE number, what
// the org pays on that route. On credits that is the charged amount the
// ledger debits; on an own key it is the provider's price, and no credit is
// debited. The provider cost and the margin inside a credits charge are
// never shown apart.
//
// Amounts are integer micro-dollars so the expected text is exact; the
// upstream and margin strings are what a broken surface would show.

export const CREDITS = {
  chargedMicros: 42000,
  upstreamMicros: 40000,
  marginMicros: 2000,
  charged: "$0.042",
  upstream: "$0.04",
  margin: "$0.002",
};

export const OWN_KEY = {
  chargedMicros: 13000,
  upstreamMicros: 13000,
  charged: "$0.013",
};

// Six decimal places, as Django's request-log serializer writes `cost`.
export const usd6 = (micros) => {
  const whole = Math.trunc(micros / 1_000_000);
  const fraction = String(micros % 1_000_000).padStart(6, "0");
  return `${whole}.${fraction}`;
};

// Eight decimal places, as it writes cost_upstream and cost_markup.
const usd8 = (micros) => `${usd6(micros)}00`;

/** A request log row (agentcc/serializers/request_log.py) on credits. */
export function creditsLog({
  id = "log-credits",
  chargedMicros = CREDITS.chargedMicros,
  upstreamMicros = CREDITS.upstreamMicros,
  startedAt = "2026-11-09T09:00:00Z",
} = {}) {
  return {
    id,
    request_id: `req-${id}`,
    model: "openai/gpt-5",
    provider: "openrouter",
    status_code: 200,
    latency_ms: 812,
    started_at: startedAt,
    input_tokens: 1000,
    output_tokens: 2000,
    total_tokens: 3000,
    cost: usd6(chargedMicros),
    cost_source: "catalog",
    generation_id: `req-${id}`,
    billing_mode: "managed",
    credential_type: "managed",
    cost_upstream: usd8(upstreamMicros),
    cost_markup: usd8(chargedMicros - upstreamMicros),
    attempt_count: 1,
    agent_id: null,
    agent_step: null,
    agent_run_id: null,
    metadata: {
      billing_mode: "managed",
      cost_base: usd6(upstreamMicros),
      cost_markup_percent: "5",
      cost_source: "catalog",
    },
    billing: {
      mode: "managed",
      credential_type: "managed",
      cost_upstream: usd6(upstreamMicros),
      cost_source: "catalog",
    },
  };
}

/** A request log row on the org's own provider key. */
export function ownKeyLog({
  id = "log-own-key",
  chargedMicros = OWN_KEY.chargedMicros,
  startedAt = "2026-11-09T09:05:00Z",
} = {}) {
  return {
    ...creditsLog({ id, startedAt }),
    model: "openai/gpt-4o",
    provider: "openai",
    cost: usd6(chargedMicros),
    cost_source: "modeldb",
    billing_mode: "byok",
    credential_type: "byok",
    cost_upstream: usd8(chargedMicros),
    cost_markup: null,
    metadata: {
      billing_mode: "byok",
      cost_base: usd6(chargedMicros),
      cost_source: "modeldb",
    },
    billing: {
      mode: "byok",
      credential_type: "byok",
      cost_upstream: usd6(chargedMicros),
      cost_source: "modeldb",
    },
  };
}

// One UTC day of requests: four on credits, two on own keys.
export const DAY_ROWS = [
  creditsLog({ id: "d1", chargedMicros: 42000, upstreamMicros: 40000, startedAt: "2026-11-09T00:10:00Z" }),
  ownKeyLog({ id: "d2", chargedMicros: 13000, startedAt: "2026-11-09T03:00:00Z" }),
  creditsLog({ id: "d3", chargedMicros: 10500, upstreamMicros: 10000, startedAt: "2026-11-09T08:30:00Z" }),
  creditsLog({ id: "d4", chargedMicros: 21, upstreamMicros: 20, startedAt: "2026-11-09T12:00:00Z" }),
  ownKeyLog({ id: "d5", chargedMicros: 500000, startedAt: "2026-11-09T17:45:00Z" }),
  creditsLog({ id: "d6", chargedMicros: 1260000, upstreamMicros: 1200000, startedAt: "2026-11-09T23:59:00Z" }),
];

/** "$1.312521" (or "-$0.04") as integer micro-dollars, exactly. */
export function microsOf(text) {
  const match = /^(-?)\$(\d[\d,]*)(?:\.(\d{1,6}))?$/.exec(String(text).trim());
  if (!match) throw new Error(`not an exact USD amount: ${text}`);
  const whole = Number(match[2].replace(/,/g, ""));
  const fraction = Number((match[3] || "").padEnd(6, "0"));
  return (match[1] ? -1 : 1) * (whole * 1_000_000 + fraction);
}

export const sumMicros = (values) => values.reduce((sum, v) => sum + v, 0);
