// Key-to-agent bindings and the gateway rule they must satisfy.
//
// While ANY agent cap exists, the gateway (ResolveAdmission) refuses every
// execution key that has no administrative binding with 402
// call_cap_unenforceable, before any cap is selected, including keys that
// have no cap at all. With no agent cap, key caps and the per-run ceiling
// work without a single binding. Binding to an agent that has no cap is
// allowed: it is how a key is kept working without an agent cap.
import { IDENTITY, hasOwn, isMap, normalizeAgents } from "./admissionAliases";

const filled = (value) => typeof value === "string" && value.trim() !== "";
const validIdentity = (value) =>
  typeof value === "string" && IDENTITY.test(value);

export function bindingRows(agents) {
  const bindings = normalizeAgents(agents).key_bindings;
  if (!isMap(bindings)) return [];
  return Object.entries(bindings).map(([key, agent]) => ({
    key,
    agent: String(agent),
  }));
}

// Hard validation, run before any write. Returns the key -> agent map to send.
export function checkBindings(rows, agentIds) {
  const map = {};
  rows.forEach(({ key, agent }, index) => {
    if (!validIdentity(key) || !validIdentity(agent))
      throw new Error(
        `Binding ${index + 1}: use an ASCII key ID and agent identity of 1–128 characters, with no spaces.`,
      );
    if (hasOwn(map, key))
      throw new Error(
        `Duplicate binding for key ${key}. A key maps to exactly one agent.`,
      );
    map[key] = agent;
  });
  if (agentIds.length === 0) return map;
  if (Object.keys(map).length === 0)
    throw new Error(
      "Agent caps need key bindings: with no binding, the gateway refuses requests from every execution key, including keys that have no cap. Add a key-to-agent binding for each key that should keep working. Code: 402 call_cap_unenforceable.",
    );
  const reached = new Set(Object.values(map));
  for (const id of agentIds) {
    if (!reached.has(id))
      throw new Error(
        `Agent cap "${id}" is unreachable: no key binding points to it, so it can never apply. Bind a key to "${id}" or remove the cap.`,
      );
  }
  return map;
}

// What the draft will do to execution keys, and what must be acknowledged.
// keys: { status: idle|loading|ready|error, keys: [{ id, name, status }] }.
export function bindingReport(draft, config, keys) {
  const agentIds = (draft?.agents || []).map((row) => row.id).filter(filled);
  const keyCapIds = (draft?.key_ids || []).map((row) => row.id).filter(filled);
  const bindings = (draft?.bindings || []).filter((row) => filled(row.key));
  const status = keys?.status ?? "idle";
  const ready = status === "ready";
  const required = agentIds.length > 0;
  const loading = status === "idle" || status === "loading";
  const listUnavailable = required && status === "error";
  const active = ready
    ? (keys.keys || []).filter((key) => key.status === "active")
    : [];
  const activeIds = new Set(active.map((key) => key.id));
  const boundKeys = new Set(bindings.map((row) => row.key));
  const capped = new Set(agentIds);

  // Every key the panel knows would be refused: active organization keys, plus
  // any key that has a cap (known even when the key list cannot be read).
  const known = new Map();
  if (required) {
    active.forEach((key) => known.set(key.id, key.name || key.id));
    keyCapIds.forEach((id) => known.has(id) || known.set(id, id));
  }
  const unbound = [...known]
    .filter(([id]) => !boundKeys.has(id))
    .map(([id, name]) => ({ id, name }));

  const rollout = config?.routing?.rollout;
  const candidate = isMap(rollout)
    ? normalizeAgents(rollout.routing?.agents).key_bindings
    : undefined;
  const repeated = isMap(candidate) ? candidate : {};
  const rolloutConflicts =
    isMap(rollout) && Object.keys(rollout).length > 0
      ? bindings
          .filter(
            (row) =>
              filled(row.agent) &&
              (!hasOwn(repeated, row.key) || repeated[row.key] !== row.agent),
          )
          .map((row) => row.key)
      : [];

  return {
    required,
    loading,
    listUnavailable,
    unbound,
    uncapped: required
      ? bindings.filter((row) => filled(row.agent) && !capped.has(row.agent))
      : [],
    unknownKeys: ready
      ? [...new Set([...bindings.map((row) => row.key), ...keyCapIds])]
          .filter((id) => !activeIds.has(id))
          .sort()
      : [],
    rolloutConflicts,
    needsAck: required && !loading && (listUnavailable || unbound.length > 0),
    signature: JSON.stringify({
      unavailable: listUnavailable,
      unbound: unbound.map((key) => key.id).sort(),
    }),
  };
}
