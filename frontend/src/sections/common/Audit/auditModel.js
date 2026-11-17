// Audit words (W4-7). Pure: the drawer's day groups, sentences, before /
// after rows and the period filter. Entries come from GET /model-hub/audit/
// ({action_label, summary, diff} are the server's words).

export const PERIODS = Object.freeze([
  { key: "7", label: "Last 7 days", days: 7 },
  { key: "30", label: "Last 30 days", days: 30 },
  { key: "90", label: "Last 90 days", days: 90 },
  { key: "all", label: "All time", days: null },
]);
export const DEFAULT_PERIOD = "30";
const DAY_MS = 24 * 60 * 60 * 1000;

/** The `since` ISO time for a period key (null for all time). */
export function sinceFor(periodKey, now = Date.now()) {
  const p = PERIODS.find((x) => x.key === periodKey);
  return p?.days ? new Date(now - p.days * DAY_MS).toISOString() : null;
}

const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

/** "Today", "Yesterday", or "Wed 22 Oct" (the viewer's time zone). */
export function dayLabel(iso, now = Date.now()) {
  const d = new Date(iso);
  const today = new Date(now);
  const yesterday = new Date(now - DAY_MS);
  if (dayKey(d) === dayKey(today)) return "Today";
  if (dayKey(d) === dayKey(yesterday)) return "Yesterday";
  return d
    .toLocaleDateString("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
    })
    .replace(",", "");
}

/** Entries (newest first) in day groups, keeping their order. */
export function groupByDay(entries = [], now = Date.now()) {
  const groups = [];
  entries.forEach((e) => {
    const label = dayLabel(e.at, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.entries.push(e);
    else groups.push({ label, entries: [e] });
  });
  return groups;
}

/** "Maya Chen changed access". */
export const sentenceOf = (e) =>
  `${e.actor?.name ?? "Someone"} ${e.action_label ?? e.action}`;

/** Before / after rows: the server's `diff`, else derived from `changes`. */
export function diffRows(e) {
  if (Array.isArray(e?.diff)) return e.diff;
  const b = e?.changes?.before ?? {};
  const a = e?.changes?.after ?? {};
  const fmt = (v) =>
    v == null
      ? "—"
      : Array.isArray(v)
        ? v.join(", ")
        : typeof v === "object"
          ? JSON.stringify(v)
          : String(v);
  return [...new Set([...Object.keys(b), ...Object.keys(a)])].map((k) => ({
    field: k.replace(/_/g, " "),
    before: fmt(b[k]),
    after: fmt(a[k]),
  }));
}

/** The action filter's choices: the server's groups that have entries. */
export const actionChoices = (groups = []) =>
  groups
    .filter((g) => g.count > 0)
    .map((g) => ({
      key: g.key,
      label: `${g.label} (${g.count})`,
      actions: g.actions,
    }));

/** The query for the drawer's filters. */
export function auditParams({
  scope,
  groupKey,
  groups = [],
  actor,
  period,
  now = Date.now(),
}) {
  const group = groups.find((g) => g.key === groupKey);
  const since = sinceFor(period, now);
  return {
    scope,
    ...(group ? { action: group.actions.join(",") } : {}),
    ...(actor ? { actor } : {}),
    ...(since ? { since } : {}),
  };
}

/** Settings › Audit log's scope: the workspace (W4 §9, M6). */
export const workspaceAuditScope = (workspaceId) =>
  workspaceId ? `workspace:${workspaceId}` : null;

/** Export CSV's columns (W4-7; W4 §9 W3 B integration, M6), through writers.js. */
export const AUDIT_CSV_COLUMNS = Object.freeze([
  { key: "at", label: "When" },
  { key: "person", label: "Person" },
  { key: "action", label: "Action" },
  { key: "scope", label: "Where" },
  { key: "target", label: "What" },
  { key: "summary", label: "Summary" },
  { key: "before", label: "Before" },
  { key: "after", label: "After" },
  { key: "note", label: "Note" },
]);

/** One CSV row per entry (the read never carries link tokens). */
export const auditCsvRows = (entries = []) =>
  entries.map((e) => ({
    at: e.at,
    person: e.actor?.name ?? "",
    action: e.action_label ?? e.action,
    scope: e.scope?.name ?? e.scope?.id ?? "",
    target: e.target?.label ?? e.target?.id ?? "",
    summary: e.summary ?? "",
    before: e.changes?.before ?? null,
    after: e.changes?.after ?? null,
    note: e.note ?? "",
  }));

/** `audit-log-support-agent-golden-set.csv`. */
export const auditCsvName = (title) =>
  `audit-log${
    title
      ? `-${String(title)
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")}`
      : ""
  }.csv`;
