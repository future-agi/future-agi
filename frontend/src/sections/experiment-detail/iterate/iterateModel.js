import { runModelOf } from "src/sections/common/RunSources/rerunWhere";
import { cappedPerTry, toolModeFactors } from "./childCost";
import {
  fmtCost as g0Cost,
  fmtNumber,
  fmtPct as g0Pct,
} from "src/sections/common/format/fmt";
import { disagreement } from "src/sections/common/Analysis/runStats";
// The Iterate dock's pure rules (R2-SPEC §4.3.1, §0.4): which rows a scope
// reruns, which change tabs a run kind gets, the change list and the I1
// body, the price and "lands as" lines. No React, no requests.
import {
  estimateRun,
  playbackRate,
} from "src/sections/common/Analysis/runStats";
import {
  engineTry,
  costPerTry as modelCostPerTry,
} from "src/sections/common/RunSources/declaredUses";
import { canRerunHere, runKindOf } from "src/sections/common/Runs/runKind";
import { parseRunLabel, runLabel } from "src/sections/common/Runs/runLabel";
// the interface contract (review M2): the dock's `Failed (13)` reads the one Failed rule.
import { failsSome } from "./failedRule";

export const MAX_RUNS_PER_EXPERIMENT = 12;
export const MAX_QUICK_TEST_ROWS = 20;
// v2.1 g4 (knob:dock:trials): up to 3 tries per row, each traced by the
// one run engine (tries 4+ had no trace of their own).
export const MAX_DOCK_TRIALS = 3;
export const TRIAL_CHOICES = [1, 2, 3];

// ------------------------------------------------------------- formatting

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** `$0.20`, `$0.79`, `$0.011`, `$0.0040` (G0 fmt.js, R3 F1). */
export function fmtUsd(v) {
  return g0Cost(v);
}

/** `about 10 s`, `about 1 min`, `about 3 min`. */
export function fmtAbout(seconds) {
  if (!isNum(seconds)) return "—";
  if (seconds < 60) return `about ${Math.max(1, Math.round(seconds))} s`;
  return `about ${Math.round(seconds / 60)} min`;
}

/** `65.6%` (one decimal) from a 0–1 rate (G0 fmt.js). */
export function fmtPct(rate) {
  return g0Pct(rate);
}

// ----------------------------------------------------------------- scopes

export const SCOPE_ORDER = [
  "all",
  "failed",
  "flaky",
  "regressed",
  "rows",
  "filter",
  "row",
];

const scored = (t) => t === 1 || t === 0 || t === "error";
const trialsOf = (cell) =>
  Array.isArray(cell) ? cell : Array.isArray(cell?.trials) ? cell.trials : [];

/** Pass rate of one cell's scored trials, or null when none was scored. */
export function cellRate(cell) {
  const s = trialsOf(cell).filter(scored);
  return s.length ? s.filter((t) => t === 1).length / s.length : null;
}
// iterate r3 G3 (P2): `10 fail every try` in the Failed chip's breakdown.
const failsEvery = (cell) => {
  const s = trialsOf(cell).filter(scored);
  return s.length > 0 && s.every((t) => t === 0 || t === "error");
};

/** True when a matrix row matches a URL row reference (id, `T7`, `7`, `Row 7`). */
export function rowMatches(row, ref) {
  if (!row || ref == null) return false;
  const r = String(ref).trim();
  if (r === row.row_id) return true;
  if (row.label && r === row.label) return true;
  const m = /^(?:T|Row\s*)?(\d+)$/i.exec(r);
  return Boolean(m) && Number(m[1]) === row.n;
}

/**
 * The rows each scope reruns, as matrix row ids, for the run being iterated.
 * @param {{rows: object[]}} matrix  R3 shape=matrix (must include runId)
 * @param {string} runId  the parent run
 * @param {{baselineId?: string|null, rows?: string[], filterRowIds?: string[]|null,
 *   rowId?: string|null}} opts
 * @returns {{all: string[], failed: string[], flaky: string[],
 *   regressed: string[]|null, rows: string[]|null, filter: string[]|null,
 *   row: string[]|null}}  null = the option does not apply
 */
export function scopeSets(matrix, runId, opts = {}) {
  const { baselineId, rows, filterRowIds, rowId } = opts;
  const own = (matrix?.rows ?? []).filter((r) => r.cells?.[runId]);
  const ids = (list) => list.map((r) => r.row_id);
  const pick = (refs) =>
    ids(own.filter((r) => refs.some((ref) => rowMatches(r, ref))));
  const hasBase = Boolean(baselineId && baselineId !== runId);
  return {
    all: ids(own),
    failed: ids(own.filter((r) => failsSome(r.cells[runId]))),
    failedEvery: ids(own.filter((r) => failsEvery(r.cells[runId]))),
    // R2D-17: the Matrix's Flaky (runStats.disagreement over this run's
    // cells), so the dock and the Matrix chip count the same rows.
    flaky: disagreement({
      rows: own.map((r) => ({
        id: r.row_id,
        n: r.n,
        cells: { [runId]: r.cells[runId] },
      })),
    }).flaky,
    regressed: hasBase
      ? ids(
          own.filter((r) => {
            const base = cellRate(r.cells[baselineId]);
            const mine = cellRate(r.cells[runId]);
            return base !== null && mine !== null && mine < base;
          }),
        )
      : null,
    rows: rows?.length ? pick(rows) : null,
    filter: Array.isArray(filterRowIds)
      ? ids(own.filter((r) => filterRowIds.includes(r.row_id)))
      : null,
    row: rowId ? pick([rowId]) : null,
  };
}

/**
 * iterate r2 G3 (P4): `Failing rows first, then the other 17 (30)` — the
 * failing rows run with the change, then the rest rerun from that run (the
 * old two-click path in one go). `{first, rest}` or null when there is
 * nothing to split.
 */
export function thenSplit(sets) {
  const failed = sets?.failed ?? [];
  const all = sets?.all ?? [];
  if (!failed.length || failed.length >= all.length) return null;
  const set = new Set(failed);
  return { first: [...failed], rest: all.filter((id) => !set.has(id)) };
}

/** The segmented control's options: `[{type, label, count, tip}]`. */
export function scopeOptions(
  sets,
  { parentLabel, baselineLabel, source = "Selection", causeLabel = null },
) {
  const n = (list) => list?.length ?? 0;
  const rowWord = (k) => (k === 1 ? "row" : "rows");
  const out = [];
  const add = (type, label, tip) =>
    out.push({ type, label, count: n(sets[type]), tip });
  // R4 implementation (8e): `Failed (13)`; each tip is the chip's one help line.
  add("all", `All (${n(sets.all)})`, `All: every row ${parentLabel} ran`);
  // iterate r3 G3 (P2): every chip says `(n)`; Failed breaks its number
  // down (`13 rows have a failing try; 10 fail every try; 6 fail
  // order_found`); Flaky is the one definition the grid footer and the
  // compare footer use (mixed pass and fail tries on this run).
  const every = sets.failedEvery ? n(sets.failedEvery) : null;
  add(
    "failed",
    `Failed (${n(sets.failed)})`,
    [
      `${n(sets.failed)} ${n(sets.failed) === 1 ? "row has" : "rows have"} a failing try on ${parentLabel}`,
      every !== null ? `${every} fail every try` : null,
      causeLabel && sets.rows ? `${n(sets.rows)} fail ${causeLabel}` : null,
    ]
      .filter(Boolean)
      .join("; "),
  );
  add(
    "flaky",
    `Flaky (${n(sets.flaky)})`,
    `Flaky: mixed pass and fail tries on ${parentLabel} (${n(sets.flaky)} ${rowWord(n(sets.flaky))})`,
  );
  if (sets.regressed && baselineLabel)
    add(
      "regressed",
      `Regressed (${n(sets.regressed)})`,
      `${n(sets.regressed)} ${rowWord(n(sets.regressed))} pass less often on ${parentLabel} than on ${baselineLabel}`,
    );
  // iterate r2 G3 (P3): a finding's rows read `Rows failing order_found
  // (6)`; `1 selected row` / `These 3 rows` only for rows the user picked.
  if (sets.rows)
    add(
      "rows",
      causeLabel
        ? `Rows failing ${causeLabel} (${n(sets.rows)})`
        : n(sets.rows) === 1
          ? "1 selected row"
          : `These ${n(sets.rows)} rows`,
      causeLabel
        ? `Finding: ${n(sets.rows)} ${rowWord(n(sets.rows))} fail ${causeLabel} on ${parentLabel} — the failure this fix aims at`
        : `${source}: ${n(sets.rows)} ${rowWord(n(sets.rows))}`,
    );
  if (sets.filter)
    add(
      "filter",
      `Current filter (${n(sets.filter)})`,
      `${n(sets.filter)} ${rowWord(n(sets.filter))} match the filters in view`,
    );
  if (sets.row) add("row", "This row (1)", "The row you were looking at");
  const then = thenSplit(sets);
  if (then)
    out.push({
      type: "then",
      label: `Failing rows first, then the other ${then.rest.length} (${n(sets.all)})`,
      count: n(sets.all),
      tip: `Runs the fix on the ${n(then.first)} failing ${rowWord(n(then.first))}, then reruns the other ${then.rest.length} from that run — one verdict for every row`,
    });
  return out;
}

// ---------------------------------------------------------------- presets

export const KEEP_PREFIX_TEXT =
  "Copy the order reference exactly as written, including any prefix (e.g. A-7781).";
export const REVERT_V2_TEXT = "Copy the order reference exactly as written.";
/** The world's third fix (iterationScripts NORMALISE_TEXT). */
export const NORMALISE_ID_TEXT =
  'Accept "A-7781", "a7781" or "7781" and look up the canonical id.';

const PREFIX_CHIPS = [
  { id: "keep", label: "Keep the prefix", text: KEEP_PREFIX_TEXT },
  { id: "revert", label: "Revert to v2", text: REVERT_V2_TEXT },
];
/** options-truth G6: the world's third fix, shown after the two presets. */
const WORLD_FIX_CHIPS = [
  { id: "normalise", label: "Normalise the ID", text: NORMALISE_ID_TEXT },
];

/**
 * Change presets a finding can name (`change=<id>`). Each opens the Agent
 * node tab on its node with its suggestion chips; `cause` is the finding's
 * title for the banner's `Cause "…" confirmed by rerun.`; `fixed` is what
 * the fix restores when it works (iterate r2 G3 P2: `Target failure fixed
 * on 6 of 6 rows (order id keeps "A-")`).
 */
export const CHANGE_PRESETS = Object.freeze({
  RC_PREFIX: {
    id: "RC_PREFIX",
    tab: "agent",
    node: "classify_intent",
    chip: "keep",
    cause: 'Order id loses its "A-" prefix',
    fixed: 'order id keeps "A-"',
    chips: PREFIX_CHIPS,
  },
  RC_ORDER_ID: {
    id: "RC_ORDER_ID",
    tab: "agent",
    node: "classify_intent",
    chip: "keep",
    cause: 'Order id loses its "A-" prefix',
    fixed: 'order id keeps "A-"',
    chips: PREFIX_CHIPS,
  },
});

/**
 * R4 8e: an iteration is named from its change: the suggestion chip whose
 * text the new prompt holds (`Keep the prefix`), else null.
 */
export function changeNameOf(prompt) {
  const p = String(prompt ?? "");
  return PREFIX_CHIPS.find((c) => p.includes(c.text))?.label ?? null;
}

/** The preset a `change=` / `cause=` value names (`RC_PREFIX:Keep` too). */
export function presetOf(change, cause) {
  const key = (v) => String(v ?? "").split(":")[0];
  return CHANGE_PRESETS[key(change)] ?? CHANGE_PRESETS[key(cause)] ?? null;
}

/** Suggestion chips for a node prompt (the digits-only prompt drops prefixes). */
export function suggestionsFor(nodeId, prompt, preset) {
  if (preset && preset.node === nodeId) return preset.chips;
  if (nodeId === "classify_intent" && /digits only/i.test(prompt ?? ""))
    return PREFIX_CHIPS;
  return [];
}

/** Every chip the dock shows for a node: the presets, then the world's. */
export function chipsFor(nodeId, prompt, preset) {
  const base = suggestionsFor(nodeId, prompt, preset);
  return base.length ? [...base, ...WORLD_FIX_CHIPS] : base;
}

// ------------------------------------------------------------------- tabs

export const TAB_LABELS = {
  agent: "Agent node",
  prompt: "Prompt",
  model: "Model & params",
  tools: "Tools",
};

/**
 * Change tabs that apply to a run, in order. dataset integration FE-0 (owner Q12): only
 * a prompt or agent run has something Future AGI can change and run again
 * itself. A code, harness, voice or uploaded run came from a generated
 * runner script that does not exist, and an endpoint or traces run has
 * nothing to edit here, so they get no tab and no Test a fix door.
 */
export function changeTabsFor(run) {
  if (!run || !canRerunHere(run)) return [];
  const c = run.config ?? {};
  // L7b G3: a TTS / STT / image run changes only its model (no prompt
  // messages, no LLM sampling params).
  if (c.media?.type) return ["model"];
  switch (runKindOf(run)) {
    case "agent":
      return c.tools?.length ? ["agent", "tools"] : ["agent"];
    // v2.1 g4 (change:prompt:messages): a prompt run changes its model,
    // params and tools; message edits are not offered (the run engine does
    // not follow edited instructions yet).
    case "prompt":
      return c.tools?.length ? ["model", "tools"] : ["model"];
    default:
      return [];
  }
}

/** True when Test a fix applies to a run (it has at least one change tab). */
export const canIterate = (run) => changeTabsFor(run).length > 0;

/** LLM nodes of an agent config (the node picker), `[{id, model, prompt}]`. */
export const llmNodes = (config) =>
  (config?.agent?.nodes ?? []).filter(
    (n) => n.type === "llm" || typeof n.prompt === "string",
  );

// ---------------------------------------------------------------- changes

const differs = (a, b) =>
  a !== undefined && String(a ?? "") !== String(b ?? "");

/**
 * The I1 `changes[]` for a draft, vs the parent's config. Unchanged fields
 * are left out. Draft fields (all optional): `agent{node, prompt,
 * maxIterations}`, `prompt{messages[]}`, `model{id, temperature,
 * maxTokens}`, `tools{modes{name: mode}, maxToolTurns}`, `harness{model,
 * maxTurns, timeoutS, maxCostUsd, instructions}` (`instructions` = text
 * appended to the instructions file).
 */
export function collectChanges(config, draft = {}) {
  const c = config ?? {};
  const out = [];
  const { agent, prompt, model, tools, harness } = draft;
  if (agent?.node) {
    const node = (c.agent?.nodes ?? []).find((n) => n.id === agent.node);
    if (differs(agent.prompt, node?.prompt))
      out.push({
        axis: "agent",
        node: agent.node,
        field: "prompt",
        value: agent.prompt,
      });
  }
  if (agent && differs(agent.maxIterations, c.agent?.limits?.max_iterations))
    out.push({
      axis: "agent",
      field: "max_iterations",
      value: Number(agent.maxIterations),
    });
  (prompt?.messages ?? []).forEach((text, i) => {
    if (differs(text, c.prompt?.messages?.[i]?.content))
      out.push({ axis: "prompt", field: `messages.${i}`, value: text });
  });
  const parentModel = c.harness?.model ?? runModelOf(c);
  if (model && differs(model.id, parentModel))
    out.push({ axis: "model", field: "id", value: model.id });
  if (model && differs(model.temperature, c.model?.params?.temperature))
    out.push({
      axis: "params",
      field: "temperature",
      value: Number(model.temperature),
    });
  if (model && differs(model.maxTokens, c.model?.params?.max_tokens))
    out.push({
      axis: "params",
      field: "max_tokens",
      value: Number(model.maxTokens),
    });
  Object.entries(tools?.modes ?? {}).forEach(([name, mode]) => {
    const t = (c.tools ?? []).find((x) => x.name === name);
    if (t && differs(mode, t.mode))
      out.push({ axis: "tools", node: name, field: "mode", value: mode });
  });
  if (tools && differs(tools.maxToolTurns, c.max_tool_turns))
    out.push({
      axis: "tools",
      field: "max_tool_turns",
      value: Number(tools.maxToolTurns),
    });
  const h = c.harness ?? {};
  if (harness) {
    if (differs(harness.model, h.model))
      out.push({ axis: "model", field: "id", value: harness.model });
    [
      ["maxTurns", "max_turns"],
      ["timeoutS", "timeout_s"],
      ["maxCostUsd", "max_cost_usd"],
    ].forEach(([k, field]) => {
      if (differs(harness[k], h[field]))
        out.push({ axis: "harness", field, value: Number(harness[k]) });
    });
    if (String(harness.instructions ?? "").trim())
      out.push({
        axis: "harness",
        field: "instructions_append",
        value: harness.instructions.trim(),
      });
  }
  return out;
}

/** `v3.1` after `v3`, `v4.1` after `v4.0`… (the draft agent version name). */
export function nextAgentVersion(version) {
  const m = /^v?(\d+)(?:\.(\d+))?/.exec(String(version ?? ""));
  if (!m) return "v1.1";
  return `v${m[1]}.${m[2] ? Number(m[2]) + 1 : 1}`;
}

/**
 * v2.1 g4: the first agent version name from `asked` on that no known
 * version holds (`v3.1` taken → `v3.2`), so the default Save-as name never
 * points the run at an existing version. Pure.
 * @param {string} asked
 * @param {Iterable<string|null|undefined>} taken
 */
export function freeAgentVersion(asked, taken = []) {
  const names = new Set(
    [...taken].filter(Boolean).map((n) => String(n).toLowerCase()),
  );
  let name = asked;
  for (let k = 0; k < 500 && names.has(String(name).toLowerCase()); k += 1)
    name = nextAgentVersion(name);
  return name;
}

/** `V7` after `V6` (the draft prompt version name). */
export function nextPromptVersion(version) {
  const m = /(\d+)/.exec(String(version ?? ""));
  return m ? `V${Number(m[1]) + 1}` : "V2";
}

/**
 * The I1 body (CONTRACT.md "I1"). `scope` is `{type, row_ids, label}`;
 * `all` sends no row ids (the server resolves them); every other scope
 * sends the ids the dock counted (v2.1 g4).
 */
export function buildIterateBody({
  parentRunId,
  quickTest = false,
  scopeType,
  rowIds = [],
  scopeLabel,
  trials,
  carryOver = true,
  changes = [],
  saveAs = null,
  causeId = null,
  from = null,
}) {
  const scope = { type: scopeType };
  // v2.1 g4 (knob:dock:scope): every scope but All sends the rows the dock
  // counted and priced, so the rows shown are the rows that run.
  if (
    ["rows", "filter", "row"].includes(scopeType) ||
    (scopeType !== "all" && rowIds.length)
  )
    scope.row_ids = [...rowIds];
  if (scopeLabel) scope.label = scopeLabel;
  const body = {
    parent_run_id: parentRunId,
    kind: quickTest ? "quick_test" : "iteration",
    scope,
    trials: quickTest ? 1 : trials,
    carry_over: quickTest ? false : Boolean(carryOver),
    changes,
  };
  if (saveAs && changes.length) body.save_as = saveAs;
  if (causeId) body.cause_id = causeId;
  if (from) body.from = from;
  return body;
}

// ------------------------------------------------------------ run preview

/** Mean cost per attempt of a run from its matrix cells, or undefined. */
export function costPerAttempt(matrix, runId) {
  let sum = 0;
  let n = 0;
  (matrix?.rows ?? []).forEach((r) => {
    const costs = r.cells?.[runId]?.cost_usd ?? [];
    costs.forEach((c) => {
      if (isNum(c)) {
        sum += c;
        n += 1;
      }
    });
  });
  return n ? sum / n : undefined;
}

/**
 * The preview line's numbers (F10 estimateRun): `{rows, trials, attempts,
 * usd, seconds, rough}`.
 */
export function previewOf({
  rows,
  trials,
  kind,
  costPerAttemptUsd,
  key,
  fromModel,
  toModel,
  fromTools,
  toTools,
  capUsd,
  fromMaxTokens = null,
  toMaxTokens = null,
}) {
  const ratio = childCostRatio({
    kind,
    fromModel,
    toModel,
    fromMaxTokens,
    toMaxTokens,
  });
  // L3 G9 (fix 9): tool modes and the cost cap move the per-try cost the
  // same way the run records it (childCost.js).
  // v2.1 g4 (change:tools:*): the run engine prices a prompt / agent try by
  // its tokens, whatever its tools' modes, so their modes do not move it.
  const tools =
    kind === "prompt" || kind === "agent"
      ? { cost: 1, latency: 1 }
      : toolModeFactors(fromTools ?? [], toTools ?? []);
  const perCall = isNum(costPerAttemptUsd)
    ? cappedPerTry(costPerAttemptUsd * ratio * tools.cost, capUsd)
    : null;
  const est = estimateRun({
    rows,
    trials,
    targets: [{ key: key ?? "parent", kind }],
    evals: [],
    history: isNum(perCall)
      ? [{ target_key: key ?? "parent", cost_per_attempt: perCall }]
      : [],
  });
  const attempts = rows * trials;
  return {
    rows,
    trials,
    attempts,
    usd: kind === "code" ? 0 : est.total_usd ?? est.cost_usd,
    seconds: est.seconds,
    rough: est.rough,
    ratePerS: attempts ? Math.round(playbackRate(attempts) * 10) / 10 : null,
    modelNote:
      Math.abs(ratio - 1) > 1e-9
        ? `priced at ${toModel ?? fromModel}${toMaxTokens != null && String(toMaxTokens) !== String(fromMaxTokens) ? `, max ${toMaxTokens} tokens` : ""} (${fromModel} × ${ratio.toFixed(2)})`
        : null,
  };
}

/**
 * v2.1 g4 (change:model:*, info:dock:preview-line): a child try's cost
 * against its parent's, from the one cost model the run engine prices
 * tries with (declaredUses.engineTry: model and max tokens). The engine
 * plays a child at its parent's recorded usage, so the preview is the
 * parent's cost per try times this ratio. Other kinds: list prices. Pure.
 */
export function childCostRatio({
  kind,
  fromModel,
  toModel,
  fromMaxTokens = null,
  toMaxTokens = null,
}) {
  const num = (v) =>
    v === null || v === undefined || v === "" || !Number.isFinite(Number(v))
      ? null
      : Number(v);
  if (kind === "prompt" || kind === "agent") {
    const was = engineTry({
      kind,
      model: fromModel,
      maxTokens: num(fromMaxTokens),
    });
    const now = engineTry({
      kind,
      model: toModel ?? fromModel,
      maxTokens: num(toMaxTokens) ?? num(fromMaxTokens),
    });
    if (was?.usd > 0 && now?.usd >= 0) return now.usd / was.usd;
  }
  const was = fromModel ? modelCostPerTry(fromModel) : null;
  const now = toModel ? modelCostPerTry(toModel) : null;
  return was && now && fromModel !== toModel ? now / was : 1;
}

/** `6 rows × 3 trials = 18 attempts · ≈ $0.20 · about 10 s`. */
export function previewLine(p, kind) {
  const rowWord = p.rows === 1 ? "row" : "rows";
  const trialWord = p.trials === 1 ? "trial" : "trials";
  const head = `${fmtNumber(p.rows)} ${rowWord} × ${p.trials} ${trialWord} = ${fmtNumber(p.attempts)} ${p.attempts === 1 ? "attempt" : "attempts"}`;
  if (kind === "code") return head;
  return `${head} · ≈ ${fmtUsd(p.usd)} · ${fmtAbout(p.seconds)}`;
}

// --------------------------------------------------------------- lands as

const letterOf = (run) =>
  run?.letter ?? parseRunLabel(runLabel(run))?.letter ?? null;
const ordinalOf = (run) =>
  run?.experiment_ordinal ?? parseRunLabel(runLabel(run))?.ordinal ?? null;

const nextLetterAfter = (runs) => {
  const codes = runs
    .map(letterOf)
    .filter((l) => l && l.length === 1)
    .map((l) => l.charCodeAt(0));
  return String.fromCharCode(codes.length ? Math.max(...codes) + 1 : 65);
};

/**
 * Where the new run lands. `datasetRuns` = R1 runs of the dataset (listed).
 * @returns {{label: string, experimentName: string, newExperiment: boolean,
 *   sentence: string}}
 */
export function landsAs({ parent, datasetRuns, quickTest, parentLabel }) {
  const base = parentLabel ?? runLabel(parent);
  if (quickTest)
    return {
      label: "Quick test",
      experimentName: parent?.experiment_name ?? "",
      newExperiment: false,
      sentence: `Runs as a quick test, not listed on the leaderboard, compared with ${base}.`,
    };
  const runs = datasetRuns ?? [];
  const expRuns = runs.filter((r) => r.experiment_id === parent?.experiment_id);
  const name = parent?.experiment_name ?? "";
  if (expRuns.length < MAX_RUNS_PER_EXPERIMENT) {
    const label = `#${ordinalOf(parent)}${nextLetterAfter(expRuns)}`;
    return {
      label,
      experimentName: name,
      newExperiment: false,
      sentence: `Lands as ${label} in "${name}", compared with ${base}.`,
    };
  }
  const iterName = `${name} · iterations`;
  const existing = runs.filter((r) => r.experiment_name === iterName);
  if (existing.length && existing.length < MAX_RUNS_PER_EXPERIMENT) {
    const label = `#${ordinalOf(existing[0])}${nextLetterAfter(existing)}`;
    return {
      label,
      experimentName: iterName,
      newExperiment: false,
      sentence: `This experiment has ${MAX_RUNS_PER_EXPERIMENT} runs. Lands as ${label} in "${iterName}", still compared with ${base}.`,
    };
  }
  const ordinal =
    1 + Math.max(0, ...runs.map(ordinalOf).filter((x) => isNum(x)));
  const label = `#${ordinal}A`;
  return {
    label,
    experimentName: iterName,
    newExperiment: true,
    sentence: `This experiment has ${MAX_RUNS_PER_EXPERIMENT} runs. Lands as ${label} in a new experiment "${iterName}", still compared with ${base}.`,
  };
}

// ------------------------------------------------------------- focus run

/**
 * The run an `Iterate` door opens on (R2-SPEC §4.2): the focused candidate
 * (`cand=`), else the baseline the URL names, else the dataset baseline
 * among the runs in view, else the page's baseline, else the first run.
 * @param {{runIds?: string[], runs?: object[], baselineId?: string|null,
 *   cand?: string|null, urlBaseline?: string|null}} opts  `runs` = R1 runs
 * @returns {string|null}
 */
export function focusRunOf({
  runIds = [],
  runs = [],
  baselineId,
  cand,
  urlBaseline,
}) {
  const ids = (runIds ?? []).filter(Boolean);
  const inView = (id) => Boolean(id) && (!ids.length || ids.includes(id));
  const datasetBaseline = (runs ?? []).find(
    (r) => r.is_dataset_baseline && inView(r.run_id),
  )?.run_id;
  return (
    [
      cand,
      urlBaseline !== "none" ? urlBaseline : null,
      datasetBaseline,
      baselineId,
      ids[0],
    ].find(inView) ?? null
  );
}

/**
 * focusRunOf over the runs Test a fix applies to (dataset integration FE-0): a run whose
 * kind has no change tab is never the dock's run. A run not loaded yet is
 * not ruled out (the door settles once the runs load).
 * @returns {string|null}
 */
export function iterableFocusOf({ runIds = [], runs = [], ...rest }) {
  const byId = new Map((runs ?? []).map((r) => [r.run_id, r]));
  const ok = (id) => !byId.has(id) || canIterate(byId.get(id));
  const ids = (runIds ?? []).filter(Boolean);
  const usable = ids.filter(ok);
  if (ids.length && !usable.length) return null;
  const id = focusRunOf({ runIds: usable, runs, ...rest });
  if (id && ok(id)) return id;
  return usable[0] ?? null;
}

const showValue = (v) =>
  v === null || v === undefined || v === "" ? "default" : String(v);
/**
 * options-truth L5 G2: a saved version's commit message from the changes it
 * makes (pure): `max iterations 12 → 5 · respond prompt edited`.
 * @param {object[]} changes  I1 changes (collectChanges)
 * @param {object} [config]   the parent's run config
 */
export function commitMessageOf(changes = [], config = {}) {
  const c = config ?? {};
  return (changes ?? [])
    .map((x) => {
      const axis = x.axis ?? (x.node ? "agent" : "prompt");
      if (axis === "agent" && x.field === "max_iterations")
        return `max iterations ${showValue(c.agent?.limits?.max_iterations)} → ${x.value}`;
      if (axis === "agent")
        return `${x.node ?? "agent"} ${x.field ?? "prompt"} edited`;
      if (axis === "prompt") {
        const i = /^messages\.(\d+)$/.exec(String(x.field ?? ""))?.[1];
        return i != null
          ? `prompt message ${Number(i) + 1} edited`
          : "prompt edited";
      }
      if (axis === "model")
        return `model ${showValue(c.harness?.model ?? runModelOf(c))} → ${x.value}`;
      if (axis === "params")
        return `${String(x.field).replace(/_/g, " ")} ${showValue(c.model?.params?.[x.field])} → ${x.value}`;
      if ((axis === "tools" || axis === "tool") && x.field === "max_tool_turns")
        return `max tool turns ${showValue(c.max_tool_turns)} → ${x.value}`;
      if (axis === "tools" || axis === "tool") {
        const was = (c.tools ?? []).find((t) => t.name === x.node)?.mode;
        return `${x.node} ${showValue(was)} → ${x.value}`;
      }
      return null;
    })
    .filter(Boolean)
    .join(" · ");
}
