import {
  IDENTITY,
  isMap,
  normalizeAgents,
  normalizeBudgets,
  normalizeCap,
} from "./admissionAliases";
import { bindingRows, checkBindings } from "./admissionBindings";
import { parseUsd, usdText } from "./admissionUsd";

export { normalizeAgents, normalizeBudgets } from "./admissionAliases";
export { bindingReport, bindingRows } from "./admissionBindings";
export {
  AMOUNT_CONTRACT,
  amountNote,
  amountView,
  effectiveMicros,
  formatMicros,
  parseUsd,
  usdText,
} from "./admissionUsd";

export function policyRevision(config) {
  const revision = normalizeBudgets(config?.budgets).policy_revision ?? 0;
  if (!Number.isSafeInteger(revision) || revision < 0) {
    throw new Error("Policy revision is invalid. Reload the current policy.");
  }
  return revision;
}

const capRows = (caps) =>
  isMap(caps)
    ? Object.entries(caps).map(([id, cap]) => ({ id, ...normalizeCap(cap) }))
    : [];

// The stored policy as the server reads it: every supported spelling resolved
// with the backend's precedence, so display, draft and configured state never
// disagree with what is enforced.
export function readPolicy(config) {
  const budgets = normalizeBudgets(config?.budgets);
  const agents = normalizeAgents(config?.routing?.agents);
  const enabled = budgets.admission_caps_enabled === true;
  const runCeiling = agents.max_run_usd ?? null;
  return {
    enabled,
    configured: enabled || runCeiling !== null,
    agentCaps: capRows(budgets.agents),
    keyCaps: capRows(budgets.key_ids),
    runCeiling,
    bindings: bindingRows(agents),
  };
}

export function admissionDraft(config) {
  const view = readPolicy(config);
  const rows = (caps) =>
    caps.map(({ id, limit }) => ({ id, limit: usdText(limit) }));
  return {
    agents: rows(view.agentCaps),
    key_ids: rows(view.keyCaps),
    bindings: view.bindings,
    runLimit: view.runCeiling === null ? "" : usdText(view.runCeiling),
    ack: "",
  };
}

function capMap(rows) {
  const identities = new Set();
  return Object.fromEntries(
    rows.map(({ id, limit }) => {
      if (!IDENTITY.test(id))
        throw new Error(
          "Use an ASCII identity of 1–128 characters; no spaces.",
        );
      if (identities.has(id))
        throw new Error(
          "Duplicate cap identity. Each identity needs one cap per scope.",
        );
      identities.add(id);
      return [
        id,
        {
          limit: parseUsd(limit).value,
          period: "daily",
          hard: true,
          action: "block",
        },
      ];
    }),
  );
}

export function admissionPayload(config, draft, activate) {
  // Normalize first: every spelling of a field this panel owns collapses to
  // its canonical key, so clearing the canonical key clears the field.
  const budgets = {
    ...normalizeBudgets(config?.budgets),
    policy_revision: policyRevision(config),
    admission_caps_enabled: activate,
  };
  const stored = normalizeAgents(config?.routing?.agents);
  const agents = { ...stored };
  if (activate) {
    if (
      !draft.agents.length &&
      !draft.key_ids.length &&
      draft.runLimit === ""
    ) {
      throw new Error(
        "Add an agent cap, key cap, or per-run ceiling before activating.",
      );
    }
    const caps = {
      agents: capMap(draft.agents),
      key_ids: capMap(draft.key_ids),
    };
    // A draft that carries no binding rows keeps the stored bindings.
    const bindings = checkBindings(
      Array.isArray(draft.bindings) ? draft.bindings : bindingRows(stored),
      draft.agents.map((row) => row.id),
    );
    Object.assign(budgets, {
      enabled: true,
      fail_mode: "closed",
      no_max_tokens: "clamp",
      ...caps,
    });
    agents.unpriced_under_cap = "reject";
    if (Object.keys(bindings).length) agents.key_bindings = bindings;
    else delete agents.key_bindings;
    if (draft.runLimit !== "")
      agents.max_run_usd = parseUsd(draft.runLimit).value;
    else delete agents.max_run_usd;
  } else {
    // Disabling daily admission alone does NOT remove the independent run
    // ceiling. Bindings and every other setting are preserved in the
    // replacement; the ceiling goes in every spelling (the object is already
    // canonical, so removing the canonical key removes them all).
    delete agents.max_run_usd;
  }
  return {
    budgets,
    routing: { ...config?.routing, agents },
    change_description: activate
      ? "Activate admission caps"
      : "Deactivate admission caps",
  };
}

export function admissionError(error) {
  const body = error?.response?.data || error;
  const code = typeof body?.code === "string" ? body.code : "";
  const message =
    typeof body?.message === "string"
      ? body.message
      : typeof body?.detail === "string"
        ? body.detail
        : "The server refused this operation.";
  return [
    code,
    message,
    Number.isSafeInteger(body?.policy_revision)
      ? `Server revision ${body.policy_revision}.`
      : "",
  ]
    .filter(Boolean)
    .join(" — ");
}

export function admissionStatus(error) {
  return error?.statusCode ?? error?.response?.status;
}
