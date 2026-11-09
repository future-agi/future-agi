import { formatExactMoney } from "src/sections/common/format/money";
// L7c g5 (identity:provenance-card): what "What this run used" copies as
// JSON and lists under Parameters. Pure; reads the canonical R1 run.
import { settingsOf } from "./provenanceModel";
import { aggOfReducer } from "src/sections/common/Analysis/trialReducers";

/**
 * The run's config plus what the run record says it ran on: the data scope
 * (dataset version, splits, rows, seeded sample), tries and at a time, the
 * eval versions it scored with, how each row's tries became its value
 * (`trial_reducer`, null on the run = mean) and the run's metadata.
 */
export function provenanceJsonOf(run) {
  const r = run ?? {};
  return {
    config: r.config ?? {},
    data: {
      dataset_version: r.dataset_version?.number ?? null,
      splits: (r.splits ?? []).map((s) => s?.name).filter(Boolean),
      rows: r.examples ?? r.progress?.total ?? null,
      sample: r.sample ?? null,
    },
    trials: r.trials ?? null,
    concurrency: r.concurrency ?? null,
    eval_versions: r.eval_versions ?? {},
    trial_reducer: r.trial_reducer ?? "mean",
    metadata: r.metadata ?? {},
  };
}

const MODEL_PARAMS = [
  ["temperature", "temperature"],
  ["max_tokens", "max_tokens"],
  ["top_p", "top_p"],
];

/**
 * Parameters a run without declared params ran with: its model params
 * (temperature, max_tokens, top_p) and its settings (tries, at a time,
 * cost cap, timeout), in the paramRows shape. Only what is set.
 */
export function settingsRows(config = {}) {
  const c = config ?? {};
  const m = c.model?.params ?? {};
  const s = settingsOf(c);
  const row = (name, value) => ({
    name,
    label: name,
    value: String(value),
    full: String(value),
    changed: false,
    defaultText: null,
    source: null,
  });
  const out = MODEL_PARAMS.filter(([k]) => m[k] != null).map(([k, name]) =>
    row(name, m[k]),
  );
  if (s.tries) out.push(row("tries", s.tries));
  if (s.concurrency) out.push(row("at_a_time", s.concurrency));
  if (s.max_cost_usd != null) out.push(row("cost_cap_usd", s.max_cost_usd));
  if (s.max_cost_per_try_usd != null)
    out.push(row("cost_per_try_usd", s.max_cost_per_try_usd));
  if (s.timeout_s) out.push(row("timeout_s", s.timeout_s));
  return out;
}

const settingRow = (name, value, label = name) => ({
  name,
  label,
  value: String(value),
  full: String(value),
  changed: false,
  defaultText: null,
  source: null,
});

/** Model params alone (temperature, max_tokens, top_p), as paramRows. */
export function modelParamRows(config = {}) {
  const m = (config ?? {}).model?.params ?? {};
  return MODEL_PARAMS.filter(([k]) => m[k] != null).map(([k, name]) =>
    settingRow(name, m[k]),
  );
}

/**
 * L7c g5 (identity:provenance-card): the card's `Run settings`: what the
 * run record says it ran with: tries and at a time (the config's limits,
 * else the run's own `trials` / `concurrency`), its caps and timeout, the
 * data scope (dataset version, splits, rows or seeded sample), the eval
 * versions it scored with and how each row's tries became its value. The
 * same facts provenanceJsonOf copies. Only what is known.
 */
export function runSettingsRows(run) {
  const r = run ?? {};
  const s = settingsOf(r.config ?? {});
  const tries = s.tries ?? (Number(r.trials) || null);
  const atOnce = s.concurrency ?? (Number(r.concurrency) || null);
  const usd = formatExactMoney;
  const out = [];
  if (tries) out.push(settingRow("tries", tries, "Tries"));
  if (atOnce) out.push(settingRow("at_a_time", atOnce, "At a time"));
  if (s.max_cost_usd != null)
    out.push(settingRow("cost_cap_usd", usd(s.max_cost_usd), "Cost cap"));
  if (s.max_cost_per_try_usd != null)
    out.push(
      settingRow("cost_per_try_usd", usd(s.max_cost_per_try_usd), "Per try"),
    );
  if (s.timeout_s)
    out.push(settingRow("timeout_s", `${s.timeout_s} s`, "Timeout"));
  const data = provenanceJsonOf(r).data;
  const scope = [
    data.dataset_version != null ? `v${data.dataset_version}` : null,
    data.splits.length ? data.splits.join(", ") : null,
    data.sample?.n
      ? `${data.sample.n} random rows (seed ${data.sample.seed ?? 0})`
      : data.rows != null
        ? `${data.rows} rows`
        : null,
  ].filter(Boolean);
  if (scope.length) out.push(settingRow("data", scope.join(" · "), "Data"));
  const versions = Object.entries(r.eval_versions ?? {})
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${String(k).replace(/^eval:/, "")} v${v}`);
  if (versions.length)
    out.push(settingRow("eval_versions", versions.join(", "), "Evals"));
  if (tries > 1)
    out.push(
      settingRow(
        "reducer",
        `${aggOfReducer(r.trial_reducer ?? "mean")} of ${tries} tries`,
        "Each row",
      ),
    );
  return out;
}
