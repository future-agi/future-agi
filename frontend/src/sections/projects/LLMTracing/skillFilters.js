import { paths } from "src/routes/paths";
import {
  EVIDENCE,
  FAILED_REQUESTS,
  LOAD_FAILED,
  LOADED,
  RAN_AFTER_LOADING,
  askedFor,
  failedLoadChip,
  labelNow,
  metricPhrase,
  plural,
  versionName,
} from "src/sections/skills/runtime/runtimeCopy";

// Observe side of skill traces (SKILL-TRACES §6.3, §7.1, §7.2, lane ST9).
//
// The `SKILL` filter contract is the server's (lane ST6,
// tracer/services/clickhouse/query_builders/filters.py SKILL_FILTER_COLUMNS
// and validate_skill_filter_groups). This module mirrors it so Observe never
// sends a request the server rejects, and never rewrites a server-built link:
// "landing equals the cell" (UX-02) holds only when the §6.3 items reach the
// list exactly as the link carried them. A load is never "use".

export const SKILL_COL_TYPE = "SKILL";

// ---------------------------------------------------------------------------
// Observe copy that runtimeCopy.js (lane ST10) does not carry yet.
//
// PENDING MOVE: these strings belong in
// src/sections/skills/runtime/runtimeCopy.js (§2.1, §7.8). ST9 may not edit
// that file, so they are kept in this one marked block and re-exported from
// here only. When ST10 adds them, replace this block with imports. Every word
// follows §2.1: "Loaded", never "Used"; "Failed requests", never "Error rate";
// "Ran after loading", never "In scope".
export const OBSERVE_SKILL_COPY = Object.freeze({
  group: "Skills",
  skillsColumn: "Skills",
  agentColumn: "Agent",
  contextColumn: "Context",
  fields: Object.freeze({
    "skill.id": "Skill",
    "skill.name": "Skill",
    "skill.version_id": "Version",
    "skill.version": "Version",
    "skill.requested_label": "Asked for",
    "skill.agent": "Agent",
    "skill.service": "Service",
    "skill.context_id": "Context",
    "skill.context_version_id": "Context version",
    "skill.binding_id": "Connected app",
    "skill.evidence": "Evidence",
    "skill.load_result": "Load result",
    "skill.digest": "Contents",
    "skill.in_scope": RAN_AFTER_LOADING,
    "skill.root_status": FAILED_REQUESTS,
    "skill.trace_cohort": "Traces from Runtime",
  }),
  more: "More",
  andAlsoLoaded: "and also loaded…",
  loadResult: Object.freeze({ loaded: LOADED, failed: LOAD_FAILED }),
  rootStatus: Object.freeze({
    error: "failed requests",
    ok: "requests that didn't fail",
  }),
  noRecordedLoadOf: (subject) => `No recorded load of ${subject}`,
  noRecordedLoadTooltip:
    "This includes traces from apps that can't record loads.",
  unknownSkill: "Unknown skill",
  shortId: (id) => `${String(id).slice(0, 8)}…`,
  versionOption: (version, labelsNow = []) =>
    labelsNow.length
      ? `${versionName(version)} (${labelsNow.map(labelNow).join(", ")})`
      : versionName(version),
  skillOption: (name, productionVersion) =>
    productionVersion == null
      ? name
      : `${name} · ${labelNow("production")}: ${versionName(productionVersion)}`,
  moreChips: (count) => `+${count}`,
  skillsUnavailable: "Skills unavailable",
  loadingSkills: "Loading skills",
  moreFor: (label) => `More for ${label}`,
  retry: "Retry",
  traceChip: ({ names, failed }) =>
    `Skills: ${names.join(", ")}${failed ? ` · ${failed} failed to load` : ""}`,
  failedOnlyChip: (failed) => `Skills: ${plural(failed, "load")} failed`,
  linkedNotLoaded: ({ context, version, names }) =>
    `Linked in ${context}${version == null ? "" : ` ${versionName(version)}`} and not loaded in this trace: ${names.join(", ")}`,
  howVersionIsDoing: (version) => `How ${versionName(version)} is doing`,
  beforeFirstLoad: (name) =>
    `This span started before ${name} was loaded, so it has no skill attributes.`,
  matchedByReportedName: "Matched to a skill by the name the app reported",
  allTracesThatLoaded: "All traces that loaded this version",
  openSkillVersion: "Open skill version",
  addAsFilter: "Filter by this version",
  card: Object.freeze({
    title: "Skill load",
    failedTitle: "Skill load failed",
    name: "Skill",
    nameAsReported: "Name as reported",
    version: "Version",
    digest: "Contents digest",
    copyDigest: "Copy digest",
    askedFor: "Asked for",
    via: "Via",
    evidence: "Evidence",
    cached: "From a cached copy",
    rootFile: "Root file",
    files: "Files",
    bytes: "Bytes",
    errorType: "Error type",
    yes: "Yes",
    no: "No",
    digestCopied: "Digest copied",
    rootFileYes: "SKILL.md or the whole skill",
    rootFileNo: "A supporting file first",
    ranAfterLoadingTruncated: "Only the first 32 loaded versions are listed.",
  }),
  context: "Agent context",
  connectedApp: "Connected app",
  loadsCount: (count) => plural(count, "load"),
  digestVerified: "Contents match the published version",
  sourceBar: Object.freeze({
    label: "Opened from a skill",
    backTo: (name) => `Back to ${name}`,
    compareBefore: (version) =>
      `Compare with ${versionName(version)} before the move`,
    compareAfter: (version) =>
      `Compare with ${versionName(version)} after the move`,
    afterMove: ({ window, label, toVersion }) =>
      `${window} after ${label} moved to ${versionName(toVersion)}`,
    beforeMove: ({ window, label, toVersion }) =>
      `${window} before ${label} moved to ${versionName(toVersion)}`,
    afterMoveNoWindow: ({ label, toVersion }) =>
      `after ${label} moved to ${versionName(toVersion)}`,
    beforeMoveNoWindow: ({ label, toVersion }) =>
      `before ${label} moved to ${versionName(toVersion)}`,
    linkUnreadable:
      "This link's skill filters couldn't be read, so none of its filters were applied.",
    sortNotApplied:
      "The list can't sort these traces by latency yet, so the slowest aren't first.",
  }),
  cohort: Object.freeze({
    title: "Traces matching these filters",
    error: "Couldn't load the numbers for these traces.",
    loading: "Loading the numbers for these traces",
  }),
  invalidSkillFilter: Object.freeze({
    root_status_alone: "Failed requests needs a skill filter beside it.",
    version_without_skill: "Choose a skill for this version.",
    failed_group_column: "A failed load has no version, evidence or contents to filter by.",
    loaded_and_failed: "A skill filter can show loaded or failed loads, not both.",
  }),
  savedViewBlocked:
    "This view can't be saved yet: saved views don't keep more than one skill filter or a version range.",
  splitBySkillVersion: "Split by skill version",
  widget: Object.freeze({
    skill: "Skill",
    skillVersion: "Skill version",
    skillAgent: "Skill agent",
    versionAttributeWarning:
      "fi.skill.version_id is recorded on skill.load spans only, so this breakdown counts those spans, not whole requests. Use the Skill version breakdown to measure requests.",
  }),
});

// ---------------------------------------------------------------------------
// The §6.3 column table, mirrored exactly from ST6's validator
// (tracer/services/clickhouse/query_builders/filters.py SKILL_FILTER_COLUMNS,
// validate_skill_filter_item, validate_skill_filter_groups). A link is
// refused here only when the server would refuse it too.

const TEXT = "text";
const NUMBER = "number";

/** column_id → { type, ops, failedGroup } (failedGroup: valid in a load_result=failed group). */
export const SKILL_COLUMNS = Object.freeze({
  "skill.id": { type: TEXT, ops: ["equals", "in", "not_in", "not_equals"], failedGroup: true },
  "skill.version_id": { type: TEXT, ops: ["equals", "in", "not_in", "not_equals"], failedGroup: false },
  "skill.name": { type: TEXT, ops: ["equals", "in", "contains"], failedGroup: true },
  "skill.version": { type: NUMBER, ops: ["equals", "in", "gte", "lte"], failedGroup: false },
  "skill.requested_label": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.digest": { type: TEXT, ops: ["equals"], failedGroup: false },
  "skill.evidence": { type: TEXT, ops: ["in"], failedGroup: false },
  "skill.agent": { type: TEXT, ops: ["equals", "in"], failedGroup: true, allowEmpty: true },
  "skill.service": { type: TEXT, ops: ["equals", "in"], failedGroup: true, allowEmpty: true },
  "skill.context_id": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.context_version_id": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.binding_id": { type: TEXT, ops: ["equals", "in"], failedGroup: false },
  "skill.in_scope": { type: TEXT, ops: ["equals", "in"], failedGroup: false, spanOnly: true },
  "skill.load_result": { type: TEXT, ops: ["equals"], failedGroup: true },
  "skill.root_status": { type: TEXT, ops: ["equals"], failedGroup: true },
  "skill.trace_cohort": { type: TEXT, ops: ["equals"], failedGroup: true },
});
export const SKILL_COLUMN_IDS = Object.freeze(Object.keys(SKILL_COLUMNS));

export const EVIDENCE_VALUES = Object.freeze(Object.keys(EVIDENCE));
export const LOAD_RESULT_VALUES = Object.freeze(["loaded", "failed"]);
export const ROOT_STATUS_VALUES = Object.freeze(["error", "ok"]);
const ENUM_VALUES = {
  "skill.evidence": EVIDENCE_VALUES,
  "skill.load_result": LOAD_RESULT_VALUES,
  "skill.root_status": ROOT_STATUS_VALUES,
};
const NEGATIVE_OPS = new Set(["not_in", "not_equals"]);
const LIST_OPS = new Set(["in", "not_in"]);
const MAX_VALUES = 1000;
const MAX_VALUE_UTF8_BYTES = 16384;
const MAX_GROUP = 2147483647;
const MAX_VERSION = 4294967295;
// The server's filter item keys (tracer/serializers/filters.py
// FILTER_ITEM_SCHEMA, with ST6's `group`), plus the UI-only `id` and `_meta`.
const ITEM_KEYS = new Set([
  "column_id",
  "group",
  "display_name",
  "property_id",
  "source",
  "output_type",
  "filter_config",
  "id",
  "_meta",
]);
const PASSTHROUGH_TEXT_KEYS = ["property_id", "source", "output_type"];
const CONFIG_KEYS = new Set(["col_type", "filter_type", "filter_op", "filter_value"]);

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (value) => typeof value === "string" && UUID_RE.test(value);

const configOf = (item) => item?.filter_config || item?.filterConfig || {};
const colTypeOf = (item) => configOf(item).col_type || configOf(item).colType || "";

/** True for a §6.3 `SKILL` item (the server matches `col_type` exactly). */
export const isSkillFilterItem = (item) =>
  Boolean(item) && typeof item === "object" && colTypeOf(item) === SKILL_COL_TYPE;

/** The correlation group of a `SKILL` item; absent means 0 (§6.3). */
export const skillGroupOf = (item) =>
  Number.isInteger(item?.group) && item.group >= 0 ? item.group : 0;

const utf8Bytes = (text) => new TextEncoder().encode(text).length;

/**
 * Why the server would reject this one `SKILL` item, or null. A mirror of
 * `validate_skill_filter_item`: exact type and operator, no coercion of
 * strings to numbers, lists only for in/not_in, at most 1,000 values,
 * strings up to 16 KiB, an empty string only for `skill.agent` (an unnamed
 * agent), and the fixed value sets for evidence, load result and root status.
 */
export function skillItemError(item) {
  if (!isSkillFilterItem(item)) return "not_skill";
  const columnId = item.column_id;
  const column = SKILL_COLUMNS[columnId];
  if (!column) return "unknown_column";
  if (item.group !== undefined && (!Number.isInteger(item.group) || item.group < 0 || item.group > MAX_GROUP)) {
    return "bad_group";
  }
  const config = item.filter_config;
  if (!config || typeof config !== "object") return "no_config";
  if (Object.keys(config).some((key) => !CONFIG_KEYS.has(key))) return "unknown_config_key";
  const op = config.filter_op;
  if (config.filter_type !== column.type || !column.ops.includes(op)) return "bad_operator";
  const value = config.filter_value;
  const values = LIST_OPS.has(op) ? value : [value];
  if (!Array.isArray(values) || !values.length || values.length > MAX_VALUES) return "bad_list";
  for (const member of values) {
    if (column.type === NUMBER) {
      if (!Number.isInteger(member) || member < 1 || member > MAX_VERSION) return "bad_version";
    } else if (typeof member !== "string" || utf8Bytes(member) > MAX_VALUE_UTF8_BYTES) {
      return "bad_text";
    } else if (!member && !column.allowEmpty) {
      return "empty_value";
    }
  }
  const allowed = ENUM_VALUES[columnId];
  if (allowed && values.some((member) => !allowed.includes(member))) return "bad_choice";
  return null;
}

/** The item in canonical API shape: known keys only, `group` kept when it is not 0. */
function canonicalSkillItem(item) {
  const group = skillGroupOf(item);
  const displayName = item.display_name;
  const config = item.filter_config;
  const valueLabel = item._meta?.valueLabel;
  const passthrough = Object.fromEntries(
    PASSTHROUGH_TEXT_KEYS.filter((key) => typeof item[key] === "string" && item[key]).map((key) => [key, item[key]]),
  );
  return {
    column_id: item.column_id,
    ...passthrough,
    ...(typeof displayName === "string" && displayName ? { display_name: displayName } : {}),
    ...(group ? { group } : {}),
    ...(typeof valueLabel === "string" && valueLabel ? { _meta: { valueLabel } } : {}),
    filter_config: {
      col_type: SKILL_COL_TYPE,
      filter_type: config.filter_type,
      filter_op: config.filter_op,
      filter_value: config.filter_value,
    },
  };
}

/**
 * A `SKILL` item exactly as received, in canonical shape, or null when the
 * server would reject it. Nothing is rewritten: a link's items reach the
 * list as the link carried them ("landing equals the cell", UX-02). Unknown
 * item keys make the item unreadable rather than being dropped silently.
 */
export function exactSkillItem(item) {
  if (skillItemError(item)) return null;
  if (Object.keys(item).some((key) => !ITEM_KEYS.has(key))) return null;
  return canonicalSkillItem(item);
}

// --- Items built by the filter panel (not links) ---------------------------
// The panel stores single equality as a one-item list and multi-value
// equality as `in`, and numbers as strings. Its items are mapped to an
// operator and value type the column accepts, then validated like any other.

function panelOp(column, op, value) {
  const { ops } = column;
  const multi = Array.isArray(value) && value.length > 1;
  let next = op || "equals";
  if (next === "equals" && (multi || !ops.includes("equals")) && ops.includes("in")) next = "in";
  if (next === "not_equals" && (multi || !ops.includes("not_equals")) && ops.includes("not_in")) next = "not_in";
  if (next === "in" && !multi && !ops.includes("in")) next = "equals";
  if (next === "not_in" && !multi && !ops.includes("not_in")) next = "not_equals";
  if (next === "greater_than_or_equal") next = "gte";
  if (next === "less_than_or_equal") next = "lte";
  return ops.includes(next) ? next : null;
}

function panelValue(column, op, raw) {
  const values = (Array.isArray(raw) ? raw : [raw]).map((value) => {
    if (column.type !== NUMBER) return typeof value === "string" ? value.trim() : value;
    if (typeof value === "string" && /^\d+$/.test(value.trim())) return Number(value.trim());
    return value;
  });
  if (LIST_OPS.has(op)) return values;
  return values.length === 1 ? values[0] : undefined;
}

/**
 * One `SKILL` item built or edited in the filter panel, mapped to what the
 * column accepts, or null when it still is not valid. `grain` "trace" refuses
 * "Ran after loading", which is span grain only (§6.3).
 */
export function normalizeSkillItem(item, { grain = "trace" } = {}) {
  if (!isSkillFilterItem(item)) return null;
  const columnId = item.column_id ?? item.columnId;
  const column = SKILL_COLUMNS[columnId];
  if (!column) return null;
  if (column.spanOnly && grain !== "span") return null;
  const config = configOf(item);
  const rawValue = config.filter_value ?? config.filterValue;
  const op = panelOp(column, config.filter_op ?? config.filterOp, rawValue);
  if (!op) return null;
  const value = panelValue(column, op, rawValue);
  if (value === undefined) return null;
  const candidate = {
    column_id: columnId,
    ...(item.group !== undefined ? { group: item.group } : {}),
    ...(item.display_name ?? item.displayName ? { display_name: item.display_name ?? item.displayName } : {}),
    filter_config: { col_type: SKILL_COL_TYPE, filter_type: column.type, filter_op: op, filter_value: value },
  };
  return skillItemError(candidate) ? null : canonicalSkillItem(candidate);
}

/** Group `SKILL` items by correlation group, in first-seen order, with their indices in `filters`. */
export function skillGroups(filters = []) {
  const groups = new Map();
  (filters || []).forEach((item, index) => {
    if (!isSkillFilterItem(item)) return;
    const group = skillGroupOf(item);
    if (!groups.has(group)) groups.set(group, { group, indices: [], items: [] });
    groups.get(group).indices.push(index);
    groups.get(group).items.push(item);
  });
  return [...groups.values()];
}

const valueOf = (item) => configOf(item).filter_value ?? configOf(item).filterValue;
const opOf = (item) => configOf(item).filter_op ?? configOf(item).filterOp;
const firstValue = (item) => {
  const value = valueOf(item);
  return Array.isArray(value) ? value[0] : value;
};

/**
 * The group rules of `validate_skill_filter_groups`, so the UI never sends a
 * 400 and never refuses a link the server accepts:
 *   - `skill.root_status` needs at least one other `SKILL` item;
 *   - a group cannot be both loaded and failed;
 *   - a `load_result = failed` group allows only skill id or name, asked for,
 *     agent, service, context, context version, load result and root status;
 *   - `skill.version` needs a positive `skill.id` or `skill.name` in its group.
 * Returns `{ ok, reason }`.
 */
export function validateSkillGroups(filters = []) {
  const items = (filters || []).filter(isSkillFilterItem);
  if (!items.length) return { ok: true, reason: null };
  if (items.every((item) => item.column_id === "skill.root_status")) {
    return { ok: false, reason: "root_status_alone" };
  }
  for (const { items: members } of skillGroups(items)) {
    const results = new Set(
      members.filter((item) => item.column_id === "skill.load_result").map(firstValue),
    );
    if (results.size > 1) return { ok: false, reason: "loaded_and_failed" };
    if (results.has("failed") && members.some((item) => !SKILL_COLUMNS[item.column_id]?.failedGroup)) {
      return { ok: false, reason: "failed_group_column" };
    }
    const ids = new Set(members.map((item) => item.column_id));
    if (
      ids.has("skill.version") &&
      !members.some(
        (item) =>
          ["skill.id", "skill.name"].includes(item.column_id) && !NEGATIVE_OPS.has(opOf(item)),
      )
    ) {
      return { ok: false, reason: "version_without_skill" };
    }
  }
  return { ok: true, reason: null };
}

/** The next free correlation group: a second skill is ANDed as its own group (UX-12). */
export const nextSkillGroup = (filters = []) => {
  const groups = skillGroups(filters).map((entry) => entry.group);
  return groups.length ? Math.max(...groups) + 1 : 0;
};

/** True when any `SKILL` item sits outside the default group 0. */
export const hasGroupedSkillItems = (filters = []) =>
  (filters || []).some((item) => isSkillFilterItem(item) && skillGroupOf(item) > 0);

export const hasSkillFilter = (filters = []) =>
  (filters || []).some(isSkillFilterItem);

/**
 * Hydrate stored or linked filters. Non-`SKILL` items go through the existing
 * hydrator one at a time (so order is kept); `SKILL` items are validated here,
 * because the shared hydrator drops the `group` key's item outright.
 *
 * With `strict` (a link from a skill surface), one unreadable `SKILL` item
 * rejects the whole list: dropping it would silently widen the cohort, and the
 * list would no longer be the traces the number counted.
 *
 * Returns `{ rows, rejected }`.
 */
export function hydrateFiltersWithSkills(
  filters,
  { createId, hydrateOthers, grain = "trace", strict = false } = {},
) {
  if (!Array.isArray(filters)) return { rows: [], rejected: 0 };
  const rows = [];
  let rejected = 0;
  filters.forEach((filter) => {
    if (isSkillFilterItem(filter)) {
      // Stored and linked items are validated exactly as the server will.
      const normalized = exactSkillItem(filter);
      if (!normalized || (!strict && SKILL_COLUMNS[filter.column_id]?.spanOnly && grain !== "span")) {
        rejected += 1;
        return;
      }
      const id = filter.id || (createId ? createId() : undefined);
      rows.push({ ...normalized, ...(id ? { id } : {}) });
      return;
    }
    // Non-skill items keep today's behaviour: the shared hydrator drops an
    // unreadable one and keeps the rest.
    rows.push(...(hydrateOthers ? hydrateOthers([filter], createId) : [filter]));
  });
  // Items that are each valid can still break a group rule, and a partial
  // group is a different cohort, so the group rules apply to the whole set.
  if (!validateSkillGroups(rows).ok) rejected += 1;
  if (rejected === 0) return { rows, rejected };
  if (strict) return { rows: [], rejected };
  return { rows: rows.filter((row) => !isSkillFilterItem(row)), rejected };
}

/**
 * Persist filters. Validate `SKILL` items against their per-column contract
 * and keep their exact cohort values and correlation groups. Other items go
 * through `serializeOthers`. The shared API serializer also preserves this
 * shape when the saved-view request is sent. Order within each kind is kept.
 */
export function serializeFiltersWithSkills(filters, serializeOthers) {
  const list = filters || [];
  if (!hasSkillFilter(list)) return serializeOthers(list);
  const others = serializeOthers(list.filter((item) => !isSkillFilterItem(item)));
  const skills = list
    .filter(isSkillFilterItem)
    .map((item) => {
      // UI-only keys (row id, chip labels, picker identity) never persist.
      const { id, _meta, registryId, ...rest } = item;
      return exactSkillItem(rest);
    })
    .filter(Boolean);
  return [...others, ...skills];
}

const itemIdentity = (item) =>
  JSON.stringify([item?.column_id, opOf(item), valueOf(item)]);
// `equals`/`in` and `not_equals`/`not_in` read the same to the panel; a
// change of polarity (a negation edit) is a real change and never matches.
const polarity = (item) => (NEGATIVE_OPS.has(opOf(item)) ? "negative" : "positive");
const looseIdentity = (item) => {
  const value = valueOf(item);
  const values = (Array.isArray(value) ? value : [value]).map(String).sort();
  return JSON.stringify([item?.column_id, polarity(item), values]);
};

/**
 * Keep `SKILL` items intact through the filter panel's row round trip. The
 * panel rebuilds every item from a row, which loses `group` and may turn
 * `equals` into `in`. An item the person did not change is restored exactly
 * from `previous`; a changed one keeps the group of the item it replaced and
 * is mapped to an operator its column accepts. Items the server would reject
 * are dropped from `next`.
 */
export function reconcileSkillItems(previous = [], next = [], { grain = "trace" } = {}) {
  if (!Array.isArray(next)) return next;
  const before = (previous || []).filter(isSkillFilterItem);
  if (!before.length && !next.some(isSkillFilterItem)) return next;
  const exact = new Map(before.map((item) => [itemIdentity(item), item]));
  const loose = new Map(before.map((item) => [looseIdentity(item), item]));
  const used = new Set();
  const out = [];
  next.forEach((item) => {
    if (!isSkillFilterItem(item)) {
      out.push(item);
      return;
    }
    const match = [exact.get(itemIdentity(item)), loose.get(looseIdentity(item))].find(
      (candidate) => candidate && !used.has(candidate),
    );
    if (match) {
      used.add(match);
      out.push(match);
      return;
    }
    // An edited item stays in the correlation group of the item it replaced
    // (the first unused one with the same column); the panel drops `group`.
    const replaced = before.find(
      (candidate) => !used.has(candidate) && candidate.column_id === item.column_id,
    );
    if (replaced) used.add(replaced);
    const group = replaced ? skillGroupOf(replaced) : skillGroupOf(item);
    const normalized = normalizeSkillItem(group ? { ...item, group } : item, { grain });
    if (normalized) out.push({ ...normalized, ...(item.id ? { id: item.id } : {}) });
  });
  return out;
}

/** A stable signature of the `SKILL` and other API items, used to hide the source bar once the person edits. */
export const filtersSignature = (filters = []) =>
  JSON.stringify(
    (filters || []).map((item) => ({
      column_id: item?.column_id,
      group: isSkillFilterItem(item) ? skillGroupOf(item) : undefined,
      op: opOf(item),
      value: valueOf(item),
      col_type: colTypeOf(item) || undefined,
    })),
  );

// ---------------------------------------------------------------------------
// Chip text (FilterChips.jsx): one chip per correlation group (UX-12).

const valueText = (item, labels = {}) => {
  const value = valueOf(item);
  const values = Array.isArray(value) ? value : [value];
  return values
    .map((entry) => {
      const key = String(entry ?? "");
      const labelled = labels?.[item.column_id]?.[key];
      if (labelled) return labelled;
      if (item.column_id === "skill.evidence") return EVIDENCE[key] || key;
      if (item.column_id === "skill.version") return versionName(entry);
      if (item.column_id === "skill.requested_label") return key;
      return isUuid(key) ? OBSERVE_SKILL_COPY.shortId(key) : key;
    })
    .join(", ");
};

/**
 * Text of one Skill chip. `labels` maps column_id → { value: label } (for
 * example skill ids to names, version ids to "v3"). An item added from a
 * Skills cell carries its value's label in `_meta.valueLabel` (a UI-only key
 * the serializers strip); it wins over ids. `display_name` is the field's
 * name in this codebase, never a value, so it is not used here.
 *
 * Returns `{ text, tooltip, negative, failed }`.
 */
export function skillGroupChipText(items = [], labels = {}) {
  const byColumn = (columnId) => items.find((item) => item.column_id === columnId);
  const label = (item) => (item ? item._meta?.valueLabel || valueText(item, labels) : null);
  const skill = byColumn("skill.id") || byColumn("skill.name");
  const version = byColumn("skill.version_id") || byColumn("skill.version");
  // The chip leads with what was loaded: the skill and version, else the
  // context, else the connected app.
  const subjectItems = skill || version
    ? [skill, version].filter(Boolean)
    : [byColumn("skill.context_id") || byColumn("skill.binding_id")].filter(Boolean);
  const cohort = byColumn("skill.trace_cohort");
  const subject = subjectItems.map(label).join(" ") || (cohort ? OBSERVE_SKILL_COPY.fields["skill.trace_cohort"] : OBSERVE_SKILL_COPY.group);
  const failed = items.some(
    (item) => item.column_id === "skill.load_result" && firstValue(item) === "failed",
  );
  const negative = items.some((item) => NEGATIVE_OPS.has(opOf(item)));
  const rest = items
    .filter(
      (item) =>
        !subjectItems.includes(item) &&
        !["skill.load_result", "skill.root_status", "skill.trace_cohort"].includes(item.column_id),
    )
    .map((item) => {
      if (item.column_id === "skill.requested_label") return askedFor(label(item));
      return `${OBSERVE_SKILL_COPY.fields[item.column_id]}: ${label(item)}`;
    });
  const rootStatus = byColumn("skill.root_status");
  const head = negative
    ? OBSERVE_SKILL_COPY.noRecordedLoadOf(subject)
    : failed
      ? failedLoadChip(subject)
      : subject;
  const parts = [head, ...rest];
  if (rootStatus) {
    const status = firstValue(rootStatus);
    parts.push(OBSERVE_SKILL_COPY.rootStatus[status] || String(status));
  }
  const text = parts.join(" · ");
  return {
    text,
    tooltip: negative ? `${text}. ${OBSERVE_SKILL_COPY.noRecordedLoadTooltip}` : text,
    negative,
    failed,
  };
}

// ---------------------------------------------------------------------------
// Building items from the Skill chip editor and from a list cell.

const textItem = (columnId, op, value) => ({
  column_id: columnId,
  filter_config: { col_type: SKILL_COL_TYPE, filter_type: SKILL_COLUMNS[columnId].type, filter_op: op, filter_value: value },
});

/**
 * Items for one Skill chip (§7.1 table). Version, Evidence and Contents are
 * left out when Load result is Failed, as the chip hides them. Returns [] when
 * nothing identifies a load.
 */
export function buildSkillGroupItems({
  skillId,
  versionIds = [],
  requestedLabel,
  agent,
  service,
  contextId,
  contextVersionId,
  evidence = [],
  loadResult = "loaded",
  digest,
  inScopeVersionIds = [],
  negate = false,
  group = 0,
  grain = "trace",
} = {}) {
  const failed = loadResult === "failed";
  const items = [];
  const list = (columnId, values) => {
    const clean = (values || []).filter((value) => typeof value === "string" && value);
    if (!clean.length) return;
    const { ops } = SKILL_COLUMNS[columnId];
    const op = negate && ops.includes(clean.length > 1 ? "not_in" : "not_equals")
      ? (clean.length > 1 ? "not_in" : "not_equals")
      : clean.length > 1 ? "in" : "equals";
    items.push(textItem(columnId, op, clean.length > 1 ? clean : clean[0]));
  };
  list("skill.id", skillId ? [skillId] : []);
  if (!failed) list("skill.version_id", versionIds);
  if (requestedLabel) items.push(textItem("skill.requested_label", "equals", requestedLabel));
  if (agent !== undefined && agent !== null && agent !== "") items.push(textItem("skill.agent", "equals", agent));
  if (service) items.push(textItem("skill.service", "equals", service));
  if (contextId) items.push(textItem("skill.context_id", "equals", contextId));
  if (contextVersionId) items.push(textItem("skill.context_version_id", "equals", contextVersionId));
  if (!failed && evidence.length) items.push(textItem("skill.evidence", "in", evidence));
  if (!failed && digest) items.push(textItem("skill.digest", "equals", digest));
  if (!failed && grain === "span" && inScopeVersionIds.length) {
    items.push(textItem("skill.in_scope", inScopeVersionIds.length > 1 ? "in" : "equals", inScopeVersionIds.length > 1 ? inScopeVersionIds : inScopeVersionIds[0]));
  }
  if (!items.length) return [];
  if (failed) items.push(textItem("skill.load_result", "equals", "failed"));
  return items
    .map((item) => normalizeSkillItem(group ? { ...item, group } : item, { grain }))
    .filter(Boolean);
}

/**
 * The filter a Skills-cell chip adds (§7.1): that version, as a new group so
 * it is ANDed with any Skill chip already applied rather than merged into it.
 */
export function chipFilterItems(skill, existingFilters = []) {
  if (!skill || !isUuid(skill.version_id)) return [];
  const group = nextSkillGroup(existingFilters);
  const items = buildSkillGroupItems({ skillId: isUuid(skill.skill_id) ? skill.skill_id : undefined, versionIds: [skill.version_id], group });
  const name = skill.name || OBSERVE_SKILL_COPY.unknownSkill;
  return items.map((item) =>
    item.column_id === "skill.version_id"
      ? { ...item, _meta: { valueLabel: versionName(skill.version) } }
      : item.column_id === "skill.id"
        ? { ...item, _meta: { valueLabel: name } }
        : item,
  );
}

/** Link to a skill version page (§7.1 chip menu "Open skill version"). */
export const skillVersionHref = (skillId, version) =>
  isUuid(skillId) && Number.isInteger(version)
    ? `${paths.dashboard.skills.detail(skillId)}?version=${version}`
    : null;

/** The skill's Runtime tab with a version marked ("How v3 is doing", §7.2). */
export const skillRuntimeHref = (skillId, version) =>
  isUuid(skillId)
    ? `${paths.dashboard.skills.detail(skillId)}?tab=runtime${Number.isInteger(version) ? `&version=${version}` : ""}`
    : null;

// ---------------------------------------------------------------------------
// URL parameters from skill surfaces (contract with ST10 utils/traces.js).

const parseJson = (raw) => {
  if (typeof raw !== "string" || !raw || raw.length > 4096) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};
const optionalUuid = (value) => (value === null || value === undefined ? null : isUuid(value) ? value : undefined);
const SIDES = new Set(["before", "after"]);
const METRIC_RE = /^(all|traces|failed|failed_requests|failed_loads|slowest|latency_p95|cost_per_trace|contents_mismatch|eval_failed|eval:[0-9a-zA-Z_-]{1,64})$/;

/**
 * The §6.6 `source` object from the `skillSource` URL parameter, or null.
 * It is untrusted URL input: only known keys of the right shape are kept,
 * and they are only ever rendered as text or used to build in-app paths.
 */
export function parseSkillSource(raw) {
  const value = typeof raw === "string" ? parseJson(raw) : raw;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const skillId = optionalUuid(value.skill_id);
  const versionId = optionalUuid(value.version_id);
  const contextId = optionalUuid(value.context_id);
  const bindingId = optionalUuid(value.binding_id);
  const eventId = optionalUuid(value.event_id);
  if ([skillId, versionId, contextId, bindingId, eventId].includes(undefined)) return null;
  if (!skillId && !contextId) return null;
  const metric = typeof value.metric === "string" && METRIC_RE.test(value.metric) ? value.metric : "all";
  const side = SIDES.has(value.side) ? value.side : null;
  const agentKey = Array.isArray(value.agent_key) && value.agent_key.length === 3 && value.agent_key.every((part) => typeof part === "string" && part.length <= 256)
    ? value.agent_key
    : null;
  const window = typeof value.window === "string" && /^[1-9]\d{0,2}(h|d)$/.test(value.window) ? value.window : null;
  const snapshot = value.snapshot === undefined ? null : parseRuntimeSnapshot(value.snapshot);
  if (value.snapshot !== undefined && !snapshot) return null;
  return { skillId, versionId, contextId, bindingId, eventId, metric, side, agentKey, window, ...(snapshot ? { snapshot } : {}) };
}

/** Only bounded cohort descriptors, never URLs or recursively nested sources. */
function parseRuntimeSnapshot(value) {
  if (!value || typeof value.label !== 'string' || value.label.length > 40) return null;
  if (![value.from_version, value.to_version].every((version) => version === null || (Number.isInteger(version) && version > 0))) return null;
  const links = {};
  let project;
  let selectedMetric;
  for (const side of ['before', 'after']) {
    const entries = Object.entries(value.links?.[side] || {});
    if (entries.length !== 1) return null;
    const [metric, link] = entries[0];
    if (!METRIC_RE.test(metric) || !link || !isUuid(link.project_id) || (project && project !== link.project_id) || (selectedMetric && selectedMetric !== metric)) return null;
    project = link.project_id;
    selectedMetric = metric;
    const from = new Date(link.from).getTime();
    const to = new Date(link.to).getTime();
    if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to || to - from > 7 * 86400000) return null;
    if (!Array.isArray(link.filters) || link.filters.length !== 1 || link.filters[0]?.column_id !== 'skill.trace_cohort') return null;
    const item = normalizeSkillItem(link.filters[0]);
    if (!item || !/^[0-9a-f]{64}$/.test(item.filter_config.filter_value)) return null;
    const source = parseSkillSource({ ...link.source, snapshot: undefined });
    if (!source || source.side !== side || source.metric !== metric) return null;
    const wireSource = { skill_id: source.skillId, version_id: source.versionId, context_id: source.contextId, event_id: source.eventId,
      agent_key: source.agentKey, metric, side, window: source.window };
    const sort = parseLinkSort(link.sort);
    // Keep the exact measured bounds when switching a sorted cohort. A JS
    // Date round trip would silently discard the microseconds of a move.
    const exact = sort?.column_id === 'latency' && sort.direction === 'desc';
    if (exact && ![link.from, link.to].every(v => typeof v === 'string' && v.length <= 40)) return null;
    links[side] = { [metric]: { project_id: project, from: exact ? link.from : new Date(from).toISOString(),
      to: exact ? link.to : new Date(to).toISOString(), filters: [item], ...(sort ? { sort } : {}), source: wireSource } };
  }
  return { label: value.label, from_version: value.from_version, to_version: value.to_version, links };
}

const SORT_DIRECTIONS = new Set(["asc", "desc"]);
/** The `observeLinkSort` URL parameter (`{column_id, direction}`), or null. */
export function parseLinkSort(raw) {
  const value = typeof raw === "string" ? parseJson(raw) : raw;
  if (!value || typeof value !== "object") return null;
  if (typeof value.column_id !== "string" || !/^[a-z_.]{1,64}$/.test(value.column_id)) return null;
  if (!SORT_DIRECTIONS.has(value.direction)) return null;
  return { column_id: value.column_id, direction: value.direction };
}

/** Metric words for the source bar ("failed requests", "p95 latency", "traces"). */
export function sourceMetricPhrase(metric) {
  if (!metric || metric === "all" || metric === "traces") return "traces";
  if (metric === "failed") return metricPhrase("failed_requests");
  if (metric === "slowest") return metricPhrase("latency_p95");
  if (metric === "eval_failed") return "eval failures";
  if (metric === "contents_mismatch") return "loads with other contents";
  if (metric === "failed_loads") return "failed loads";
  return metricPhrase(metric);
}

/** A source-bar metric as the endpoint-3 `links.before` / `links.after` key (`failed` → `failed_requests`). */
export const comparisonMetricKey = (metric) =>
  metric === "failed" ? "failed_requests" : metric === "slowest" ? "latency_p95" : metric;

// ---------------------------------------------------------------------------
// The AI-assisted filter input (§7.1): phrases it should read as a Skill chip.
// Name resolution to ids is the picker's job (endpoint 5); this only parses.

const PHRASES = [
  { re: /^(.+?)\s+failed to load$/i, build: (m) => ({ skillName: m[1], loadResult: "failed" }) },
  { re: /^production version of\s+(.+)$/i, build: (m) => ({ skillName: m[1], requestedLabel: "production" }) },
  { re: /^loaded skill\s+(.+?)(?:\s+v(\d+))?$/i, build: (m) => ({ skillName: m[1], version: m[2] ? Number(m[2]) : undefined }) },
  { re: /^skill\s+(.+?)\s+v(\d+)$/i, build: (m) => ({ skillName: m[1], version: Number(m[2]) }) },
  { re: /^agent\s+(.+)$/i, build: (m) => ({ agent: m[1] }) },
];

/** Parse one phrase ("loaded skill X", "skill X v3", "production version of X", "agent Y", "X failed to load"). */
export function parseSkillPhrase(text) {
  const phrase = typeof text === "string" ? text.trim().replace(/\s+/g, " ") : "";
  if (!phrase || phrase.length > 300) return null;
  for (const { re, build } of PHRASES) {
    const match = re.exec(phrase);
    if (match) {
      const result = build(match);
      return Object.fromEntries(Object.entries(result).filter(([, value]) => value !== undefined));
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Span attributes (§3.1, §3.2): read on the client from the span already in
// the trace detail, with no extra request.

const attr = (attributes, key) => {
  if (!attributes || typeof attributes !== "object") return undefined;
  if (key in attributes) return attributes[key];
  // Some payloads nest dotted keys; walk them as a fallback.
  return key.split(".").reduce((node, part) => (node && typeof node === "object" ? node[part] : undefined), attributes);
};
const attrText = (attributes, key) => {
  const value = attr(attributes, key);
  return typeof value === "string" && value ? value : null;
};
const attrInt = (attributes, key) => {
  const value = attr(attributes, key);
  const number = typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value;
  return Number.isInteger(number) ? number : null;
};
const attrBool = (attributes, key) => {
  const value = attr(attributes, key);
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  return null;
};
const attrList = (attributes, key) => {
  let value = attr(attributes, key);
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  return Array.isArray(value) ? value.filter((item) => typeof item === "string" && item) : [];
};

export const SKILL_LOAD_SPAN = "skill.load";
export const CONTEXT_LOAD_SPAN = "context.load";

/**
 * The identity a `skill.load` span carries (§3.1), or null for other spans.
 * A failed load (status ERROR) has a reported name, a selector and an error
 * type, and never an id, version or digest.
 */
export function skillLoadIdentity(span) {
  if (!span || span.name !== SKILL_LOAD_SPAN) return null;
  const attributes = { ...(span.metadata || {}), ...(span.span_attributes || span.eval_attributes || {}) };
  const failed = span.status === "ERROR";
  return {
    failed,
    skillId: failed ? null : attrText(attributes, "fi.skill.id"),
    name: attrText(attributes, "fi.skill.name"),
    versionId: failed ? null : attrText(attributes, "fi.skill.version_id"),
    version: failed ? null : attrInt(attributes, "fi.skill.version"),
    digest: failed ? null : attrText(attributes, "fi.skill.digest"),
    requestedLabel: attrText(attributes, "fi.skill.requested_label"),
    requestedVersion: attrInt(attributes, "fi.skill.requested_version"),
    via: attrText(attributes, "fi.skill.load.via"),
    evidence: attrText(attributes, "fi.skill.load.evidence"),
    cached: attrBool(attributes, "fi.skill.load.cached"),
    rootFile: attrBool(attributes, "fi.skill.load.root_file"),
    files: attrInt(attributes, "fi.skill.files"),
    bytes: attrInt(attributes, "fi.skill.bytes"),
    errorType: failed ? attrText(attributes, "error.type") : null,
    agent: attrText(attributes, "gen_ai.agent.name"),
    contextName: attrText(attributes, "fi.context.name"),
  };
}

/**
 * "Ran after loading" for any span (§3.2): `fi.skills.refs` ("name@v3") with
 * the matching version ids, in first-load order, plus whether the list was
 * cut at 32. Empty for spans with no skill attributes.
 */
export function ranAfterLoading(span) {
  if (!span) return { refs: [], truncated: false };
  const attributes = { ...(span.metadata || {}), ...(span.span_attributes || span.eval_attributes || {}) };
  const refs = attrList(attributes, "fi.skills.refs");
  const versionIds = attrList(attributes, "fi.skills.version_ids");
  const skillIds = attrList(attributes, "fi.skills.ids");
  return {
    refs: refs.map((ref, index) => {
      const match = /^(.*)@v(\d+)$/.exec(ref);
      return {
        ref,
        name: match ? match[1] : ref,
        version: match ? Number(match[2]) : null,
        versionId: isUuid(versionIds[index]) ? versionIds[index] : null,
        skillId: isUuid(skillIds[index]) ? skillIds[index] : null,
      };
    }),
    truncated: attrBool(attributes, "fi.skills.truncated") === true,
  };
}

/** The chip text for one "Ran after loading" ref ("refund-policy v3"). */
export const ranAfterLoadingChip = ({ name, version, ref }) =>
  version === null || version === undefined ? ref : `${name} ${versionName(version)}`;

/**
 * The trace's skills read (§6.6 endpoint 7), validated. Unknown shapes read
 * as nothing loaded. `coverage.spanIdsBeforeFirstLoad` lists spans that
 * started before the first load, for the span pane's "?" hint.
 */
export function mapTraceSkillsDetail(value) {
  const body = value && typeof value === "object" ? value : {};
  const text = (item) => (typeof item === "string" && item ? item : null);
  const ids = (list) => (Array.isArray(list) ? list.filter((item) => typeof item === "string" && item) : []);
  const skills = (Array.isArray(body.skills) ? body.skills : [])
    .filter((row) => row && typeof row === "object")
    .map((row) => ({
      skillId: isUuid(row.skill_id) ? row.skill_id : null,
      name: text(row.name),
      versionId: isUuid(row.version_id) ? row.version_id : null,
      version: Number.isInteger(row.version) ? row.version : null,
      requestedLabel: text(row.requested_label),
      requestedVersion: Number.isInteger(row.requested_version) ? row.requested_version : null,
      agent: text(row.agent),
      agentKey:
        Array.isArray(row.agent_key) && row.agent_key.length === 3 && row.agent_key.every((part) => typeof part === "string")
          ? row.agent_key
          : null,
      evidence: text(row.evidence),
      loads: Number.isInteger(row.loads) ? row.loads : null,
      digestStatus: ["verified", "mismatch"].includes(row.digest_status) ? row.digest_status : null,
      spanIds: ids(row.span_ids),
    }));
  const failed = (Array.isArray(body.failed) ? body.failed : [])
    .filter((row) => row && typeof row.skill_name === "string")
    .map((row) => ({ skillName: row.skill_name, errorType: text(row.error_type), spanId: text(row.span_id) }));
  const contextBody = Array.isArray(body.contexts) ? body.contexts[0] : body.context;
  const context = contextBody && typeof contextBody === "object" && (isUuid(contextBody.context_id) || isUuid(contextBody.id))
    ? {
        id: contextBody.context_id ?? contextBody.id,
        name: text(contextBody.name),
        version: Number.isInteger(contextBody.version) ? contextBody.version : null,
        bindingId: isUuid(contextBody.binding_id) ? contextBody.binding_id : null,
        bindingName: text(contextBody.binding_name),
      }
    : null;
  const linkedNotLoaded = (Array.isArray(body.linked_not_loaded) ? body.linked_not_loaded : [])
    .map((row) => (typeof row === "string" ? row : text(row?.name)))
    .filter(Boolean);
  const versionDifferences = (Array.isArray(body.linked_version_differences) ? body.linked_version_differences : [])
    .filter(row => text(row?.skill_name) && text(row.context_name) && Number.isInteger(row.linked_version) && Number.isInteger(row.context_version)
      && Array.isArray(row.loaded_versions) && row.loaded_versions.length && row.loaded_versions.every(Number.isInteger))
    .map(row => ({ name: row.skill_name, context: row.context_name, linked: row.linked_version, contextVersion: row.context_version, loaded: row.loaded_versions }));
  const coverage = body.coverage && typeof body.coverage === "object" ? body.coverage : {};
  return {
    skills,
    failed,
    context,
    // Older servers returned only names and could mark the wrong version as absent.
    linkedNotLoaded: linkedNotLoaded.filter(name => !skills.some(row => row.skillId && row.versionId && row.name === name && row.digestStatus === 'verified')),
    versionDifferences,
    coverage: {
      spanIdsBeforeFirstLoad: ids(coverage.span_ids_before_first_load ?? coverage.before_first_load_span_ids),
      firstSkillName: skills.find(row => row.skillId && row.versionId && row.digestStatus === 'verified')?.name ?? null,
    },
    empty: !skills.length && !failed.length && !context,
  };
}

/** "Skills: refund-policy v3, tone v1 · 1 failed to load" (§7.2, UX-13). */
export const traceSkillsChipText = (detail) => {
  if (!detail || detail.empty) return null;
  const names = detail.skills.map((row) => `${row.name || OBSERVE_SKILL_COPY.unknownSkill} ${versionName(row.version)}`);
  if (!names.length && detail.failed.length) return OBSERVE_SKILL_COPY.failedOnlyChip(detail.failed.length);
  if (!names.length && detail.context) {
    return `${OBSERVE_SKILL_COPY.context}: ${detail.context.name || OBSERVE_SKILL_COPY.shortId(detail.context.id)}`;
  }
  return OBSERVE_SKILL_COPY.traceChip({ names, failed: detail.failed.length });
};
