// Iteration history (R2-SPEC §4.3.3, §4.3.4): the lineage ribbon's chain
// and hop labels, and the leaderboard's nesting of iterations under their
// root run. Pure; runs are R1 runs with `parent_run_id` and `iteration`.
import { configDiff } from "src/sections/common/Runs/configDiff";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { changeNameOf } from "./iterateModel";

export const MAX_CHAIN = 8;
export const MAX_HOP_LABEL = 32;

const byCreated = (a, b) =>
  String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")) ||
  (a.iteration?.n ?? 0) - (b.iteration?.n ?? 0);

/** `{byId, children}` of the runs that take part in a lineage. */
function index(runs) {
  const byId = new Map((runs ?? []).map((r) => [r.run_id, r]));
  const children = new Map();
  (runs ?? []).forEach((r) => {
    const p = r.parent_run_id;
    if (!p || !byId.has(p)) return;
    if (!children.has(p)) children.set(p, []);
    children.get(p).push(r);
  });
  children.forEach((list) => list.sort(byCreated));
  return { byId, children };
}

/** The root run id of a run (walks parents; stops at a missing parent). */
export function rootOf(runs, runId) {
  const { byId } = index(runs);
  let cur = byId.get(runId);
  const seen = new Set();
  while (cur?.parent_run_id && byId.has(cur.parent_run_id)) {
    if (seen.has(cur.run_id)) break;
    seen.add(cur.run_id);
    cur = byId.get(cur.parent_run_id);
  }
  return cur?.run_id ?? null;
}

const leafPaths = (id, children, prefix = []) => {
  const path = [...prefix, id];
  const kids = children.get(id) ?? [];
  if (!kids.length) return [path];
  return kids.flatMap((k) => leafPaths(k.run_id, children, path));
};

/**
 * The branch to show for the runs in view: the longest root → leaf path that
 * contains the focus run (else the newest leaf), and how many other branches
 * the root has.
 * @returns {{rootId: string|null, path: string[], otherBranches: string[][]}}
 */
export function lineageBranch(runs, focusIds = []) {
  const { children } = index(runs);
  const ids = (focusIds ?? []).filter((id) => rootOf(runs, id));
  // Prefer a run in view whose lineage has iterations (on an experiment
  // page the first run is often a plain one).
  const focus =
    ids.find((id) => children.get(rootOf(runs, id))?.length) ?? ids[0];
  if (!focus) return { rootId: null, path: [], otherBranches: [] };
  const rootId = rootOf(runs, focus);
  if (!children.get(rootId)?.length)
    return { rootId, path: [rootId], otherBranches: [] };
  const paths = leafPaths(rootId, children);
  const inView = (p) => (focusIds ?? []).filter((id) => p.includes(id)).length;
  const best = [...paths].sort(
    (a, b) => inView(b) - inView(a) || b.length - a.length,
  )[0];
  const others = paths.filter((p) => p !== best);
  return { rootId, path: best, otherBranches: others };
}

const nodeShort = (id) => `${String(id).split("_")[0]} fix`;

/** The config part of a hop label: `classify fix`, `model gpt-4o`, `3fa91c2 → 91bc0e4`. */
export function changeShort(parent, child) {
  if (!parent?.config || !child?.config) return "";
  const chips = configDiff(parent, child).filter(
    (c) => !["dataset", "trials", "evals"].includes(c.axis),
  );
  if (!chips.length) return "";
  const c = chips[0];
  const more = chips.length > 1 ? ` +${chips.length - 1}` : "";
  const nodes = c.detail?.nodes ?? [];
  let text;
  if (c.axis === "agent" && nodes.length === 1)
    text = changeNameOf(nodes[0].to?.prompt) ?? nodeShort(nodes[0].id);
  else if (c.axis === "agent" && nodes.length > 1)
    text = `${nodes.length} nodes`;
  else if (c.axis === "code")
    text = c.label.replace(/^code\s+/, "").replace(/\s*\(.*\)$/, "");
  else text = c.label;
  return `${text}${more}`;
}

/** `classify fix · 6 rows`, `all 30 rows`, `temperature 0.2 → 0.9 · 4 rows` (≤ 32). */
export function hopLabel(parent, child) {
  const it = child?.iteration ?? {};
  const rerun = it.rows_rerun ?? 0;
  const scope =
    !it.rows_carried && rerun
      ? `all ${rerun} rows`
      : rerun
        ? `${rerun} ${rerun === 1 ? "row" : "rows"}`
        : "";
  let change = changeShort(parent, child);
  const room = MAX_HOP_LABEL - (scope ? scope.length + 3 : 0);
  if (change.length > room)
    change = `${change.slice(0, Math.max(0, room - 1)).trimEnd()}…`;
  return [change, scope].filter(Boolean).join(" · ");
}

/**
 * `Compare chain`: the last 8 runs of the path, oldest first; the baseline
 * is the root when it fits, else the oldest shown.
 */
export function chainCompare(path) {
  const shown = path.slice(-MAX_CHAIN);
  return {
    runIds: shown,
    baselineId: shown[0] ?? null,
    capped: path.length > MAX_CHAIN,
    total: path.length,
  };
}

// --------------------------------------------------------- leaderboard

/**
 * Lineage fields joined onto leaderboard runs (which come without them):
 * `__root` (root run id or null), `__iterations` (root: its iteration runs,
 * newest last), `__depth` (0 root / plain run, 1 iteration).
 * @param {object[]} runs  leaderboard runs (include=metrics)
 * @param {object[]} lineageRuns  R1 runs with parent_run_id / iteration
 */
export function joinLineage(runs, lineageRuns) {
  const lin = new Map((lineageRuns ?? []).map((r) => [r.run_id, r]));
  const withLin = (runs ?? []).map((r) => {
    const l = lin.get(r.run_id);
    return l
      ? {
          ...r,
          parent_run_id: l.parent_run_id ?? null,
          iteration: l.iteration ?? null,
          listed: l.listed,
        }
      : r;
  });
  const all = [...lin.values()];
  return withLin.map((r) => {
    const isIter = Boolean(r.parent_run_id && r.iteration);
    const root = isIter
      ? r.iteration.root_run_id ?? rootOf(all, r.run_id)
      : null;
    return { ...r, __root: root, __depth: isIter ? 1 : 0 };
  });
}

/**
 * Iterations of a root whose own leaderboard row is hidden list as plain
 * top-level runs (`__depth: 0`), so they stay reachable. The pinned
 * baseline is that root: its natural row is filtered out and the pinned
 * copy has no iterations toggle. `__root` is kept (`group=iteration`).
 * Pure; returns the input when there is no hidden root.
 * @param {object[]} runs  joinLineage output
 * @param {string|null} hiddenRootId
 */
export function liftIterationsOf(runs, hiddenRootId) {
  if (!hiddenRootId) return runs ?? [];
  return (runs ?? []).map((r) =>
    r.__depth === 1 && r.__root === hiddenRootId ? { ...r, __depth: 0 } : r,
  );
}

/**
 * Rows in display order with iterations right under their root; collapsed
 * roots hide their iterations. Roots gain `__iterations` (count) and
 * `__latest` (newest iteration run). Iterations whose root is not in the
 * list stay top-level.
 * @param {object[]} runs  joinLineage output, already in grid order
 * @param {Set<string>} expanded  root run ids that are open
 */
export function nestIterations(runs, expanded = new Set()) {
  const ids = new Set(runs.map((r) => r.run_id));
  const kids = new Map();
  runs.forEach((r) => {
    if (r.__depth === 1 && ids.has(r.__root)) {
      if (!kids.has(r.__root)) kids.set(r.__root, []);
      kids.get(r.__root).push(r);
    }
  });
  kids.forEach((list) => list.sort(byCreated));
  const out = [];
  runs.forEach((r) => {
    if (r.__depth === 1 && ids.has(r.__root)) return;
    const list = kids.get(r.run_id) ?? [];
    out.push(
      list.length
        ? {
            ...r,
            __iterations: list.length,
            __latest: list[list.length - 1],
            // W1-1: how many of them are later runs of the root's own
            // config (#2A·2 under #2A), not changed setups.
            __kept: list.filter((k) => isLaterRunOf(k, r)).length,
          }
        : { ...r, __depth: 0 },
    );
    if (list.length && expanded.has(r.run_id))
      list.forEach((k) => out.push({ ...k, __depth: 1 }));
  });
  return out;
}

/** W1-1: whether `run` is a later run of `root`'s own config (`#2A·2` of `#2A`). */
export function isLaterRunOf(run, root) {
  return (
    Boolean(run && root) &&
    run.experiment_id === root.experiment_id &&
    run.letter === root.letter &&
    (Number(run.attempt) || 1) > (Number(root.attempt) || 1)
  );
}

/**
 * W1-1 (review should-fix 4): the words of a nested root. Later runs of
 * its own config are runs, not iterations: `2 more runs` · `latest run
 * #2A·3`; changed setups (Test a fix) keep `2 iterations` · `latest
 * iteration #2G`.
 * @param {{__iterations?: number, __kept?: number}} root
 */
export function nestWordsOf(root) {
  const n = Number(root?.__iterations) || 0;
  const runs = n > 0 && Number(root?.__kept) === n;
  return {
    count: runs
      ? `${n} more ${n === 1 ? "run" : "runs"}`
      : `${n} ${n === 1 ? "iteration" : "iterations"}`,
    latest: runs ? "latest run" : "latest iteration",
  };
}

/** `group=iteration`: key = the root run id; label = `#2B · 2 iterations`. */
export function iterationGroupOf(run, runs) {
  const rootId = run.__root ?? run.run_id;
  const root = (runs ?? []).find((r) => r.run_id === rootId) ?? run;
  const n = (runs ?? []).filter((r) => r.__root === rootId).length;
  const kept = (runs ?? []).filter(
    (r) => r.__root === rootId && isLaterRunOf(r, root),
  ).length;
  return {
    key: `root:${rootId}`,
    label: n
      ? `${runLabel(root)} · ${nestWordsOf({ __iterations: n, __kept: kept }).count}`
      : runLabel(root),
  };
}

/**
 * Re-nest after a sort (AG Grid `postSortRows`): each iteration moves right
 * under its root, oldest first; iterations whose root is not in the list
 * keep their place. Pure; returns a new array.
 * @param {object[]} items  grid nodes (or runs)
 * @param {(item: object) => object} [dataOf]  item → run (default: identity)
 */
export function nestSorted(items, dataOf = (x) => x) {
  const ids = new Set(items.map((it) => dataOf(it)?.run_id).filter(Boolean));
  const kids = new Map();
  items.forEach((it) => {
    const d = dataOf(it);
    if (d?.__depth === 1 && ids.has(d.__root)) {
      if (!kids.has(d.__root)) kids.set(d.__root, []);
      kids.get(d.__root).push(it);
    }
  });
  kids.forEach((list) => list.sort((a, b) => byCreated(dataOf(a), dataOf(b))));
  const out = [];
  items.forEach((it) => {
    const d = dataOf(it);
    if (d?.__depth === 1 && ids.has(d.__root)) return;
    out.push(it);
    (kids.get(d?.run_id) ?? []).forEach((k) => out.push(k));
  });
  return out;
}
