// Words for the public dataset page (W4-6). Pure over the resolve payload
// (GET /tracer/shared/{token}/ -> {resource_type: "dataset", data}).
import {
  asObject,
  detectValueKind,
  rawText,
} from "src/sections/common/ValueViewer/valueKind";

export const shortDate = (iso) =>
  iso
    ? new Date(iso)
        .toLocaleDateString("en-GB", {
          weekday: "short",
          day: "numeric",
          month: "short",
        })
        .replace(",", "")
    : "";

/** "Support agent - golden set · v6 · Shared by Maya Chen · Read-only · Expires Wed 29 Oct". */
export function headerParts(data) {
  return [
    data?.name,
    data?.version?.number != null ? `v${data.version.number}` : null,
    data?.shared_by?.name ? `Shared by ${data.shared_by.name}` : null,
    "Read-only",
    data?.expires_at ? `Expires ${shortDate(data.expires_at)}` : null,
  ].filter(Boolean);
}

/** The tabs the link includes, in order. */
export function tabsFor(data) {
  return [
    { key: "rows", label: "Rows" },
    data?.experiments ? { key: "experiments", label: "Experiments" } : null,
    data?.feedback ? { key: "feedback", label: "Feedback" } : null,
  ].filter(Boolean);
}

/** A cell as text (objects as JSON). */
export function cellText(v) {
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const EMPTY = "—";
const argText = (x) =>
  typeof x === "string" ? JSON.stringify(x) : JSON.stringify(x ?? null);
const isToolCall = (x) =>
  x && typeof x === "object" && typeof x.name === "string" && "args" in x;
const callText = (c) =>
  `${c.name}(${Object.entries(c.args ?? {})
    .map(([k, x]) => `${k}: ${argText(x)}`)
    .join(", ")})`;

/**
 * A cell in the table, the way the grid reads it: "—" when empty, a chat as
 * its turns, tool calls as name(arg: value), other JSON compact.
 */
export function cellSummary(v, dataType) {
  const kind = detectValueKind(v, dataType);
  if (kind === "empty") return EMPTY;
  const o = kind === "json_string" || kind === "chat" ? asObject(v) : v;
  if (kind === "chat")
    return o.map((m) => `${m.role}: ${cellText(m.content)}`).join(" · ");
  if (Array.isArray(o) && o.every(isToolCall))
    return o.map(callText).join(", ");
  if (o && typeof o === "object") return JSON.stringify(o);
  return String(v);
}

/** A cell in the opened row: chats one turn a line, JSON indented. */
export function cellDetail(v, dataType) {
  const kind = detectValueKind(v, dataType);
  if (kind === "empty") return EMPTY;
  const o = kind === "json_string" || kind === "chat" ? asObject(v) : v;
  if (kind === "chat")
    return o.map((m) => `${m.role}: ${cellText(m.content)}`).join("\n");
  return rawText(o);
}

export function pageInfo(rows) {
  if (!rows) return { pages: 1, from: 0, to: 0, total: 0 };
  const pages = Math.max(1, Math.ceil(rows.total / rows.page_size));
  const from = rows.total ? (rows.page - 1) * rows.page_size + 1 : 0;
  const to = Math.min(rows.total, rows.page * rows.page_size);
  return { pages, from, to, total: rows.total };
}

export const rangeLine = (rows) => {
  const p = pageInfo(rows);
  return p.total ? `${p.from}–${p.to} of ${p.total} rows` : "No rows";
};

/** "Correctness 82%" or "Groundedness 0.71". */
export function metricText(m) {
  if (m?.pass_rate != null)
    return `${m.name} ${Math.round(m.pass_rate * 100)}%`;
  if (m?.avg != null) return `${m.name} ${Number(m.avg).toFixed(2)}`;
  return `${m?.name ?? ""} —`;
}

export const scoredLine = (run) => `${run.scored} of ${run.total} scored`;
export const sharedTitle = (data) => `Shared dataset — ${data?.name ?? ""}`;

/** A feedback entry's label: "Quality:" ("Label:" when the label is gone). */
// A label that already ends in punctuation ("Correct?") gets no colon.
export const feedbackLabel = (f) => {
  const label = f?.label ?? "Label";
  return /[?:!.]$/.test(label) ? label : `${label}:`;
};

/** What follows the value: the note, then who gave it. */
export const feedbackMeta = (f) =>
  [f?.notes ? `“${f.notes}”` : null, f?.by ?? null].filter(Boolean).join(" · ");
