import { samplingPolicyErrors } from "./samplingPolicyForm";

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
      : { start: startDate, end: endDate },
    default: live ? { rate: Number(samplingRate ?? 10) } : { target_n: 100 },
    strata: [],
    group_by: [],
  };
  if (samplingWindowInvalid(policy))
    policy.window = {
      start: new Date(Date.now() - 86400000).toISOString(),
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
  return live
    ? `${policy.default.rate}% of ${units}`
    : `${Number(policy.default.target_n).toLocaleString()} ${units}`;
}

export const simpleSelectionText = (policy, rowType) => {
  const units = pickNames(policy.sampling_unit);
  const amount = policy.strata?.length
    ? simpleGroupSelectionText(policy) ||
      `Complete the per-group ${policy.mode === "live" ? "shares" : "counts"}`
    : policy.mode === "live"
      ? `${policy.default?.rate}% of new ${units}`
      : `up to ${Number(policy.default?.target_n).toLocaleString()} ${units}`;
  return `${amount}; evaluate every matching ${rowNames(rowType).replace(/s$/, "")} in each picked group.`;
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
  return `${selected.toLocaleString()} ${pickNames(policy.sampling_unit)} · ${rows.toLocaleString()} ${rowNames(rowType)} evaluated`;
}
