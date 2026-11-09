// Search and grouping rules shared by the run pickers (BaselinePicker,
// RunPickerPopover).
import { parseRunLabel } from "./runLabel";

/** BaselinePicker value for "no baseline" (absolute mode). */
export const NO_BASELINE = "none";

/**
 * Every name a run is found by (IT-R2 G2 P1: also its framework, harness,
 * voice platform and every model it called, so `crewai` finds #3C and
 * `sonnet` finds every the coding harness run).
 */
export function runSearchWords(run) {
  const t = run?.target ?? {};
  const c = run?.config ?? {};
  return [
    run?.label?.replace(/^#/, ""),
    run?.name,
    run?.experiment_name,
    t.model,
    t.prompt_name,
    t.graph_name,
    t.branch,
    t.commit,
    t.framework,
    t.harness,
    c.framework?.name,
    c.framework?.id,
    c.harness?.name,
    c.harness?.id,
    c.voice?.provider,
    c.voice?.providerName,
    c.code?.framework,
    ...(Array.isArray(c.models) ? c.models.map((m) => m?.id) : []),
  ].filter(Boolean);
}

/** Case-insensitive match on `#4A`, `4A`, names, framework, harness, models. */
export function runMatches(run, query) {
  const q = String(query ?? "")
    .trim()
    .toLowerCase()
    .replace(/^#/, "");
  if (!q) return true;
  // W1-1: `#2A.2`, `#2A-2` and `#2A 2` find `#2A·2` (people can't type `·`).
  const typed = parseRunLabel(q.toUpperCase());
  if (typed?.attempt && run?.label) {
    const own = parseRunLabel(run.label);
    if (
      own &&
      own.ordinal === typed.ordinal &&
      own.letter === typed.letter &&
      own.attempt === typed.attempt
    )
      return true;
  }
  return runSearchWords(run).some((s) => String(s).toLowerCase().includes(q));
}

/** True when two runs call the same prompt or agent graph. */
export function sameTarget(a, b) {
  if (!a || !b) return false;
  const ta = a.target ?? {};
  const tb = b.target ?? {};
  if (ta.graph_id && tb.graph_id) return ta.graph_id === tb.graph_id;
  if (ta.prompt_id && tb.prompt_id) return ta.prompt_id === tb.prompt_id;
  if (ta.url && tb.url) return ta.url === tb.url;
  if (a.target_type === "external" && b.target_type === "external") return true;
  return false;
}
