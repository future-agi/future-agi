import { budgetRows, budgetsEnabled } from "./budgetLevels";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FORBIDDEN = new Set(["__proto__", "constructor", "prototype"]);
const LEVELS = new Set(["org", "key", "agent_run", "agent_key"]);
const own = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key);
const invalid = (error) => ({ error });
const validIdentity = (id) =>
  Boolean(id) &&
  id.length <= 256 &&
  !Array.from(id).some(
    (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
  ) &&
  !FORBIDDEN.has(id);

export function parseBudgetLink(params) {
  if (!params.has("level") && !params.has("id")) return null;
  if (["level", "id", "org"].some((key) => params.getAll(key).length > 1)) {
    return invalid("The budget link has duplicate parameters.");
  }
  const level = params.get("level");
  const id = params.get("id") || "";
  const orgId = params.get("org") || null;
  if (
    !LEVELS.has(level) ||
    !validIdentity(id) ||
    (orgId && !validIdentity(orgId))
  ) {
    return invalid("The budget link must name a valid level and budget ID.");
  }
  if (level === "agent_key") {
    return UUID.test(id)
      ? { level, id, orgId, mode: "workspace" }
      : invalid("An agent key budget link must name the key's workspace UUID.");
  }
  if (level === "agent_run") {
    if (id === "policy") return { level, id, orgId, mode: "config" };
    if (
      id === "caller" ||
      (id.startsWith("run:") && validIdentity(id.slice(4)))
    ) {
      return {
        level,
        id,
        orgId,
        readOnly: true,
        notice:
          "The console cannot raise an existing run ledger or a caller-supplied budget. Existing run limits can only decrease. Ask the run owner to review the caller budget and start a new run when appropriate; changing the organization ceiling does not unblock this run.",
      };
    }
    return invalid(
      "A run budget link must name policy, caller, or run:<scope>.",
    );
  }
  if (level === "org" && (id === "current" || UUID.test(id))) {
    if (UUID.test(id) && orgId && orgId !== id)
      return invalid("The budget link names two different organizations.");
    return { level, id, mode: "config", orgId: UUID.test(id) ? id : orgId };
  }
  const separator = id.indexOf(":");
  const prefix = id.slice(0, separator);
  const identity = id.slice(separator + 1);
  const groups =
    level === "key"
      ? { name: "keys", id: "key_ids" }
      : { agent: "agents", team: "teams", user: "users", tag: "tags" };
  if (separator < 1 || !own(groups, prefix) || !validIdentity(identity)) {
    return invalid(
      "The budget link does not identify a supported budget entry.",
    );
  }
  return { level, id, orgId, mode: "config", group: groups[prefix], identity };
}

export function resolveBudgetTarget(link, data) {
  if (!link || link.error || link.readOnly) return link;
  if (link.mode === "workspace") {
    if (
      data?.workspace !== link.id ||
      !["daily", "weekly", "monthly"].includes(data?.budget_period)
    ) {
      return invalid(
        "The workspace budget response could not be verified. Reload the cap.",
      );
    }
    const limit = Number(data.budget_usd);
    if (!Number.isFinite(limit) || limit < 0)
      return invalid("The saved workspace limit is invalid.");
    return {
      ...link,
      limit,
      period: data.budget_period,
      title: "Workspace agent key budget",
      notice:
        "This policy applies separately to every agent key in this workspace. The period stays unchanged. A saved increase reaches live keys at the next gateway key sync, not immediately.",
    };
  }
  if (!data || typeof data !== "object")
    return invalid("The saved budget could not be loaded.");
  const budgets = data.budgets || {};
  const revision = budgets.policy_revision ?? 0;
  if (!Number.isSafeInteger(revision) || revision < 0)
    return invalid("Reload the cap to read its policy revision.");
  const base = { ...link, revision };
  if (link.level === "agent_run") {
    const limit = data.routing?.agents?.max_run_usd;
    if (typeof limit !== "number" || !Number.isFinite(limit) || limit < 0) {
      return invalid("There is no saved administrative run ceiling to raise.");
    }
    return {
      ...base,
      limit,
      title: "Organization run ceiling",
      period: "per new run",
      notice:
        "This changes the organization ceiling for new runs, not an existing run ledger. Caller and runtime-token budgets can be lower. The gateway ignores higher limits for existing scopes.",
      inactive:
        !budgetsEnabled(budgets) || budgets.admission_caps_enabled !== true,
    };
  }
  if (link.group === "agents" || link.group === "key_ids") {
    const entry = own(budgets[link.group], link.identity)
      ? budgets[link.group][link.identity]
      : null;
    if (
      !entry ||
      typeof entry.limit !== "number" ||
      !Number.isFinite(entry.limit) ||
      entry.limit < 0
    ) {
      return invalid(
        "This daily cap is not present in the selected organization's saved policy.",
      );
    }
    return {
      ...base,
      limit: entry.limit,
      period: "daily",
      title: link.group === "agents" ? "Agent daily cap" : "Key daily cap",
      inactive:
        !budgetsEnabled(budgets) || budgets.admission_caps_enabled !== true,
      notice:
        "Only the limit changes. Spend, held requests, activation, bindings and enforcement settings stay unchanged.",
    };
  }
  const rows = budgetRows(budgets);
  const row = link.group
    ? rows.find((item) => item.level === `${link.group}:${link.identity}`)
    : rows.find((item) => item.group === "org" && item.inForce);
  if (!row || !Number.isFinite(row.limit) || row.limit <= 0) {
    return invalid(
      "This budget is not present as a positive limit in the selected organization.",
    );
  }
  return {
    ...base,
    limit: row.limit,
    period: row.period,
    title: `${row.label} budget`,
    entryKey: row.group === "org" ? row.level : null,
    flat: row.group === "org" && typeof budgets[row.level] === "number",
    inactive: !budgetsEnabled(budgets),
    notice:
      "Only the limit changes. The period, action and any per-model sub-budgets stay unchanged. A lower sub-budget can still refuse a request.",
  };
}

export function budgetRaisePayload(target, value) {
  if (!target || target.error || target.readOnly)
    throw new Error("This cap has no console writer.");
  const amount = Number(value);
  const maximum = target.mode === "workspace" ? 1000000 : 1000000000;
  if (
    String(value).trim() === "" ||
    !Number.isFinite(amount) ||
    amount <= target.limit ||
    amount > maximum
  ) {
    throw new Error(
      `Enter a limit greater than ${target.limit} and no more than ${maximum} USD.`,
    );
  }
  if (target.mode === "workspace") {
    if (!/^\d+(?:\.\d{1,2})?$/.test(String(value).trim())) {
      throw new Error(
        "Use at most two decimal places for a workspace agent key budget.",
      );
    }
    return { budget_usd: amount.toFixed(2), budget_period: target.period };
  }
  const budgets = { policy_revision: target.revision };
  if (target.level === "agent_run") {
    return { budgets, routing: { agents: { max_run_usd: amount } } };
  }
  if (target.group)
    budgets[target.group] = { [target.identity]: { limit: amount } };
  else budgets[target.entryKey] = target.flat ? amount : { limit: amount };
  return { budgets };
}
