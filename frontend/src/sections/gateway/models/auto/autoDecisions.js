// The auto routing page's readers over auto-routed request logs: what auto
// chose, from which candidates, and why the others were set aside. The
// decision document is the one the request drawer reads
// (logs/autoDecision.js).

import {
  asList,
  asNumber,
  asText,
  parseAutoDecision,
} from "../../logs/autoDecision";
import { orgModelPageId } from "../orgServed";

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
    .filter(Boolean);
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
