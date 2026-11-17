// options-truth L3 G3 (R1, R2): the per-run inputs every estimate surface
// reads (drawer footer, Add card, Review headline and run lines, Details,
// tooltip), from the same values the create path sends (S5). Pure.
import { decisionModelOf } from "src/sections/common/DecisionJudges/decisionModels";
import {
  latencyOf,
  modelPriceOf,
} from "src/sections/common/RunSources/declaredUses";
import { JUDGE_COST_PER_CALL } from "src/sections/common/Analysis/runStats";

/**
 * The tries a card carries itself (S5): a voice card's "Repeat each call",
 * a code or harness card's "Tries per task" (`r4.limits.tries`). Null for
 * runs that take the experiment's Trials (prompt, agent graph, endpoint).
 */
export function ownTriesOf(card) {
  const r4 = card?.r4;
  // L5 G3: an endpoint, A2A or MCP card's own `limits.tries`.
  if (!r4) return Number(card?.limits?.tries) || null;
  if (r4.kind === "voice")
    return Number(r4.slot?.voice?.tries) || Number(r4.limits?.tries) || null;
  return Number(r4.limits?.tries) || null;
}

/** The tries one run plays: its own, else the experiment's Trials. */
export function runTriesOf(card, trials) {
  return ownTriesOf(card) ?? Math.max(1, Math.round(Number(trials) || 1));
}

/**
 * `Tries per run: #9A 3 · #9B 2` for the runs that carry their own tries,
 * null when none does.
 * @param {object[]} cards  promptConfig
 * @param {{letter, configIndex}[]} runs  flattenRuns(cards)
 */
export function ownTriesLine(cards, runs, { ordinal } = {}) {
  const parts = (runs ?? [])
    .map((run) => {
      const t = ownTriesOf(cards?.[run.configIndex]);
      if (!t) return null;
      const label = ordinal != null ? `#${ordinal}${run.letter}` : run.letter;
      return `${label} ${t}`;
    })
    .filter(Boolean);
  return parts.length ? `Tries per run: ${parts.join(" · ")}` : null;
}

/**
 * At a time a run plays (S5): the card's own number (already capped by the
 * agent it serves), never above the experiment's "At most N at once" and,
 * in a sandbox, never above min(2 × vCPU, 10). Runs without their own
 * number play at the experiment's number.
 * @returns {{value: number|null, reason: string|null}}
 */
export function resolvedAtOnce(card, { maxConcurrency, sandboxMax } = {}) {
  const r4 = card?.r4 ?? null;
  const own =
    r4?.kind === "voice"
      ? Number(r4?.slot?.voice?.concurrency) || null
      : Number(r4?.limits?.concurrency) || null;
  const exp = Number(maxConcurrency) || null;
  const caps = [
    exp != null
      ? { v: exp, why: `the experiment runs at most ${exp} at once` }
      : null,
    sandboxMax
      ? {
          v: sandboxMax,
          why: `the sandbox runs at most ${sandboxMax} at a time`,
        }
      : null,
  ].filter(Boolean);
  if (own == null) {
    const v = Math.min(...caps.map((c) => c.v));
    return { value: Number.isFinite(v) ? v : null, reason: null };
  }
  const binding = caps.filter((c) => c.v < own).sort((a, b) => a.v - b.v)[0];
  return binding
    ? { value: binding.v, reason: `${binding.why}; you set ${own}` }
    : { value: own, reason: null };
}

// A try's slow end: about 1.5 × its p50 (tail calls, tool retries).
export const P90_OVER_P50 = 1.5;

/**
 * v2.1 (tts:models, stt:models, cost:review): the media models whose unit
 * price in declaredUses.UNIT_PRICES is the run engine's own price (the
 * prototype's voiceWorld TTS_MODELS / STT_MODELS); l7c-g3-media.test.js
 * holds every entry equal to the engine. A TTS / STT model outside it is
 * not priced before it runs (no dollar figure), never priced from a table
 * the engine does not charge. Image is a hidden type (the capability contract).
 */
export const ENGINE_PRICED_MEDIA = Object.freeze(["tts-1-hd", "whisper-1"]);
const IMAGE_MODELS = /^(dall-e|gpt-image|gemini-2\.5-flash-image|imagen)/;
export const enginePricesMedia = (model) =>
  ENGINE_PRICED_MEDIA.includes(String(model ?? "").trim()) ||
  IMAGE_MODELS.test(String(model ?? ""));

// The run engine scores for about 3 s after its last try, at its playback
// speed of 20 (experiment-sim SCORING_LAG_S x runEngine PLAYBACK_SPEED):
// a minute on the run's own clock.
export const ENGINE_SCORING_S = 60;

/**
 * v2.1 (review:headline, review:concurrency): the minutes the run engine's
 * own ETA reads when the run starts: its tries x the engine's time per try
 * (engineTry ms) / tries at a time, plus the scoring minute. `[m, m]`.
 */
export function engineEtaMinutes(tries, atOnce, p50Ms) {
  const t = Math.max(0, Number(tries) || 0);
  const c = Math.max(1, Number(atOnce) || 1);
  const sec = (t * Math.max(0, Number(p50Ms) || 0)) / 1000 / c;
  const m = Math.max(1, Math.round((sec + ENGINE_SCORING_S) / 60));
  return [m, m];
}

/**
 * v2.1 (result:endpoint-estimate, review:worst-math): the tries a run with
 * a run-level cost cap plays: the engine judges try after try and stops
 * every remaining try once the next one's judge spend would pass the cap
 * (r4-sources-runs.sourceRecords). `judgeUsdPerTry` 0 = nothing stops it.
 */
export function triesUnderCap(tries, cap, judgeUsdPerTry) {
  const t = Math.max(0, Math.round(Number(tries) || 0));
  const c = Number(cap);
  const j = Number(judgeUsdPerTry) || 0;
  if (!(c > 0) || !(j > 0)) return t;
  return Math.max(0, Math.min(t, Math.floor((c + 1e-12) / j)));
}

/**
 * Seconds one try takes at a model, as `[p50, p90]` (S5: the model's
 * MODEL_LATENCY_MS, p50 → 1.5 × p50), or null when the model is unknown.
 * `scale` carries a kind's multiple of a model call (a harness task runs
 * many model turns) and sandbox vCPU effects.
 */
export function trySecondsAt(model, scale = 1) {
  const ms = latencyOf(model);
  if (!ms) return null;
  const p50 = (ms / 1000) * scale;
  return [p50, p50 * P90_OVER_P50];
}

/**
 * Minutes `[lo, hi]` for `attempts` tries at `atOnce`, each `[p50, p90]`
 * seconds (waves of `atOnce`).
 */
export function minutesFor(attempts, atOnce, [lo, hi]) {
  const a = Math.max(0, Number(attempts) || 0);
  const c = Math.max(1, Number(atOnce) || 1);
  const waves = Math.ceil(a / c);
  const m1 = Math.max(1, Math.round((waves * lo) / 60));
  const m2 = Math.max(m1, Math.round((waves * hi) / 60));
  return [m1, m2];
}

const DEFAULT_JUDGE_MODEL = "gpt-4o-mini";
/** Input tokens of one decision call (a row's state and one question). */
export const DECISION_CALL_INPUT_TOKENS = 500;
// A model's list price (MODEL_PRICES, then EXTRA_MODEL_PRICES) or, for a
// Custom model, its own stored prices: the order runEngine.priceOf reads.
const sumPrice = (m, customPrices = null) => {
  const p = modelPriceOf(m, customPrices);
  return p ? p[0] + p[1] : null;
};

/**
 * USD of one judge call at an eval's judge model: the default judge's
 * price (JUDGE_COST_PER_CALL at gpt-4o-mini) scaled by the model's price,
 * the same rule the run engine bills (runEngine.judgeOf). H-B1 (the interface contract): a
 * Custom model is priced by its own prices (`customPrices`,
 * customPricesOf of GET /model-hub/custom-models/), as the engine prices
 * it from the same rows.
 * @param {string|null} judgeModel
 * @param {{customPrices?: Object<string, number[]>|null}} [opts]
 */
export function judgeCallUsd(judgeModel, { customPrices = null } = {}) {
  // the interface contract: a decision model bills its input tokens at decision:<model>
  // per-token pricing (no output); a call reads about the row's state and
  // one question.
  const dm = decisionModelOf(judgeModel);
  if (dm?.inputPerMTok != null)
    return (DECISION_CALL_INPUT_TOKENS * dm.inputPerMTok) / 1e6;
  const base = sumPrice(DEFAULT_JUDGE_MODEL);
  const own = judgeModel ? sumPrice(judgeModel, customPrices) : null;
  if (!own || !base) return JUDGE_COST_PER_CALL;
  return (JUDGE_COST_PER_CALL * own) / base;
}

/**
 * the interface contract: whether an eval makes a judge call per try (the one rule the
 * estimator counts and the run engine bills): a check or a code eval runs
 * no judge; every other eval calls its judge, the default judge when it
 * names none.
 */
export function billsJudgeCall(e) {
  // L7b cost:review: a wizard card carries its template (`templateDetails`,
  // the eval list item: eval_type llm | code | agent) and a task check its
  // `check:` key, so the estimate counts them as the engine bills them.
  const type = String(
    e?.type ??
      e?.eval_type ??
      e?.evalType ??
      e?.template?.eval_type ??
      e?.templateDetails?.eval_type ??
      e?.evalTemplate?.eval_type ??
      "",
  );
  const key = String(
    e?.key ?? e?.templateDetails?.key ?? e?.evalTemplate?.key ?? "",
  );
  if (/^check:/.test(key)) return false;
  // v2.1 (cost:review): Task success is the task's checks, billed as the
  // engine bills it (runEngine.evalBillTypeOf: `eval:task_success` = check).
  const template = String(
    e?.template_name ??
      e?.templateName ??
      e?.evalTemplateName ??
      e?.templateDetails?.template_name ??
      e?.templateDetails?.name ??
      e?.evalTemplate?.template_name ??
      "",
  );
  if (/^(eval:)?task_success$/i.test(key) || /^task_success$/i.test(template))
    return false;
  return !/check/i.test(type) && !/^code$/i.test(type);
}

/**
 * The judge model an eval entry of the form names, or null (L5 G3: from its
 * definition: the entry's judge, its picked model — the create body's
 * `model`, which the run bills its judge calls at — its config's model,
 * then its template's model).
 */
const modelName = (v) =>
  typeof v === "string" ? v.trim() || null : v?.value ?? v?.name ?? null;
export const judgeModelOf = (e) =>
  [
    e?.judge_model,
    e?.judgeModel,
    e?.model,
    e?.config?.judge_model,
    e?.config?.model,
    e?.config?.config?.judge_model,
    e?.template?.model,
    e?.templateModel,
  ]
    .map(modelName)
    .find(Boolean) ?? null;

/**
 * Replay modes of a conversations dataset (the run engine's
 * runEngineProfiles.REPLAY_MODES, mirrored: frontend code never imports
 * the mock): the share of a full re-run's model calls each mode makes.
 */
export const REPLAY_COST_SHARE = Object.freeze({
  score_only: 0,
  failing_turn: 0.15,
  rollout: 0.45,
  full: 1,
});
export const REPLAY_LABEL = Object.freeze({
  score_only: "Score only",
  failing_turn: "Failing turn",
  rollout: "Rollout",
  full: "Full re-run",
});

/** `{share, label}` of the form's replay mode, or null without replay. */
export function replayOf(values) {
  const mode = values?.replay?.mode ?? values?.mode ?? null;
  if (!mode || !(mode in REPLAY_COST_SHARE)) return null;
  return { mode, share: REPLAY_COST_SHARE[mode], label: REPLAY_LABEL[mode] };
}

/**
 * Rows that will run (carry-over on: only the rows the source did not
 * run; a door's picked rows: those).
 */
export function rowsToRun({ rows, carry }) {
  const r = Math.max(0, Math.round(Number(rows) || 0));
  return carry ? Math.max(0, Number(carry.toRun) || 0) : r;
}

/**
 * options-truth L5 G3: the models an agent graph version's llm nodes use,
 * in graph order (version detail `nodes[].prompt_template.model`).
 */
export function nodeModelsOf(versionDetail) {
  return (versionDetail?.nodes ?? [])
    .map((n) => n?.prompt_template?.model ?? n?.model ?? null)
    .map((m) => (typeof m === "string" ? m : m?.name ?? m?.value ?? null))
    .filter(Boolean);
}

/** `{name: [in, out]}` USD per 1M of the org's custom models (stored). */
export function customPricesOf(customModels) {
  const out = {};
  (customModels ?? []).forEach((m) => {
    const name = m?.model_name ?? m?.user_model_id ?? m?.name;
    const i = Number(m?.input_token_cost ?? m?.input_cost_per_1m);
    const o = Number(m?.output_token_cost ?? m?.output_cost_per_1m);
    if (
      name &&
      m?.input_token_cost != null &&
      m?.output_token_cost != null &&
      Number.isFinite(i) &&
      Number.isFinite(o)
    )
      out[name] = [i, o];
  });
  return out;
}

/**
 * The cards with what the engine prices them by: an agent card gets its
 * version's node models; plus the custom models' prices (pure).
 * @returns {{promptConfig: object[], customPrices: object}}
 */
export function withEngineInputs(cards, { versions = [], customModels } = {}) {
  const byId = new Map((versions ?? []).map((v) => [v?.id, v]));
  const promptConfig = (cards ?? []).map((c) => {
    if (c?.targetType !== "agent" || !byId.has(c?.agentVersion)) return c;
    const models = nodeModelsOf(byId.get(c.agentVersion));
    return models.length ? { ...c, nodeModels: models } : c;
  });
  return { promptConfig, customPrices: customPricesOf(customModels) };
}

const ENDPOINT_TARGETS = new Set(["http", "a2a", "mcp_tool"]);

/**
 * L5 G3: the create entries with each endpoint, A2A or MCP card's own
 * limits (`limits: {tries, max_cost_usd}`), which the run engine plays.
 * Pure; entries and runs are in the same order.
 */
export function withEndpointLimits(entries, cards, runs) {
  return (entries ?? []).map((entry, i) => {
    if (!ENDPOINT_TARGETS.has(entry?.target_type)) return entry;
    const lim = cards?.[runs?.[i]?.configIndex]?.limits ?? null;
    const tries = Number(lim?.tries) || null;
    const cap = Number(lim?.maxCostUsd) || null;
    if (!tries && !cap) return entry;
    return {
      ...entry,
      limits: {
        ...(entry.limits ?? {}),
        ...(tries ? { tries } : {}),
        ...(cap ? { max_cost_usd: cap } : {}),
      },
    };
  });
}

// The text a message part holds (string, or `[{type: "text", text}]`).
const partText = (c) =>
  typeof c === "string"
    ? c
    : Array.isArray(c)
      ? c.map((x) => (typeof x === "string" ? x : x?.text ?? "")).join(" ")
      : "";
const cellOf = (row, id) => {
  const v = row?.values?.[id] ?? row?.[id];
  const raw =
    v && typeof v === "object" && "cell_value" in v ? v.cell_value : v;
  return raw == null ? "" : typeof raw === "string" ? raw : JSON.stringify(raw);
};

/**
 * L5 G3: what a TTS run is priced by: the mean characters of the text each
 * row's clip speaks (the card's non-system messages with `{{column}}`
 * filled from the sample rows, at most 600 as the run cuts it). Null
 * without a TTS card or sample rows. Pure.
 * @returns {{chars: number}|null}
 */
export function mediaUnitsOf({ cards, rows, columns } = {}) {
  const card = (cards ?? []).find((c) => c?.experimentType === "tts");
  const sample = rows ?? [];
  if (!card || !sample.length) return null;
  const byName = new Map((columns ?? []).map((c) => [c.name, c.id]));
  // v2.1: a variable reads its mapped column, as the run fills it
  // (mediaRuns.promptTextOf); else the column of its own name.
  const mapping = card.variableMapping ?? card.variable_mapping ?? {};
  const colOf = (v) => {
    const m = mapping[v];
    const c = m && typeof m === "object" ? m.column ?? m.value ?? null : m;
    return c || v;
  };
  const raw = (card.messages ?? [])
    .filter((m) => m?.role !== "system")
    .map((m) => partText(m.content))
    .join(" ")
    .trim();
  if (!raw) return null;
  const lengths = sample.map((row) =>
    Math.min(
      600,
      raw
        .replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, v) =>
          cellOf(row, byName.get(colOf(v)) ?? colOf(v)),
        )
        .trim().length,
    ),
  );
  const chars = lengths.reduce((a, b) => a + b, 0) / lengths.length;
  return chars > 0 ? { chars } : null;
}
