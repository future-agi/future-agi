// Per-step checks of the New / Edit experiment drawer (PRODUCT-SPEC §3.8).
// The stepper shows ✓ or ⚠ n from these; `Next` and `Run experiment` stop
// on the first step with issues. Pure: values in, messages out.
import {
  CONCURRENCY_RANGE,
  MAX_RUNS_PER_EXPERIMENT,
  PROMPT_CONFIG_TYPE,
  R2_RUN_TYPES,
  TRIALS_RANGE,
} from "./constants";
import { flattenRuns, isExistingCard, runCount, targetTypeOf } from "./runPlan";
import { mappingStatus, unmappedMessage } from "./variableMapping";
import { toolIssues, toolWarnings } from "./toolSetup";
import { rowLimitStepIssues } from "src/sections/common/RunSettings/rowLimitsModel";
// R2 cards report their own issues through an optional `issues(item)`
// export (namespace imports: absent until the card's owner adds it).
import * as codeCard from "./ExternalRunCard";
import * as tracesCard from "./LinkedTracesCard";
import * as harnessCard from "./HarnessRunCard";

const R2_CARD_ISSUES = {
  [PROMPT_CONFIG_TYPE.CODE]: (item) => codeCard.issues?.(item) ?? [],
  [PROMPT_CONFIG_TYPE.TRACES]: (item) => tracesCard.issues?.(item) ?? [],
  [PROMPT_CONFIG_TYPE.HARNESS]: (item) => harnessCard.issues?.(item) ?? [],
  // L7b G2 (source:running:a2a / mcp_tool): a card that has not reached
  // its agent or server cannot run.
  [PROMPT_CONFIG_TYPE.A2A]: (item) =>
    item?.a2a?.card && item?.a2a?.skill_id
      ? []
      : ["Discover the agent card and pick a skill"],
  [PROMPT_CONFIG_TYPE.MCP_TOOL]: (item) =>
    item?.mcp_tool?.server?.name && item?.mcp_tool?.tool
      ? []
      : ["Connect the server and pick a tool"],
};

export const NAME_TAKEN_MESSAGE =
  "An experiment with this name already exists on this dataset";

const isHttpsUrl = (url) => {
  try {
    const u = new URL(String(url ?? "").trim());
    return u.protocol === "https:" && Boolean(u.host);
  } catch {
    return false;
  }
};

const PRIVATE_HOST =
  /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$|\.local$|\.internal$/i;

/**
 * CR#32: what to say under the HTTP endpoint field once it loses focus.
 * `null` when there is nothing to say (empty or a good public https URL).
 * @returns {{level: "error"|"warning", text: string}|null}
 */
export function httpUrlHint(url) {
  const raw = String(url ?? "").trim();
  if (!raw) return null;
  let u;
  try {
    u = new URL(raw);
  } catch {
    return {
      level: "error",
      text: "Use a full https:// URL, for example https://api.acme.ai/reply",
    };
  }
  if (PRIVATE_HOST.test(u.hostname))
    return {
      level: "warning",
      text: "Future AGI's servers cannot reach localhost or a private network. Use a tunnel (https).",
    };
  if (u.protocol !== "https:" || !u.host)
    return {
      level: "error",
      text: "Use a full https:// URL, for example https://api.acme.ai/reply",
    };
  return null;
}

/** Step 1 · Configuration. */
export function configurationIssues(values, { nameTaken = false } = {}) {
  const issues = [];
  if (!String(values?.name ?? "").trim()) issues.push("Name is required");
  else if (nameTaken) issues.push(NAME_TAKEN_MESSAGE);
  const type = values?.experimentType ?? "llm";
  const config = values?.promptConfig ?? [];
  if (!config.length) {
    issues.push("Add at least one run");
    return issues;
  }
  if (runCount(type, config) > MAX_RUNS_PER_EXPERIMENT)
    issues.push(`Up to ${MAX_RUNS_PER_EXPERIMENT} runs per experiment`);
  if (type !== "llm") {
    config.forEach((item) => {
      if (!(item?.model ?? []).some((m) => m?.value))
        issues.push("Pick at least one model");
    });
    return issues;
  }
  config.forEach((item) => {
    const kind = targetTypeOf(item);
    const name = item?.name ?? "this run";
    issues.push(...rowLimitStepIssues(item));
    if (kind === PROMPT_CONFIG_TYPE.PROMPT) {
      if (!item?.promptVersion || item?.isDraft)
        issues.push(`Pick a saved version of ${name}`);
      if (!(item?.model ?? []).some((m) => m?.value))
        issues.push(`Pick a model for ${name}`);
      // Tools block (R2-SPEC §5.3.2): `Live tool lookup_order has no endpoint`.
      issues.push(...toolIssues(item));
    } else if (kind === PROMPT_CONFIG_TYPE.AGENT) {
      if (!item?.agentVersion || item?.isDraft)
        issues.push(`Pick a saved version of ${name}`);
    } else if (kind === PROMPT_CONFIG_TYPE.EXTERNAL) {
      if (!String(item?.runName ?? "").trim())
        issues.push("Run name is required");
    } else if (R2_RUN_TYPES.includes(kind)) {
      // R2 cards: only what the card's own issues() export reports.
      const cardIssues = R2_CARD_ISSUES[kind];
      if (cardIssues) issues.push(...cardIssues(item));
    } else if (kind === PROMPT_CONFIG_TYPE.HTTP) {
      if (!isHttpsUrl(item?.http?.url))
        issues.push("Enter a valid https:// URL");
      if (!String(item?.http?.outputPath ?? "").trim())
        issues.push("Output path is required, e.g. $.reply");
    }
  });
  return [...new Set(issues)];
}

/** Step 2 · Data: every run's variables are mapped to a column. */
export function dataIssues(values, { columns = [], isEditing = false } = {}) {
  // Review S8 (W1-6): a ticked sample with no row count would run every
  // row; it blocks Continue and says why.
  const sampleIssue =
    !isEditing &&
    values?.sample?.on &&
    !values?.rowIds?.length &&
    !(Math.round(Number(values.sample.n)) > 0)
      ? ["Enter how many rows to sample, or untick A random sample"]
      : [];
  if ((values?.experimentType ?? "llm") !== "llm") return sampleIssue;
  const config = values?.promptConfig ?? [];
  const runs = flattenRuns(config);
  const issues = [];
  config.forEach((item, configIndex) => {
    if (targetTypeOf(item) === PROMPT_CONFIG_TYPE.EXTERNAL) return;
    if (R2_RUN_TYPES.includes(targetTypeOf(item))) return;
    if (isEditing && isExistingCard(item)) return;
    const { unmapped } = mappingStatus(
      item?.variables ?? [],
      item?.variableMapping ?? {},
      columns,
    );
    if (!unmapped.length) return;
    const letters = runs
      .filter((r) => r.configIndex === configIndex)
      .map((r) => r.letter);
    const who = letters.length ? `${letters.join(", ")}: ` : "";
    issues.push(`${who}${unmappedMessage(unmapped)}`);
  });
  return [...sampleIssue, ...issues];
}

/** Step 3 · Evaluations: at least one (C29). */
export function evaluationIssues(values) {
  return (values?.userEvalMetrics ?? []).length
    ? []
    : ["Add at least one evaluation"];
}

const inRange = (v, { min, max }) =>
  Number.isInteger(Number(v)) && Number(v) >= min && Number(v) <= max;

/** Step 4 · Review & run: trials, concurrency, metadata. */
export function reviewIssues(values) {
  const issues = [];
  if (!inRange(values?.trials ?? 1, TRIALS_RANGE))
    issues.push(`Trials must be ${TRIALS_RANGE.min}–${TRIALS_RANGE.max}`);
  if (!inRange(values?.maxConcurrency ?? 10, CONCURRENCY_RANGE))
    issues.push(
      `Concurrency must be ${CONCURRENCY_RANGE.min}–${CONCURRENCY_RANGE.max}`,
    );
  const keys = (values?.metadata ?? [])
    .filter((m) => String(m?.key ?? "").trim() || String(m?.value ?? "").trim())
    .map((m) => String(m?.key ?? "").trim());
  if (keys.some((k) => !k)) issues.push("Every metadata value needs a key");
  if (keys.includes("source")) issues.push("`source` is set by Future AGI");
  if (new Set(keys).size !== keys.length)
    issues.push("Metadata keys must be unique");
  return issues;
}

/**
 * Warnings of step 1 (never block): prompt cards whose tools answer from
 * recordings while some rows have none (`24 rows have no recording; they
 * will use mocks`).
 * @param {object} values
 * @param {{rowCount?: number|null}} [ctx]
 */
export function configurationWarnings(values, { rowCount } = {}) {
  const out = [];
  (values?.promptConfig ?? []).forEach((item) => {
    if (targetTypeOf(item) !== PROMPT_CONFIG_TYPE.PROMPT) return;
    out.push(...toolWarnings(item, { rowCount }));
  });
  return [...new Set(out)];
}

/** Issues of every step, in step order. */
export function stepIssues(values, ctx = {}) {
  return [
    configurationIssues(values, ctx),
    dataIssues(values, ctx),
    evaluationIssues(values),
    reviewIssues(values),
  ];
}
