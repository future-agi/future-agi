/**
 * Scenario provenance — who added a scenario, when, and via what path.
 *
 * PRD reference: `product/agent-simulation-harness/04-prd-v6.md` §6.1.2
 * ("Provenance has a form, not just words"), Q27 §3023 (batch identity),
 * and T8f (grouping by pack / persona / use case / objective).
 *
 * Rules:
 * 1. Every scenario carries `addedAt` (ISO), `addedBy` (actor object) and
 *    `source` (how it entered the suite). `batchId` groups scenarios
 *    added together — one generation session, one drawer submit, or one
 *    chat turn is one batch.
 * 2. Provenance is stamped at the add-site. `ensureProvenance` back-fills
 *    plausible values for scenarios that predate this contract so demo
 *    data reads correctly.
 * 3. Never mutates. Always returns a new object.
 */

import { paths } from "src/routes/paths";
import { improvementUrl } from "../improvements/improvementNav";

const CURRENT_USER = {
  kind: "user",
  id: "u_vel",
  name: "Vel",
  email: "velalagan@futureagi.com",
};

const SYSTEM_ACTOR = { kind: "system", id: "sys", name: "System" };
const ASSISTANT_ACTOR = {
  kind: "assistant",
  id: "sim_builder",
  name: "Simulation builder",
};

/** Actor for the currently signed-in user. Kept in one place so a real
 *  auth wiring later swaps one function, not many call-sites. */
export const currentUser = () => CURRENT_USER;

/**
 * Sources: how a scenario entered the suite. Every scenario has exactly
 * one source. Kept flat rather than an enum so consumers can render a
 * label + icon without a switch.
 */
export const SOURCES = {
  derived: {
    id: "derived",
    label: "Auto-derived",
    short: "Auto",
    icon: "solar:magic-stick-3-linear",
    actor: SYSTEM_ACTOR,
    /* PRD §6.1.2 form: pulsing hollow ring — "we started this". */
    formKind: "auto",
  },
  template: {
    id: "template",
    label: "From template",
    short: "Template",
    icon: "solar:folder-2-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
  },
  manual: {
    id: "manual",
    label: "Added manually",
    short: "Manual",
    icon: "solar:pen-linear",
    actor: CURRENT_USER,
    /* PRD §6.1.2 form: filled amber square — "you started this". */
    formKind: "user",
  },
  /* Written by the builder chat from what someone asked for. */
  chat: {
    id: "chat",
    label: "From chat",
    short: "Chat",
    icon: "solar:chat-round-line-linear",
    actor: ASSISTANT_ACTOR,
    /* PRD §6.1.2 form: green filled mark — "the assistant did this". */
    formKind: "assistant",
  },
  /* The id chat batches carried before "chat" — same source, same badge. */
  "builder-chat": {
    id: "builder-chat",
    label: "From chat",
    short: "Chat",
    icon: "solar:chat-round-line-linear",
    actor: ASSISTANT_ACTOR,
    formKind: "assistant",
  },
  /* Written by Add scenarios → Generate. Not "derived": derived is what the
     environment wrote when it was built, and a later generation is an
     addition someone asked for. */
  generated: {
    id: "generated",
    label: "Generated",
    short: "Generated",
    icon: "solar:magic-stick-3-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  "dataset-import": {
    id: "dataset-import",
    label: "Imported from dataset",
    short: "Dataset",
    icon: "solar:database-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  /* Written by the environment itself when a rebuild taught the world a
     tool a new agent version calls. */
  rebuild: {
    id: "rebuild",
    label: "Added by a rebuild",
    short: "Rebuild",
    icon: "solar:refresh-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
  },
  /* Written for a persona picked from the workspace persona library. */
  "persona-library": {
    id: "persona-library",
    label: "From persona library",
    short: "Library",
    icon: "solar:users-group-rounded-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  /* Other evidence through the same import review — never called production. */
  transcript: {
    id: "transcript",
    label: "From an uploaded transcript",
    short: "Transcript",
    icon: "solar:document-text-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
  },
  simulation: {
    id: "simulation",
    label: "From a failed simulation",
    short: "Simulation",
    icon: "solar:play-circle-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
  },
  production: {
    id: "production",
    label: "From production trace",
    short: "Prod",
    icon: "solar:server-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
  },
  /* Built from an Error Feed issue's traces when someone pressed Fix. */
  "error-feed": {
    id: "error-feed",
    label: "From Error Feed",
    short: "Error Feed",
    icon: "solar:bug-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  /* Written by an improvement to reproduce what it was fixing, then added
     to the environment by a person (RSI PRD). */
  improvement: {
    id: "improvement",
    label: "From an improvement",
    short: "Improvement",
    icon: "solar:magic-stick-3-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  /* A variation of a scenario that failed (or came out flaky) in one of this
     environment's runs, written to reproduce that failure. */
  "failing-case": {
    id: "failing-case",
    label: "From a failing case",
    short: "Failing case",
    icon: "solar:play-circle-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
};

/** Given a source id, return the descriptor (safe fallback to derived). */
export const sourceOf = (id) => SOURCES[id] || SOURCES.derived;

/**
 * Stamp provenance onto a scenario object. Non-mutating.
 *
 * `opts.source` — one of the SOURCES keys.
 * `opts.actor` — override the source's default actor (e.g. a non-current user
 *   who added it via the drawer).
 * `opts.at` — override the timestamp (defaults to now); useful for back-fill.
 * `opts.batchId` — override the batch id (defaults to a hash of source + at).
 */
export function stampProvenance(scenario, opts = {}) {
  const source = opts.source || "manual";
  const at = opts.at || new Date().toISOString();
  const actor = opts.actor || sourceOf(source).actor;
  const batchId = opts.batchId || defaultBatchId(source, at);
  return {
    ...scenario,
    addedAt: scenario.addedAt || at,
    addedBy: scenario.addedBy || actor,
    source: scenario.source || source,
    batchId: scenario.batchId || batchId,
  };
}

/**
 * Ensure a scenario has provenance. Back-fills plausible defaults so
 * scenarios that predate this contract (or that come from the mock
 * factories) still read correctly. Deterministic per scenario id — the
 * timestamp is derived from a hash of the id so repeated reads don't
 * shimmer.
 */
export function ensureProvenance(scenario) {
  if (scenario?.addedAt && scenario?.addedBy && scenario?.source)
    return scenario;
  /* Back-fill for demo data, shaped like a real suite's history: most
     scenarios arrived in one batch when the environment was built, and a
     few smaller batches were added later — by you and by teammates, days
     apart. A batch is one addition, not a kind of scenario. Deterministic
     per id-hash so the split is stable across renders. */
  /* By goal, not by row: a goal's scenarios were written together, so they
     arrive in one batch rather than scattered one per batch. */
  const slot = hashInt(scenario?.useCase || scenario?.id || "") % 100;
  const lane = BACKFILL_LANES.find((l) => slot < l.upTo) || BACKFILL_LANES[0];
  const at = new Date(Date.now() - lane.hoursAgo * 3600 * 1000);
  at.setMinutes(0, 0, 0);
  return stampProvenance(scenario, {
    source: lane.source,
    actor: lane.actor,
    at: at.toISOString(),
    batchId: `batch_${lane.id}`,
  });
}

const TEAMMATE_MAYA = {
  kind: "user",
  id: "u_maya",
  name: "Maya Rao",
  email: "maya@example.com",
};
const TEAMMATE_ARJUN = {
  kind: "user",
  id: "u_arjun",
  name: "Arjun Kapoor",
  email: "arjun@example.com",
};

/* Oldest first. `upTo` is the cumulative share of scenarios in the lane. */
const BACKFILL_LANES = [
  {
    id: "build",
    upTo: 76,
    source: "derived",
    actor: CURRENT_USER,
    hoursAgo: 24 * 8 + 3,
  },
  {
    id: "chat",
    upTo: 88,
    source: "builder-chat",
    actor: CURRENT_USER,
    hoursAgo: 24 * 4 + 6,
  },
  {
    id: "dataset",
    upTo: 96,
    source: "dataset-import",
    actor: TEAMMATE_MAYA,
    hoursAgo: 24 * 2 - 2,
  },
  {
    id: "manual",
    upTo: 100,
    source: "manual",
    actor: TEAMMATE_ARJUN,
    hoursAgo: 5,
  },
];

/**
 * Batch id — one generation session / drawer submit / chat turn = one
 * batch. Every row of one addition carries the same id; two additions,
 * however close together, never do.
 */
export function defaultBatchId(source, atIso) {
  /* To the millisecond, not the minute: two additions a few seconds apart
     are two batches. Callers compute it once per addition and stamp every
     row with it, so one addition still shares one id. */
  const ms = Date.parse(atIso) || Date.now();
  return `batch_${source}_${ms.toString(36)}`;
}

/** Small, stable hash → non-negative int. */
function hashInt(s) {
  const str = String(s || "");
  let h = 0;
  for (let i = 0; i < str.length; i += 1)
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * Group scenarios by batch id, newest batch first. Each group carries
 * summary metadata for the Recent-additions strip.
 */
export function groupByBatch(scenarios) {
  const map = new Map();
  scenarios.forEach((sc) => {
    const s = ensureProvenance(sc);
    const key = s.batchId;
    const cur = map.get(key) || {
      batchId: key,
      source: s.source,
      addedBy: s.addedBy,
      addedAt: s.addedAt,
      /* Why the batch exists, when the batch knows (a rebuild does). */
      /* Older rebuilds named the stored world label, which can differ from
         the version the bar shows — read them without it. */
      note:
        s.batchNote?.replace(
          /^when environment v\d+ learned/,
          "when the environment learned",
        ) || null,
      scenarios: [],
    };
    cur.scenarios.push(s);
    /* Batch timestamp is the earliest addedAt in the batch so the
       "added 2h ago" reads as when the batch started. */
    if (new Date(s.addedAt) < new Date(cur.addedAt)) cur.addedAt = s.addedAt;
    map.set(key, cur);
  });
  return [...map.values()].sort(
    (a, b) => new Date(b.addedAt) - new Date(a.addedAt),
  );
}

/**
 * Human-legible label for a scenario's provenance. Used under the
 * scenario name in row detail.
 */
export function provenanceLabel(scenario) {
  const s = ensureProvenance(scenario);
  const src = sourceOf(s.source);
  const by = s.addedBy?.kind === "user" ? s.addedBy.name : src.short;
  return `${by} · ${relativeTime(s.addedAt)} · via ${src.label.toLowerCase()}`;
}

/** "2h ago" / "3d ago" style relative time. Stable enough for a demo. */
export function relativeTime(iso) {
  if (!iso) return "";
  const diffMs = Date.now() - Date.parse(iso);
  const min = Math.round(diffMs / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.round(d / 30);
  return `${mo}mo ago`;
}

/**
 * Where a scenario came from, as something to click: the Error Feed issue it
 * was built from, the run whose failure it reproduces, or the chat that wrote
 * it. Sources with nowhere to go back to (generated, derived, a dataset, a
 * script) return `to: null` and keep only the tooltip.
 *
 *   { source, label, ref, title, detail, to, chat }
 *     ref    — the short id the badge shows ("ISS-4501", "Run 3", "Chat")
 *     title  — first tooltip line ("From Error Feed · ISS-4501 — <title>")
 *     detail — second tooltip line (who, when, and anything specific)
 *     to     — a route, or null
 *     chat   — true when the way back is the builder chat, not a route
 *     observe — for a scenario built from production: { line, to } — the
 *              original conversation stays in Observe, and `to` opens its trace
 *
 * `ctx` ({ envId, envState }) lets an improvement-made scenario link to the
 * improvement that wrote it.
 */
export function sourceLinkOf(scenario, ctx = {}) {
  const s = ensureProvenance(scenario);
  const src = sourceOf(s.source);
  const by = s.addedBy?.kind === "user" ? s.addedBy.name : src.short;
  const when = relativeTime(s.addedAt);
  const base = {
    source: src,
    label: src.label,
    ref: null,
    to: null,
    chat: false,
  };

  const issue = s.errorFeedIssue;
  if (issue?.id) {
    const traces = issue.traceIds?.length
      ? `Built from traces ${issue.traceIds.join(", ")}`
      : null;
    return {
      ...base,
      source: SOURCES["error-feed"],
      label: SOURCES["error-feed"].label,
      ref: issue.id,
      title: `From Error Feed · ${issue.id}${issue.title ? ` — ${issue.title}` : ""}`,
      detail: [traces, `${by} · ${when}`].filter(Boolean).join(" · "),
      to: paths.dashboard.errorFeed.detail(issue.id),
      /* The scenario keeps the problem; the conversation itself stays where it was recorded. */
      observe: {
        line: "The original conversation stays in Observe.",
        to: `${paths.dashboard.errorFeed.detail(issue.id)}?tab=traces${issue.traceIds?.[0] ? `&trace=${issue.traceIds[0]}` : ""}`,
      },
    };
  }
  const fc = s.failingCase;
  if (fc?.runId && fc?.envId) {
    const how =
      fc.verdict === "flaky"
        ? `came out flaky${fc.passes != null ? ` (${fc.passes} of ${fc.repeats} passed)` : ""}`
        : "failed";
    return {
      ...base,
      source: SOURCES["failing-case"],
      label: SOURCES["failing-case"].label,
      ref: `Run ${fc.runOrdinal}`,
      title: `From a failing case · Run ${fc.runOrdinal} — ${fc.taskName} ${how}${fc.evalName ? ` on ${fc.evalName}` : ""}`,
      detail: `Agent ${fc.agentVersion || "—"} · ${by} · ${when}`,
      to: paths.dashboard.simulate.simulationRun(fc.envId, fc.runId),
    };
  }
  /* A production conversation replayed word for word (Phase 2). */
  const replay = s.productionReplay;
  if (replay?.conversationId) {
    return {
      ...base,
      source: SOURCES.production,
      label: SOURCES.production.label,
      ref: "Replay",
      title: `From production · ${replay.channel || "conversation"} replayed word for word`,
      detail: [
        `${replay.turns?.length || 0} caller turns`,
        `${by} · ${when}`,
      ].join(" · "),
      observe: {
        line: "The original conversation stays in Observe.",
        to: `${paths.dashboard.observe}?trace=${replay.conversationId}`,
      },
    };
  }
  if (s.fromImprovement?.id && !s.failingCase) {
    /* Links to the improvement that wrote it — its run. */
    const record = (ctx.envState?.optimizations || []).find(
      (o) => o.id === s.fromImprovement.id,
    );
    const envId = ctx.envId || ctx.env?.id || s.env || null;
    return {
      ...base,
      source: SOURCES.improvement,
      label: SOURCES.improvement.label,
      ref: s.fromImprovement.id,
      title: `From an improvement · ${s.fromImprovement.name || s.fromImprovement.id}`,
      detail: `${by} · ${when}`,
      to: record && envId ? improvementUrl(envId, record, ctx.envState) : null,
    };
  }
  if (s.source === "chat" || s.source === "builder-chat") {
    return {
      ...base,
      ref: "Chat",
      title: s.chatAsk ? `From chat · “${s.chatAsk}”` : "From chat",
      detail: `${by} · ${when}`,
      chat: true,
    };
  }
  /* Built from a production trace or cluster — the conversations stay in Observe. */
  if (src.id === "production") {
    const trace = s.productionCluster?.traceIds?.[0] || s.traceId || null;
    return {
      ...base,
      title: src.label,
      detail: `${by} · ${when}`,
      observe: {
        line: "The original conversation stays in Observe.",
        to: `${paths.dashboard.observe}${trace ? `?trace=${trace}` : ""}`,
      },
    };
  }
  return { ...base, title: src.label, detail: `${by} · ${when}` };
}
