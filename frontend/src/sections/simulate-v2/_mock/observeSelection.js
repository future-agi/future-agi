/*
  What Observe's "Create environment test" hands an environment (evidence.md,
  step 1: reuse the Replay selection, "review the exact selection before
  proceeding"): the sessions, traces or calls picked there, by ID. The
  environment keeps them as imported evidence, so the review opens on exactly
  those — nothing is picked a second time from another list.

  A Simulator project's calls are our own simulated calls, never production
  evidence ("never disguise one as another"), so they import as simulated.
*/
import { paths } from "src/routes/paths";

/* Long selections travel as a count of everything matching instead. */
const MAX_IDS = 50;

const NOUN = { sessions: "session", traces: "trace", calls: "call" };
export const selectionNoun = (kind, n = 1) =>
  `${NOUN[kind] || "trace"}${n === 1 ? "" : "s"}`;

/** Where Create environment test sends the selection. */
export const observeImportHref = (
  envId,
  { kind = "traces", ids = [], all = false, total = null, project = null },
) => {
  const q = new URLSearchParams({ add: "replay", from: "observe", kind });
  if (all || ids.length > MAX_IDS) {
    q.set("all", "1");
    if (total != null) q.set("n", String(total));
  } else q.set("ids", ids.join(","));
  if (project) q.set("project", project);
  return `${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?${q}`;
};

/** The selection a link carries, or null when it didn't come from Observe. */
export const observeSelectionOf = (searchParams) => {
  if (searchParams.get("from") !== "observe") return null;
  const ids = (searchParams.get("ids") || "").split(",").filter(Boolean);
  const all = searchParams.get("all") === "1";
  if (!ids.length && !all) return null;
  return {
    kind: searchParams.get("kind") || "traces",
    ids,
    all,
    total: Number(searchParams.get("n")) || null,
    project: searchParams.get("project") || null,
  };
};

const shortId = (id) => {
  const s = String(id);
  /* A UUID reads by its first block, as Observe shows it; other IDs in full. */
  return /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(s) ? s.slice(0, 8) : s;
};

/**
 * The picked items as import evidence. The prototype has no trace contents
 * for real Observe IDs, so each borrows the evidence of one of the agent's
 * sample traces — but keeps its own ID, so the review names what was picked.
 */
export const selectionTracesOf = (sel, pool) => {
  if (!sel || !pool.length) return [];
  const n = sel.all
    ? Math.min(sel.total || pool.length, pool.length)
    : sel.ids.length;
  return Array.from({ length: n }, (_, i) => {
    const base = pool[i % pool.length];
    const real = sel.all ? null : sel.ids[i];
    const traceId = real ? shortId(real) : base.traceId;
    return {
      ...base,
      id: `obs-${sel.kind}-${real || base.traceId}`,
      traceId,
      observeId: real,
      unit: sel.kind,
      ...(sel.kind === "calls" && { sourceKind: "simCall" }),
    };
  });
};
