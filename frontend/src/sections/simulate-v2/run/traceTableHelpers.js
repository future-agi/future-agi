/*
  Plain helpers behind the trace table — the per-call metrics (CSAT, latency),
  the column and grouping definitions, and the use-case label. Kept out of
  TraceTable.jsx so that file only exports components (Fast Refresh), and so
  the analytics, compare and widget code can read them without the table.
*/
import { goalLabel } from "../_mock/scenarios";

const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1) h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
  return h;
};
/*
  CSAT — the simulated caller's satisfaction with the call, 0–10, the same
  scale the product uses. It follows how the call went, how the persona
  tends to rate (an impatient or angry caller is harder to please), and a
  little per-call spread; it is not derived from any eval, so it can
  disagree with them, which is the point of having it. A call that wasn't
  measured has no rating.
*/
const CSAT_BASE = { passed: 8, flaky: 6, failed: 4, error: 3 };
const HARD_TO_PLEASE = /impatient|angry|frustrat|skeptic|sceptic|hurry|rush/i;
export const csatOf = (t) => {
  if (!t || t.status === "unmeasured") return null;
  const base = CSAT_BASE[t.status] ?? 6;
  const mood = HARD_TO_PLEASE.test([...(t.persona?.traits || []), t.persona?.name].join(" ")) ? -1 : 0;
  const spread = (hash(`${t.id}:csat`) % 5) - 2;
  return Math.max(0, Math.min(10, base + mood + spread));
};
/* A run that records response times (new runs) carries its own — null when
   the agent's side didn't report one, which reads as unverified, not as fast. */
export const latencyOf = (t) => ("responseMs" in (t || {}) ? t.responseMs : 280 + (hash(t.id) % 320));

/*
  Column definitions live outside the picker so both the picker and the
  table body read them from one source of truth. `defaultOn` decides
  what a user sees on first load — the important columns for reading a
  run are the run's own outputs (call details), the system metrics
  (CSAT, turns, latency, tokens) and how the evals scored. Scenario
  details (persona, scenario title, ideal outcome, conversation
  branch) restate the setup, useful for debugging but noisy for
  scanning, so they start hidden and turn on via the picker. `group`
  drives the section headers in the picker menu.
*/
export const TRACE_COLUMNS = [
  /* Run details always sits first (right after the checkbox) so the
     row's identity — its status/duration/id — reads before any of the
     optional scenario-detail columns. */
  { key: "callDetails", label: "Run details",          defaultOn: true,  width: 150, group: "Run details" },
  { key: "persona",     label: "Persona",              defaultOn: true,  width: 210, group: "Scenario details" },
  { key: "scenario",    label: "Scenario",             defaultOn: false, width: 300, group: "Scenario details" },
  { key: "expected",    label: "Ideal outcome",        defaultOn: false, width: 300, group: "Scenario details" },
  { key: "branch",      label: "Conversation branch", defaultOn: false, width: 260, group: "Scenario details" },
  { key: "csat",        label: "CSAT",                 defaultOn: true,  width: 84,  group: "System metrics" },
  { key: "turns",       label: "Turns",                defaultOn: true,  width: 92,  group: "System metrics" },
  { key: "latency",     label: "Latency",              defaultOn: true,  width: 96,  group: "System metrics" },
  { key: "tokens",      label: "Tokens",               defaultOn: true,  width: 96,  group: "System metrics" },
];

/* Each eval is its own column. Stored as an exclusion marker on the
   visibility Set (`eval:<id>:hidden`), so a new eval added later defaults
   to visible without touching the stored state. */
export const HIDDEN_EVAL = (id) => `eval:${id}:hidden`;
export const isEvalHidden = (columns, evalId) => columns?.has(HIDDEN_EVAL(evalId));
export const visibleEvals = (columns, evals) =>
  (evals || []).filter((e) => !isEvalHidden(columns, e.id));

export const defaultTraceColumns = () =>
  new Set(TRACE_COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));

export const GROUPINGS = [
  { id: "useCase", label: "Goal",              icon: "solar:target-linear" },
  /* Every sub-goal a call has — a list axis, so a call with several
     sub-goals sits in each of their groups and the groups overlap rather
     than split the run. Said in the menu and on each group's count. */
  {
    id: "allSubGoals", label: "Sub-goal", icon: "solar:checklist-minimalistic-linear",
    overlaps: true, hint: "A scenario with several sub-goals appears under each of them.",
  },
  /* Member — for an agent made of several agents (a squad, a flow, an app
     with handoffs): the member a conversation ended with. Offered only when
     the run's agent has more than one member. */
  {
    id: "member", label: "Member", icon: "solar:users-group-rounded-linear",
    multiAgent: true, hint: "Each conversation counts for the member it ended with.",
  },
  { id: "persona", label: "Persona",           icon: "solar:user-rounded-linear" },
  { id: "rule",    label: "Rule broken",       icon: "solar:shield-cross-linear" },
  { id: "subGoal", label: "Failure sub-goal", icon: "solar:map-linear" },
  { id: "pattern", label: "Failure pattern",   icon: "solar:danger-triangle-linear" },
  { id: "status",  label: "Status",            icon: "solar:check-circle-linear" },
  /* Verdict · cause — fail · agent, required evaluation, the inconclusive
     kinds. Rows carry it as `verdictCause`. */
  { id: "cause",   label: "Verdict cause",     icon: "solar:tag-linear" },
  /* Persona-attribute buckets — same-accent / same-age-band callers
     cluster together, so a reader can spot regional or generational
     regressions ("older callers keep dropping mid-flow"). */
  { id: "accent",  label: "Accent",             icon: "solar:global-linear" },
  { id: "age",     label: "Age",                icon: "solar:calendar-linear" },
  /* Attack — adversarial pressure the scenario applies (rule probe,
     data trap, prompt-injection, edge branch). Groups the run by how
     it was trying to break the agent. */
  { id: "attack",  label: "Attack",             icon: "solar:bug-linear" },
  /* Task — bucket by scenario name. Trials of the same task cluster,
     so flakiness across trials becomes obvious. */
  { id: "task",    label: "Task",               icon: "solar:document-text-linear" },
  /* Escape hatch — flat table, no grouping at all. Kept last so the
     dropdown reads "…then no grouping" as the exit. */
  { id: "none",    label: "No grouping",        icon: "solar:list-linear" },
];

/*
  Use-case label for a task, matching the ScenariosStep grouping so
  a run's traces slice into the same buckets the env creation screen
  showed. Prefers the scenario's `useCase` sentence (populated by the
  scenario mock — "Look up an existing booking by reference or email");
  falls back to parsing title / id in the same way ScenariosStep does
  for legacy tasks that predate the field.
*/
export const humanize = (s = "") => s
  .replace(/[_-]/g, " ")
  .replace(/\b\w/g, (c) => c.toUpperCase())
  .trim();

export function deriveUseCaseLabel(t) {
  if (t?.useCase) return goalLabel(t.useCase);
  const id = String(t?.id || "");
  const title = String(t?.title || "");
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return `Complete a call that requires ${humanize(m[1])}`;
    return "Tool use";
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 60).trim();
    return short || "Rule enforcement";
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table) return `${humanize(table)} data`;
    return "Data traps";
  }
  if (id.includes("-adversarial-")) return title || "Adversarial pressure";
  if (id.includes("-edge-")) return title || "Edge cases";
  return "Other";
}
