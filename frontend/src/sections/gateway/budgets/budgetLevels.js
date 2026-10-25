// What the gateway enforces from an org's stored budgets section, read the
// way Django pushes it (agentcc/services/config_push.py _transform_budgets):
// one definition shared by the Budgets page and Org Config > Budgets.

// The Budgets page's Set Budget dialog stores these two org-level entries.
export const ORG_DIALOG_KEYS = ["org_limit", "hard_limit"];

export const ORG_LEVEL_LABELS = {
  org_limit: "Organization",
  hard_limit: "Hard Limit",
  organization: "Organization",
  org: "Organization",
};

const LEVEL_GROUPS = [
  { key: "teams", label: "Team" },
  { key: "users", label: "User" },
  { key: "keys", label: "Key" },
  { key: "tags", label: "Tag" },
];

const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

const isEntry = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const isPositive = (value) =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

// When a Set Budget save was made (ms), or null for an entry with no stamp or
// an unreadable one.
export function savedAtMs(entry) {
  const value = isEntry(entry) ? entry.saved_at : undefined;
  if (typeof value !== "string" || !ISO_DATE_TIME.test(value)) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

// Of the two dialog entries, the one the gateway enforces: the one saved
// last when either is stamped (a stamped save beats an unstamped one), else
// Organization over Hard Limit.
export function orgEntryInForce(budgets) {
  const b = isEntry(budgets) ? budgets : {};
  let latest = null;
  ORG_DIALOG_KEYS.forEach((key) => {
    const ms = savedAtMs(b[key]);
    if (ms !== null && (latest === null || ms > latest.ms)) {
      latest = { key, ms };
    }
  });
  if (latest) return latest.key;
  if (b.org_limit !== undefined && b.org_limit !== null) return "org_limit";
  if (isEntry(b.hard_limit)) return "hard_limit";
  return null;
}

// The org-level limit the gateway enforces (0 when none).
export function effectiveOrgLimit(budgets) {
  const b = isEntry(budgets) ? budgets : {};
  const key = orgEntryInForce(b);
  const value = key ? b[key] : undefined;
  if (isEntry(value) && isPositive(value.limit)) return value.limit;
  if (isPositive(b.org_limit)) return b.org_limit;
  for (const orgKey of ["organization", "org"]) {
    if (isEntry(b[orgKey]) && isPositive(b[orgKey].limit)) {
      return b[orgKey].limit;
    }
  }
  return 0;
}

// Whether the org's budgets are on: as stored when the org chose, else on
// exactly when some level has a limit above 0.
export function budgetsEnabled(budgets) {
  const b = isEntry(budgets) ? budgets : {};
  if (b.enabled !== undefined && b.enabled !== null) return Boolean(b.enabled);
  if (effectiveOrgLimit(b) > 0) return true;
  return LEVEL_GROUPS.some(
    ({ key }) =>
      isEntry(b[key]) &&
      Object.values(b[key]).some((e) => isEntry(e) && isPositive(e.limit)),
  );
}

export function budgetsEnabledIsExplicit(budgets) {
  return (
    isEntry(budgets) &&
    budgets.enabled !== undefined &&
    budgets.enabled !== null
  );
}

// Whether an org-level entry was stored by the Set Budget dialog (an object)
// rather than as Org Config's flat fields.
export function isDialogEntry(value) {
  return isEntry(value);
}

export function entryAction(entry) {
  const action =
    entry?.action ||
    entry?.action_mode ||
    entry?.actionMode ||
    entry?.on_exceed ||
    entry?.onExceed;
  if (typeof action === "string" && action) return action.toLowerCase();
  if (entry?.hard === true) return "block";
  if (entry?.hard === false) return "warn";
  return null;
}

// Org Config's flat org budget keeps its action at the top level.
function topLevelAction(budgets) {
  return {
    action: budgets.action,
    action_mode: budgets.action_mode,
    on_exceed: budgets.on_exceed,
  };
}

// The period a level counts spend over, as the gateway reads it.
function orgPeriod(budgets, entry) {
  return (
    (isEntry(entry) && entry.period) ||
    budgets.org_period ||
    budgets.orgPeriod ||
    budgets.default_period ||
    budgets.defaultPeriod ||
    "monthly"
  );
}

// The period the org-level budget counts over (the period the push applies
// to a Set Budget save): org_period, else default_period, else monthly.
export function orgBudgetPeriod(budgets) {
  return orgPeriod(isEntry(budgets) ? budgets : {}, undefined);
}

function levelPeriod(budgets, entry) {
  return (
    (isEntry(entry) && entry.period) ||
    budgets.default_period ||
    budgets.defaultPeriod ||
    "monthly"
  );
}

// Every budget level as a row: the org-level entries (with which one is in
// force) and each team, user, key and tag entry.
export function budgetRows(budgets) {
  const b = isEntry(budgets) ? budgets : {};
  const inForce = orgEntryInForce(b);
  const rows = [];

  ORG_DIALOG_KEYS.forEach((key) => {
    const value = b[key];
    // A boolean hard_limit is Org Config's flag, not a level.
    const isLevel =
      key === "org_limit"
        ? typeof value === "number" || isEntry(value)
        : isEntry(value);
    if (!isLevel) return;
    const entry = isEntry(value) ? value : { limit: value };
    rows.push({
      id: key,
      level: key,
      group: "org",
      label: ORG_LEVEL_LABELS[key],
      limit: Number(entry.limit ?? 0),
      period: orgPeriod(b, entry),
      action:
        entryAction(isEntry(value) ? entry : topLevelAction(b)) ||
        (typeof b.hard_limit === "boolean"
          ? b.hard_limit
            ? "block"
            : "warn"
          : key === "hard_limit"
            ? "block"
            : "warn"),
      throttleRpm: entry.throttle_rpm,
      alertThreshold: entry.alert_threshold,
      inForce: inForce === key,
      overriddenBy:
        inForce && inForce !== key ? ORG_LEVEL_LABELS[inForce] : null,
      laterSave:
        inForce && inForce !== key && savedAtMs(b[inForce]) !== null
          ? true
          : false,
      removable: true,
      entry,
    });
  });

  ["organization", "org"].forEach((key) => {
    if (!isEntry(b[key])) return;
    rows.push({
      id: key,
      level: key,
      group: "org",
      label: ORG_LEVEL_LABELS[key],
      limit: Number(b[key].limit ?? 0),
      period: orgPeriod(b, b[key]),
      action: entryAction(b[key]) || "block",
      throttleRpm: b[key].throttle_rpm,
      inForce: inForce === null,
      overriddenBy: inForce ? ORG_LEVEL_LABELS[inForce] : null,
      laterSave: false,
      // Set through the API; remove-budget pops the key.
      removable: true,
      entry: b[key],
    });
  });

  LEVEL_GROUPS.forEach(({ key, label }) => {
    if (!isEntry(b[key])) return;
    Object.entries(b[key]).forEach(([name, entry]) => {
      if (!isEntry(entry)) return;
      rows.push({
        id: `${key}:${name}`,
        level: `${key}:${name}`,
        group: key,
        label: `${label} ${name}`,
        limit: Number(entry.limit ?? 0),
        period: levelPeriod(b, entry),
        action: entryAction(entry) || "block",
        throttleRpm: entry.throttle_rpm,
        inForce: true,
        overriddenBy: null,
        laterSave: false,
        removable: false,
        entry,
      });
    });
  });

  return rows;
}

// The periods whose org spend the Budgets page reads, sorted, once each.
export function orgSpendPeriods(rows) {
  return [
    ...new Set(rows.filter((r) => r.group === "org").map((r) => r.period)),
  ].sort();
}
