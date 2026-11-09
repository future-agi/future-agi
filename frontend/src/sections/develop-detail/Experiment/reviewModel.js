// What Review & run says before the run starts (R4 lane Ra MUST 7, P4, P6).
// Pure: from the form values, the dataset's columns and the rows in scope.
import { JSON_PATH_SEPARATOR } from "./variableMapping";
import {
  BUILTIN_TOOLS,
  PERMISSION_LABELS,
} from "src/sections/common/RunSources/recipe/recipeModel";
import { mcpToolCount } from "src/sections/common/Runs/provenanceModel";
import { runUsesOf } from "src/sections/common/RunSettings/runUses";
import { fmtCost } from "src/sections/common/format/fmt";
import { resolvedAtOnce } from "./estimateInputs";
import { cardTools } from "./toolSetup";
import { runInputsOf } from "./runInputs";
import { shuffled } from "src/utils/seededRandom";
import { reducerLabel } from "src/sections/common/Analysis/trialReducers";

const cellValue = (row, columnId) => {
  const c = row?.[columnId];
  if (c && typeof c === "object" && "cell_value" in c) return c.cell_value;
  return c;
};
const isEmpty = (v) =>
  v === null ||
  v === undefined ||
  (typeof v === "string" && !v.trim()) ||
  (Array.isArray(v) && !v.length);

const MODALITY = {
  image: "image",
  images: "image",
  audio: "audio",
  document: "document",
  video: "video",
};

/**
 * `Each row sends customer_message (text)` (P6, ir-10): the columns the
 * runs read, with their modality, a nested path as mapped
 * (`customer_message ← conversation.$[-1].content`); agent, A2A and MCP
 * runs add what their card sends (runInputs.runInputsOf). v2.1
 * (review:rows-send): no claim about which turn a path holds.
 */
export function rowsSendLine(
  promptConfig = [],
  columns = [],
  { inputColumn } = {},
) {
  const byId = Object.fromEntries((columns ?? []).map((c) => [c.id, c]));
  const sends = new Map();
  (promptConfig ?? []).forEach((card) => {
    (runInputsOf(card, columns) ?? []).forEach((t) => sends.set(t, "fixed"));
    // IT-R2 G1 P10: a prompt variable says which column feeds it
    // (`user_request ← instruction`).
    Object.entries(card?.variableMapping ?? {}).forEach(([variable, v]) => {
      // IT-R4 G1 P1: `customer_tier (empty)` · `customer_email = "x"`.
      if (v && typeof v === "object") {
        if (v.kind === "empty") sends.set(`${variable} (empty)`, "fixed");
        else if (v.kind === "constant" && v.value)
          sends.set(`${variable} = "${v.value}"`, "fixed");
        return;
      }
      const [id, path] = String(v ?? "").split(JSON_PATH_SEPARATOR);
      const col = byId[id];
      if (!col) return;
      const key =
        variable && variable !== col.name
          ? `${variable} ← ${col.name}${path ? `.${path}` : ""}`
          : col.name;
      sends.set(key, MODALITY[col.data_type] ?? "text");
    });
    if (card?.r4 && inputColumn && !sends.has(inputColumn)) {
      const col = (columns ?? []).find((c) => c.name === inputColumn);
      sends.set(inputColumn, MODALITY[col?.data_type] ?? "text");
    }
  });
  if (!sends.size) return null;
  const list = [...sends.entries()]
    .map(([n, m]) =>
      m === "fixed" || (m === "text" && n.includes(" ← ")) ? n : `${n} (${m})`,
    )
    .join(" · ");
  return `Each row sends ${list}`;
}

/**
 * Evals whose mapped input is empty on some rows in scope (P4, ir-5):
 * `[{index, evalName, column, missing, total}]`. Those rows score
 * `Skipped`, never `Pass`.
 */
// A mapping key that names an eval's reference (expected answer).
const REFERENCE_KEY = /expected|reference|ground_?truth|answer/i;
// Evals that compare with a reference even when their mapping names none.
const REFERENCE_EVAL = /correctness/i;

export function emptyInputWarnings(
  evals = [],
  rows = [],
  columns = [],
  { useStats = false, referenceColumnId } = {},
) {
  const byId = Object.fromEntries((columns ?? []).map((c) => [c.id, c]));
  // Dataset evals may map by column name (`expected: "expected_reply"`).
  const byName = Object.fromEntries((columns ?? []).map((c) => [c.name, c]));
  const out = [];
  (evals ?? []).forEach((e, index) => {
    const own = e?.config?.mapping ?? e?.mapping ?? {};
    // L5 G3: an eval that needs a reference and maps none reads the chosen
    // Expected answers column (the create path's `column_id`).
    const needsRef =
      referenceColumnId &&
      !Object.keys(own).some((k) => REFERENCE_KEY.test(k)) &&
      REFERENCE_EVAL.test(
        String(e?.name ?? e?.evalTemplateName ?? e?.templateName ?? ""),
      );
    const mapping = needsRef ? { ...own, expected: referenceColumnId } : own;
    Object.values(mapping).forEach((v) => {
      const ref = String(v ?? "").split(JSON_PATH_SEPARATOR)[0];
      const col = byId[ref] ?? byName[ref];
      if (!col) return;
      const id = col.id;
      // All rows: the column's own stats (every row); a selection: its rows.
      const total = useStats
        ? Number(col.stats?.total) || 0
        : (rows ?? []).length;
      if (!total) return;
      const missing = useStats
        ? Number(col.stats?.empty_count) || 0
        : rows.filter((r) => isEmpty(cellValue(r, id))).length;
      if (!missing) return;
      if (out.some((w) => w.index === index && w.column === col.name)) return;
      out.push({
        index,
        evalName: e?.name ?? e?.evalTemplateName ?? "This eval",
        column: col.name,
        missing,
        total,
      });
    });
  });
  return out;
}

/** `Correctness needs expected_reply · 6 of 6 rows have none · …` */
/**
 * L5 G3: the warning over the rows that run, naming the column:
 * `Correctness needs intent · empty on 1 of 20 selected rows · those rows
 * show Skipped`.
 */
export const emptyInputLine = (w, rowNoun = "row", { selected = false } = {}) =>
  `${w.evalName} needs ${w.column} · empty on ${w.missing} of ${w.total}${selected ? " selected" : ""} ${rowNoun}${w.total === 1 ? "" : "s"} · those ${rowNoun}s show Skipped`;

export const emptyInputText = (w, rowNoun = "row") =>
  `${w.evalName} needs ${w.column} · ${w.missing} of ${w.total} ${rowNoun}${w.total === 1 ? "" : "s"} ${w.missing === 1 ? "has" : "have"} none · those ${rowNoun}s will show Skipped, not Pass`;

/**
 * IT-R5 G1 P6: a run's tools, MCP servers and permissions, as short text
 * (`—` when empty; a code run with nothing declared: `your tools`).
 */
export function usesOf(kind, recipe, r4) {
  if (kind === "harness") {
    const on = BUILTIN_TOOLS.filter(
      (t) => recipe?.builtin_tools?.[t.id] ?? t.default,
    ).map((t) => t.label);
    const mcp = (recipe?.mcp_servers ?? []).map((m) => {
      const n = mcpToolCount(m);
      return n ? `${m.name} (${n} tools)` : m.name;
    });
    return {
      tools: on.length ? on.join(", ") : "—",
      mcp: mcp.length ? mcp.join(", ") : "—",
      permissions:
        PERMISSION_LABELS[recipe?.permissions ?? "edit"] ??
        recipe?.permissions ??
        "—",
    };
  }
  const d = r4?.declared ?? null;
  const tools = (d?.tools ?? []).filter(Boolean);
  const mcp = (d?.mcp ?? []).filter(Boolean);
  if (kind === "code")
    return {
      tools: tools.length ? tools.join(", ") : "your tools",
      mcp: mcp.length ? mcp.join(", ") : "—",
      permissions: "—",
    };
  return {
    tools: tools.length ? tools.join(", ") : "—",
    mcp: mcp.length ? mcp.join(", ") : "—",
    permissions: "—",
  };
}

/**
 * IT-R5 G1 P4: caps in one unit: `$1.00 per try`; a per-run cap reads
 * `$0.17 per try · $5.00 per run of 30`.
 */
export function capText(
  cap,
  { perRun = false, tries = 0, runLevel = false } = {},
) {
  const c = Number(cap);
  if (!Number.isFinite(c) || c <= 0) return null;
  // L5 G3: a run-level cap (no per-try cap exists) reads as the run's:
  // `$5.00 for the run of 60 tries (≈ $0.083 a try)`.
  if (runLevel) {
    const t = Math.max(0, Math.round(Number(tries) || 0));
    return t
      ? `${fmtCost(c)} for the run of ${t.toLocaleString("en-US")} ${t === 1 ? "try" : "tries"} (≈ ${fmtCost(c / t)} a try)`
      : `${fmtCost(c)} for the run`;
  }
  if (!perRun) return `${fmtCost(c)} per try`;
  const t = Math.max(0, Math.round(Number(tries) || 0));
  if (!t) return `${fmtCost(c)} per run`;
  return `${fmtCost(c / t)} per try · ${fmtCost(c)} per run of ${t}`;
}

/** The model a code card's run uses (S5), or null when none is known. */
function codeModelOf(r4) {
  if (!r4) return null;
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

/**
 * IT-R2 G1 P7: what each run uses, side by side (pure): `[{label, name,
 * model, atOnce, capPerTry}]` and, when runs differ, one sentence:
 * `Runs differ: the coding harness Code uses claude-sonnet-4-5, the coding harness CLI uses
 * gpt-5-codex (each harness runs its own vendor's models) · 2 vs 4 at a
 * time`. Model / cap come from the run's own recipe (or its default).
 * @param {object[]} cards  promptConfig
 * @param {{letter, name, configIndex}[]} runs  flattenRuns(cards)
 * @param {{ordinal?: number, defaultsOf?: Function}} [opts]
 */
export function runsCompareTable(cards = [], runs = [], opts = {}) {
  const { ordinal, defaultsOf, maxConcurrency } = opts;
  const rows = (runs ?? []).map((run) => {
    const card = cards?.[run.configIndex] ?? {};
    const r4 = card.r4 ?? null;
    const recipe =
      r4?.slot?.harness ??
      (r4?.kind === "harness" && defaultsOf ? defaultsOf(r4.entryId) : null);
    const kind = r4?.kind ?? run.targetType;
    const model =
      recipe?.model ??
      card.harness_config?.model ??
      (run.model ? String(run.model) : null) ??
      null;
    const atOnce =
      Number(r4?.limits?.concurrency) ||
      Number(r4?.slot?.voice?.concurrency) ||
      null;
    // v2.1 (review:compare-table): an endpoint card's cap is its own
    // limits row (Tries per row · judge spend cap), as the line above says.
    const endpointCap = ENDPOINT_CAP_KINDS.has(kind)
      ? Number(card?.limits?.maxCostUsd) || null
      : null;
    const capPerTry =
      kind === "harness"
        ? Number(recipe?.budget?.max_cost_usd) || null
        : Number(r4?.limits?.maxCostUsd) || endpointCap || null;
    // options-truth L3 G3 (S5): the number the run plays at.
    const resolved = resolvedAtOnce(card, { maxConcurrency }).value;
    const uses = {
      ...usesOf(kind, recipe, r4),
      ...runUsesOf(card, { kind, run }),
    };
    // v2.1: a prompt run's tools are the ones its card sends.
    if (kind === "prompt" && (!uses.tools || uses.tools === "—")) {
      const names = promptToolNames(card);
      if (names.length) uses.tools = names.join(", ");
    }
    return {
      label: ordinal != null ? `#${ordinal}${run.letter}` : run.letter,
      name: r4?.kind === "voice" ? run.name : r4?.entryName ?? run.name,
      configIndex: run.configIndex,
      atOnceResolved: resolved ?? atOnce,
      kind,
      // IT-R4 G1 P5: a Your code run's declared model, when given.
      // options-truth L3 G3 (S5): a changed model param, else the model
      // the agent declared when it connected.
      model:
        uses.model ??
        model ??
        (kind === "code" ? codeModelOf(r4) : null) ??
        r4?.declared?.model ??
        (kind === "code" ? "your code's model" : null),
      atOnce,
      capPerTry,
      capIsRun: kind !== "harness",
      capIsJudges: endpointCap != null && capPerTry === endpointCap,
      tools: uses.tools ?? "—",
      mcp: uses.mcp ?? "—",
      // v2.1: the secrets a run sends (endpoint headers / A2A auth); the
      // harness permission levels are gone with the harness source.
      permissions:
        kind === "harness" || uses.permissions === "—"
          ? null
          : uses.permissions ?? null,
    };
  });
  const harnesses = rows.filter((r) => r.kind === "harness" && r.model);
  const models = [...new Set(harnesses.map((r) => r.model))];
  const conc = [
    ...new Set(rows.map((r) => r.atOnceResolved ?? r.atOnce).filter(Boolean)),
  ];
  const bits = [];
  if (models.length > 1)
    bits.push(
      `${harnesses.map((r) => `${r.name} uses ${r.model}`).join(", ")} (each harness runs its own vendor's models)`,
    );
  // v2.1 (review:compare-table): any column that differs between runs.
  const differs = (key) =>
    new Set(rows.map((r) => String(r[key] ?? "—"))).size > 1;
  const named = (key, word) =>
    bits.push(
      `${word}: ${rows.map((r) => `${r.label} ${r[key] ?? "—"}`).join(" · ")}`,
    );
  if (!models.length && differs("model")) named("model", "model");
  if (differs("tools")) named("tools", "tools");
  if (differs("mcp")) named("mcp", "MCP");
  if (conc.length > 1) bits.push(`${conc.join(" vs ")} at a time`);
  if (differs("capPerTry"))
    bits.push(
      `cap: ${rows.map((r) => `${r.label} ${r.capPerTry ? fmtCost(r.capPerTry) : "none"}`).join(" · ")}`,
    );
  return {
    rows,
    differ: bits.length ? `Runs differ: ${bits.join(" · ")}` : null,
    limitsDiffer: conc.length > 1,
    hasPermissions: rows.some((r) => r.permissions),
  };
}

const ENDPOINT_CAP_KINDS = new Set(["http", "a2a", "mcp_tool"]);

/** The tool names a prompt card sends (toolSetup.cardTools, as the body). */
function promptToolNames(card) {
  return cardTools(card?.toolSetup ?? {}, card?.configuration?.tools)
    .map((t) => t?.name)
    .filter(Boolean);
}

/**
 * v2.1 (review:summary-cards): the Settings card's scoring line, the same
 * values the create body sends: `Errored tries count as 0 · each row scored
 * by Median of its 3 tries` (the reducer only when a row has 2+ tries).
 */
export function scoringSummary({
  errorPolicy,
  trialReducer,
  maxTries = 1,
} = {}) {
  const policy =
    errorPolicy === "exclude"
      ? "Errored tries left out of scores"
      : "Errored tries count as 0";
  const k = Math.max(1, Math.round(Number(maxTries) || 1));
  if (k <= 1) return policy;
  return `${policy} · each row scored by ${reducerLabel(trialReducer ?? "mean", k)} of its ${k} tries`;
}

/**
 * v2.1 (review:empty-input-warning): the rows a seeded random sample runs,
 * the same pick the create makes (experimentsCreate.sampleOfBody: rows in
 * dataset order, `shuffled(ids, "sample", seed)`, the first n). Pure.
 * @param {object[]} rows  every row in scope (table rows, dataset order)
 * @param {{n: number, seed: number}|null} sample
 */
export function sampleRowsOf(rows = [], sample = null) {
  if (!sample?.n) return rows ?? [];
  const ordered = (rows ?? [])
    .map((r, i) => ({ r, k: Number(r?.order ?? r?.row_order ?? r?.n ?? i) }))
    .sort((a, b) => a.k - b.k)
    .map((x) => x.r);
  const byId = new Map(ordered.map((r) => [r.row_id ?? r.id, r]));
  return shuffled([...byId.keys()], "sample", String(sample.seed ?? 0))
    .slice(0, sample.n)
    .map((id) => byId.get(id));
}
