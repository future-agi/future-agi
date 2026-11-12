// Server-consistent fakes for the admission-cap panel tests.
//
// They restate the pinned Django cap-policy/CAS rules and the gateway's
// ResolveAdmission binding rule independently of product code, so a "success"
// is derived from what the UI actually sent and can never be invented by a
// hand-written mock response.
export const ACTIVE_URL = "/agentcc/org-configs/active/";
export const CREATE_URL = "/agentcc/org-configs/";
export const KEYS_URL = "/agentcc/api-keys/";

const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
const MAX_USD = 1_000_000_000;
const AGENT_ALIASES = [
  ["key_bindings", "keyBindings"],
  ["max_run_usd", "maxRunUsd", "maxRunUSD"],
  ["unpriced_under_cap", "unpricedUnderCap"],
];
const BUDGET_ALIASES = [
  ["admission_caps_enabled", "admissionCapsEnabled"],
  ["policy_revision", "policyRevision"],
  ["key_ids", "keyIds"],
  ["fail_mode", "failMode"],
  ["no_max_tokens", "noMaxTokens"],
];
const has = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

// Pydantic AliasChoices: the first spelling PRESENT (even as null) wins and
// every other spelling is dropped.
function canonical(value, table) {
  const result = { ...value };
  for (const choices of table) {
    const present = choices.filter((choice) => has(result, choice));
    if (!present.length) continue;
    result[choices[0]] = result[present[0]];
    for (const alias of present) {
      if (alias !== choices[0]) delete result[alias];
    }
  }
  return result;
}

function refuse(statusCode, body) {
  return Promise.reject({ statusCode, ...body });
}

// Mirrors agentcc/services/cap_policy.py validate_cap_policy for an enabled policy.
function capPolicyProblem(budgets, routing) {
  if (budgets.enabled !== true) return "budgets.enabled: must be true";
  if ((budgets.fail_mode ?? "closed") !== "closed")
    return "budgets.fail_mode: enabled admission caps require closed";
  if (budgets.no_max_tokens !== "clamp")
    return "budgets.no_max_tokens: enabled admission caps require clamp";
  for (const name of ["agents", "key_ids"]) {
    for (const [id, cap] of Object.entries(budgets[name] || {})) {
      if (!IDENTITY.test(id)) return `budgets.${name}.${id}: bad identity`;
      const { limit } = cap;
      if (
        typeof limit !== "number" ||
        !Number.isFinite(limit) ||
        limit < 0 ||
        limit > MAX_USD
      )
        return `budgets.${name}.${id}.limit: must be finite USD`;
      if ((cap.period ?? "daily") !== "daily")
        return `budgets.${name}.${id}.period: only daily`;
    }
  }
  const agents = routing.agents || {};
  if ((agents.unpriced_under_cap ?? "reject") !== "reject")
    return "routing.agents.unpriced_under_cap: enabled admission caps require reject";
  for (const [key, agent] of Object.entries(agents.key_bindings || {})) {
    if (!IDENTITY.test(key) || !IDENTITY.test(agent))
      return "routing.agents.key_bindings: bad identity";
  }
  const rollout = routing.rollout;
  if (rollout && Object.keys(rollout).length) {
    const candidate = canonical(rollout.routing?.agents || {}, AGENT_ALIASES);
    const other = candidate.key_bindings || {};
    for (const [key, agent] of Object.entries(agents.key_bindings || {})) {
      if (other[key] !== agent)
        return "routing.rollout.routing.agents.key_bindings: cannot drop or change stored bindings";
    }
  }
  return "";
}

// The gateway's ResolveAdmission: once ANY agent cap exists, every execution
// key needs an administrative binding or it is refused before cap selection.
export function gatewayVerdict(config, keyId) {
  const budgets = canonical(config?.budgets || {}, BUDGET_ALIASES);
  if (budgets.admission_caps_enabled !== true) return { admitted: true };
  const agents = canonical(config?.routing?.agents || {}, AGENT_ALIASES);
  const bound = (agents.key_bindings || {})[keyId];
  if (Object.keys(budgets.agents || {}).length > 0 && !bound)
    return { admitted: false, status: 402, code: "call_cap_unenforceable" };
  return { admitted: true, agent: bound };
}

export function createFakeBackend({
  organization = "org-a",
  config = null,
  keys = [],
  keysError = null,
  // The gateway's spend-ledger status the real active read always reports
  // beside the result (admission_cap_ledger); "ready" unless a test says not.
  ledger = "ready",
} = {}) {
  const state = { config, keys, keysError, ledger, gets: [], posts: [] };
  return {
    state,
    get: async (url, options = {}) => {
      state.gets.push({ url, options });
      if (url === KEYS_URL) {
        if (state.keysError) throw state.keysError;
        return { data: { status: true, result: state.keys } };
      }
      if (url === ACTIVE_URL)
        return {
          data: {
            status: true,
            result: state.config,
            admission_cap_ledger: state.ledger,
          },
        };
      throw new Error(`unexpected GET ${url}`);
    },
    post: async (url, body, options = {}) => {
      state.posts.push({ url, body, options });
      if (url !== CREATE_URL) throw new Error(`unexpected POST ${url}`);
      // Work on a copy: the recorded request must stay exactly as sent.
      const sent = JSON.parse(JSON.stringify(body));
      const stored = canonical(state.config?.budgets || {}, BUDGET_ALIASES);
      const current = stored.policy_revision ?? 0;
      const budgets = canonical(sent.budgets || {}, BUDGET_ALIASES);
      const expected = budgets.policy_revision ?? 0;
      if (expected !== current)
        return refuse(409, {
          code: "cap_policy_revision_conflict",
          policy_revision: current,
        });
      const routing = { ...sent.routing };
      if (routing.agents)
        routing.agents = canonical(routing.agents, AGENT_ALIASES);
      if (budgets.admission_caps_enabled === true) {
        const problem = capPolicyProblem(budgets, routing);
        if (problem) return refuse(400, { message: problem });
      }
      const revision = current + 1;
      for (const name of ["agents", "key_ids"]) {
        for (const cap of Object.values(budgets[name] || {})) {
          if (budgets.admission_caps_enabled === true)
            cap.activated_at = "2026-11-06T12:00:00Z";
        }
      }
      state.config = {
        organization,
        version: (state.config?.version ?? 0) + 1,
        created_by: "creator-not-activator",
        budgets: { ...budgets, policy_revision: revision },
        routing,
      };
      return {
        data: {
          status: true,
          result: {
            ...state.config,
            policy_revision: revision,
            gateway_synced: true,
            warnings: [],
          },
        },
      };
    },
    verdict: (keyId) => gatewayVerdict(state.config, keyId),
  };
}
