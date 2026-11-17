import { formatExactMoney } from "src/sections/common/format/money";
// Try on one row (R2-SPEC §5.3.2, Fix 3): the pure parts. A run card's
// config as an I1 quick-test `config`, the row picker's labels and search,
// and the result panel's one-line summary. Nothing here is a run: the
// panel says `not saved`.
import { PROMPT_CONFIG_TYPE } from "./constants";
import { configTools, promptToolsPayload } from "./toolSetup";
import { cardExtrasOf } from "src/sections/common/RunSettings/cardExtras";

const isScalar = (v) =>
  v === null || ["string", "number", "boolean"].includes(typeof v);

/**
 * L3 G8 P5: a card's params for one model as the create body sends them
 * (the form keys a dotted model id as `gemini-2__DOT__5-flash`): scalar
 * values and the response format.
 */
export function cardModelParams(card, modelId) {
  const all = card?.modelParams ?? {};
  const raw =
    all[modelId] ?? all[String(modelId ?? "").replace(/\./g, "__DOT__")];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw).filter(
      ([k, v]) =>
        isScalar(v) ||
        (k === "responseFormat" && v && typeof v === "object") ||
        (k === "response_format" && v && typeof v === "object"),
    ),
  );
}

const mappingOf = (card) =>
  card?.variableMapping && Object.keys(card.variableMapping).length
    ? { variable_mapping: { ...card.variableMapping } }
    : {};

/** Kinds `Try on one row` works on (code / traces / HTTP: no button). */
export const TRY_KINDS = Object.freeze(["prompt", "agent", "harness"]);
export const canTryKind = (kind) => TRY_KINDS.includes(kind);

/** Harness phases the panel shows while the sandbox works (prototype ~6 s). */
export const HARNESS_PHASES = Object.freeze([
  { label: "Starting sandbox", at_ms: 0 },
  { label: "Running", at_ms: 1500 },
  { label: "Checking", at_ms: 4500 },
]);

const cellText = (cell) => {
  const v = cell && typeof cell === "object" ? cell.cell_value : cell;
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
};

/** 1-based row number from a table row (`order` is 0-based, may be a string). */
export function rowNumberOf(row) {
  const n = Number(row?.order);
  return Number.isFinite(n) ? n + 1 : null;
}

/** `T1` on task suites, else `Row 1`. */
export function rowLabel(row, { datasetKind } = {}) {
  const n = rowNumberOf(row);
  if (n === null) return "Row";
  return datasetKind === "task_suite" ? `T${n}` : `Row ${n}`;
}

/**
 * A short title for the row picker: the `title` column when there is one,
 * else the first text input (one line, 80 chars).
 */
export function rowTitle(row, columns = []) {
  if (!row) return "";
  const byName = (name) =>
    columns.find((c) => String(c?.name).toLowerCase() === name);
  const titleCol = byName("title");
  const pick =
    (titleCol && cellText(row[titleCol.id])) ||
    columns
      .filter((c) => !c?.data_type || c.data_type === "text")
      .map((c) => cellText(row[c.id]))
      .find((t) => t && t.trim());
  const text = String(pick ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 80 ? `${text.slice(0, 79)}…` : text;
}

/** Row picker search: by number (`7`, `T7`, `Row 7`) or by title words. */
export function filterRows(rows, query, { columns = [], datasetKind } = {}) {
  const q = String(query ?? "")
    .trim()
    .toLowerCase();
  if (!q) return rows ?? [];
  const num = q.replace(/^(row|t)\s*/i, "");
  // IT-R1 G1: `5` + Enter picks Row 5, not the first title with a 5 in it.
  if (/^\d+$/.test(num)) {
    const exact = (rows ?? []).filter((r) => String(rowNumberOf(r)) === num);
    if (exact.length) {
      const rest = (rows ?? []).filter(
        (r) =>
          String(rowNumberOf(r)) !== num &&
          (String(rowNumberOf(r)).startsWith(num) ||
            rowTitle(r, columns).toLowerCase().includes(q)),
      );
      return [...exact, ...rest];
    }
  }
  return (rows ?? []).filter((r) => {
    const n = rowNumberOf(r);
    if (/^\d+$/.test(num) && String(n).startsWith(num)) return true;
    if (rowLabel(r, { datasetKind }).toLowerCase() === q) return true;
    return rowTitle(r, columns).toLowerCase().includes(q);
  });
}

/**
 * The I1 quick-test `config` of a run card (r2Shapes RunConfig): the card
 * as it is now. Prompt: first model, the card's tools and modes. Agent:
 * graph + version. Harness: the card's `harness_config`. Null when the card
 * cannot be tried yet (no version / model).
 */
export function cardRunConfig(card) {
  const type = card?.targetType;
  if (type === PROMPT_CONFIG_TYPE.PROMPT) {
    const model = (card.model ?? []).find((m) => m?.value);
    if (!card.promptVersion || !model) return null;
    const tools = configTools(card);
    // The create body's tool keys (modes, Live endpoints, on_unrecorded).
    const sent = promptToolsPayload(card);
    const endpointOf = (name) =>
      sent.tools?.find((t) => t.name === name)?.endpoint ?? null;
    return {
      kind: "prompt",
      prompt: {
        id: card.promptId ?? null,
        name: card.name ?? null,
        version: card.versionLabel ?? null,
        version_id: card.promptVersion,
      },
      model: {
        id: model.value,
        params: cardModelParams(card, model.value),
      },
      ...mappingOf(card),
      ...(tools.length
        ? {
            tools: tools.map((t) =>
              endpointOf(t.name) ? { ...t, endpoint: endpointOf(t.name) } : t,
            ),
            max_tool_turns:
              sent.max_tool_turns ??
              (Number(card.toolSetup?.maxToolTurns) || 8),
            ...(card.toolSetup?.onUnrecorded
              ? { on_unrecorded: sent.on_unrecorded }
              : {}),
          }
        : {}),
      ...cardExtrasOf(card),
      trials: 1,
    };
  }
  if (type === PROMPT_CONFIG_TYPE.AGENT) {
    if (!card.agentVersion) return null;
    return {
      kind: "agent",
      agent: {
        graph_id: card.agentId ?? null,
        name: card.name ?? null,
        version: card.versionLabel ?? null,
        version_id: card.agentVersion,
      },
      ...mappingOf(card),
      ...cardExtrasOf(card),
      trials: 1,
    };
  }
  if (type === PROMPT_CONFIG_TYPE.HARNESS) {
    const h = card.harness_config ?? {};
    return {
      kind: "harness",
      harness: {
        name: h.name ?? null,
        model: h.model ?? null,
        sandbox: h.sandbox ?? null,
        max_turns: h.limits?.max_turns ?? null,
        timeout_s: h.limits?.timeout_s ?? null,
        max_cost_usd: h.limits?.max_cost_usd ?? null,
      },
      trials: 1,
    };
  }
  return null;
}

/**
 * `$0.004` · `$0.011` (3 decimals under 10 cents) · `$0.21` · `$6.21`;
 * under a tenth of a cent: two significant digits (`$0.0006`, `$0.00024`),
 * as the trace header prints it.
 */
export function formatUsd(v) {
  return formatExactMoney(v, {
    minimumFractionDigits:
      Number(v) === 0 ? 0 : Math.abs(Number(v)) < 0.1 ? 3 : 2,
  });
}

/** `2.1 s` · `74 s` · `1 min` · `412 ms`. */
export function formatWall(ms) {
  if (ms === null || ms === undefined || ms === "") return "—";
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  const s = n / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

/** The modes of the tools that ran: `mock` · `recorded` · `live` · null. */
export function toolModeOf(summary) {
  const modes = [...new Set((summary?.tool_modes ?? []).filter(Boolean))];
  if (!modes.length || !summary?.tool_calls) return null;
  return modes.length === 1 ? modes[0] : "mixed";
}

/** First `n` lines of the reply and whether there is more. */
export function clampLines(text, n = 4) {
  const lines = String(text ?? "").split("\n");
  return {
    head: lines.slice(0, n).join("\n"),
    more: lines.length > n,
    lines: lines.length,
  };
}

/**
 * The evals that would score this attempt: Checks always (they belong to
 * the row), judges only when the Evaluations step has evals.
 */
export function visibleEvals(evals, { hasEvals = false } = {}) {
  return (evals ?? []).filter((e) => e?.type === "check" || hasEvals);
}

/** `order_found ✓` / `✗` / `–` (not reached). */
export function evalGlyph(e) {
  if (e?.passed === true) return "✓";
  if (e?.passed === false) return "✗";
  return "–";
}

/** Short eval name: `check:order_found` → `order_found`. */
export const evalShortName = (e) =>
  String(e?.key ?? e?.name ?? "").replace(/^(check|eval):/, "") || e?.name;

/** What the running panel says after `elapsedMs`. */
export function runningLabel(kind, elapsedMs) {
  const t = Math.max(0, Number(elapsedMs) || 0);
  if (kind === "harness") {
    const phase = [...HARNESS_PHASES].reverse().find((p) => t >= p.at_ms);
    return phase?.label ?? HARNESS_PHASES[0].label;
  }
  return `Running · step ${Math.min(9, 1 + Math.floor(t / 350))}`;
}

/** Inline error text: `Couldn't run this row: …`. */
export function tryErrorText(error) {
  const msg =
    error?.response?.data?.result ??
    error?.result ??
    error?.message ??
    "Something went wrong.";
  const text = typeof msg === "string" ? msg : "Something went wrong.";
  return `Couldn't run this row: ${text}`;
}

// ------------------------------------------------ IT-R1 G1 P10 (truth)

/** Tool evals that cannot pass when the try made no tool call. */
export const TOOL_EVAL_KEYS = Object.freeze([
  "eval:tool_call_accuracy",
  "eval:argument_match",
]);
export const isToolEval = (e) =>
  TOOL_EVAL_KEYS.includes(e?.key) ||
  TOOL_EVAL_KEYS.includes(`eval:${evalShortName(e)}`);

/** The reason a tool eval reads when the try made 0 tool calls. */
export const NO_TOOL_CALL_REASON =
  "No tool call was made, so there is nothing to score as correct.";

// Past-tense / done-deal claims an answer can only make after a tool acted
// (`Cancelled the kettle`, `I've refunded $64.50`, `arrives Tuesday`).
const ACTION_CLAIM =
  /\b(cancel+ed|refunded|issued (?:a|the|your) (?:refund|credit|replacement)|processed|rescheduled|updated (?:your|the)|changed (?:your|the)|re-?shipped|shipped|is in transit|arrives?\b|will arrive|scheduled (?:a|the|your)|escalated|booked|taken care of|(?:has|have) been (?:cancel+ed|refunded|updated|changed|processed|sent))\b/i;

/** Does a reply claim an action was taken? (pure, conservative) */
export function claimsAction(text) {
  return ACTION_CLAIM.test(String(text ?? ""));
}

/**
 * `Says it acted, but made no tool call`: the reply claims an action and the
 * try made 0 tool calls (and the run had tools to call). Null otherwise.
 * @param {{output?: string, trace_summary?: {tool_calls?: number}}} attempt
 * @param {{hasTools?: boolean}} [o]
 */
export function unbackedClaim(attempt, { hasTools = true } = {}) {
  if (!attempt || !hasTools) return null;
  const calls = Number(attempt.trace_summary?.tool_calls ?? 0);
  if (calls > 0 || !claimsAction(attempt.output)) return null;
  return {
    label: "Says it acted, but made no tool call",
    detail:
      "The reply claims an action (a cancel, a refund, a delivery date) but the model never called a tool, so nothing happened. Check the tool's description and that the prompt asks the model to use it.",
  };
}

/**
 * Tool evals fail on a try with zero tool calls (pure): the rest pass
 * through untouched.
 */
export function scoreToolEvals(evals, { toolCalls = 0, hasTools = true } = {}) {
  if (!hasTools || Number(toolCalls) > 0) return evals ?? [];
  return (evals ?? []).map((e) =>
    isToolEval(e) ? { ...e, passed: false, reason: NO_TOOL_CALL_REASON } : e,
  );
}

/**
 * `2 variables are not mapped — the model will see empty text` (null when
 * every variable has a column).
 * @param {string[]} variables
 * @param {Record<string,string>} mapping
 */
/**
 * IT-R2 G1 P10: unmapped variables block the try (pure): `2 variables are
 * not mapped: {{customer_email}}, {{order_context}}. The model would see
 * empty text.`
 */
// IT-R4 G1 P1: `Map customer_email first` (with `Go to Data`).
export function unmappedBlockText(missing = []) {
  const n = missing.length;
  if (!n) return null;
  if (n === 1) return `Map ${missing[0]} first`;
  if (n === 2) return `Map ${missing[0]} and ${missing[1]} first`;
  return `Map ${missing[0]} and ${n - 1} more variables first`;
}

export function unmappedWarning(variables, mapping = {}) {
  const missing = (variables ?? []).filter((v) => !mapping?.[v]);
  if (!missing.length) return null;
  const n = missing.length;
  return {
    missing,
    text: `${n} ${n === 1 ? "variable is" : "variables are"} not mapped — the model will see empty text`,
  };
}
