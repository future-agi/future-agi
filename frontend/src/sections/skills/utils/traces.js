import { formatDate } from "src/utils/report-utils";
import { paths } from "src/routes/paths";
import { isSkillTracesEnabled } from "src/sections/skills/runtime/skillTracesFlag";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY_MS = 24 * 60 * 60 * 1000;

// Observe reads the filter rows from this parameter (LLMTracing/common.js
// OBSERVE_LINK_FILTER_PARAM) and shows them as visible, removable filters.
const OBSERVE_LINK_FILTER_PARAM = "observeLinkFilter";
/**
 * Contract for lane ST9 (Observe): the §6.6 `source` object, as JSON, for the
 * source bar (UX-02). Presentational only; it never changes the filter.
 */
export const SKILL_SOURCE_PARAM = "skillSource";
/**
 * Contract for lane ST9 (Observe): the list sort a link asks for, as JSON
 * `{column_id, direction}`. The `slowest` link uses it to open the cohort
 * sorted by root latency, slowest first ([V14]).
 */
export const OBSERVE_LINK_SORT_PARAM = "observeLinkSort";
export const SKILL_LINK_KINDS = Object.freeze(["all", "failed", "slowest", "eval_failed"]);

function windowDates(windowStart, windowEnd) {
  const hasDates = windowStart !== undefined || windowEnd !== undefined;
  if (hasDates && (!windowStart || !windowEnd)) return null;
  const end = hasDates ? new Date(windowEnd) : new Date();
  const start = hasDates ? new Date(windowStart) : new Date(end.getTime() - 7 * DAY_MS);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end) return null;
  return { start, end };
}

function observeUrl(projectId, filters, dates, extra = {}) {
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  params.set(OBSERVE_LINK_FILTER_PARAM, JSON.stringify(filters));
  // Match the Observe date picker's local-time serialization, including seconds.
  params.set(
    "primaryTraceDateFilter",
    JSON.stringify({ dateFilter: [formatDate(dates.start), formatDate(dates.end)], dateOption: "Custom" }),
  );
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== null) params.set(key, JSON.stringify(value));
  });
  return `/dashboard/observe/${encodeURIComponent(projectId)}/llm-tracing?${params.toString()}`;
}

const legacyItem = (versionId) => ({
  column_id: "fi.skill.version_id",
  filter_config: { col_type: "SPAN_ATTRIBUTE", filter_type: "text", filter_op: "equals", filter_value: versionId },
});
const skillItem = (columnId, value) => ({
  column_id: columnId,
  filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: value },
});

// A §6.6 link is built by the server from §6.3 items only, so the list it
// opens is exactly the cohort the cell counted (UX-02, "landing equals the
// cell"). The client never rewrites those items; it only checks their shape.
const validItems = (items) =>
  Array.isArray(items) &&
  items.length > 0 &&
  items.every(
    (item) =>
      item &&
      typeof item.column_id === "string" &&
      item.column_id &&
      item.filter_config &&
      typeof item.filter_config === "object" &&
      typeof item.filter_config.col_type === "string",
  );
const linkItems = (link) => link?.filters ?? link?.items;
const validSort = (sort) =>
  sort && typeof sort.column_id === "string" && ["asc", "desc"].includes(sort.direction) ? sort : null;

/**
 * The link from a Skills surface into Observe (SKILL-TRACES §7.8, UX-02).
 *
 * Two call shapes:
 *
 *   buildSkillTraceLink({ projectId, versionId, windowStart?, windowEnd? })
 *     The fetch-adoption link (governance/AdoptionPanel.jsx) and older callers:
 *     every trace that loaded one immutable version.
 *
 *   buildSkillTraceLink({ link, kind, projectId?, windowStart?, windowEnd?, source? })
 *     A §6.6 link object `{project_id, from, to, filters, sort?, source?}` for
 *     one number (`kind` is all, failed, slowest or eval_failed). projectId
 *     overrides `link.project_id` for a version row that covers several
 *     projects (the project menu, UX-03); the window defaults to the link's.
 *
 * With `skillTraces` on, the link carries the `SKILL` items (§6.3), the sort
 * for `slowest` and the source bar parameter. With it off, only the `all`
 * link exists and it carries the original scalar filter: the one
 * `SPAN_ATTRIBUTE fi.skill.version_id equals` item, which matches the
 * runtime-emitted `skill.load` span (SPEC-v1.1 addendum; §3.1). Any other
 * kind returns null.
 *
 * The caller must take projectId from the authorized project picker or from a
 * server response scoped to readable projects. This link does not confer
 * access. Navigation stays in the same tab (`<Link component={RouterLink}>` or
 * `navigate`), so Back returns to the card; never `window.open`.
 */
export function buildSkillTraceLink({
  projectId,
  versionId,
  link,
  kind = "all",
  windowStart,
  windowEnd,
  source,
  skillTraces = isSkillTracesEnabled(),
}) {
  const project = projectId ?? link?.project_id;
  if (typeof project !== "string" || !project) return null;
  const start = windowStart !== undefined || windowEnd !== undefined ? windowStart : link?.from;
  const end = windowStart !== undefined || windowEnd !== undefined ? windowEnd : link?.to;
  const dates = windowDates(start, end);
  if (!dates) return null;
  const linkSource = source ?? link?.source ?? null;

  if (!link) {
    if (!UUID_PATTERN.test(versionId)) return null;
    if (!skillTraces) return observeUrl(project, [legacyItem(versionId)], dates);
    return observeUrl(project, [skillItem("skill.version_id", versionId)], dates, { [SKILL_SOURCE_PARAM]: linkSource });
  }
  if (!SKILL_LINK_KINDS.includes(kind)) return null;
  if (!skillTraces) {
    const legacyVersion = versionId ?? linkSource?.version_id;
    if (kind !== "all" || !UUID_PATTERN.test(legacyVersion)) return null;
    return observeUrl(project, [legacyItem(legacyVersion)], dates);
  }
  const items = linkItems(link);
  if (!validItems(items)) return null;
  return observeUrl(project, items, dates, {
    [OBSERVE_LINK_SORT_PARAM]: validSort(link.sort),
    [SKILL_SOURCE_PARAM]: linkSource,
  });
}

/**
 * The Connected apps "Runtime traces" ⋯ item (§7.4, lane UXF4 commit 2):
 * traces whose skill loads came through one context binding. Null when
 * `skillTraces` is off or an id is not a UUID.
 */
export function buildContextBindingTraceLink({
  projectId,
  contextId,
  bindingId,
  windowStart,
  windowEnd,
  skillTraces = isSkillTracesEnabled(),
}) {
  if (!skillTraces || typeof projectId !== "string" || !projectId) return null;
  if (!UUID_PATTERN.test(contextId) || !UUID_PATTERN.test(bindingId)) return null;
  const dates = windowDates(windowStart, windowEnd);
  if (!dates) return null;
  return observeUrl(projectId, [skillItem("skill.context_id", contextId), skillItem("skill.binding_id", bindingId)], dates, {
    [SKILL_SOURCE_PARAM]: { context_id: contextId, binding_id: bindingId, metric: "all" },
  });
}

/**
 * The stable URL of a Runtime tab, optionally opened on one move card
 * (`?tab=runtime&move=<event_id>&window=24h`, UX-05). `kind` is "skill" or
 * "agent_context".
 */
export function runtimeTabUrl({ kind = "skill", id, eventId, window: moveWindow, absolute = false }) {
  if (typeof id !== "string" || !id) return null;
  const base = kind === "agent_context" ? paths.dashboard.skills.contextDetail(id) : paths.dashboard.skills.detail(id);
  const params = new URLSearchParams({ tab: "runtime" });
  if (eventId) {
    params.set("move", eventId);
    params.set("window", moveWindow || "24h");
  }
  const relative = `${base}?${params.toString()}`;
  if (!absolute || typeof window === "undefined") return relative;
  return new URL(relative, window.location.origin).href;
}
