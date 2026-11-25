/*
  Plain helpers behind the trace table — the per-call metrics (CSAT, latency),
  the column and grouping definitions, and the use-case label. Kept out of
  TraceTable.jsx so that file only exports components (Fast Refresh), and so
  the analytics, compare and widget code can read them without the table.
*/
import { goalLabel } from "../_mock/scenarios";

const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1)
    h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
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
  const mood = HARD_TO_PLEASE.test(
    [...(t.persona?.traits || []), t.persona?.name].join(" "),
  )
    ? -1
    : 0;
  const spread = (hash(`${t.id}:csat`) % 5) - 2;
  return Math.max(0, Math.min(10, base + mood + spread));
};
/*
  The one average reply latency, in ms — the run table, the compare view and
  the transcript drawer all read this. Reply times on a call run ~1-2.5s
  (endpointing, transcription, model, voice). A run that records response
  times (new runs) marks the ones the agent's side didn't report as null,
  which reads as unverified, not as fast.
*/
export const latencyOf = (t) => {
  if (!t) return null;
  if ("responseMs" in t && t.responseMs == null) return null;
  /* An epoch's call keeps its scenario but not its speed. */
  /* Seeded per run and per trial: each call is its own. */
  const h = hash(
    `${t.id}${t.runSeed ? `:${t.runSeed}` : ""}${t.trialIndex ? `:t${t.trialIndex}` : ""}`,
  );
  return Math.round((900 + (h % 1700)) * (t.latencyScale || 1));
};
/* "840ms" under a second, "2.1s" from there. */
export const formatLatency = (ms) =>
  ms == null ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;

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
  {
    key: "callDetails",
    label: "Run details",
    defaultOn: true,
    width: 150,
    group: "Run details",
  },
  {
    key: "persona",
    /* Glossary: the actor is the simulated person (name, age, voice); the
       persona is how they behave — in the hovercard's traits. */
    label: "Actor",
    defaultOn: true,
    width: 210,
    group: "Scenario details",
  },
  {
    key: "scenario",
    label: "Scenario",
    defaultOn: false,
    width: 300,
    group: "Scenario details",
  },
  {
    key: "expected",
    label: "Ideal outcome",
    defaultOn: false,
    width: 300,
    group: "Scenario details",
  },
  {
    key: "branch",
    label: "Conversation branch",
    defaultOn: false,
    width: 260,
    group: "Scenario details",
  },
  {
    key: "csat",
    label: "CSAT",
    defaultOn: true,
    width: 84,
    group: "System metrics",
  },
  {
    key: "turns",
    label: "Turns",
    defaultOn: true,
    width: 92,
    group: "System metrics",
  },
  {
    key: "latency",
    label: "Latency",
    defaultOn: true,
    width: 96,
    group: "System metrics",
  },
  {
    key: "tokens",
    label: "Tokens",
    defaultOn: true,
    width: 96,
    group: "System metrics",
  },
];

/* Each eval is its own column. Stored as an exclusion marker on the
   visibility Set (`eval:<id>:hidden`), so a new eval added later defaults
   to visible without touching the stored state. */
export const HIDDEN_EVAL = (id) => `eval:${id}:hidden`;
export const isEvalHidden = (columns, evalId) =>
  columns?.has(HIDDEN_EVAL(evalId));
export const visibleEvals = (columns, evals) =>
  (evals || []).filter((e) => !isEvalHidden(columns, e.id));

export const defaultTraceColumns = () =>
  new Set(TRACE_COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));

export const GROUPINGS = [
  { id: "useCase", label: "Goal", icon: "solar:target-linear" },
  /* Every sub-goal a call has — a list axis, so a call with several
     sub-goals sits in each of their groups and the groups overlap rather
     than split the run. Said in the menu and on each group's count. */
  {
    id: "allSubGoals",
    label: "Sub-goal",
    icon: "solar:checklist-minimalistic-linear",
    overlaps: true,
    hint: "A scenario with several sub-goals appears under each of them.",
  },
  /* Member — for an agent made of several agents (a squad, a flow, an app
     with handoffs): the member a conversation ended with. Offered only when
     the run's agent has more than one member. */
  {
    id: "member",
    label: "Member",
    icon: "solar:users-group-rounded-linear",
    multiAgent: true,
    hint: "Each conversation counts for the member it ended with.",
  },
  { id: "persona", label: "Actor", icon: "solar:user-rounded-linear" },
  { id: "rule", label: "Rule broken", icon: "solar:shield-cross-linear" },
  { id: "subGoal", label: "Failure sub-goal", icon: "solar:map-linear" },
  {
    id: "pattern",
    label: "Failure pattern",
    icon: "solar:danger-triangle-linear",
  },
  { id: "status", label: "Status", icon: "solar:check-circle-linear" },
  /* Verdict · cause — fail · agent, required evaluation, the inconclusive
     kinds. Rows carry it as `verdictCause`. */
  { id: "cause", label: "Verdict cause", icon: "solar:tag-linear" },
  /* Persona-attribute buckets — same-accent / same-age-band callers
     cluster together, so a reader can spot regional or generational
     regressions ("older callers keep dropping mid-flow"). */
  { id: "accent", label: "Accent", icon: "solar:global-linear" },
  { id: "age", label: "Age", icon: "solar:calendar-linear" },
  /* Attack — adversarial pressure the scenario applies (rule probe,
     data trap, prompt-injection, edge branch). Groups the run by how
     it was trying to break the agent. */
  { id: "attack", label: "Attack", icon: "solar:bug-linear" },
  /* Task — bucket by scenario name. Trials of the same task cluster,
     so flakiness across trials becomes obvious. */
  { id: "task", label: "Task", icon: "solar:document-text-linear" },
  /* Escape hatch — flat table, no grouping at all. Kept last so the
     dropdown reads "…then no grouping" as the exit. */
  { id: "none", label: "No grouping", icon: "solar:list-linear" },
];

/*
  Use-case label for a task, matching the ScenariosStep grouping so
  a run's traces slice into the same buckets the env creation screen
  showed. Prefers the scenario's `useCase` sentence (populated by the
  scenario mock — "Look up an existing booking by reference or email");
  falls back to parsing title / id in the same way ScenariosStep does
  for legacy tasks that predate the field.
*/
export const humanize = (s = "") =>
  s
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

/*
  The rule a task was checking: rule-enforcement scenarios are titled
  "Refuse a request that would break: <the rule>", and everything after the
  colon is the rule text (or, for a scenario in the rule set, the title is the
  rule). Tasks that aren't rule checks have none. Read by
  the Rule broken grouping and the Rule filter.
*/
export const ruleOf = (t) => {
  const title = String(t?.title || "");
  const m = title.match(/^Refuse a request that would break:\s*(.+)$/);
  if (m) return m[1].trim();
  /* A rule scenario can be titled by the rule itself. */
  return /-rule-/.test(String(t?.id || "")) && title ? title.trim() : null;
};

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

/* The useful half of a task id is the trailing variant — everything after the
   last numeric segment ("…-core-0-routine" → "Routine", "…-0-off-topic" →
   "Off Topic"). That's the one thing that differs row-to-row inside a group,
   so it becomes the run's title instead of the repeated raw id. */
export function variantLabel(id) {
  const parts = String(id || "")
    .split("-")
    .filter(Boolean);
  let lastNum = -1;
  parts.forEach((p, i) => {
    if (/^\d+$/.test(p)) lastNum = i;
  });
  const tail = lastNum >= 0 ? parts.slice(lastNum + 1) : [];
  if (tail.length) return humanize(tail.join(" "));
  /* No trailing variant — fall back to the test-kind token before the number. */
  if (lastNum > 0) return humanize(parts[lastNum - 1]);
  return humanize(parts.slice(-1)[0] || "Run");
}

/*
  What a group row of repeated scenarios says about its results (24 Nov
  review). Several scenarios: how many passed / were flaky / failed, zeros
  left out, so the counts add up to the group. One scenario (grouped by
  persona, say): that scenario's trials — "1 of 3 trials passed".
*/
export const groupOutcomeParts = (group) => {
  const rows = group?.rows || [];
  /* One scenario that wasn't scored: say so — never a blank. */
  if (rows.length === 1 && ["unmeasured", "error"].includes(rows[0].status)) {
    const word = group.trialsWhenOne ? "Not scored" : "1 scenario not scored";
    return [
      { key: "not scored", n: "", word, dot: "text.disabled", text: word },
    ];
  }
  if (
    group.trialsWhenOne &&
    rows.length === 1 &&
    (rows[0].samples?.length || 0) > 1
  ) {
    const samples = rows[0].samples;
    const passed = samples.filter((s) => s === "passed").length;
    const status = rows[0].status;
    const dot =
      status === "passed"
        ? "status.success.solid"
        : status === "flaky"
          ? "status.warning.solid"
          : "status.danger.solid";
    return [
      {
        key: "trials",
        n: `${passed} of ${samples.length}`,
        word: "trials passed",
        dot,
        text: `${passed} of ${samples.length} trials passed`,
      },
    ];
  }
  const o = group?.outcomes || {};
  const parts = [
    ["passed", o.passed || 0, "status.success.solid"],
    ["flaky", o.mixed || 0, "status.warning.solid"],
    ["failed", o.failing || 0, "status.danger.solid"],
    /* So the counts add up to the group's scenarios. */
    ["not scored", o.unmeasured || 0, "text.disabled"],
  ]
    .filter(([, n]) => n > 0)
    .map(([word, n, dot]) => ({
      key: word,
      n,
      word,
      dot,
      text: `${n} ${word}`,
    }));
  /* The unit is always said, so a count never reads as trials:
     "3 scenarios failed", or "3 scenarios: 1 passed · 2 failed". */
  const total = parts.reduce((a, p) => a + p.n, 0);
  const noun = (n) => `${n} scenario${n === 1 ? "" : "s"}`;
  if (parts.length === 1) {
    const [p] = parts;
    return [{ ...p, n: noun(p.n), text: `${noun(p.n)} ${p.word}` }];
  }
  return parts.length
    ? [
        {
          key: "total",
          n: "",
          word: `${noun(total)}:`,
          dot: "text.secondary",
          text: `${noun(total)}:`,
          lead: true,
        },
        ...parts,
      ]
    : parts;
};

/* The parts as one line: "3 scenarios: 1 passed · 2 failed". */
export const groupOutcomeText = (parts) =>
  parts
    .map((p, i) =>
      i === 0 ? p.text : `${parts[i - 1].lead ? " " : " · "}${p.text}`,
    )
    .join("");
