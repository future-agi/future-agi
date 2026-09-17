/**
 * Global "Improvements" list — every self-improvement run the user has
 * kicked off, from whichever surface kicked it off.
 *
 * The record shape is the same one the sim-flow already writes into
 * `envState.optimizations`. We add a `source` field so a row on the global
 * list can name where it came from — Simulation for the ones the run screen
 * files, Dataset for the ones the dataset optimizer files. Observe isn't a
 * real source in this build — Observe has no "improve" flow — so we don't
 * mint any and don't offer it in the source column.
 *
 * `flattenImprovements(state)` folds every env's `optimizations` together
 * plus seeds two mock Dataset entries so the source column has variety
 * during the prototype. Simulation entries carry `envId`; Dataset entries
 * carry `datasetId` + `datasetName` — presented by the list as one unified
 * "Target" column.
 */

/* ── source badges the UI reads ─────────────────────────────────────────── */

export const IMPROVEMENT_SOURCES = {
  simulation: {
    id: "simulation",
    label: "Simulation",
    icon: "solar:test-tube-linear",
  },
  dataset: {
    id: "dataset",
    label: "Dataset",
    icon: "solar:database-linear",
  },
};

/* ── seeded Dataset-source records ──────────────────────────────────────── */

/*
 * These aren't produced by any real code path in this prototype — the
 * dataset-optimizer lives in a different section and doesn't feed simulate-v2.
 * Seeded here so the "Source" column shows more than one value in the demo.
 * Real dataset optimizations get folded in when they start writing records
 * to a shared store, at which point this seed goes away.
 */
const SEEDED_DATASET_IMPROVEMENTS = [
  {
    id: "OPT-DS-00001",
    name: "Dataset optimize · Refunds golden set",
    source: "dataset",
    datasetId: "ds_refunds_golden",
    datasetName: "Refunds golden set",
    model: "claude-opus-5",
    optimizer: "protegi",
    status: "completed",
    createdAt: "2026-09-13T09:20:00.000Z",
    completedAt: "2026-09-13T09:44:00.000Z",
    result: {
      trials: new Array(9).fill(null).map((_, i) => ({ n: i + 1, score: 62 + i * 3 })),
      trainScore: 91,
      heldBase: 68,
      heldScore: 84,
      gap: 7,
      winner: { n: 8, note: "Added a tool-evidence gate before task-success can pass" },
    },
  },
  {
    id: "OPT-DS-00002",
    name: "Dataset optimize · Booking assistant",
    source: "dataset",
    datasetId: "ds_booking_1k",
    datasetName: "Booking assistant · 1k dataset",
    model: "claude-sonnet-5",
    optimizer: "mipro",
    status: "completed",
    createdAt: "2026-09-10T14:05:00.000Z",
    completedAt: "2026-09-10T14:38:00.000Z",
    result: {
      trials: new Array(11).fill(null).map((_, i) => ({ n: i + 1, score: 71 + i * 2 })),
      trainScore: 93,
      heldBase: 74,
      heldScore: 79,
      gap: 14,
      winner: { n: 10, note: "Rewrote the closing-turn rules" },
    },
  },
  {
    id: "OPT-DS-00003",
    name: "Dataset optimize · Support triage",
    source: "dataset",
    datasetId: "ds_support_triage",
    datasetName: "Support triage",
    model: "claude-haiku-4-5",
    optimizer: "protegi",
    status: "running",
    createdAt: "2026-09-15T11:12:00.000Z",
    result: {
      trials: new Array(4).fill(null).map((_, i) => ({ n: i + 1, score: 66 + i * 2 })),
    },
  },
];

/**
 * Every improvement record the app knows about, newest first.
 *
 * Simulation-source rows come from every env's `optimizations` array in the
 * store — tagged with `source: "simulation"` and carrying the env they
 * target so the row can link back to the run detail. Dataset-source rows
 * come from the seed above.
 */
export function flattenImprovements(state) {
  const envs = state?.myEnvironments || [];
  const byEnv = state?.byEnv || {};
  const simRows = [];
  envs.forEach((env) => {
    const envState = byEnv[env.id];
    const list = envState?.optimizations || [];
    list.forEach((rec) => {
      /* `optimizationId` scopes numbering per env — every env has its own
         OPT-00001..N — so IDs collide when we fold envs into one global
         list. `rowId` namespaces the record's id with the env id so the
         table has a stable, unique row key without changing the underlying
         optimization id the detail view addresses. */
      simRows.push({
        ...rec,
        rowId: `${env.id}::${rec.id}`,
        source: "simulation",
        envId: env.id,
        envName: env.name,
      });
    });
  });
  const datasetRows = SEEDED_DATASET_IMPROVEMENTS.map((r) => ({ ...r, rowId: r.id }));
  return [...simRows, ...datasetRows]
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
}

/** Look one up by id from the folded list — used by the detail route.
 *  Matches on rowId (the composite key) first, then falls back to the raw
 *  optimization id so links carrying either shape resolve. */
export function findImprovement(state, id) {
  const list = flattenImprovements(state);
  return list.find((r) => r.rowId === id) || list.find((r) => r.id === id) || null;
}

/**
 * Number the leader-column reads. Returns null when the run hasn't finished
 * or has no result yet, so the list can show "Running" instead of a fake %.
 */
export function heldOutHeadline(rec) {
  const r = rec?.result;
  if (!r || rec.status !== "completed") return null;
  const lift = (r.heldScore ?? 0) - (r.heldBase ?? 0);
  return {
    score: r.heldScore,
    base: r.heldBase,
    lift,
    tone: lift > 0 ? "#16A34A" : lift < 0 ? "#DC2626" : "text.secondary",
  };
}

/** How many trials the run has attempted so far (regardless of status). */
export const trialCount = (rec) => rec?.result?.trials?.length || 0;

/**
 * Best-guess target label for one row. Simulation rows target an env, Dataset
 * rows target a dataset — one column, two shapes.
 */
export const targetLabel = (rec) => {
  if (rec.source === "simulation") return rec.envName || "Environment";
  if (rec.source === "dataset") return rec.datasetName || "Dataset";
  return "—";
};

export default flattenImprovements;
