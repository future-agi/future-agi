// The runs an experiment form describes, in the order the create payload
// sends them (PRODUCT-SPEC §3.8). A prompt card expands to one run per
// model; agent, From code and HTTP cards are one run each. The letter of a
// run is its position here, so `#9B` in the drawer is `#9B` in results.
import { variantLetter } from "src/sections/common/Runs/runLabel";
import { PROMPT_CONFIG_TYPE } from "./constants";

/** Target type of a form run card (legacy cards carry only ids). */
export function targetTypeOf(item) {
  if (item?.targetType) return item.targetType;
  if (item?.agentId) return PROMPT_CONFIG_TYPE.AGENT;
  return PROMPT_CONFIG_TYPE.PROMPT;
}

const hostOf = (url) => {
  try {
    const u = new URL(url);
    return `${u.host}${u.pathname === "/" ? "" : u.pathname}`;
  } catch {
    return url;
  }
};

/** Display name of one run (no letter). */
export function runDisplayName(item, model) {
  const type = targetTypeOf(item);
  const version = item?.versionLabel ? ` ${item.versionLabel}` : "";
  if (type === PROMPT_CONFIG_TYPE.PROMPT)
    return `${item?.name ?? "Prompt"}${version}${model ? ` × ${model}` : ""}`;
  if (type === PROMPT_CONFIG_TYPE.AGENT)
    return `${item?.name ?? "Agent"}${version}`;
  if (type === PROMPT_CONFIG_TYPE.EXTERNAL)
    return item?.runName?.trim() || "From code";
  if (type === PROMPT_CONFIG_TYPE.HTTP) {
    // CR#34: name from the host only once the URL parses.
    const url = item?.http?.url?.trim();
    let valid = false;
    try {
      valid = Boolean(url && new URL(url).host);
    } catch {
      valid = false;
    }
    return valid
      ? `${item?.http?.method ?? "POST"} ${hostOf(url)}`
      : "HTTP endpoint";
  }
  return item?.name ?? "Run";
}

const modelsOf = (item) => (item?.model ?? []).filter((m) => m?.value);

/**
 * Flattened runs of an LLM experiment form.
 * @returns {{index, letter, configIndex, modelIndex, targetType, model, name}[]}
 */
export function flattenRuns(promptConfig = []) {
  const runs = [];
  (promptConfig ?? []).forEach((item, configIndex) => {
    const type = targetTypeOf(item);
    if (type === PROMPT_CONFIG_TYPE.PROMPT) {
      modelsOf(item).forEach((m, modelIndex) =>
        runs.push({
          configIndex,
          modelIndex,
          targetType: type,
          model: m.value,
          name: runDisplayName(item, m.value),
        }),
      );
      return;
    }
    runs.push({
      configIndex,
      modelIndex: null,
      targetType: type,
      model: null,
      name: runDisplayName(item),
    });
  });
  return runs.map((r, index) => ({
    ...r,
    index,
    letter: variantLetter(index),
  }));
}

/**
 * Edit mode: a card whose runs all exist already (they keep the variable
 * mapping they ran with; only new runs are mapped on the Data step).
 */
export function isExistingCard(item) {
  if (targetTypeOf(item) === PROMPT_CONFIG_TYPE.PROMPT) {
    const models = modelsOf(item);
    return models.length > 0 && models.every((m) => Boolean(m?.runId));
  }
  return Boolean(item?.runId);
}

/** Letters per card: `{[configIndex]: ["A", "B"]}` (prompt: one per model). */
export function lettersByConfig(promptConfig = []) {
  const out = {};
  flattenRuns(promptConfig).forEach((r) => {
    out[r.configIndex] = [...(out[r.configIndex] ?? []), r.letter];
  });
  return out;
}

/** Runs of a non-LLM (TTS / STT / Image) form: one per model (TTS: voice). */
export function countNonLlmRuns(promptConfig = []) {
  return (promptConfig ?? []).reduce((sum, item) => {
    return (
      sum +
      modelsOf(item).reduce(
        (n, m) => n + (Array.isArray(m?.voices) ? m.voices.length || 1 : 1),
        0,
      )
    );
  }, 0);
}

/** Number of runs the form will create. */
export function runCount(experimentType, promptConfig) {
  return experimentType === "llm" || !experimentType
    ? flattenRuns(promptConfig).length
    : countNonLlmRuns(promptConfig);
}

/**
 * True when every run is From code or My code (the drawer ends on "Waiting
 * for results"; My code runs show their Run from code panel there).
 */
export const allRunsExternal = (promptConfig = []) => {
  const runs = flattenRuns(promptConfig);
  return (
    runs.length > 0 &&
    runs.every(
      (r) =>
        r.targetType === PROMPT_CONFIG_TYPE.EXTERNAL ||
        (r.targetType === PROMPT_CONFIG_TYPE.CODE &&
          // R4 Connect live runs on the connected machine: nothing to upload,
          // so it lands on results like any started run (integrator, wave 1).
          promptConfig?.[r.configIndex]?.r4?.form !== "connect" &&
          // Remote endpoint: Future AGI calls the URL for each row, so
          // nothing waits on the user's machine (options-truth L1 G2).
          promptConfig?.[r.configIndex]?.r4?.mode !== "remote"),
    )
  );
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Footer line, in Review's words (v2.1 estimate:details): "48 rows × 3
 * tries × 2 runs = 288 tries". Returns null while the row count is unknown.
 */
export function executionSummary({
  examples,
  trials = 1,
  runs = 0,
  perRun = null,
}) {
  if (examples == null || Number.isNaN(Number(examples))) return null;
  // L5 G3: runs with their own tries sum per run:
  // `20 rows · #9A ×2 · #9B ×3 = 100 tries`.
  const own = (perRun ?? []).map((r) => Math.max(1, Number(r?.tries) || 1));
  if (own.length && new Set(own).size > 1) {
    const sum = own.reduce((s, t) => s + Number(examples) * t, 0);
    return `${plural(Number(examples), "row")} · ${perRun
      .map((r, i) => `${r.label} ×${own[i]}`)
      .join(" · ")} = ${plural(sum, "try", "tries")}`;
  }
  if (own.length) trials = own[0];
  const total = Number(examples) * Number(trials || 1) * Number(runs || 0);
  return `${plural(Number(examples), "row")} × ${plural(
    Number(trials || 1),
    "try",
    "tries",
  )} × ${plural(Number(runs || 0), "run")} = ${plural(total, "try", "tries")}`;
}

const DRAFT_TYPE_LABEL = {
  [PROMPT_CONFIG_TYPE.PROMPT]: "prompt",
  [PROMPT_CONFIG_TYPE.AGENT]: "agent",
  [PROMPT_CONFIG_TYPE.EXTERNAL]: "from code",
  [PROMPT_CONFIG_TYPE.HTTP]: "HTTP endpoint",
  [PROMPT_CONFIG_TYPE.CODE]: "code",
  [PROMPT_CONFIG_TYPE.HARNESS]: "harness",
  [PROMPT_CONFIG_TYPE.TRACES]: "traces",
};

/**
 * CR#26: what a discarded draft loses, in words:
 * `2 runs (prompt, HTTP endpoint) · 3 mapped variables · 2 evaluations`.
 * Empty string when there is nothing worth naming.
 */
export function draftSummary(values = {}) {
  const runs = flattenRuns(values.promptConfig ?? []);
  // R4 (lane Ra §S): runs from the picker are named by what and where
  // (`LangGraph on This machine`, `Claude Code in Docker`).
  const cards = values.promptConfig ?? [];
  const types = [
    ...new Set(
      runs.map((r) => {
        const r4 = cards[r.configIndex]?.r4;
        if (r4?.entryName && r4?.whereName)
          return `${r4.entryName} ${r4.mode === "sandbox" ? "in" : "on"} ${r4.whereName}`;
        return DRAFT_TYPE_LABEL[r.targetType] ?? "run";
      }),
    ),
  ];
  const mapped = (values.promptConfig ?? []).reduce(
    (n, item) =>
      n + Object.values(item?.variableMapping ?? {}).filter(Boolean).length,
    0,
  );
  const evals = (values.userEvalMetrics ?? []).length;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return [
    runs.length ? `${plural(runs.length, "run")} (${types.join(", ")})` : null,
    mapped ? plural(mapped, "mapped variable") : null,
    evals ? plural(evals, "evaluation") : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
