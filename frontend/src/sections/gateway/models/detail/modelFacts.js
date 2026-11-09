import {
  formatExactMoney,
  multiplyMoneyValues,
  sumMoneyValues,
  isMoneyValue,
  moneySign,
} from "src/sections/common/format/money";
// Pure readers for the model page: pricing rows (provider price and what a
// managed request is billed: the provider's price plus the markup), the
// routes the gateway reaches the model by, and the Try it result. Each
// route's 24 h traffic is routeHealth.js.

import { paths } from "src/routes/paths";

import { formatTokenCount } from "../catalog";
import { ACCESS } from "../catalogRows";
import { MANAGED_PROVIDER_LABEL } from "../../utils/providerLabel";

// The key dialog opens from this deep link.
export const CREATE_KEY_PATH = `${paths.dashboard.gateway.keys}?create=1`;

/** The model id a request should send, and whether it bills managed credit. */
export function callTarget(row) {
  if (!row) return null;
  const sold = row.access === ACCESS.MANAGED || row.access === ACCESS.BOTH;
  if (sold && (row.managedOn || !row.ownRoutes.length)) {
    return { model: row.id, managed: true };
  }
  if (row.ownRoutes.length) {
    return { model: row.ownRoutes[0].sendAs, managed: false };
  }
  return null;
}

// The one playground: the Prompts workbench, with this model and prompt.
export function promptsHandoffPath({ model, prompt, system }) {
  const query = new URLSearchParams({ model });
  if (prompt) query.set("prompt", prompt);
  if (system) query.set("system", system);
  return `${paths.dashboard.gateway.playground}?${query}`;
}

// Keep decimal inputs as decimal strings through scaling and markup.
const perMillion = (perToken) => multiplyMoneyValues(perToken, "1000000");
const withMarkup = (value, markupPercent) =>
  multiplyMoneyValues(
    value,
    sumMoneyValues(["1", multiplyMoneyValues(markupPercent ?? 0, "0.01")]),
  );
const tidy = (value) => {
  if (!isMoneyValue(value)) return null;
  const exact = formatExactMoney(value, {
    prefix: "",
    minimumFractionDigits: 0,
  });
  // Keep legacy numeric model consumers only when the decimal round-trip is exact.
  const numeric = Number(exact);
  return formatExactMoney(numeric, { prefix: "", minimumFractionDigits: 0 }) ===
    exact
    ? numeric
    : exact;
};

const PRICE_ROWS = [
  ["prompt", "Input"],
  ["completion", "Output"],
  ["cache_read", "Cached input"],
  ["cache_write", "Cache write"],
];

/**
 * Per-1M-token pricing rows: { label, provider, managed }. Managed is null
 * when managed credits cannot buy the model. Tiered prices (price_tiers) add
 * rows "Input above 100K prompt tokens" and so on.
 */
export function pricingRows(model, { sold, markupPercent } = {}) {
  // A blocked offer's legacy price card is not a current provider quote.
  // Do not gate on `sold`: valid own-key routes still show provider prices.
  if (model?.state === "blocked") return [];
  const rows = [];
  const add = (label, perToken, billedPerMillion) => {
    const provider = tidy(perMillion(perToken));
    if (provider == null) return;
    rows.push({
      label,
      provider,
      managed: sold
        ? tidy(billedPerMillion ?? withMarkup(provider, markupPercent))
        : null,
    });
  };
  const prices = model?.prices || {};
  PRICE_ROWS.forEach(([key, label]) => {
    const billed =
      key === "prompt"
        ? model?.billed_input_per_mtok
        : key === "completion"
          ? model?.billed_output_per_mtok
          : undefined;
    add(label, prices[key], billed);
  });
  (Array.isArray(model?.price_tiers) ? model.price_tiers : []).forEach(
    (tier) => {
      const from = formatTokenCount(tier.min_prompt_tokens);
      const tierPrices = tier.prices || {};
      PRICE_ROWS.forEach(([key, label]) =>
        add(`${label} above ${from} prompt tokens`, tierPrices[key]),
      );
    },
  );
  return rows;
}

/** Per-request prices (web search), { label, provider, managed }. */
export function perRequestPrices(model, { sold, markupPercent } = {}) {
  if (model?.state === "blocked") return [];
  const prices = model?.prices || {};
  return [
    ["request", "Request fee (per call)"],
    ["web_search", "Web search, per search"],
  ]
    .filter(([key]) => moneySign(prices[key]) === 1)
    .map(([key, label]) => ({
      label,
      provider: prices[key],
      managed: sold ? tidy(withMarkup(prices[key], markupPercent)) : null,
    }));
}

/**
 * The routes this gateway reaches the model by: managed (when managed
 * credits can buy it) and each of the org's own providers that serve it.
 */
export function modelRoutes(row) {
  if (!row) return [];
  const routes = [];
  if (row.access === ACCESS.MANAGED || row.access === ACCESS.BOTH) {
    routes.push({
      key: "managed",
      label: MANAGED_PROVIDER_LABEL,
      provider: row.model?.provider || "",
      sendAs: row.id,
      managed: true,
      on: row.managedOn,
    });
  }
  row.ownRoutes.forEach((r) =>
    routes.push({
      key: `own:${r.providerId}`,
      label: r.providerName,
      provider: r.providerId,
      sendAs: r.sendAs,
      // A model only the org's providers serve is sent by its page id; the
      // provider lists it under its own name (orgServed.js).
      ...(r.listedAs && r.listedAs !== r.sendAs
        ? { listedAs: r.listedAs }
        : {}),
      managed: false,
      on: true,
    }),
  );
  return routes;
}

export function formatLatency(ms) {
  const n = Number(ms);
  if (ms == null || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} s`;
  return `${Math.round(n)} ms`;
}

export function formatRate(percent) {
  const n = Number(percent);
  if (percent == null || !Number.isFinite(n)) return "—";
  return `${Number(n.toFixed(1))}%`;
}

const lowerKeys = (obj) =>
  Object.fromEntries(
    Object.entries(obj && typeof obj === "object" ? obj : {}).map(([k, v]) => [
      k.toLowerCase(),
      v,
    ]),
  );

/**
 * The test-playground answer as the Try it tab shows it. Header names arrive
 * in any case.
 */
export function readTryResult(result) {
  const headers = lowerKeys(result?.guardrail_headers);
  const body =
    result?.body && typeof result.body === "object" ? result.body : {};
  const status = Number(result?.status_code) || null;
  const ok = status != null && status >= 200 && status < 300;
  const usage = body.usage || {};
  const content = body.choices?.[0]?.message?.content;
  const errorMessage =
    body.error?.message ||
    (typeof body.error === "string" ? body.error : "") ||
    body.detail ||
    "";
  return {
    ok,
    status,
    blocked: Boolean(result?.blocked),
    answer: typeof content === "string" ? content : "",
    error: ok ? "" : errorMessage || "The gateway refused the request.",
    requestId: headers["x-agentcc-request-id"] || "",
    cost:
      headers["x-agentcc-cost"] ??
      (usage.cost != null ? String(usage.cost) : ""),
    latencyMs: headers["x-agentcc-latency-ms"] ?? "",
    billingMode: headers["x-agentcc-billing-mode"] || "",
    inputTokens: usage.prompt_tokens ?? null,
    outputTokens: usage.completion_tokens ?? null,
  };
}

const eventData = (events, name) =>
  events.find((e) => e.event === name)?.data || null;

/**
 * A streamed Try it as the tab shows it, from the events read so far
 * (components/gateway-test-request.js parseEvents). Same fields as
 * readTryResult, plus `streaming` while the answer is still arriving. A
 * refusal arrives as one "result" event and reads like the JSON test. With
 * `finished`, a stream that ended without its last event is a failure.
 */
export function readStreamResult(events = [], { finished = false } = {}) {
  const refusal = eventData(events, "result");
  if (refusal) return { ...readTryResult(refusal), streaming: false };
  const meta = eventData(events, "meta") || {};
  const done = eventData(events, "done");
  const failure = eventData(events, "error");
  const head = done || meta;
  const headers = lowerKeys(head.guardrail_headers);
  const facts = done?.agentcc_metadata || {};
  const usage = done?.usage || {};
  const status = Number(head.status_code) || null;
  const answer = events
    .filter((e) => e.event === "delta")
    .map((e) => e.data?.content || "")
    .join("");
  const cut = finished && !done && !failure;
  const error = failure
    ? failure.message || "The stream stopped."
    : cut
      ? "The stream ended before the answer was complete."
      : "";
  // An unpriced request's final chunk still says cost 0; it names no source.
  const priced =
    Boolean(facts.cost_source) ||
    (typeof facts.cost === "number" && facts.cost > 0);
  return {
    ok: !error && (status == null || (status >= 200 && status < 300)),
    streaming: !done && !error,
    status,
    blocked: Boolean(head.blocked),
    answer,
    error,
    requestId: facts.request_id || headers["x-agentcc-request-id"] || "",
    cost: priced ? String(facts.cost) : headers["x-agentcc-cost"] ?? "",
    latencyMs: facts.latency_ms ?? headers["x-agentcc-latency-ms"] ?? "",
    billingMode: facts.billing_mode || headers["x-agentcc-billing-mode"] || "",
    inputTokens: usage.prompt_tokens ?? null,
    outputTokens: usage.completion_tokens ?? null,
  };
}
