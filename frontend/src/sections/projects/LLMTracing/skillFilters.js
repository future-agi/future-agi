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
// The §6.3 column table, as ST6 validates it.

const TEXT = "text";
const NUMBER = "number";

/** column_id → { type, ops, failedGroup } (failedGroup: valid in a load_result=failed group). */
export const SKILL_COLUMNS = Object.freeze({
  "skill.id": { type: TEXT, ops: ["equals", "in", "not_in", "not_equals"], failedGroup: true },
  "skill.version_id": { type: TEXT, ops: ["equals", "in", "not_in", "not_equals"], failedGroup: false },
  "skill.name": { type: TEXT, ops: ["equals", "in", "contains"], failedGroup: true },
  "skill.version": { type: NUMBER, ops: ["equals", "in"], failedGroup: false },
  "skill.requested_label": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.digest": { type: TEXT, ops: ["equals"], failedGroup: false },
  "skill.evidence": { type: TEXT, ops: ["in"], failedGroup: false },
  "skill.agent": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.service": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.context_id": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.context_version_id": { type: TEXT, ops: ["equals", "in"], failedGroup: true },
  "skill.binding_id": { type: TEXT, ops: ["equals", "in"], failedGroup: false },
  "skill.in_scope": { type: TEXT, ops: ["equals", "in"], failedGroup: false, spanOnly: true },
  "skill.load_result": { type: TEXT, ops: ["equals"], failedGroup: true },
  "skill.root_status": { type: TEXT, ops: ["equals"], failedGroup: true },
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
const MAX_VALUES = 64;
const MAX_VALUE_LENGTH = 512;
const MAX_GROUP = 2147483647;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (value) => typeof value === "string" && UUID_RE.test(value);

const configOf = (item) => item?.filter_config || item?.filterConfig || {};
const colTypeOf = (item) =>
  String(configOf(item).col_type || configOf(item).colType || "").toUpperCase();

/** True for a §6.3 `SKILL` item (any casing of the stored key aliases). */
export const isSkillFilterItem = (item) =>
  Boolean(item) && typeof item === "object" && colTypeOf(item) === SKILL_COL_TYPE;

/** The correlation group of a `SKILL` item; absent means 0 (§6.3). */
export const skillGroupOf = (item) =>
  Number.isInteger(item?.group) && item.group >= 0 ? item.group : 0;

const cleanText = (value) => {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text && text.length <= MAX_VALUE_LENGTH ? text : null;
};

function normalizeValue(columnId, type, op, raw) {
  const list = LIST_OPS.has(op);
  const values = Array.isArray(raw) ? raw : [raw];
  if (!values.length || values.length > MAX_VALUES) return undefined;
  const normalized = values.map((value) => {
    if (type === NUMBER) {
      const number = typeof value === "number" ? value : Number(value);
      return Number.isInteger(number) && number > 0 ? number : null;
    }
    const text = cleanText(value);
    if (text === null) return null;
    const allowed = ENUM_VALUES[columnId];
    return allowed && !allowed.includes(text) ? null : text;
  });
  if (normalized.some((value) => value === null)) return undefined;
  if (list) return normalized;
  return normalized.length === 1 ? normalized[0] : undefined;
}

function normalizeOp(columnId, op, value) {
  const { ops } = SKILL_COLUMNS[columnId];
  const multi = Array.isArray(value) && value.length > 1;
  let next = op || "equals";
  // The panel stores single equality as a one-item list and multi-value
  // equality as `in`. Map only to an operator the column accepts.
  if (next === "equals" && (multi || !ops.includes("equals")) && ops.includes("in")) next = "in";
  if (next === "not_equals" && (multi || !ops.includes("not_equals")) && ops.includes("not_in")) next = "not_in";
  if (next === "in" && !multi && !ops.includes("in")) next = "equals";
  if (next === "not_in" && !multi && !ops.includes("not_in")) next = "not_equals";
  return ops.includes(next) ? next : null;
}

/**
 * One `SKILL` item in the canonical API shape, or null when the server would
 * reject it. `grain` is "trace" or "span" ("Ran after loading" is span grain
 * only). The `group` key is kept only when it is not the default 0, and
 * `display_name` (an API item key) is kept for chip text.
 */
export function normalizeSkillItem(item, { grain = "trace" } = {}) {
  if (!isSkillFilterItem(item)) return null;
  const columnId = item.column_id ?? item.columnId;
  const column = SKILL_COLUMNS[columnId];
  if (!column) return null;
  if (column.spanOnly && grain !== "span") return null;
  if (item.group !== undefined && (!Number.isInteger(item.group) || item.group < 0 || item.group > MAX_GROUP)) return null;
  const config = configOf(item);
  const rawValue = config.filter_value ?? config.filterValue;
  const op = normalizeOp(columnId, config.filter_op ?? config.filterOp, rawValue);
  if (!op) return null;
  const value = normalizeValue(columnId, column.type, op, rawValue);
  if (value === undefined) return null;
  const group = skillGroupOf(item);
  const displayName = item.display_name ?? item.displayName;
  return {
    column_id: columnId,
    ...(typeof displayName === "string" && displayName ? { display_name: displayName } : {}),
    ...(group ? { group } : {}),
    filter_config: {
      col_type: SKILL_COL_TYPE,
      filter_type: column.type,
      filter_op: op,
      filter_value: value,
    },
  };
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
 * The §6.3 group rules ST6 enforces, so the UI never sends a 400:
 *   - `skill.root_status` needs at least one other `SKILL` item;
 *   - `skill.version` is valid only beside `skill.id` or `skill.name`;
 *   - a `load_result = failed` group rejects version, digest, evidence,
 *     binding and "Ran after loading" items, because failed rows have none.
 * Returns `{ ok, reason }`.
 */
export function validateSkillGroups(filters = []) {
  const items = (filters || []).filter(isSkillFilterItem);
  if (!items.length) return { ok: true, reason: null };
  if (items.every((item) => item.column_id === "skill.root_status")) {
    return { ok: false, reason: "root_status_alone" };
  }
  for (const { items: members } of skillGroups(items)) {
    const ids = new Set(members.map((item) => item.column_id));
    if (ids.has("skill.version") && !ids.has("skill.id") && !ids.has("skill.name")) {
      return { ok: false, reason: "version_without_skill" };
    }
    const failed = members.some(
      (item) => item.column_id === "skill.load_result" && firstValue(item) === "failed",
    );
    if (failed && members.some((item) => !SKILL_COLUMNS[item.column_id]?.failedGroup)) {
      return { ok: false, reason: "failed_group_column" };
    }
    if (members.filter((item) => item.column_id === "skill.load_result").length > 1) {
      return { ok: false, reason: "duplicate_load_result" };
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
      const normalized = normalizeSkillItem(filter, { grain });
      if (!normalized) {
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
 * Persist filters. Lists with only default-group `SKILL` items keep the
 * existing serializer untouched (it accepts `col_type: SKILL`). Lists with a
 * grouped item serialize the other items through `serializeOthers` and the
 * `SKILL` items here, keeping `group`, because the shared contract does not
 * know that key yet (see the ST9 `remaining` list).
 */
export function serializeFiltersWithSkills(filters, serializeOthers) {
  const list = filters || [];
  if (!hasGroupedSkillItems(list)) return serializeOthers(list);
  const others = serializeOthers(list.filter((item) => !isSkillFilterItem(item)));
  const skills = list
    .filter(isSkillFilterItem)
    .map((item) => normalizeSkillItem(item, { grain: "span" }))
    .filter(Boolean);
  return [...others, ...skills];
}

const itemIdentity = (item) =>
  JSON.stringify([item?.column_id, opOf(item), valueOf(item)]);
const looseIdentity = (item) => {
  const value = valueOf(item);
  const values = (Array.isArray(value) ? value : [value]).map(String).sort();
  return JSON.stringify([item?.column_id, values]);
};

/**
 * Keep `SKILL` items intact through the filter panel's row round trip. The
 * panel rebuilds every item from a row, which loses `group` and may turn
 * `equals` into `in`. An item the person did not change is restored exactly
 * from `previous`; a changed one is normalized to an operator its column
 * accepts. Items the server would reject are dropped from `next`.
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
    const normalized = normalizeSkillItem(item, { grain });
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
 * example skill ids to names, version ids to "v3"). Items may carry their own
 * `display_name` from the server link; it wins over ids.
 *
 * Returns `{ text, tooltip, negative, failed }`.
 */
export function skillGroupChipText(items = [], labels = {}) {
  const byColumn = (columnId) => items.find((item) => item.column_id === columnId);
  const label = (item) => (item ? item.display_name || valueText(item, labels) : null);
  const skill = byColumn("skill.id") || byColumn("skill.name");
  const version = byColumn("skill.version_id") || byColumn("skill.version");
  // The chip leads with what was loaded: the skill and version, else the
  // context, else the connected app.
  const subjectItems = skill || version
    ? [skill, version].filter(Boolean)
    : [byColumn("skill.context_id") || byColumn("skill.binding_id")].filter(Boolean);
  const subject = subjectItems.map(label).join(" ") || OBSERVE_SKILL_COPY.group;
  const failed = items.some(
    (item) => item.column_id === "skill.load_result" && firstValue(item) === "failed",
  );
  const negative = items.some((item) => NEGATIVE_OPS.has(opOf(item)));
  const rest = items
    .filter(
      (item) =>
        !subjectItems.includes(item) &&
        !["skill.load_result", "skill.root_status"].includes(item.column_id),
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
      ? { ...item, display_name: `${name} ${versionName(skill.version)}` }
      : item.column_id === "skill.id"
        ? { ...item, display_name: name }
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
const METRIC_RE = /^(all|traces|failed|failed_requests|slowest|latency_p95|cost_per_trace|eval_failed|eval:[0-9a-zA-Z_-]{1,64})$/;

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
  return { skillId, versionId, contextId, bindingId, eventId, metric, side, agentKey, window };
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
