// W1-1 kept runs on the leaderboard (pure). Every rerun keeps its run, so a
// config can have several (#2A, #2A·2, …). The leaderboard lists the newest
// run of each config (R1 `latest_of_config`) with a "2 runs" chip; the chip
// shows that config's runs in place and the View switch shows every run.
// The URL keeps the choice: `every=all` or `every=<config>,<config>`.
import {
  formatRunLabel,
  parseRunLabel,
} from "src/sections/common/Runs/runLabel";
import {
  isLaterRunOf,
  nestWordsOf,
} from "src/sections/experiment-detail/iterate/lineageModel";

export const EVERY_ALL = "all";

/** The config a run belongs to (its experiment and letter). */
export const configKeyOf = (run) =>
  `${run?.experiment_id ?? ""}|${run?.letter ?? ""}`;

/** The `every` URL value read: {all, configs}. */
export function parseEvery(raw) {
  const parts = String(raw ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  return {
    all: parts.includes(EVERY_ALL),
    configs: new Set(parts.filter((x) => x !== EVERY_ALL)),
  };
}

/** The `every` URL value written (empty = only the newest runs). */
export function serializeEvery({ all = false, configs = new Set() } = {}) {
  if (all) return EVERY_ALL;
  return [...configs].join(",");
}

/** Show or hide one config's earlier runs. */
export function toggleConfig(raw, key) {
  const cur = parseEvery(raw);
  const configs = new Set(cur.configs);
  if (configs.has(key)) configs.delete(key);
  else configs.add(key);
  return serializeEvery({ all: cur.all, configs });
}

/** Whether a run's earlier runs are shown. */
export const isExpanded = (raw, run) => {
  const cur = parseEvery(raw);
  return cur.all || cur.configs.has(configKeyOf(run));
};

/**
 * The runs the leaderboard lists: the newest run of each config, plus every
 * run of a config whose chip is open, or every run with `every=all`.
 */
export function visibleRuns(runs = [], raw = "") {
  const cur = parseEvery(raw);
  if (cur.all) return runs;
  return runs.filter(
    (r) => r?.latest_of_config !== false || cur.configs.has(configKeyOf(r)),
  );
}

/**
 * L7c g5 (kept:label-search): the runs a search shows. The visible runs
 * that match, plus a kept run that is not the newest of its config when the
 * query names it by its label (`#2A·2`, `#2A.2`, `2a-2`), so a dotted-label
 * search finds a kept run at any position. `matches(run, q)` is runMatches.
 */
export function searchRuns(runs = [], raw = "", query = "", matches) {
  const shown = visibleRuns(runs, raw).filter((r) => matches(r, query));
  const typed = parseRunLabel(
    String(query ?? "")
      .trim()
      .replace(/^#/, "")
      .toUpperCase(),
  );
  if (!typed?.attempt) return shown;
  const seen = new Set(shown.map((r) => r.run_id));
  const kept = runs.filter((r) => {
    if (seen.has(r.run_id)) return false;
    const own = parseRunLabel(r?.label);
    return (
      own &&
      own.ordinal === typed.ordinal &&
      own.letter === typed.letter &&
      own.attempt === typed.attempt
    );
  });
  return [...shown, ...kept];
}

/** L7c g5 (kept:nesting-words): a later run of its config (`#2C·2`). */
export const isKeptRun = (run) => (Number(run?.attempt) || 1) > 1;

/**
 * L7c g5 (kept:nesting-words): a nested root's kept runs are every run
 * nested under it that is a later run of ITS config, whichever config
 * that is (`#2B·2` and `#2C·2` under root `#2B`), so a kept run of an
 * iteration is never counted as an iteration. `rows` are the grid's
 * lineage rows (`__root`, `__depth`); roots get `__kept`. Pure.
 */
export function withKeptCounts(nested, rows) {
  const kept = new Map();
  (rows ?? []).forEach((r) => {
    if (r?.__depth === 1 && r.__root && isKeptRun(r))
      kept.set(r.__root, (kept.get(r.__root) ?? 0) + 1);
  });
  return (nested ?? []).map((r) =>
    r?.__iterations ? { ...r, __kept: kept.get(r.run_id) ?? 0 } : r,
  );
}

/**
 * L7c g5 (kept:nesting-words): the words of a nested root that holds kept
 * runs and iterations. Kept runs are never called iterations (`2
 * iterations · 3 more runs`), and `latest iteration` names only an
 * iteration (a kept run is the `latest run`). Pure; `__iterations` counts
 * every nested run, `__kept` the kept ones.
 */
export function nestWordsOfRoot(root) {
  const base = nestWordsOf(root);
  const n = Number(root?.__iterations) || 0;
  const kept = Math.min(n, Number(root?.__kept) || 0);
  const iters = n - kept;
  const latest = root?.__latest;
  // L7c g5: any later run of a config is a kept run (`#2C·2` under root
  // `#2B` too), never `latest iteration`.
  const latestKept =
    Boolean(latest) && (isLaterRunOf(latest, root) || isKeptRun(latest));
  return {
    count:
      kept && iters
        ? `${iters} ${iters === 1 ? "iteration" : "iterations"} · ${kept} more ${kept === 1 ? "run" : "runs"}`
        : base.count,
    latest: latestKept
      ? "latest run"
      : base.latest === "latest run" && iters
        ? "latest iteration"
        : base.latest,
  };
}

/** The config's first label (`#2A` for `#2A·2`). */
export function configLabelOf(run) {
  const p = parseRunLabel(run?.label);
  return p ? formatRunLabel({ ordinal: p.ordinal, letter: p.letter }) : "";
}

/**
 * The chip of a config with more than one run: `2 runs`, tooltip
 * `Show every run of #2A` (or `Show only the latest run of #2A` when open).
 * null for a config with one run.
 */
export function runsChipOf(run, raw = "") {
  const n = Number(run?.attempts_count) || 1;
  if (n < 2) return null;
  const open = isExpanded(raw, run);
  const config = configLabelOf(run);
  return {
    text: `${n} runs`,
    tooltip: open
      ? `Show only the latest run of ${config}`
      : `Show every run of ${config}`,
    open,
    key: configKeyOf(run),
  };
}

/**
 * The Compare picker's groups: one per config ("#2A · <name> · 2 runs"),
 * newest run first ("#2A·2"). Configs keep their first appearance's order.
 */
export function compareGroups(runs = []) {
  const groups = new Map();
  runs.forEach((r) => {
    const key = configKeyOf(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  });
  return [...groups.entries()].map(([key, list]) => {
    const sorted = [...list].sort(
      (a, b) => (Number(b.attempt) || 1) - (Number(a.attempt) || 1),
    );
    const head = sorted[sorted.length - 1];
    const name = head?.name ?? head?.experiment_name ?? "";
    return {
      key,
      title: [
        configLabelOf(head),
        name,
        `${list.length} ${list.length === 1 ? "run" : "runs"}`,
      ]
        .filter(Boolean)
        .join(" · "),
      runs: sorted,
    };
  });
}
