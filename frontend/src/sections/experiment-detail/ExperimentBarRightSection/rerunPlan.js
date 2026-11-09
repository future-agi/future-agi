// IT-R3 G6 P2: what each Rerun menu item would do, from the runs' own matrix
// cells (R3 `shape=matrix`): rows that failed a check, rows that errored,
// the money and the wall time the last run took, and where each run
// executes. Pure; ExperimentRow renders the lines.
import { fmtCost, fmtCount } from "src/sections/common/format/fmt";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import {
  estimateText,
  rerunEstimate,
  runnerPays,
  sumEstimates,
} from "src/sections/common/Runs/rerunEstimate";

const isFail = (t) => t === 0 || t === false;
const isError = (t) => t === "error";
const trialsOf = (cell) =>
  Array.isArray(cell) ? cell : Array.isArray(cell?.trials) ? cell.trials : [];
const sum = (xs) =>
  (xs ?? []).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);

/**
 * Where one run executes: `{key, text, short}` — text `on alice@mbp-14` /
 * `in CI` / `in Future AGI`; short `alice@mbp-14` / `CI` / `this machine`.
 */
export function placeOf(run) {
  let where = null;
  try {
    where = run?.config ? provenanceOf(run)?.where ?? null : null;
  } catch {
    where = null;
  }
  const mode = where?.mode ?? null;
  if (mode === "ci") return { key: "ci", text: "in CI", short: "CI" };
  if (mode === "local") {
    const name = where.text ?? where.label ?? "this machine";
    const self = /^this machine$/i.test(name);
    // A harness sandbox names its runtime (`Docker`): runs go *in* it.
    const runtime = /^(docker|podman|colima)\b/i.test(name);
    return {
      key: `local:${name.toLowerCase()}`,
      text: self ? "on this machine" : runtime ? `in ${name}` : `on ${name}`,
      short: self ? "this machine" : name,
    };
  }
  if (mode === "sandbox")
    return { key: "sandbox", text: "in a sandbox", short: "sandbox" };
  if (mode === "remote") {
    // L7c g5 (after:runs-where-line): a remote run names its host, else
    // `your endpoint`; never the mode word `Remote endpoint`.
    const host =
      where.text && where.text !== "Remote endpoint" ? where.text : null;
    return {
      key: `remote:${host ?? "endpoint"}`,
      text: `on ${host ?? "your endpoint"}`,
      short: host ?? "your endpoint",
    };
  }
  return { key: "platform", text: "in Future AGI", short: "Future AGI" };
}

/**
 * IT-R3 G6 P2: the tab row's runs line: `3 runs · this machine` (one place)
 * or `3 runs · 2 on alice@mbp-14, 1 in CI`; null for fewer than 2 runs.
 */
export function runsWhereText(runs) {
  const list = runs ?? [];
  if (list.length < 2) return null;
  const places = new Map();
  list.forEach((r) => {
    const p = placeOf(r);
    const g = places.get(p.key) ?? { ...p, n: 0 };
    g.n += 1;
    places.set(p.key, g);
  });
  const groups = [...places.values()];
  const where =
    groups.length === 1
      ? groups[0].short
      : groups
          .sort((a, b) => b.n - a.n)
          .map((g) => `${g.n} ${g.text}`)
          .join(", ");
  return `${fmtCount(list.length, "run")} · ${where}`;
}

/** `3 on alice@mbp-14, 1 in CI` (one group: `3 runs on alice@mbp-14`). */
export function placesText(runs) {
  const groups = new Map();
  (runs ?? []).forEach((r) => {
    const p = placeOf(r);
    const g = groups.get(p.key) ?? { text: p.text, n: 0 };
    g.n += 1;
    groups.set(p.key, g);
  });
  const list = [...groups.values()];
  if (!list.length) return null;
  if (list.length === 1) return `${fmtCount(list[0].n, "run")} ${list[0].text}`;
  return list
    .sort((a, b) => b.n - a.n)
    .map((g) => `${g.n} ${g.text}`)
    .join(", ");
}

/** `about 6 min` / `under a minute` / `about 1 h 10 min`. */
export function aboutTime(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const min = Math.round(ms / 60000);
  if (min < 1) return "under a minute";
  if (min < 60) return `about ${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `about ${h} h${m ? ` ${m} min` : ""}`;
}

/** The per-try cost cap a run's config sets (USD), or null (pure). */
export function perTryCapOf(config = {}) {
  const v =
    config?.limits?.max_cost_per_try_usd ??
    config?.harness?.max_cost_usd ??
    config?.harness?.budget?.max_cost_usd ??
    null;
  const n = Number(v);
  return v != null && v !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Per run: rows that failed a check (a scored try that did not pass), rows
 * that errored, and the cost / wall time of everything vs of those rows.
 * @param {object} matrix  R3 matrix `{rows: [{row_id, cells}]}`
 * @param {object[]} runs  experiment runs (`run_id`, `label`, `concurrency`,
 *   `config` when known)
 */
export function rerunPlan(matrix, runs) {
  const rows = matrix?.rows ?? [];
  const perRun = (runs ?? []).map((run) => {
    const id = run.run_id ?? run.id;
    let failed = 0;
    let errored = 0;
    let cost = 0;
    let wall = 0;
    let failedCost = 0;
    let failedWall = 0;
    let attempts = 0;
    let rowsRan = 0;
    let maxTrials = 0;
    rows.forEach((row) => {
      const cell = row.cells?.[id];
      if (!cell) return;
      const trials = trialsOf(cell);
      rowsRan += 1;
      attempts += trials.length;
      maxTrials = Math.max(maxTrials, trials.length);
      const c = sum(cell.cost_usd);
      const w = sum(cell.wall_ms);
      cost += c;
      wall += w;
      const f = trials.some(isFail);
      const e = !f && trials.some(isError);
      if (f) failed += 1;
      if (e) errored += 1;
      if (f || e) {
        failedCost += c;
        failedWall += w;
      }
    });
    const at = Math.max(
      1,
      Number(run.concurrency ?? run.config?.execution?.concurrency) || 1,
    );
    // IT-R5 G6 P5: the one estimator (rerunEstimate), from this run's own
    // cost and wall time per try; the dialog reads the same numbers.
    const perTry = {
      costPerTry:
        attempts > 0 && cost > 0
          ? cost / attempts
          : run.metrics?.cost_per_attempt ?? null,
      msPerTry: attempts > 0 && wall > 0 ? wall / attempts : null,
      trials: Number(run.trials) || maxTrials || 1,
      concurrency: at,
      runnerPaid: runnerPays(run),
      // options-truth L5 G2: a try never costs more than the run's cap.
      maxCostPerTry: perTryCapOf(run.config),
    };
    return {
      id,
      perTry,
      failedEstimate: rerunEstimate({ rows: failed + errored, ...perTry }),
      allEstimate: rerunEstimate({ rows: rowsRan, ...perTry }),
      label: run.label,
      run,
      failed,
      errored,
      cost,
      failedCost,
      // Tries run `concurrency` at a time.
      ms: wall / at,
      failedMs: failedWall / at,
    };
  });
  // Runs execute side by side: the experiment takes as long as its slowest run.
  const ms = Math.max(0, ...perRun.map((r) => r.ms));
  const withFails = perRun.filter((r) => r.failed + r.errored > 0);
  return {
    perRun,
    all: {
      cost: sum(perRun.map((r) => r.cost)),
      ms,
      places: placesText(runs),
      estimate: sumEstimates(perRun.map((r) => r.allEstimate)),
    },
    failed: {
      rows: sum(perRun.map((r) => r.failed)),
      errored: sum(perRun.map((r) => r.errored)),
      runs: withFails,
      cost: sum(withFails.map((r) => r.failedCost)),
      ms: Math.max(0, ...withFails.map((r) => r.failedMs)),
      places: placesText(withFails.map((r) => r.run)),
      estimate: sumEstimates(withFails.map((r) => r.failedEstimate)),
    },
  };
}

/** `≈ $0.57 · about 6 min · 3 on alice@mbp-14, 1 in CI` (unknown parts drop). */
export function planLine({ cost, ms, places }) {
  return [
    Number.isFinite(cost) && cost > 0 ? `≈ ${fmtCost(cost)}` : null,
    aboutTime(ms),
    places,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The `Failed tasks (49)` item (IT-R5 G6 P5, one vocabulary with the
 * Rerun-with-changes dialog): the count is rows that failed a check plus
 * rows that errored; the line is the one estimate
 * (`49 tasks · 147 tries · ~$1.79 · ~2 min`), then per run when more than
 * one (`#2A 17 · #2B 13`) and the errored count when any.
 */
export function failedItemText(plan, noun = "row") {
  const f = plan?.failed ?? { rows: 0, errored: 0, runs: [] };
  const runs = f.runs ?? [];
  const total = f.rows + f.errored;
  if (!total)
    return {
      enabled: false,
      primary: `Failed ${noun}s only`,
      secondary: `Not available: no ${noun} failed a check · errored ${noun}s: 0`,
    };
  return {
    enabled: true,
    primary: `Failed ${noun}s (${total})`,
    secondary: [
      f.estimate ? estimateText(f.estimate, noun) : fmtCount(total, noun),
      runs.length > 1
        ? runs.map((r) => `${r.label} ${r.failed + r.errored}`).join(" · ")
        : null,
      f.errored ? `${f.errored} errored` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  };
}

const stable = (v) => {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v)
      .sort()
      .filter((k) => v[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${stable(v[k])}`)
      .join(",")}}`;
  return JSON.stringify(v ?? null);
};

/**
 * L3 G9 (fix 8): a short hash of what a run runs (its model, params,
 * tools, MCP servers, harness, agent, prompt, voice, code and limits), so
 * the Rerun menu can tell a config that changed after its last full run
 * (pure; the mock records it after a rerun in place).
 */
export function configHashOf(config = {}) {
  const c = config ?? {};
  const pick = {
    kind: c.kind,
    model: c.model,
    params: c.params?.values,
    tools: c.tools,
    mcp_servers: c.mcp_servers,
    max_tool_turns: c.max_tool_turns,
    harness: c.harness,
    agent: c.agent,
    prompt: c.prompt,
    voice: c.voice,
    code: c.code ? { entry: c.code.entry, commit: c.code.commit } : undefined,
    limits: c.limits,
  };
  const text = stable(pick);
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

/**
 * L3 G9 (fix 8): did this run already run every row on its current config?
 * True after a rerun in place on that config (`iteration.ran_config_hash`)
 * or when it carried no rows from its parent; unknown (no iteration
 * record) counts as not yet (pure).
 */
export function ranOnItsConfig(run) {
  const it = run?.iteration;
  if (!it) return false;
  if (it.ran_config_hash)
    return it.ran_config_hash === configHashOf(run.config);
  return it.rows_carried === 0;
}

/**
 * L2 G9: `Changed configs only` (pure). The runs of this experiment whose
 * config differs from the run they came from (`parent_run_id`), by the
 * compare pages' own diff (`diff(a, b)` → chips). Disabled with the true
 * reason when none differs.
 * @param {object[]} runs  this experiment's runs ({run_id, label, config, parent_run_id})
 * @param {object[]} all   every run the parents may be in (same shape)
 * @param {(a: object, b: object) => object[]} diff
 */
export function changedConfigItem(runs = [], all = [], diff = () => []) {
  const byId = new Map([...all, ...runs].map((r) => [r.run_id, r]));
  const differs = runs
    .map((r) => {
      const parent = r.parent_run_id ? byId.get(r.parent_run_id) : null;
      if (!parent?.config || !r.config) return null;
      const chips = diff(parent.config, r.config) ?? [];
      return chips.length ? { run: r, parent, chips } : null;
    })
    .filter(Boolean);
  // L3 G9 (fix 8): a run that already ran every row on its own config (no
  // carried rows, or rerun in place on this config) has nothing to rerun.
  const ran = differs.filter((c) => ranOnItsConfig(c.run));
  const changed = differs.filter((c) => !ranOnItsConfig(c.run));
  if (!changed.length)
    return {
      enabled: false,
      runs: [],
      primary: "Changed configs only",
      secondary: ran.length
        ? `${ran.map((c) => c.run.label).join(", ")} already ran on ${ran.length === 1 ? "its config" : "their configs"}`
        : runs.some((r) => r.parent_run_id)
          ? "No run's config changed since its last run"
          : "No run here came from another run, so no config changed",
    };
  const labels = changed.map((c) => c.run.label).join(", ");
  return {
    enabled: true,
    runs: changed.map((c) => c.run),
    primary: `Rerun ${fmtCount(changed.length, "run")} whose config changed (${labels})`,
    secondary: changed
      .map(
        (c) =>
          `${c.run.label} vs ${c.parent.label}: ${c.chips
            .slice(0, 2)
            .map((x) => x.text ?? x.label)
            .join(" · ")}`,
      )
      .join(" · "),
  };
}
