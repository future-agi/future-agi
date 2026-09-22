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

const CURRENT_USER = {
  kind: "user",
  id: "u_vel",
  name: "Vel",
  email: "velalagan@futureagi.com",
};

const SYSTEM_ACTOR = { kind: "system", id: "sys", name: "System" };
const ASSISTANT_ACTOR = { kind: "assistant", id: "sim_builder", name: "Simulation builder" };

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
  "builder-chat": {
    id: "builder-chat",
    label: "Added via builder chat",
    short: "Chat",
    icon: "solar:chat-round-line-linear",
    actor: ASSISTANT_ACTOR,
    /* PRD §6.1.2 form: green filled mark — "the assistant did this". */
    formKind: "assistant",
  },
  "dataset-import": {
    id: "dataset-import",
    label: "Imported from dataset",
    short: "Dataset",
    icon: "solar:database-linear",
    actor: CURRENT_USER,
    formKind: "user",
  },
  production: {
    id: "production",
    label: "From production trace",
    short: "Prod",
    icon: "solar:server-linear",
    actor: SYSTEM_ACTOR,
    formKind: "auto",
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
  if (scenario?.addedAt && scenario?.addedBy && scenario?.source) return scenario;
  /* Back-fill for demo data: split existing scenarios into three
     lanes with different human sources so the batch cards read as
     "Vel added N scenarios via X" rather than "System · Auto-derived"
     (per user directive 2026-09-22: "should be added by user name").
     Deterministic per id-hash so the split is stable across renders. */
  const idHash = hashInt(scenario?.id || "");
  const lane = idHash % 3;
  const laneSource = ["manual", "builder-chat", "dataset-import"][lane];
  const laneDaysAgo = [1, 3, 6][lane];
  const laneHour = [9, 15, 11][lane];
  const at = new Date(Date.now() - laneDaysAgo * 24 * 3600 * 1000);
  at.setHours(laneHour, 0, 0, 0);
  const atIso = at.toISOString();
  return stampProvenance(scenario, {
    source: laneSource,
    actor: CURRENT_USER,
    at: atIso,
    batchId: `batch_${laneSource}_lane${lane}`,
  });
}

/**
 * Batch id — one generation session / drawer submit / chat turn = one
 * batch. Deterministic per (source, minute-bucket) so several scenarios
 * added in the same call carry the same batch id.
 */
export function defaultBatchId(source, atIso) {
  const minute = Math.floor(Date.parse(atIso) / 60000);
  return `batch_${source}_${minute}`;
}

/** Small, stable hash → non-negative int. */
function hashInt(s) {
  const str = String(s || "");
  let h = 0;
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
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
      scenarios: [],
    };
    cur.scenarios.push(s);
    /* Batch timestamp is the earliest addedAt in the batch so the
       "added 2h ago" reads as when the batch started. */
    if (new Date(s.addedAt) < new Date(cur.addedAt)) cur.addedAt = s.addedAt;
    map.set(key, cur);
  });
  return [...map.values()].sort((a, b) => new Date(b.addedAt) - new Date(a.addedAt));
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
