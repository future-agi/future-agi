// Stored policy can carry the older camelCase spellings the backend contract
// still accepts (AliasChoices). The server normalizes every incoming
// section with one rule: the first spelling PRESENT in the object wins, in the
// order canonical, then the aliases (so a canonical null still shadows an
// alias), and every other spelling is dropped. Reads expose the stored JSON
// unchanged, so the panel must apply the same rule before it displays, drafts
// or clears anything; otherwise a hidden alias survives a "clear".
//
// Only the fields this panel reads or writes are listed. Every other field is
// left exactly as stored; the server normalizes it on write.
export const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;

export const AGENT_ALIASES = {
  key_bindings: ["key_bindings", "keyBindings"],
  max_run_usd: ["max_run_usd", "maxRunUsd", "maxRunUSD"],
  unpriced_under_cap: ["unpriced_under_cap", "unpricedUnderCap"],
};
export const BUDGET_ALIASES = {
  admission_caps_enabled: ["admission_caps_enabled", "admissionCapsEnabled"],
  policy_revision: ["policy_revision", "policyRevision"],
  key_ids: ["key_ids", "keyIds"],
  fail_mode: ["fail_mode", "failMode"],
  no_max_tokens: ["no_max_tokens", "noMaxTokens"],
};
export const CAP_ALIASES = {
  activated_at: ["activated_at", "activatedAt"],
};

export const isMap = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);
export const hasOwn = (value, key) =>
  Object.prototype.hasOwnProperty.call(value, key);

export function normalizeAliases(value, table) {
  if (!isMap(value)) return {};
  const result = { ...value };
  for (const [field, choices] of Object.entries(table)) {
    const present = choices.filter((choice) => hasOwn(result, choice));
    if (!present.length) continue;
    result[field] = result[present[0]];
    for (const alias of present) {
      if (alias !== field) delete result[alias];
    }
  }
  return result;
}

export const normalizeAgents = (agents) =>
  normalizeAliases(agents, AGENT_ALIASES);
export const normalizeBudgets = (budgets) =>
  normalizeAliases(budgets, BUDGET_ALIASES);
export const normalizeCap = (cap) => normalizeAliases(cap, CAP_ALIASES);
