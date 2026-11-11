import isEqual from "lodash/isEqual";
import omit from "lodash/omit";
import {
  samplingPolicyErrors,
  samplingPolicyForApi,
} from "./samplingPolicyForm";

// The task screen intentionally edits only the two simple selection methods.
// Anything else remains an opaque, read-only policy until explicitly replaced.
export const pickLevels = (rowType) =>
  ({
    spans: ["span", "trace", "session", "user", "attribute"],
    traces: ["trace", "session", "user", "attribute"],
    sessions: ["session", "user", "attribute"],
  })[rowType] || [];

export const pickNames = (unit) =>
  ({
    span: "spans",
    trace: "traces",
    session: "conversations",
    user: "users",
    attribute: "custom IDs",
  })[unit] || "groups";
export const pickName = (unit) =>
  ({
    span: "span",
    trace: "trace",
    session: "conversation",
    user: "user",
    attribute: "custom ID",
  })[unit] || "group";
export const rowNames = (rowType) =>
  ({
    spans: "spans",
    traces: "traces",
    sessions: "conversations",
    voiceCalls: "voice calls",
  })[rowType] || "rows";
export const pickLabel = (unit, rowType) =>
  unit === pickLevels(rowType)[0]
    ? `Individual ${rowNames(rowType)}`
    : {
        trace: "Traces",
        session: "Conversations",
        user: "Users",
        attribute: "Custom ID",
      }[unit];

const onlyKeys = (value, keys) =>
  value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  Object.keys(value).every((key) => keys.includes(key));
const scalar = (value) =>
  ["string", "number", "boolean"].includes(typeof value) &&
  (typeof value !== "number" || Number.isFinite(value));
const fieldOK = (field) =>
  onlyKeys(field, ["source", "key"]) &&
  [undefined, "span", "metadata", "resource"].includes(field.source) &&
  typeof field.key === "string" &&
  field.key.length > 0;
const sameField = (a, b) =>
  (a.source || "span") === (b.source || "span") && a.key === b.key;
const valueKey = (value) => `${typeof value}:${JSON.stringify(value)}`;
// The existing sampling query compares scalar text (query.py:129-133), so
// number 42 and string "42" must not become apparently separate groups.
export const samplingMatcherValue = (value) => {
  if (typeof value === "number" && value !== 0 && Math.abs(value) < 0.0001)
    return value
      .toExponential()
      .replace(
        /e([+-])(\d+)$/,
        (_, sign, digits) => `e${sign}${digits.padStart(2, "0")}`,
      );
  return String(value);
};

export function isSimpleSamplingPolicy(policy) {
  if (
    !onlyKeys(policy, [
      "version",
      "sampling_unit",
      "unit_attribute",
      "missing_id",
      "mode",
      "window",
      "default",
      "strata",
      "group_by",
      "coordination",
    ])
  )
    return false;
  if (
    policy.version !== 1 ||
    !pickLevels("spans").includes(policy.sampling_unit) ||
    ![undefined, "exclude"].includes(policy.missing_id) ||
    ![undefined, "independent"].includes(policy.coordination) ||
    !["closed", "live"].includes(policy.mode) ||
    !onlyKeys(policy.window, ["start", "end"]) ||
    (policy.mode === "live" && policy.window.end != null) ||
    (policy.group_by !== undefined &&
      (!Array.isArray(policy.group_by) || policy.group_by.length !== 0))
  )
    return false;
  if (
    policy.unit_attribute &&
    (policy.sampling_unit !== "attribute" || !fieldOK(policy.unit_attribute))
  )
    return false;
  const key = policy.mode === "live" ? "rate" : "target_n";
  if (
    !onlyKeys(policy.default, [key, "filters"]) ||
    !Object.hasOwn(policy.default, key) ||
    (policy.default.filters !== undefined &&
      (!Array.isArray(policy.default.filters) ||
        policy.default.filters.length !== 0))
  )
    return false;
  if (!Array.isArray(policy.strata)) return false;
  const names = new Set(),
    values = new Set();
  let first;
  return policy.strata.every((rule) => {
    if (
      !onlyKeys(rule, ["name", "filters", key]) ||
      !Object.hasOwn(rule, key) ||
      typeof rule.name !== "string" ||
      !rule.name ||
      names.has(rule.name) ||
      !Array.isArray(rule.filters) ||
      rule.filters.length !== 1
    )
      return false;
    const predicate = rule.filters[0];
    if (
      !onlyKeys(predicate, ["source", "key", "op", "values"]) ||
      !fieldOK({ source: predicate.source, key: predicate.key }) ||
      predicate.op !== "in" ||
      !Array.isArray(predicate.values) ||
      predicate.values.length !== 1 ||
      !scalar(predicate.values[0])
    )
      return false;
    if (first && !sameField(first, predicate)) return false;
    const identity = samplingMatcherValue(predicate.values[0]);
    if (values.has(identity)) return false;
    first = predicate;
    names.add(rule.name);
    values.add(identity);
    return true;
  });
}

// One rule for the percentage: its field error, the method switch, the
// selection summary and the save guard below can never disagree.
export const isValidSamplingRate = (value) =>
  value !== "" &&
  value != null &&
  Number.isFinite(Number(value)) &&
  Number(value) >= 1 &&
  Number(value) <= 100;

export function samplingContextError(values) {
  const policy = values.samplingPolicy;
  if (!policy)
    return isValidSamplingRate(values.samplingRate)
      ? ""
      : "Enter a percentage from 1 to 100.";
  if (!pickLevels(values.rowType || "spans").includes(policy.sampling_unit))
    return "The pick level cannot be finer than the rows being evaluated. Replace the advanced settings or restore the original row type.";
  if (policy.mode === "live" && values.runType !== "continuous")
    return "New incoming group picking requires the New incoming data schedule.";
  if (samplingWindowInvalid(policy))
    return "Choose a valid past sampling window of at most 31 days for historical data.";
  return "";
}

export const samplingWindowInvalid = (policy) => {
  if (!policy) return false;
  const start = +new Date(policy.window?.start),
    end = +new Date(policy.window?.end);
  return (
    typeof policy.window?.start !== "string" ||
    !Number.isFinite(start) ||
    start > Date.now() ||
    (policy.mode === "live" && policy.window?.end != null) ||
    (policy.mode === "closed" &&
      (typeof policy.window?.end !== "string" ||
        !Number.isFinite(end) ||
        end <= start ||
        end - start > 31 * 86400000 ||
        end > Date.now()))
  );
};

const DAY_MS = 86400000;
export const SAMPLING_WINDOW_MAX_DAYS = 31;

// One local date-time format for every sampling window and freeze time.
export const samplingLocalDate = (value) => {
  const date = new Date(value);
  return value && Number.isFinite(+date)
    ? new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(date)
    : "Not set";
};
// The same local format without the time, for a one-line mention.
export const samplingLocalDay = (value) => {
  const date = new Date(value);
  return value && Number.isFinite(+date)
    ? new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(date)
    : "Not set";
};

// The task's own time range, fitted to a past group-picking window of at most
// 31 days: a rolling preset's future end becomes now, and a longer range keeps
// its most recent 31 days (`shortened`). Null when nothing of it is in the past.
export function fitSamplingWindow(startDate, endDate, now = Date.now()) {
  const start = +new Date(startDate),
    end = +new Date(endDate);
  if (
    !startDate ||
    !endDate ||
    !Number.isFinite(start) ||
    !Number.isFinite(end)
  )
    return null;
  const fittedEnd = Math.min(end, now - 1000);
  const fittedStart = Math.max(
    start,
    fittedEnd - SAMPLING_WINDOW_MAX_DAYS * DAY_MS,
  );
  if (fittedStart >= fittedEnd) return null;
  return {
    window: {
      start: new Date(fittedStart).toISOString(),
      end: new Date(fittedEnd).toISOString(),
    },
    shortened: fittedStart > start,
  };
}

export function simpleSamplingPolicy({
  rowType,
  runType,
  startDate,
  endDate,
  samplingRate,
}) {
  const live = runType === "continuous";
  const policy = {
    version: 1,
    sampling_unit: pickLevels(rowType).includes("session")
      ? "session"
      : pickLevels(rowType)[0],
    missing_id: "exclude",
    mode: live ? "live" : "closed",
    window: live
      ? { start: new Date(Date.now() - 1000).toISOString(), end: null }
      : fitSamplingWindow(startDate, endDate)?.window || {
          start: startDate,
          end: endDate,
        },
    default: live ? { rate: Number(samplingRate ?? 10) } : { target_n: 100 },
    strata: [],
    group_by: [],
  };
  if (samplingWindowInvalid(policy))
    policy.window = {
      start: new Date(Date.now() - DAY_MS).toISOString(),
      end: new Date(Date.now() - 1000).toISOString(),
    };
  return policy;
}

// A percentage is not a population count. Switching back to a past window
// explicitly resets every simple group to the same disclosed starting count.
export function resetHistoricalSamplingCounts(policy, values) {
  if (!isSimpleSamplingPolicy(policy) || policy.mode !== "live") return policy;
  const reset = (rule) => {
    const next = { ...rule, target_n: 100 };
    delete next.rate;
    return next;
  };
  return {
    ...policy,
    mode: "closed",
    window: simpleSamplingPolicy({ ...values, runType: "historical" }).window,
    default: reset(policy.default),
    strata: policy.strata.map(reset),
  };
}

export function simpleGroupsForValues(policy, field, values) {
  const key = policy.mode === "live" ? "rate" : "target_n";
  const usedNames = new Set(policy.strata.map((rule) => rule.name));
  return values.map((value) => {
    const existing = policy.strata.find(
      (rule) =>
        sameField(rule.filters[0], field) &&
        valueKey(rule.filters[0].values[0]) === valueKey(value),
    );
    if (existing) return existing;
    let index = 1;
    while (usedNames.has(`Group ${index}`)) index += 1;
    const name = `Group ${index}`;
    usedNames.add(name);
    return {
      name,
      filters: [{ ...field, op: "in", values: [value] }],
      [key]: policy.mode === "live" ? 25 : 50,
    };
  });
}

export function advancedSamplingSummary(policy) {
  const settings = [];
  if (
    policy.coordination === "project" ||
    policy.panel !== undefined ||
    policy.prn_start !== undefined
  )
    settings.push("cross-task selection");
  if (policy.cell_quotas) settings.push("saved group quotas");
  if (policy.group_by?.length) settings.push("grouping fields");
  if (policy.missing_id === "row")
    settings.push("individual fallback for missing IDs");
  if (
    [policy.default, ...(policy.strata || [])].some(
      (rule) =>
        rule && ["min_n", "max_n", "rare_below"].some((key) => key in rule),
    )
  )
    settings.push("additional count rules");
  if (!settings.length) settings.push("a selection rule not editable here");
  return `This task uses advanced sampling settings set via the API: ${settings.join(", ")}. Kept unchanged unless you replace them.`;
}

export const samplingGroupValueLabel = (value) =>
  value === "" ? '""' : String(value);

// Requested quotas are not previewed or executed counts. Only the simple,
// disjoint groups can be added, and live shares must never be added together.
export function simpleGroupSelectionText(policy) {
  if (
    !isSimpleSamplingPolicy(policy) ||
    !policy.strata.length ||
    Object.keys(samplingPolicyErrors(policy)).length
  )
    return null;
  const live = policy.mode === "live";
  const key = live ? "rate" : "target_n";
  const amount = (rule) =>
    live ? `${rule.rate}%` : rule.target_n.toLocaleString();
  const groups = [
    ...policy.strata.map(
      (rule) =>
        `${samplingGroupValueLabel(rule.filters[0].values[0])} ${amount(rule)}`,
    ),
    `all other ${amount(policy.default)}`,
  ].join(" · ");
  const units = pickNames(policy.sampling_unit);
  if (live) return `Shares of new ${units}: ${groups}`;
  const total = policy.strata.reduce(
    (sum, rule) => sum + rule[key],
    policy.default[key],
  );
  if (!Number.isSafeInteger(total)) return null;
  return `Up to ${total.toLocaleString()} ${units} in total: ${groups}`;
}

// Short label for list cells. A saved policy, not the legacy sampling_rate,
// decides what a cohort task evaluates; never show its default 100%.
export function compactSamplingLabel(policy, samplingRate) {
  if (!policy) {
    const rate = samplingRate == null ? NaN : Number(samplingRate);
    return Number.isFinite(rate) ? `${rate}%` : "—";
  }
  if (!isSimpleSamplingPolicy(policy)) return "Advanced";
  const units = pickNames(policy.sampling_unit);
  const live = policy.mode === "live";
  if (policy.strata.length) {
    if (live) return `Per-group % of ${units}`;
    const total = policy.strata.reduce(
      (sum, rule) => sum + Number(rule.target_n),
      Number(policy.default.target_n),
    );
    return Number.isSafeInteger(total)
      ? `Up to ${total.toLocaleString()} ${units}`
      : `Per-group ${units}`;
  }
  // A historical count is a ceiling bounded by what exists, as in the header.
  return live
    ? `${policy.default.rate}% of ${units}`
    : `Up to ${Number(policy.default.target_n).toLocaleString()} ${units}`;
}

export const simpleSelectionText = (policy, rowType) => {
  const units = pickNames(policy.sampling_unit);
  const amount = policy.strata?.length
    ? simpleGroupSelectionText(policy) ||
      `Complete the per-group ${policy.mode === "live" ? "shares" : "counts"}`
    : policy.mode === "live"
      ? `${policy.default?.rate}% of new ${units}, picked at random`
      : `Up to ${Number(policy.default?.target_n).toLocaleString()} ${units}, picked at random`;
  // Picking individual rows has no group to expand into.
  if (policy.sampling_unit === pickLevels(rowType)[0]) return `${amount}.`;
  return `${amount}; every matching ${rowNames(rowType).replace(/s$/, "")} in a picked ${pickName(policy.sampling_unit)} is evaluated.`;
};

export function compactSamplingPreview(preview, policy, rowType) {
  if (
    !preview ||
    !Array.isArray(preview.strata) ||
    !preview.strata.every((cell) =>
      ["selected_units", "selected_rows"].every(
        (key) => Number.isSafeInteger(cell[key]) && cell[key] >= 0,
      ),
    )
  )
    return null;
  const selected = preview.strata.reduce(
    (sum, cell) => sum + cell.selected_units,
    0,
  );
  const rows = preview.strata.reduce(
    (sum, cell) => sum + cell.selected_rows,
    0,
  );
  if (!Number.isSafeInteger(selected) || !Number.isSafeInteger(rows))
    return null;
  return `${unitCount(selected, policy.sampling_unit)} · ${rowCount(rows, rowType)} evaluated`;
}

// "1 conversation", "2 conversations"; "1 span", "0 spans".
export const unitCount = (count, unit) =>
  `${count.toLocaleString()} ${count === 1 ? pickName(unit) : pickNames(unit)}`;
export const rowCount = (count, rowType) =>
  `${count.toLocaleString()} ${count === 1 ? rowNames(rowType).replace(/s$/, "") : rowNames(rowType)}`;

// The API compares a policy after its own numeric coercion; an incomplete
// policy cannot be saved, so it counts as changed.
const comparablePolicy = (policy) => {
  if (policy == null) return null;
  try {
    return samplingPolicyForApi(policy);
  } catch {
    return { incomplete: policy };
  }
};
// policy_task_filters: a policy task's population ignores the legacy task
// window keys, so they never make an unchanged population look edited.
const populationFilters = (filters) =>
  omit(filters || {}, ["date_range", "date_preset"]);

// What the API's population_changed check would see between the saved task
// and this edit: the sampling policy, the filters a save sends, the project
// and the row type. Null when nothing changed, or when neither side samples
// (the check then does not apply). Otherwise names what changed, for copy.
export function samplingPopulationChange(saved, draft) {
  const before = saved || {};
  const after = draft || {};
  if (before.policy == null && after.policy == null) return null;
  const policyChanged = !isEqual(
    comparablePolicy(before.policy),
    comparablePolicy(after.policy),
  );
  const filtersChanged = !isEqual(
    populationFilters(before.filters),
    populationFilters(after.filters),
  );
  const scopeChanged =
    before.project !== after.project || before.rowType !== after.rowType;
  const changed = [
    policyChanged && "the sampling",
    filtersChanged && "the filters",
    scopeChanged && "the project or row type",
  ].filter(Boolean);
  return changed.length ? changed.join(" and ") : null;
}

// The Re-run / Save choice for a sampled task, as dialog copy plus each
// option's own consequence. Two facts bound it: the API refuses Edit & Re-run
// once the population changed (Delete & rerun only), and the sample is keyed
// by the policy, filters, project and row type, so an unchanged historical
// task keeps its frozen picks under either option. Null keeps the dialog's
// generic copy.
export function samplingRunChoice(savedPolicy, change) {
  if (change)
    return {
      content: `You changed ${change}, so the current sample no longer applies.`,
      options: {
        fresh_run: {
          description:
            "Removes the current results and picks a new sample with your changes.",
        },
        edit_rerun: {
          disabled: true,
          description: `Not available: you changed ${change}.`,
        },
      },
    };
  if (savedPolicy?.mode !== "closed") return null;
  const units = pickNames(savedPolicy.sampling_unit);
  return {
    content: "Choose what happens to the current sample.",
    options: {
      fresh_run: {
        description: `Removes the current results and evaluates the same picked ${units} again.`,
      },
      edit_rerun: {
        description: `Keeps the ${units} already picked and their results, and evaluates only what is missing.`,
      },
    },
  };
}

const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

const cellRuleName = (cell) => {
  try {
    const [name] = JSON.parse(cell.stratum);
    return typeof name === "string" ? name : null;
  } catch {
    return null;
  }
};

// One row per rule of a simple policy (each group, then everything else),
// with the preview cell whose identity ["<rule name>", []] names it. The
// preview counts only groups that have members, so a rule with no cell had
// none in the window. Null when the preview cannot be read rule by rule.
export function samplingPreviewGroups(preview, policy) {
  if (
    !preview ||
    !isSimpleSamplingPolicy(policy) ||
    !Array.isArray(preview.strata) ||
    !preview.strata.every((cell) =>
      ["eligible_units", "selected_units", "selected_rows"].every((key) =>
        isCount(cell[key]),
      ),
    )
  )
    return null;
  const cells = new Map(
    preview.strata.map((cell) => [cellRuleName(cell), cell]),
  );
  const ruleNames = new Set([
    "default",
    ...policy.strata.map((rule) => rule.name),
  ]);
  if (
    cells.size !== preview.strata.length ||
    [...cells.keys()].some((name) => !ruleNames.has(name))
  )
    return null;
  const key = policy.mode === "live" ? "rate" : "target_n";
  const units = pickNames(policy.sampling_unit);
  const row = (name, label, rule) => {
    const cell = cells.get(name);
    return {
      key: name,
      label,
      requested: rule[key],
      eligible: cell ? cell.eligible_units : 0,
      selected: cell ? cell.selected_units : 0,
      rows: cell ? cell.selected_rows : 0,
    };
  };
  return [
    ...policy.strata.map((rule) =>
      row(rule.name, samplingGroupValueLabel(rule.filters[0].values[0]), rule),
    ),
    row(
      "default",
      policy.strata.length ? `All other ${units}` : `All ${units}`,
      policy.default,
    ),
  ];
}

// A historical count is bounded by what exists: say so when the preview
// window holds fewer groups than the counts ask for, never silently. `short`
// lists the groups below their count, so the copy can name them.
export function samplingPreviewShortfall(groups, policy) {
  if (!groups || policy?.mode !== "closed") return null;
  const requested = groups.reduce((sum, group) => sum + group.requested, 0);
  const selected = groups.reduce((sum, group) => sum + group.selected, 0);
  if (!Number.isSafeInteger(requested) || selected >= requested) return null;
  return {
    requested,
    selected,
    short: groups.filter((group) => group.selected < group.requested),
  };
}

const SHORT_GROUPS_NAMED = 3;
// "Europe has 8 of the 10 requested, so 23 of 25 conversations can be
// picked." Names at most three short groups, then counts the rest.
export function samplingShortfallText(shortfall, unit) {
  const { requested, selected, short } = shortfall;
  const units = pickNames(unit);
  const total = `${selected.toLocaleString()} of ${requested.toLocaleString()} ${units} can be picked`;
  if (short.length === 1) {
    const [group] = short;
    return `${group.label} has ${group.selected.toLocaleString()} of the ${group.requested.toLocaleString()} requested, so ${total}. Widen the window or lower its count.`;
  }
  const named = short
    .slice(0, SHORT_GROUPS_NAMED)
    .map(
      (group) =>
        `${group.label} (${group.selected.toLocaleString()} of ${group.requested.toLocaleString()})`,
    );
  const rest = short.length - named.length;
  const list =
    rest > 0
      ? `${named.join(", ")} and ${rest} more group${rest === 1 ? "" : "s"}`
      : `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}`;
  return `${list} have fewer than requested, so ${total}. Widen the window or lower those counts.`;
}

// The preview's work line, without repeating the headline's picked and
// evaluated counts: what they were picked from, and the runs they cost.
export function samplingPreviewWork(preview, policy, rowType) {
  if (!preview || !policy || !Array.isArray(preview.strata)) return null;
  const counts = ["eligible_units", "selected_rows"];
  if (
    !preview.strata.every((cell) => counts.every((key) => isCount(cell[key])))
  )
    return null;
  const [eligible, rows] = counts.map((key) =>
    preview.strata.reduce((sum, cell) => sum + cell[key], 0),
  );
  const from = `Picked from ${eligible.toLocaleString()} matching ${eligible === 1 ? pickName(policy.sampling_unit) : pickNames(policy.sampling_unit)}.`;
  const evaluators = preview.evaluator_count;
  if (!Number.isSafeInteger(evaluators) || evaluators <= 0)
    return `${from} Add evaluators to estimate runs.`;
  const runs = rows * evaluators;
  return `${from} Each ${rowNames(rowType).replace(/s$/, "")} runs ${evaluators.toLocaleString()} evaluator${evaluators === 1 ? "" : "s"}: ${runs.toLocaleString()} run${runs === 1 ? "" : "s"}.`;
}
