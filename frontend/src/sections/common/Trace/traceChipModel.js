// What a result's TraceChip says (R2-SPEC §2.1). Pure. Reads ONLY the
// additive per-result `trace_summary[trial-1]` (r2Shapes.js TraceSummary);
// it never fetches a trace.
//
// Frozen API (R2 manifest row 42): TRACE_CHIP_STATES, cellTraceChipProps,
// traceSummaryLabel, noTraceTooltip.

export const TRACE_CHIP_STATES = Object.freeze([
  "ok",
  "running",
  "error",
  "none",
  "carried",
  "infra",
]);

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

/**
 * `6 steps · 2 tool calls` · `14 turns · 7 tool calls` (harness) ·
 * `1 LLM call` (no tools). Matches results/drawer/alignSteps.js totalsText()
 * without the time.
 */
export function traceSummaryLabel(summary) {
  const steps = num(summary?.steps);
  const tools = num(summary?.tool_calls);
  const llm = num(summary?.llm_calls);
  if (summary?.turns !== null && summary?.turns !== undefined)
    return `${count(num(summary.turns), "turn", "turns")} · ${count(tools, "tool call", "tool calls")}`;
  if (steps > 0 && tools === 0 && llm === steps)
    return count(llm, "LLM call", "LLM calls");
  return `${count(steps, "step", "steps")} · ${count(tools, "tool call", "tool calls")}`;
}

/** Why a result has no trace, by the run's source kind. */
export function noTraceTooltip(sourceKind) {
  if (sourceKind === "http")
    return 'This endpoint didn\'t return a trace id. Set "Trace id path" on the run.';
  if (sourceKind === "upload")
    return "This upload has no trace_id. Add trace_id or spans to the payload.";
  return "No trace was recorded for this result.";
}

/** The one tool mode when every tool span shares it, else null. */
function singleMode(summary) {
  const modes = (summary?.tool_modes ?? []).filter(Boolean);
  if (!modes.length || num(summary?.tool_calls) === 0) return null;
  return modes.every((m) => m === modes[0]) ? modes[0] : null;
}

const MODE_TEXT = {
  mock: "Tools answered from fixtures (mock)",
  recorded: "Tools replayed from recordings",
  live: "Tools called live",
};

/**
 * Chip props for one result and trial.
 * @param {object} result  R3 result / matrix cell (with `trace_summary[]`)
 * @param {number} [trial=1]  1-based
 * @param {{sourceKind?: string}} [opts]  runKind of the run (for `none`)
 * @returns {{state: "ok"|"running"|"error"|"none"|"carried"|"infra",
 *   label: string, mode: "mock"|"recorded"|"live"|null, tooltip: string}}
 */
export function cellTraceChipProps(result, trial = 1, { sourceKind } = {}) {
  const t = Math.max(1, Number(trial) || 1);
  const summary = (result?.trace_summary ?? [])[t - 1] ?? null;
  const status = summary?.status ?? "none";

  if (!summary || status === "none")
    return {
      state: "none",
      label: "No trace",
      mode: null,
      tooltip: noTraceTooltip(sourceKind),
    };

  if (status === "running") {
    const label = `Running · step ${Math.max(1, num(summary.steps))}`;
    return { state: "running", label, mode: null, tooltip: label };
  }

  if (status === "error") {
    const step = num(summary.error?.step ?? summary.steps);
    const label =
      summary.error?.timeout_s !== undefined &&
      summary.error?.timeout_s !== null
        ? `Timed out at step ${step} · ${num(summary.error.timeout_s)} s`
        : `Failed at step ${step}${summary.error?.text ? ` · ${summary.error.text}` : ""}`;
    return {
      state: "error",
      label,
      mode: singleMode(summary),
      tooltip: `${label} (${traceSummaryLabel(summary)})`,
    };
  }

  if (status === "carried") {
    const from = summary.carried_from ?? "the parent run";
    const label = `Carried from ${from}`;
    return {
      state: "carried",
      label,
      mode: null,
      tooltip: `Not rerun in this iteration; the result and trace are ${from}'s.`,
    };
  }

  if (status === "infra") {
    const label = `Didn't run · ${summary.reason ?? "setup failed"}`;
    return {
      state: "infra",
      label,
      mode: null,
      tooltip: `${label}. Infra failures are excluded from pass rates.`,
    };
  }

  const label = traceSummaryLabel(summary);
  const mode = singleMode(summary);
  return {
    state: "ok",
    label,
    mode,
    tooltip: mode ? `${label} · ${MODE_TEXT[mode] ?? mode}` : label,
  };
}

/**
 * IT-R1 G4: the ONE "does this try have a trace" predicate (Why, Steps,
 * Graph and Timeline all read it): recorded steps, a trace id (spans), a
 * call transcript (`output_kind: "call"` or a call value with turns) or a
 * harness transcript. Pure.
 * @param {object} trial  task-compare trial (`steps`, `trace_id`, `output`,
 *   `output_kind`, `workspace`)
 */
export function trialHasTrace(trial) {
  if (!trial || typeof trial !== "object") return false;
  if ((trial.steps ?? []).length > 0) return true;
  if (trial.trace_id) return true;
  if (trial.output_kind === "call") return true;
  const out = trial.output;
  const call =
    out && typeof out === "object"
      ? out
      : typeof out === "string" && out.trim().startsWith("{")
        ? (() => {
            try {
              return JSON.parse(out);
            } catch {
              return null;
            }
          })()
        : null;
  if (call?.kind === "call" && (call.turns?.length || call.trace_id))
    return true;
  return Boolean(trial.workspace?.transcript?.length);
}
