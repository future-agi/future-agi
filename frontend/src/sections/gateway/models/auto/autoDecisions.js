// The auto routing page's readers over auto-routed request logs: what auto
// chose, from which candidates, and why the others were set aside. The
// decision document is the one the request drawer reads
// (logs/autoDecision.js).

import { paths } from "src/routes/paths";

import {
  asList,
  asNumber,
  asText,
  parseAutoDecision,
} from "../../logs/autoDecision";
import { orgModelPageId } from "../orgServed";

export function decisionOutcome(decision) {
  const status = decision?.statusCode;
  const http = status > 0 ? `HTTP ${status}` : "";
  if (decision?.isError || status >= 400) return `Request error${http ? ` · ${http}` : ""}`;
  if (status >= 200 && status < 300) return `${http} · No error reported`;
  return http || "Outcome not reported";
}

export function decisionLogsPath(decision) {
  if (!decision?.requestId) return null;
  const query = new URLSearchParams({ request_id: decision.requestId });
  const at = Date.parse(decision.at);
  // Explicit UTC day bounds avoid Logs' default recent window hiding an old
  // sampled decision. Request ID remains the exact filter, not a model search.
  if (Number.isFinite(at)) {
    const day = Math.floor(at / 86400000) * 86400000;
    query.set("started_after", new Date(day).toISOString());
    query.set("started_before", new Date(day + 86400000).toISOString());
  }
  return `${paths.dashboard.gateway.logs}?${query}`;
}

// When a request started, as an instant (null when unreadable). Stamps carry
// offsets and fractions, so text order is not time order.
export const instantOf = (at) => {
  const time = Date.parse(at);
  return Number.isFinite(time) ? time : null;
};

/**
 * Newest first by instant; an unreadable time last; a tie by id (descending,
 * as the grid's Time sort breaks it), so the order never shuffles.
 */
export function newestFirst(a, b) {
  const ta = instantOf(a?.at);
  const tb = instantOf(b?.at);
  if (ta !== tb) {
    if (ta === null) return 1;
    if (tb === null) return -1;
    return tb - ta;
  }
  return String(b?.id ?? "").localeCompare(String(a?.id ?? ""));
}

export function decisionsFromLogs(logs) {
  return (Array.isArray(logs) ? logs : [])
    .map((log) => {
      const meta = log?.metadata || {};
      const chosen = asText(meta.auto_model);
      if (!chosen) return null;
      const decision = parseAutoDecision(meta.auto_decision);
      return {
        id: log.id,
        requestId: log.request_id,
        at: log.started_at,
        chosen,
        provider: asText(decision?.provider) || log.provider || "",
        tier: asText(meta.auto_task_tier) || asText(decision?.task_tier) || "",
        complexity:
          asNumber(meta.auto_complexity) ?? asNumber(decision?.complexity),
        preference:
          asText(meta.auto_preference) || asText(decision?.preference) || "",
        considered:
          asNumber(meta.auto_considered) ?? asNumber(decision?.considered),
        eligible: asNumber(meta.auto_eligible),
        fallbacks: asList(meta.auto_fallbacks).length
          ? asList(meta.auto_fallbacks)
          : decision?.fallbacks || [],
        reason: asText(meta.auto_reason) || "",
        ranked: decision?.ranked || [],
        rejected: decision?.rejected || [],
        latencyMs: asNumber(log.latency_ms),
        cost: asText(log.cost),
        statusCode: asNumber(log.status_code),
        isError: Boolean(log.is_error),
      };
    })
    .filter(Boolean)
    .sort(newestFirst);
}

// The auto router's reasons for a model it could not price
// (routing/auto.go): no model info at all, or info with no price.
const NO_PRICE_REASONS = new Set([
  "no price data",
  "no price or capability data",
]);

/**
 * What recent decisions show the gateway did about each model's price:
 * "priced" when a decision ranked it with a cost estimate, "unpriced" when
 * one set it aside for no price data. The newest decision that mentions a
 * model decides. Keys are the model and "<provider>/<model>".
 */
export function gatewayPriceEvidence(decisions) {
  const evidence = new Map();
  const note = (model, provider, verdict) => {
    const id = asText(model);
    if (!id) return;
    const keys = [id];
    if (asText(provider)) keys.push(`${asText(provider)}/${id}`);
    keys.forEach((key) => {
      if (!evidence.has(key)) evidence.set(key, verdict);
    });
  };
  [...(decisions || [])].sort(newestFirst).forEach((decision) => {
    (decision?.ranked || []).forEach((r) => {
      if (asNumber(r?.est_cost_usd) != null)
        note(r.model, r.provider, "priced");
    });
    (decision?.rejected || []).forEach((r) => {
      if (NO_PRICE_REASONS.has(asText(r?.reason)))
        note(r.model, r.provider, "unpriced");
    });
  });
  return evidence;
}

/**
 * The model page id a ranked candidate ({ model, provider }) opens: the
 * catalog's page when `catalogIds` (the catalog rows' ids) has the model;
 * else, for a model one of the org's `providers` lists, "<provider>/<model>",
 * the id the gateway routes to that provider. Null when neither has a page
 * for it.
 */
export function candidatePageId(candidate, { providers, catalogIds } = {}) {
  const model = asText(candidate?.model);
  if (!model) return null;
  if (catalogIds?.has(model)) return model;
  return orgModelPageId(providers, asText(candidate?.provider), model);
}

/** [{ model, count, share }], most chosen first. */
export function chosenShare(decisions) {
  const counts = new Map();
  (decisions || []).forEach((d) =>
    counts.set(d.chosen, (counts.get(d.chosen) || 0) + 1),
  );
  const total = decisions?.length || 0;
  return [...counts]
    .map(([model, count]) => ({ model, count, share: count / total }))
    .sort((a, b) => b.count - a.count || a.model.localeCompare(b.model));
}

// The gateway's eligibility codes (routing/eligibility/codes.go).
const REJECTIONS = {
  key_model_denied: "The API key may not use this model",
  key_provider_denied: "The API key may not use this provider",
  org_provider_denied: "Your organization does not allow this provider",
  managed_not_enabled: "Future AGI credits are off",
  managed_model_not_allowed:
    "Your organization's Future AGI model policy does not allow it",
  model_blocked: "Blocked in the catalog",
  model_expired: "Retired",
  model_deprecated: "Deprecated: auto skips deprecated models",
  model_degraded: "Degraded in the catalog",
  data_policy_zdr: "Does not meet your zero data retention policy",
  data_policy_collection: "Its provider may keep data your policy forbids",
  region_not_allowed: "Not served in an allowed region",
  region_unpinnable_on_aggregator: "Its region cannot be pinned",
  // The catalog's experimental, beta and early-access models
  // (catalog.Model.Preview), refused in this environment.
  preview_blocked_in_env: "Experimental and beta models are blocked here",
  no_provider_matches: "No provider matches the request's preferences",
  price_cap: "Above your price caps",
  quantization: "Its quantization is not allowed",
  missing_parameter: "Lacks a feature the request needs",
  billing_mode_restricted: "Not allowed for this billing mode",
  tool_history_incompatible: "Cannot continue this conversation's tool calls",
};

export const rejectionText = (code) => REJECTIONS[code] || String(code || "");

/**
 * The set-aside models grouped by reason, the largest group first:
 * [{ reason, text, models }]. Ten models refused for one reason read as one
 * line.
 */
export function groupRejections(rejected) {
  const groups = new Map();
  (Array.isArray(rejected) ? rejected : []).forEach(({ model, reason }) => {
    const key = reason || "";
    if (!groups.has(key)) {
      groups.set(key, {
        reason: key,
        text: key ? rejectionText(key) : "No reason recorded",
        models: [],
      });
    }
    groups.get(key).models.push(model);
  });
  return [...groups.values()].sort((a, b) => b.models.length - a.models.length);
}
