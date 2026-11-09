// Cost before you run (R2-SPEC F10): the Review step's cost line and the
// `Start from` carry-over line. Pure: form values + R1 runs in, sentences
// out. The numbers come from runStats.estimateRun (one estimator for every
// surface); this module only picks its inputs and words the result.
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";
import {
  JUDGE_COST_PER_CALL,
  estimateRun,
} from "src/sections/common/Analysis/runStats";
import {
  TOKENS_PER_TRY,
  costPerTry,
  engineTry,
  latencyOf,
  modelPriceOf,
  unitCostOf,
} from "src/sections/common/RunSources/declaredUses";
import { cardModelParams } from "./tryOnOneRowModel";
import { mediaRunsOf } from "./StepperComponentExperiment/mediaRuns";
import { flattenRuns, targetTypeOf } from "./runPlan";
import { capText } from "./reviewModel";
import { formatUsd } from "./tryOnOneRowModel";
import { fmtCost } from "src/sections/common/format/fmt";
import { defaultRecipe } from "src/sections/common/RunSources/recipe/recipeModel";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import {
  billsJudgeCall,
  judgeCallUsd,
  judgeModelOf,
  ENGINE_PRICED_MEDIA,
  engineEtaMinutes,
  enginePricesMedia,
  minutesFor,
  ownTriesOf,
  resolvedAtOnce,
  runTriesOf,
  triesUnderCap,
  trySecondsAt,
} from "./estimateInputs";
import { estimateJudgeModelsOf } from "src/sections/common/EvalTypes/typedEvalTypes";
import { isSurfaceShown } from "src/launch/launchSwitch";

// The judge an LLM-judge eval uses when it names none (judgeCallUsd).
const DEFAULT_JUDGE = "gpt-4o-mini";
// Runs that bill nothing in Future AGI (your side runs the model).
const FREE_KINDS = new Set(["code", "traces", "external"]);
// Runs Future AGI calls at your side (an endpoint, an A2A agent, an MCP
// server): your side bills them, Future AGI only waits for the answer.
const ENDPOINT_KINDS = new Set(["http", "a2a", "mcp_tool"]);
const ENDPOINT_WORD = {
  http: "your endpoint",
  a2a: "your A2A agent",
  mcp_tool: "your MCP server",
};

/** Same-setup key of an R1 run (from its config snapshot). */
export function targetKeyOfRun(run) {
  const c = run?.config ?? {};
  const kind = c.kind ?? run?.kind ?? run?.target_type;
  if (kind === "prompt") {
    // L5 G3: a run with its own max tokens is a different setup.
    const max =
      c.model?.params?.max_tokens ??
      c.model?.params?.maxTokens ??
      run?.target?.model_params?.max_tokens ??
      null;
    return `prompt|${c.prompt?.id ?? run?.target?.prompt_id ?? ""}|${c.model?.id ?? run?.target?.model ?? ""}${max != null ? `|max=${max}` : ""}`;
  }
  if (kind === "agent") {
    // options-truth L3 G3: v2 and v3 of a graph estimate apart.
    const v = c.agent?.version_name ?? run?.target?.version_name ?? null;
    return `agent|${c.agent?.graph_id ?? run?.target?.graph_id ?? ""}${v ? `|${v}` : ""}`;
  }
  if (kind === "harness")
    return `harness|${c.harness?.name ?? ""}|${c.engine?.model ?? c.harness?.model ?? ""}`;
  return String(kind ?? "");
}

/** Same-setup key of one run of a form card. */
export function targetKeyOfCard(card, model) {
  const type = targetTypeOf(card);
  if (type === "prompt") {
    const p = cardModelParams(card, model);
    const max = p.maxTokens ?? p.max_tokens ?? null;
    return `prompt|${card?.promptId ?? ""}|${model ?? ""}${max != null && max !== "" ? `|max=${max}` : ""}`;
  }
  if (type === "agent") {
    const v = card?.versionLabel ?? card?.versionName ?? null;
    return `agent|${card?.agentId ?? ""}${v ? `|${v}` : ""}`;
  }
  if (type === "harness") {
    const name =
      card?.r4?.config?.harness?.name ??
      card?.harness_config?.name ??
      card?.r4?.entryName ??
      "";
    return `harness|${name}|${harnessModelOf(card) ?? card?.harness_config?.model ?? ""}`;
  }
  return type;
}

/** estimateRun history from R1 runs: the last finished run per setup. */
export function historyFromRuns(runs) {
  return (runs ?? [])
    .filter((r) => /completed/i.test(String(r?.status ?? "")))
    .map((r) => {
      const agg = r?.metrics?.aggregates ?? {};
      return {
        target_key: targetKeyOfRun(r),
        cost_per_attempt: agg["ops:cost"]?.avg,
        p50_ms: agg["ops:latency"]?.p50 ?? agg["ops:latency"]?.avg,
        completed_at: r.stopped_at ?? r.created_at ?? "",
        // IT-R2 G1 P6: which run the number came from, and its tokens.
        label: r.label ?? null,
        tokens_per_attempt: agg["ops:tokens"]?.avg ?? null,
      };
    })
    .filter((h) => Number.isFinite(h.cost_per_attempt));
}

/**
 * estimateRun targets of the form's runs (one per run, card order). A run
 * with its own tries (code, harness, voice) carries them (S5).
 */
/**
 * options-truth L5 G3 (one estimator): one try of a prompt or agent graph
 * run priced and timed by the run engine's own model (declaredUses
 * engineTry: the kind's tokens at the model's prices, completion tokens
 * never above the card's max tokens, an agent's llm nodes each at their own
 * model; a custom model at its stored per-1M costs). Null for other kinds.
 */
export function engineTargetOf(card, type, model, { customPrices } = {}) {
  if (type === "prompt") {
    const params = cardModelParams(card, model);
    const maxTokens =
      params.maxTokens ??
      params.max_tokens ??
      params.maxCompletionTokens ??
      params.maxOutputTokens ??
      null;
    return engineTry({ kind: "prompt", model, maxTokens, customPrices });
  }
  if (type === "agent")
    return engineTry({
      kind: "agent",
      model: card?.agentModel ?? null,
      nodeModels: card?.nodeModels ?? null,
      customPrices,
    });
  return null;
}

/**
 * The runs of a TTS / STT / Image form (one per model, TTS one per model
 * and voice), each priced per output at its model's unit price: a clip of
 * the rows' mean characters, a transcript of the rows' mean minutes, one
 * image (options-truth L5 G3).
 */
export function mediaTargetsOf(promptConfig, { mediaUnits } = {}) {
  const card = (promptConfig ?? []).find((c) =>
    ["tts", "stt", "image"].includes(String(c?.experimentType ?? "")),
  );
  if (!card) return null;
  const type = String(card.experimentType);
  return mediaRunsOf(type, promptConfig).map((r) => {
    // v2.1 (tts:models, stt:models): a media try is priced only where the
    // estimator's unit price is the run engine's own (enginePricesMedia);
    // any other media model shows no dollar figure before it runs, and
    // adds nothing to any total (kind "code": never a guessed fallback).
    const usd = enginePricesMedia(r.model)
      ? unitCostOf(r.model, mediaUnits ?? {})
      : null;
    const needs =
      type === "tts"
        ? mediaUnits?.chars != null
        : type === "stt"
          ? mediaUnits?.minutes != null
          : true;
    const priced = usd != null && needs;
    return {
      key: `${type}|${r.model}|${r.voice ?? ""}`,
      kind: priced ? type : "code",
      type,
      model: r.model,
      name: r.name,
      letter: r.letter,
      priced,
      ...(priced ? { cost_per_attempt: usd } : {}),
    };
  });
}

export function targetsOf(promptConfig, opts = {}) {
  const cards = promptConfig ?? [];
  const media = mediaTargetsOf(cards, opts);
  if (media) return media;
  return flattenRuns(cards).map((r) => {
    const card = cards[r.configIndex];
    const type = r.targetType;
    const model =
      (type === "harness" ? harnessModelOf(card) : null) ??
      r.model ??
      card?.harness_config?.model ??
      null;
    // options-truth L2 R3(d): a harness try is priced at its chosen model
    // (S5) when no finished run of the same setup says otherwise.
    const listPerTry = type === "harness" ? costPerTry(model) : null;
    const own = ownTriesOf(card);
    // L5 G3: prompt and agent tries at the engine's cost and time model
    // (an earlier finished run of the same setup still wins in estimateRun).
    const eng = engineTargetOf(card, type, r.model, opts);
    return {
      key: targetKeyOfCard(card, r.model),
      kind: FREE_KINDS.has(type) || ENDPOINT_KINDS.has(type) ? "code" : type,
      type,
      model,
      ...(own ? { tries: own } : {}),
      ...(listPerTry != null ? { cost_per_attempt: listPerTry } : {}),
      // v2.1 (review:headline): the engine's own time per try, so Review's
      // minutes are the engine's ETA (engineEtaMinutes).
      ...(eng ? { cost_per_attempt: eng.usd, p50_ms: eng.ms } : {}),
    };
  });
}

/**
 * LLM-judge evals of the form (Checks are free), each priced at its judge
 * model (options-truth L3 G3; the default judge when none is named).
 */
export function judgesOf(evals) {
  return (evals ?? []).filter(billsJudgeCall).flatMap((e) =>
    // integration S13: a typed eval makes its own judge calls a row, each at its
    // judge (a jury's seats, a decision tree's questions; a remote none).
    (estimateJudgeModelsOf(e) ?? [null]).map((m) => ({
      type: "judge",
      // L5 G3: the eval's own judge, else the default judge (gpt-4o-mini).
      judge_model: m ?? judgeModelOf(e) ?? DEFAULT_JUDGE,
      cost_per_call: judgeCallUsd(m ?? judgeModelOf(e)),
    })),
  );
}

// The slow end of a duration range: a try's p90 is about 1.5× its p50 for
// model-bound agents (tail calls, tool retries), so the range is p50 → p90.
const P90_OVER_P50 = 1.5;

/**
 * How long a run takes (options-truth L1 C10; G1 imports it so the Review
 * estimate and the simulated run agree). Tries run in waves of
 * `concurrency`; each wave takes about one p50 latency.
 * @param {{rows: number, tries?: number, concurrency?: number, p50Ms: number}} p
 * @returns {{seconds: number, minutes: [number, number]}}
 */
export function durationEstimate({ rows, tries = 1, concurrency = 1, p50Ms }) {
  const attempts =
    Math.max(0, Number(rows) || 0) * Math.max(1, Number(tries) || 1);
  const c = Math.max(1, Number(concurrency) || 1);
  const waves = Math.ceil(attempts / c);
  const seconds = Math.round((waves * Math.max(0, Number(p50Ms) || 0)) / 1000);
  const lo = Math.max(1, Math.round(seconds / 60));
  const hi = Math.max(lo, Math.round((seconds * P90_OVER_P50) / 60));
  return { seconds, minutes: [lo, hi] };
}

/** `about 1 min` · `about 4 min` (IT-R2 G1 P6: never seconds). */
export function aboutTime(seconds) {
  const s = Number(seconds) || 0;
  return `about ${Math.max(1, Math.round(s / 60))} min`;
}

const n = (x) => Number(x).toLocaleString("en-US");
/** `1 try` · `60 tries` (v2.1 data:rows:these grammar). */
export const triesText = (x) => `${n(x)} ${Number(x) === 1 ? "try" : "tries"}`;
const MEDIA_KINDS = new Set(["tts", "stt", "image"]);
const MEDIA_WORD = { tts: "clips", stt: "transcripts", image: "images" };
const callsWord = (targets) => {
  const kinds = [...new Set(targets.map((t) => t.type))];
  if (kinds.length === 1 && MEDIA_WORD[kinds[0]]) return MEDIA_WORD[kinds[0]];
  if (kinds.length === 1 && kinds[0] === "agent") return "agent calls";
  if (kinds.length === 1 && kinds[0] === "prompt") return "prompt calls";
  return "attempts";
};

/**
 * The cost line above `Run experiment`:
 * `162 agent calls (54 rows × 3 trials) · ≈ $0.16 · judges 324 calls ·
 * ≈ $0.07 · about 1 min`; a run on your side priced at its model reads
 * `16 attempts (8 rows × 2 tries) · ≈ $0.47 at gpt-4.1 prices (billed by
 * your provider) · judges 32 calls · ≈ $0.019 · about 1–3 min`, the same
 * total and breakdown as the Review headline (runEstimateLines).
 * Returns `{text, total, rough, overBudget, endpointOnly, estimate}` or
 * null when nothing will run.
 */
export function costLine({
  promptConfig,
  rows,
  trials,
  evals,
  history,
  maxConcurrency = null,
  replay = null,
  customPrices = null,
  mediaUnits = null,
}) {
  const targets = targetsOf(promptConfig, { customPrices, mediaUnits });
  const r = Math.max(0, Math.round(Number(rows) || 0));
  const t = Math.max(1, Math.round(Number(trials) || 1));
  if (!targets.length || !r) return null;
  // v2.1 (review:headline, one estimator): a prompt or agent try is priced
  // by the run engine's own model, the price the create stamps and the
  // engine charges; an earlier run's average does not override it.
  const priced = enginePricedHistory(history, targets);
  // A TTS / STT / Image run plays its Trials too (one output per try).
  const est = estimateRun({
    rows: r,
    trials: t,
    targets,
    evals: judgesOf(evals),
    history: priced,
  });
  // One story for every surface (IT-R2 G1 P6, options-truth L3 G3).
  const story = runEstimateLines({
    cards: promptConfig,
    rows: r,
    trials: t,
    estimate: est,
    history: priced,
    maxConcurrency,
    replay,
    customPrices,
    mediaUnits,
  });
  const lines = story?.lines ?? [];
  const endpointType = targets.find((x) => ENDPOINT_KINDS.has(x.type))?.type;
  const http = Boolean(endpointType);
  const runsPart = targets.length > 1 ? ` × ${targets.length} runs` : "";
  // Tries per row: one number, or each run's own (`3 / 2 tries`).
  const triesEach = [...new Set(lines.map((l) => l.tries ?? t))];
  const triesWord = (x, many) =>
    `${n(x)} ${x === 1 ? "trial" : many ? "tries" : "trials"}`;
  const triesPart =
    triesEach.length > 1
      ? `${triesEach.map(n).join(" / ")} tries`
      : triesWord(
          triesEach[0] ?? t,
          lines.some((l) => l.group !== "platform"),
        );
  const parts = [
    `${n(est.calls)} ${callsWord(targets)} (${n(r)} rows × ${triesPart}${runsPart})`,
  ];
  const platform = lines.filter(
    (l) =>
      l.group === "platform" || l.group === "harness" || l.group === "voice",
  );
  const provider = lines.filter((l) => l.group === "free" && l.billedBy);
  const unpriced = lines.filter((l) => l.group === "free" && !l.billedBy);
  if (platform.length) {
    const usd = platform.reduce((s, l) => s + (Number(l.usd) || 0), 0);
    // v2.1 (tts:models, stt:models): a run with no price adds nothing and
    // the line says so; with none priced it shows no dollar figure.
    const left = platform.filter((l) => l.priced === false).length;
    parts.push(
      left === platform.length
        ? "~$— (not priced before it runs)"
        : `≈ ${formatUsd(usd)}${est.rough || story?.rough ? " (rough)" : ""}${left ? ` (without ${left} ${left === 1 ? "run" : "runs"} not priced)` : ""}`,
    );
  }
  // options-truth L3 G3: your side's model calls are priced, not hidden.
  const byModel = new Map();
  provider.forEach((l) =>
    byModel.set(l.model, (byModel.get(l.model) ?? 0) + (Number(l.usd) || 0)),
  );
  byModel.forEach((usd, model) =>
    parts.push(
      `≈ ${formatUsd(usd)} at ${model} prices (billed by your provider)`,
    ),
  );
  if (http) parts.push(`Cost depends on ${ENDPOINT_WORD[endpointType]}`);
  else if (!platform.length && !provider.length)
    parts.push("≈ $0 in Future AGI");
  if (est.judge_calls)
    parts.push(
      `judges ${n(est.judge_calls)} calls · ≈ ${formatUsd(story?.judgeUsd ?? est.judge_cost_usd)}`,
    );
  const timeText = story?.minutes
    ? minutesText(story.minutes)
    : aboutTime(est.seconds);
  parts.push(timeText);
  const notes = [];
  if ((platform.length || provider.length) && http)
    notes.push(`cost of ${ENDPOINT_WORD[endpointType]} not included`);
  if (unpriced.length)
    notes.push(
      unpriced.some((l) => l.model)
        ? `no list price for ${unpriced
            .map((l) => l.model)
            .filter(Boolean)
            .join(", ")}, so its calls are not included`
        : "runs without a priced model are not included",
    );
  const total = story ? story.expected : est.total_usd;
  return {
    text: parts.join(" · "),
    note: notes.join(" · "),
    total,
    rough:
      Boolean(est.rough || story?.rough) &&
      (platform.length > 0 || provider.length > 0),
    overBudget: total > 10,
    estimate: est,
    story,
    timeText,
  };
}

/**
 * v2.1: the history entries the estimator may use: none for a prompt or
 * agent target the run engine prices (estimateAtCreate and the engine use
 * that price), the rest as they are. Pure.
 */
export function enginePricedHistory(history = [], targets = []) {
  const engineKeys = new Set(
    (targets ?? [])
      .filter(
        (x) =>
          (x?.type === "prompt" || x?.type === "agent") &&
          x?.cost_per_attempt != null,
      )
      .map((x) => x.key),
  );
  return (history ?? []).filter((h) => !engineKeys.has(h?.target_key));
}

/**
 * The smallest split worth trying first when a run is over $10:
 * `Try the smoke split first: 8 rows · ≈ $0.42`. Null when none is smaller.
 */
export function smokeSuggestion({ splits, rows, ...rest }) {
  const smaller = (splits ?? [])
    .filter((s) => Number(s?.row_count) > 0 && Number(s.row_count) < rows)
    .sort(
      (a, b) =>
        (/smoke/i.test(b.name) ? 1 : 0) - (/smoke/i.test(a.name) ? 1 : 0) ||
        a.row_count - b.row_count,
    );
  const split = smaller[0];
  if (!split) return null;
  const line = costLine({ ...rest, rows: split.row_count });
  if (!line) return null;
  return {
    split,
    text: `Try the ${split.name} split first: ${n(split.row_count)} rows · ≈ ${formatUsd(line.total)}`,
  };
}

/**
 * `Start from` carry-over (R2-SPEC §5.3.1): rows the source experiment
 * already ran on an older version are carried; the rest run.
 * @returns {{carried, toRun, runs, attempts, usd, text, mineNote}|null}
 */
export function carryOverLine({
  source,
  versionNumber,
  rows,
  promptConfig,
  trials,
  history,
}) {
  if (!source?.experimentId || !(source.versionNumber < versionNumber))
    return null;
  const carried = Math.min(Number(source.rows) || 0, Number(rows) || 0);
  const toRun = Math.max(0, (Number(rows) || 0) - carried);
  const targets = targetsOf(promptConfig);
  if (!targets.length || !carried) return null;
  const t = Math.max(1, Math.round(Number(trials) || 1));
  // options-truth L3 G3: the carry line prices exactly what the Review
  // headline prices for the new rows (one story, per-run tries and caps).
  const line = toRun
    ? costLine({ promptConfig, rows: toRun, trials: t, evals: [], history })
    : null;
  const est = line?.estimate ?? { calls: 0, cost_usd: 0 };
  const lines = line?.story?.lines ?? [];
  const usdOf = (group) =>
    lines
      .filter((l) => group.includes(l.group))
      .reduce((sum, l) => sum + (Number(l.usd) || 0), 0);
  const inFagi = usdOf(["platform", "harness", "voice"]);
  const provider = usdOf(["free"]);
  const unpriced = lines.some((l) => l.group === "free" && !l.billedBy);
  const triesEach = [...new Set(lines.map((l) => l.tries ?? t))];
  const triesPart =
    triesEach.length > 1
      ? `${triesEach.map(n).join(" / ")} tries`
      : `${n(t)} ${t === 1 ? "trial" : "trials"}`;
  const label = source.label ?? "the source";
  const text =
    `Carry over ${n(carried)} unchanged rows from ${label} (same configs, same row text) · ` +
    `run ${n(toRun)} new rows × ${n(targets.length)} ${targets.length === 1 ? "run" : "runs"} × ${triesPart} = ${n(est.calls)} attempts · ` +
    `≈ ${formatUsd(inFagi)} in Future AGI` +
    (provider > 0
      ? ` · ≈ ${formatUsd(provider)} billed by your provider`
      : "") +
    (unpriced ? " · runs without a priced model are not included." : ".");
  return {
    carried,
    toRun,
    runs: targets.length,
    attempts: est.calls,
    usd: inFagi + provider,
    text,
  };
}

/**
 * The carry-over the form will apply (Start from on, an older source
 * version): one function for the footer, Details and Review, so the
 * headline prices only the new rows when carry is on.
 */
export function carryOf({ values, versions, exampleCount, history }) {
  if (values?.startFrom?.carryOver === false) return null;
  const latest = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const version =
    (versions ?? []).find((v) => v.id === values?.datasetVersionId)?.number ??
    latest;
  return carryOverLine({
    source: values?.startFrom,
    versionNumber: version,
    rows: exampleCount,
    promptConfig: values?.promptConfig,
    trials: values?.trials,
    history,
  });
}

// Who bills a judge model: Future AGI's own judges (turing_*), else the
// provider whose key serves it ("Your Models" in the eval's model picker).
const JUDGE_PROVIDERS = [
  [/^(gpt|o\d|chatgpt|text-|dall-e|whisper|tts-)/i, "OpenAI"],
  [/^claude/i, "Anthropic"],
  [/^(gemini|imagen|palm)/i, "Google"],
  [/^(mistral|mixtral|codestral)/i, "Mistral"],
  [/^command/i, "Cohere"],
];
const isFagiJudge = (m) => /^turing/i.test(String(m ?? ""));
/** `OpenAI` for gpt-4o, null for a model no provider list names. */
export function judgeProviderOf(model) {
  const id = String(model ?? "").replace(/^[^/]+\//, "");
  return JUDGE_PROVIDERS.find(([re]) => re.test(id))?.[1] ?? null;
}
/**
 * The judges' lead word: `Future AGI judges` when every model is one of
 * Future AGI's (or none is named), `Judges` with a decision model (its own
 * note follows), else `Judges billed to your OpenAI key` (`… your provider
 * keys` across providers or for a custom model).
 */
function judgesWho(models) {
  const list = (models ?? []).filter(Boolean);
  if (!list.length || list.every(isFagiJudge)) return "Future AGI judges";
  if (list.some(isDecisionModel)) return "Judges";
  const keyed = list.filter((m) => !isFagiJudge(m));
  const providers = [...new Set(keyed.map(judgeProviderOf))];
  const one = providers.length === 1 && providers[0] ? providers[0] : null;
  const keyWord = one ? `your ${one} key` : "your provider keys";
  return keyed.length === list.length
    ? `Judges billed to ${keyWord}`
    : `Judges (Future AGI and ${keyWord})`;
}

/**
 * The F10 `How is this estimated?` text (selectable), built from what this
 * experiment runs (v2.1 estimate:details): the tries rule, one line per
 * kind of run it has (nothing about a kind it does not run), the judges,
 * the time and how close the estimate lands. Pure.
 * @param {{promptConfig?: object[]}} p  the form's run cards
 * @returns {string[]}
 */
export function estimateHelpOf({ promptConfig = null } = {}) {
  const cards = promptConfig ?? [];
  const media = mediaTargetsOf(cards);
  const types = new Set(
    media
      ? media.map((m) => m.type)
      : flattenRuns(cards).map((r) => r.targetType),
  );
  // L6 launch switch (estimate:details, knob:cost-line): dev runs every row
  // once, about 10 rows at a time, with no trials, caps or endpoint runs;
  // a launch build's method text says exactly that.
  const devOnly = !isSurfaceShown("exp.new.runSettings");
  const offered = devOnly
    ? (k) => !["http", "a2a", "mcp_tool"].includes(k)
    : () => true;
  const any = (...ks) =>
    ks.some((k) => types.has(k) && offered(k)) ||
    (!cards.length && ks.some(offered));
  const lines = [
    devOnly
      ? "Tries = rows: each run tries every row once."
      : "Tries = rows × each run's tries (its own Tries, else Trials).",
  ];
  if (any("prompt", "agent"))
    lines.push(
      `Prompt and agent runs: the run engine's price for the model (its list price × ${TOKENS_PER_TRY.input.toLocaleString("en-US")} input + ${TOKENS_PER_TRY.output.toLocaleString("en-US")} output tokens per try, scaled for the kind of run and capped by Max tokens; an agent's llm nodes each at their own model), marked rough.`,
    );
  if (types.has("tts"))
    lines.push(
      `TTS runs: each clip at the model's price per 1,000 characters of the text it speaks (the sample rows' mean, at most 600). Only ${ENGINE_PRICED_MEDIA.filter((m) => !/^whisper/.test(m)).join(", ")} is priced here at the price the run is billed; any other TTS model is not priced before it runs.`,
    );
  if (types.has("stt"))
    lines.push(
      "STT runs: billed per audio minute, known only once each row is transcribed, so STT tries are not priced before they run.",
    );
  if (any("http", "a2a", "mcp_tool"))
    lines.push(
      "An endpoint, A2A agent or MCP server bills you for its own calls. Its cost cap counts judge spend: the run stops once the next try's judges would pass the cap, so only the tries under it run and are judged.",
    );
  lines.push(
    `Judges = tries × LLM-judge evals, each at its judge model's price ($${JUDGE_COST_PER_CALL} a call at gpt-4o-mini). Checks and Task success are free.`,
  );
  lines.push(
    devOnly
      ? "Time: each run works through its rows about 10 at a time, at the model's time per try, plus a minute of scoring; runs go side by side, so the slowest run sets it."
      : any("prompt", "agent")
        ? "Time: a prompt or agent run takes its tries × the run engine's time per try ÷ at a time, plus a minute of scoring (the ETA the run shows when it starts); runs go side by side, so the slowest run sets it."
        : "Time = tries ÷ at a time × the model's time per try (p50 to 1.5 × p50); runs go side by side, so the slowest run sets it.",
  );
  const close = [
    any("prompt", "agent")
      ? "a prompt or agent run's spend lands within about ±10% of its estimate (tokens vary per try)"
      : null,
    types.has("tts")
      ? "a priced TTS run is billed by each row's own text: this figure uses the sample rows' mean length, and the estimate stamped on the run when it starts prices its own rows"
      : null,
    "judge spend is never above its estimate, because rows an eval skips are not judged",
  ].filter(Boolean);
  lines.push(`How close: ${close.join("; ")}.`);
  return lines;
}

/** The method text for an experiment with no run picked yet. */
export const ESTIMATE_HELP = estimateHelpOf();

/**
 * R3 P3 (SVP-4.5, MUST 6) the Review step's one line for a PM:
 * `Run 30 tasks × 3 tries × 2 runs = 180 attempts · ≈ $2.10 · about 4 min`
 * (task suites say tasks / tries, other datasets rows / trials). Reads the
 * same estimate as costLine; null without a run or a row.
 * @param {{promptConfig, rows, trials, evals, history, taskSuite?: boolean}} p
 */
export function reviewLine({ taskSuite = false, ...p }) {
  const line = costLine(p);
  if (!line) return null;
  const targets = targetsOf(p.promptConfig);
  const r = Math.max(0, Math.round(Number(p.rows) || 0));
  const t = Math.max(1, Math.round(Number(p.trials) || 1));
  const k = targets.length;
  const unit = taskSuite ? ["task", "tasks"] : ["row", "rows"];
  const tr = taskSuite ? ["try", "tries"] : ["trial", "trials"];
  const pl = (x, [one, many]) => `${n(x)} ${x === 1 ? one : many}`;
  const est = line.estimate;
  const priced =
    targets.some((x) => x.kind !== "code") ||
    (line.story?.lines ?? []).some((l) => l.billedBy === "your provider");
  const cost = priced
    ? `≈ ${fmtCost(line.total ?? (est.cost_usd ?? 0) + (est.judge_cost_usd ?? 0))}${line.rough ? " (rough)" : ""}`
    : "≈ $0 in Future AGI";
  return {
    text: `Run ${pl(r, unit)} × ${pl(t, tr)} × ${pl(k, ["run", "runs"])} = ${pl(r * t * k, ["attempt", "attempts"])} · ${cost} · ${line.timeText}`,
    overBudget: line.overBudget,
    line,
  };
}

// ------------------------------------------------- IT-R1 G1 P6 (per run)

/** Seconds one try takes, as a range, by kind (harness ≈ 60–90 s). */
const TRY_SECONDS = {
  harness: [60, 90],
  code: [10, 30],
  voice: [60, 180],
  // One request per try (HTTP, A2A message/send, MCP tools/call).
  endpoint: [1, 5],
};

const minutesRange = (tries, conc, [lo, hi], factor = 1) => {
  const c = Math.max(1, Number(conc) || 1);
  const a = Math.max(1, Math.round((tries * lo * factor) / c / 60));
  const b = Math.max(a, Math.round((tries * hi * factor) / c / 60));
  return [a, b];
};

const money = (x) => fmtCost(x).replace(/\.00$/, "");

/** `about 15–23 min` from a minutes range (never seconds). */
export const minutesText = ([a, b]) => `about ${a === b ? a : `${a}–${b}`} min`;

/** IT-R5 G1 P4: a code run with no declared model (Add model opens it). */
export const NO_MODEL_COST = "~$ — add your model to estimate";

/**
 * The model a draft run uses (S5): harness → its recipe model (or the
 * harness's default); code → a changed `model` param, else the model the
 * user typed, else the first non-helper model the agent declared.
 * @param {object} r4  a card's `r4` draft
 */
export function draftModelOf(r4) {
  if (!r4) return null;
  if (r4.kind === "harness" || r4.slot?.harness)
    return (
      r4.slot?.harness?.model ??
      r4.config?.harness?.model ??
      (r4.entryId
        ? defaultRecipe(catalogEntry(r4.entryId), null)?.model ?? null
        : null)
    );
  const schema = r4.uses?.params ?? r4.config?.params?.schema ?? [];
  const declared = schema.find((p) => p?.name === "model");
  const value = r4.params?.model ?? r4.config?.params?.values?.model;
  if (value && (!declared || String(value) !== String(declared.default)))
    return String(value);
  return (
    r4.declared?.model ??
    (r4.uses?.models ?? []).find((m) => m?.name && m.role !== "helper")?.name ??
    null
  );
}

function harnessModelOf(card) {
  const r4 = card?.r4;
  if (!r4) return card?.harness_config?.model ?? null;
  return draftModelOf({ ...r4, kind: "harness" });
}

/** A harness card's per-try cap: the recipe's, else the harness default. */
function harnessTryCap(card) {
  const r4 = card?.r4 ?? null;
  return (
    Number(r4?.slot?.harness?.max_cost_usd) ||
    Number(r4?.slot?.harness?.budget?.max_cost_usd) ||
    (r4
      ? Number(
          defaultRecipe(catalogEntry(r4.entryId), null).budget?.max_cost_usd,
        )
      : Number(card?.harness_config?.limits?.max_cost_usd)) ||
    1
  );
}

// S5 sandbox effects: vCPU changes a try's time; the sandbox runs at most
// min(2 × vCPU, 10) tries at a time.
const VCPU_FACTOR = { 1: 1.35, 2: 1, 4: 0.85, 8: 0.8 };
export function sandboxEffect(r4) {
  if (r4?.mode !== "sandbox") return { factor: 1, maxAtOnce: null };
  const sb = r4?.slot?.sandbox ?? r4?.config?.sandbox ?? {};
  const cpu = Number(sb.resources?.cpu ?? sb.cpu) || 2;
  const factor =
    VCPU_FACTOR[cpu] ?? (cpu < 2 ? 1.35 : cpu < 4 ? 1 : cpu < 8 ? 0.85 : 0.8);
  return { factor, maxAtOnce: Math.min(2 * cpu, 10), cpu };
}

/**
 * `Worst case $65.11 = 30 tries × $1.00 × 2 runs + $5.00 + $0.11 judges`:
 * harness caps grouped by tries and per-try cap, then the rest.
 */
function worstSum(parts, judgeUsd, total) {
  const groups = new Map();
  const rest = [];
  for (const p of parts) {
    if (p.perTry != null) {
      const k = `${p.tries}|${p.perTry}`;
      groups.set(k, { ...p, k: (groups.get(k)?.k ?? 0) + 1 });
    } else if (p.usd > 0) rest.push(fmtCost(p.usd));
  }
  const terms = [
    ...[...groups.values()].map(
      (g) =>
        `${triesText(g.tries)} × ${fmtCost(g.perTry)}${g.k > 1 ? ` × ${g.k} runs` : ""}`,
    ),
    ...rest,
    judgeUsd > 0
      ? `${fmtCost(judgeUsd)} judges${
          parts.some((p) => p.judgeCap)
            ? ` (endpoint judge spend stops at ${parts
                .filter((p) => p.judgeCap)
                .map((p) => fmtCost(p.judgeCap))
                .join(", ")})`
            : ""
        }`
      : null,
  ].filter(Boolean);
  return terms.length ? `Worst case ${total} = ${terms.join(" + ")}` : null;
}

/** `1 at a time (the experiment runs at most 1 at once; you set 4)`. */
const atText = (conc, reason) =>
  `${conc} at a time${reason ? ` (${reason})` : ""}`;

/** A harness card's time limit per try (recipe, else Advanced). */
function harnessTimeLimit(card) {
  const r4 = card?.r4 ?? null;
  return (
    Number(r4?.slot?.harness?.timeout_s) ||
    Number(r4?.slot?.harness?.budget?.timeout_s) ||
    Number(r4?.limits?.timeoutS) ||
    null
  );
}

/**
 * `[p50, p90]` seconds of one try of a run (S5): the run's model latency;
 * a harness task is its catalog default's task time scaled by the chosen
 * model's latency; a time limit caps the slow end; sandbox vCPU scales it.
 */
function trySecondsOf({ type, model, card, factor = 1, timeLimit }) {
  let range = null;
  if (type === "harness") {
    const def = card?.r4?.entryId
      ? defaultRecipe(catalogEntry(card.r4.entryId), null)?.model
      : null;
    const ratio =
      model && def && latencyOf(model) && latencyOf(def)
        ? latencyOf(model) / latencyOf(def)
        : 1;
    range = TRY_SECONDS.harness.map((s) => s * ratio * factor);
  } else if (FREE_KINDS.has(type)) {
    const at = model ? trySecondsAt(model, factor) : null;
    range = at ?? TRY_SECONDS.code.map((s) => s * factor);
  }
  if (range && timeLimit) range = range.map((s) => Math.min(s, timeLimit));
  return range;
}

/**
 * IT-R2 G1 P6 · one cost and time story for every surface (Review, footer,
 * details, voice config): per run `#4A the coding harness Code · 30 tries · expected ≈
 * $6.30 · cap $30 · 2 at a time · ~15–23 min`, local code `≈ $0.47 at
 * gpt-4.1 prices`; the judges
 * once; the headline `Expected ≈ $10.01 · worst case $65 (caps) · about
 * 15–23 min` (runs go side by side: the slowest run's time); and `gaps`,
 * one line when two runs of the same kind differ a lot.
 *
 * options-truth L3 G3 (S5): each run plays its own tries (`r4.limits.tries`,
 * the voice config's Repeat) or the experiment's Trials; at a time is the
 * card's number capped by the experiment's "At most N at once"
 * (`maxConcurrency`) and the sandbox; minutes come from the run's model
 * latency; a replay run costs its mode's share; `rough` when a price comes
 * from a list price, not an earlier run.
 * @returns {{lines, headline, gaps: string[], judges: string|null,
 *   expected: number, worst: number|null, minutes: number[]|null}|null}
 */
export function runEstimateLines({
  cards,
  rows,
  trials,
  estimate,
  ordinal,
  history = [],
  maxConcurrency = null,
  replay = null,
  customPrices = null,
  mediaUnits = null,
}) {
  // L5 G3: a TTS / STT / Image form's runs are one per model (TTS: voice).
  const mediaRuns = mediaTargetsOf(cards ?? [], { customPrices, mediaUnits });
  const runs = mediaRuns
    ? mediaRuns.map((m, i) => ({
        configIndex: 0,
        targetType: m.type,
        model: m.model,
        name: m.name,
        letter: m.letter,
        index: i,
        priced: m.priced,
      }))
    : flattenRuns(cards ?? []);
  const r = Math.max(0, Math.round(Number(rows) || 0));
  const t = Math.max(1, Math.round(Number(trials) || 1));
  if (!runs.length || !r) return null;
  let expected = 0;
  let worst = 0;
  let capKnown = false;
  let longest = null;
  let rough = Boolean(estimate?.rough);
  let runCount = 0;
  const harnessCosts = [];
  // IT-R5 G1 P4: the worst case shows its arithmetic (caps per try).
  const worstParts = [];
  // v2.1 (review:worst-math): an endpoint run's cap stops its judge spend;
  // its judge share is bounded by the cap in Expected and the worst case.
  const judgeCaps = [];
  // v2.1 (result:endpoint-estimate): every try is judged by the same
  // evals, so one try's judge spend and calls are the estimate's per try.
  const estCalls = Number(estimate?.calls) || 0;
  const judgeUsdPerTry = estCalls
    ? (Number(estimate?.judge_cost_usd) || 0) / estCalls
    : 0;
  const judgeCallsPerTry = estCalls
    ? (Number(estimate?.judge_calls) || 0) / estCalls
    : 0;
  let unpricedMedia = 0;
  const lines = runs.map((run, i) => {
    const card = cards[run.configIndex];
    const r4 = card?.r4 ?? null;
    const label = ordinal != null ? `#${ordinal}${run.letter}` : run.letter;
    const type = run.targetType;
    const name = r4?.entryName ?? run.name;
    const sb = sandboxEffect(r4);
    const runTries = runTriesOf(card, t);
    const tries = r * runTries;
    const at = resolvedAtOnce(card, {
      maxConcurrency,
      sandboxMax: sb.maxAtOnce,
    });
    const conc = at.value;
    const est = Number(estimate?.per_target?.[i]?.cost_usd);
    const bits = [`${label} ${name}`];
    const meta = {
      model: null,
      priced: true,
      cap: null,
      clamped: false,
      tries: runTries,
      clampReason: at.reason,
      group: "platform",
      usd: 0,
      billedBy: null,
    };
    let range = null;
    runCount += 1;
    if (type === "harness") {
      meta.group = "harness";
      bits.push(triesText(tries));
      // The drawer's recipe is the one source (its default when untouched).
      const perTry = harnessTryCap(card);
      // options-truth L2 finalize: the run also stops at its own cost cap.
      const runCap = Number(r4?.limits?.maxCostUsd) || null;
      const runCapBinds = runCap != null && runCap < perTry * tries;
      const cap = runCapBinds ? runCap : perTry * tries;
      worst += cap;
      capKnown = true;
      worstParts.push(runCapBinds ? { usd: runCap } : { tries, perTry });
      meta.model = harnessModelOf(card);
      meta.cap = perTry;
      meta.runCap = runCapBinds ? runCap : null;
      if (Number.isFinite(est)) {
        // A try never costs more than its cap (the engine stops it there).
        const usd = Math.min(est, cap);
        meta.clamped = usd < est;
        meta.usd = usd;
        bits.push(`expected ≈ ${money(usd)}`);
        expected += usd;
        const key = targetKeyOfCard(card, run.model);
        const past = (history ?? []).find((h) => h.target_key === key);
        if (estimate?.per_target?.[i]?.from_history === false) rough = true;
        harnessCosts.push({
          name,
          perTry: Math.min(est, cap) / tries,
          tokens: past?.tokens_per_attempt ?? null,
          from: past?.label ?? null,
        });
      }
      bits.push(
        `stops at ${capText(perTry)}${runCapBinds ? ` · ${fmtCost(runCap)} per run` : ""}`,
      );
      const limit = harnessTimeLimit(card);
      if (limit) bits.push(`time limit ${n(limit)} s per try`);
      if (conc) bits.push(atText(conc, at.reason));
      const sec = trySecondsOf({
        type,
        model: meta.model,
        card,
        factor: sb.factor,
        timeLimit: limit,
      });
      range = minutesFor(tries, conc, sec);
    } else if (ENDPOINT_KINDS.has(type)) {
      meta.group = "endpoint";
      meta.billedBy = ENDPOINT_WORD[type];
      bits.push(triesText(tries));
      bits.push(`billed by ${ENDPOINT_WORD[type]}, not Future AGI`);
      // L5 G3: the run's own cap (no per-try cap exists for an endpoint).
      const runCap =
        Number(r4?.limits?.maxCostUsd ?? card?.limits?.maxCostUsd) || null;
      // v2.1 (result:endpoint-estimate): the engine stops every remaining
      // try once the next one's judge spend would pass the cap, so the run
      // plays ≈k of its tries; its judge calls and minutes follow k.
      let ran = tries;
      if (runCap) {
        meta.cap = runCap;
        meta.runCap = runCap;
        ran = triesUnderCap(tries, runCap, judgeUsdPerTry);
        judgeCaps.push({ tries, ran, cap: runCap });
        // W1-6: the endpoint bills its own calls; the cap counts judge spend.
        // Review S6: "judge spend" sits with the amount it limits.
        bits.push(
          ran < tries
            ? `the cap stops this run after ≈${n(ran)} of ${triesText(tries)} (judge spend stops at ${fmtCost(runCap)})`
            : `judge spend stops at ${capText(runCap, { runLevel: true, tries })}`,
        );
      }
      meta.ranTries = ran;
      if (conc) bits.push(atText(conc, at.reason));
      range = minutesRange(Math.max(1, ran), conc, TRY_SECONDS.endpoint);
    } else if (MEDIA_KINDS.has(type)) {
      // L5 G3: one output per try (rows × Trials), priced per clip /
      // minute / image.
      const word = MEDIA_WORD[type];
      bits.push(`${n(tries)} ${word}`);
      meta.model = run.model;
      meta.tries = runTries;
      if (Number.isFinite(est) && run.priced) {
        meta.usd = est;
        bits.push(`expected ≈ ${money(est)} in Future AGI`);
        expected += est;
        worst += est;
        worstParts.push({ usd: est });
      } else {
        meta.priced = false;
        unpricedMedia += 1;
        bits.push(
          type === "stt"
            ? `~$— · ${run.model} is billed per audio minute, not priced before it runs`
            : `~$— · ${run.model} is not priced before it runs`,
        );
      }
      const sec = trySecondsAt(run.model);
      if (sec) range = minutesFor(tries, conc, sec);
      if (conc) bits.push(atText(conc, at.reason));
    } else if (FREE_KINDS.has(type)) {
      meta.group = "free";
      bits.push(triesText(tries));
      // L5 G3: a replay run on your side makes its mode's share of calls.
      const freeShare = replay ? replay.share : 1;
      if (replay) bits.push(`replay · ${replay.label}`);
      const cap = Number(r4?.limits?.maxCostUsd) || null;
      // IT-R4 G1 P5: a declared model is priced at its list prices.
      // options-truth L2 R3(a): the model the run uses (S5).
      const declaredModel = draftModelOf(r4);
      const per = declaredModel ? costPerTry(declaredModel) : null;
      meta.model = declaredModel;
      meta.cap = cap;
      if (per != null) {
        const full = per * tries * freeShare;
        // R3(c): the run stops at its cost cap, so it never costs more.
        const usd = cap ? Math.min(full, cap) : full;
        meta.clamped = usd < full;
        meta.usd = usd;
        meta.billedBy = "your provider";
        rough = true;
        bits.push(`≈ ${money(usd)} at ${declaredModel} prices`);
        expected += usd;
      } else if (declaredModel) {
        // R3(b): a model with no list price is not estimated.
        meta.priced = false;
        bits.push(`~$— · no price for ${declaredModel}`);
      } else {
        meta.priced = false;
        bits.push(NO_MODEL_COST);
      }
      if (cap) {
        bits.push(`stops at ${capText(cap, { perRun: true, tries })}`);
        worst += cap;
        capKnown = true;
        worstParts.push({ usd: cap });
      }
      const limit = Number(r4?.limits?.timeoutS) || null;
      if (conc) bits.push(atText(conc, at.reason));
      const sec = r4
        ? trySecondsOf({
            type,
            model: declaredModel,
            card,
            factor: sb.factor,
            timeLimit: limit,
          })
        : null;
      range = sec ? minutesFor(tries, conc, sec) : null;
    } else {
      bits.push(triesText(tries));
      // A replay run makes its mode's share of a full re-run's calls.
      const share = replay ? replay.share : 1;
      if (replay) bits.push(`replay · ${replay.label}`);
      // L5 G3: a prompt on a model with no price (and no earlier run of
      // the same setup) is not estimated, never guessed.
      const unpriced =
        type === "prompt" &&
        run.model &&
        !modelPriceOf(run.model, customPrices) &&
        !estimate?.per_target?.[i]?.from_history;
      if (unpriced) {
        meta.priced = false;
        meta.model = run.model;
        bits.push(`~$— · no price for ${run.model}`);
      } else if (Number.isFinite(est)) {
        const usd = est * share;
        meta.usd = usd;
        bits.push(
          share === 0
            ? "no model calls (scores the recorded replies)"
            : `expected ≈ ${money(usd)} in Future AGI`,
        );
        expected += usd;
        worst += usd;
        worstParts.push({ usd });
      }
      // Prompt and agent runs: rows × tries × p50 ÷ at a time (S5), from
      // the last run of the same setup, else the model's latency.
      const pt = estimate?.per_target?.[i];
      if (pt?.from_history === false) rough = true;
      // v2.1 (review:headline, review:concurrency): the engine's own time
      // per try (engineTry ms, the target's p50_ms), at the run's at a time
      // (the experiment's At once, 10 when unset, as the create sends),
      // plus the engine's scoring minute: the ETA the run shows at start.
      const p50Ms = Number(pt?.p50_ms) || latencyOf(run.model) || 0;
      if (p50Ms) {
        const c = conc ?? 10;
        bits.push(atText(c, at.reason));
        range = engineEtaMinutes(tries, c, p50Ms * share);
      } else {
        const s = Number(estimate?.seconds) || 0;
        const m = Math.max(1, Math.round(s / 60));
        range = [m, m];
      }
    }
    if (range) {
      bits.push(
        range[0] === range[1]
          ? `~${range[0]} min`
          : `~${range[0]}–${range[1]} min`,
      );
      if (!longest || range[1] > longest[1]) longest = range;
    }
    return { label, text: bits.join(" · "), ...meta, conc, minutes: range };
  });
  const judgeUnbounded = Number(estimate?.judge_cost_usd) || 0;
  // v2.1 (result:endpoint-estimate): tries a cap stops are never judged.
  const notRun = judgeCaps.reduce(
    (s, c) => s + Math.max(0, (Number(c.tries) || 0) - (Number(c.ran) || 0)),
    0,
  );
  const judgeCalls = Math.max(
    0,
    Math.round(
      (Number(estimate?.judge_calls) || 0) - notRun * judgeCallsPerTry,
    ),
  );
  const judgeModels = estimate?.judge_models ?? [];
  const judgeUsd = Math.max(0, judgeUnbounded - notRun * judgeUsdPerTry);
  if (judgeCaps.length) {
    capKnown = true;
    judgeCaps.forEach((c) => worstParts.push({ usd: 0, judgeCap: c.cap }));
  }
  expected += judgeUsd;
  worst += judgeUsd;
  const judges = judgeUsd
    ? `${judgesWho(judgeModels)} ≈ ${fmtCost(judgeUsd)}${judgeCalls ? ` (${n(judgeCalls)} judge calls${judgeModels.length ? ` at ${judgeModels.join(", ")}` : ""})` : ""}${
        judgeModels.some(isDecisionModel)
          ? ` · ${judgeModels.filter(isDecisionModel).join(", ")} ${
              judgeModels.filter(isDecisionModel).length > 1
                ? "bill your own provider keys"
                : "bills your own provider key"
            }, no Future AGI credits`
          : ""
      }`
    : null;
  // One line when two harness runs differ a lot (≥ 1.5×), with the why.
  const gaps = [];
  const byCost = [...harnessCosts].sort((a, b) => b.perTry - a.perTry);
  if (byCost.length >= 2 && byCost[1].perTry > 0) {
    const [hi, lo] = [byCost[0], byCost[byCost.length - 1]];
    const ratio = hi.perTry / lo.perTry;
    if (ratio >= 1.5) {
      const tok =
        hi.tokens && lo.tokens && hi.tokens / lo.tokens >= 1.2
          ? hi.tokens / lo.tokens
          : null;
      const price = tok ? ratio / tok : ratio;
      const why = [
        price >= 1.2 ? "a pricier model" : null,
        tok ? `~${tok.toFixed(1)}× the tokens per task` : null,
      ].filter(Boolean);
      const from = hi.from && lo.from ? ` (from ${hi.from} vs ${lo.from})` : "";
      gaps.push(
        `${hi.name} costs ~${ratio.toFixed(1)}× ${lo.name} here${why.length ? `: ${why.join(" and ")}` : ""}${from}`,
      );
    }
  }
  // IT-R5 G1 P4: the headline shows its breakdown inline
  // (`$10.01 = $9.90 runs + $0.11 judges`) and the worst case its sum.
  const runsUsd = expected - judgeUsd;
  const breakdown =
    judgeUsd > 0 && runsUsd > 0
      ? ` = ${fmtCost(runsUsd)} runs + ${fmtCost(judgeUsd)} judges`
      : "";
  // options-truth L3 G3: "rough" sits in the headline itself when a price
  // is a list price (no finished run of the same setup).
  const roughWord = rough && expected > 0 ? " (rough)" : "";
  // v2.1 (tts:models, stt:models): a media run with no engine price adds
  // nothing, and the headline says it is left out.
  const without = unpricedMedia
    ? ` (without ${unpricedMedia} ${unpricedMedia === 1 ? "run" : "runs"} not priced before ${unpricedMedia === 1 ? "it runs" : "they run"})`
    : "";
  const head = [
    `Expected ≈ ${money(expected)}${roughWord}${breakdown}${without}`,
    capKnown ? `worst case ${money(worst)} (caps)` : null,
    longest ? minutesText(longest) : null,
  ].filter(Boolean);
  const worstMath = capKnown
    ? worstSum(worstParts, judgeUsd, money(worst))
    : null;
  const mins = longest
    ? `~${longest[0] === longest[1] ? longest[0] : `${longest[0]}–${longest[1]}`} min`
    : null;
  // The Configuration step's running total: `3 runs · ~$10 · ~6 min`.
  const chip = [
    `${runCount} ${runCount === 1 ? "run" : "runs"}`,
    expected > 0 ? `~${money(expected)}` : "~$ —",
    mins,
  ]
    .filter(Boolean)
    .join(" · ");
  // The footer's one line (never clipped at 1280 px): same numbers.
  const short = [
    `≈ ${money(expected)}${roughWord}`,
    // S5: 2 significant figures, never $0 (a $0.05 cap is `worst $0.050`).
    capKnown ? `worst ${money(worst >= 10 ? Math.round(worst) : worst)}` : null,
    longest
      ? `${longest[0] === longest[1] ? longest[0] : `${longest[0]}–${longest[1]}`} min`
      : null,
  ].filter(Boolean);
  return {
    lines,
    headline: head.join(" · "),
    short: short.join(" · "),
    worstMath,
    chip,
    runsUsd,
    judgeUsd,
    judgeCalls,
    gaps,
    judges,
    expected,
    rough: Boolean(roughWord),
    runCount,
    worst: capKnown ? worst : null,
    minutes: longest,
  };
}
