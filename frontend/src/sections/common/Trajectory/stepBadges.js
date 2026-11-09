// W1-9 B3 · step badges of a trajectory row (pure). Each span carries the
// verdicts of the evals and checks that judged it (`eval_scores`, placed by
// the backend; the prototype's traceVerdicts). A row shows them failing
// first (`✕ refund_amount`), then score errors, N/A and skipped; passing
// verdicts stay in the tree's shield count. LLM rows say their tokens:
// `prompt 1,212 · completion 96 · cached 1,024` (cached only when > 0).
import { evalPassOf } from "src/components/traceDetail/evalScores";

export const CACHE_READ_ATTR = "llm.token_count.prompt_details.cache_read";
const ORDER = { fail: 0, error: 1, na: 2, skipped: 3, not_reached: 4 };
const fmt = (n) => Number(n).toLocaleString("en-US");

/** One badge's kind: fail | error | na | skipped | not_reached | pass. */
export function verdictKind(e) {
  if (e?.error === true || e?.status === "error") return "error";
  const v = evalPassOf(e);
  if (v === false) return "fail";
  if (v === true) return "pass";
  if (e?.status === "na") return "na";
  if (e?.status === "not_reached") return "not_reached";
  return "skipped";
}

const nameOf = (e) => e?.check_key ?? e?.eval_name ?? "eval";

/**
 * `✕ refund_amount` / `Score error · Tone` / `N/A · Correctness`; the run
 * label only when several runs judged the step (`✕ Correctness · #2A`).
 */
export function badgeLabel(e, { withRun = false } = {}) {
  const k = verdictKind(e);
  const run = withRun && e?.run_label ? ` · ${e.run_label}` : "";
  if (k === "fail") return `✕ ${nameOf(e)}${run}`;
  if (k === "error") return `Score error · ${nameOf(e)}${run}`;
  if (k === "na") return `N/A · ${nameOf(e)}${run}`;
  if (k === "not_reached") return `Not reached · ${nameOf(e)}${run}`;
  if (k === "skipped") return `Skipped · ${nameOf(e)}${run}`;
  return `✓ ${nameOf(e)}${run}`;
}

/** The reason line of a badge (tooltip): explanation or skip reason. */
export const badgeReason = (e) => e?.explanation ?? e?.skipped_reason ?? null;

/**
 * A row's badges, failing first; passing ones only when `withPass`.
 * @returns {{kind: string, label: string, reason: string|null}[]}
 */
export function stepBadgesOf(entry, { withPass = false } = {}) {
  const list = Array.isArray(entry?.eval_scores) ? entry.eval_scores : [];
  const withRun =
    new Set(list.map((e) => e?.run_label).filter(Boolean)).size > 1;
  const sorted = list
    .map((e, i) => ({ e, i, kind: verdictKind(e) }))
    .filter(({ kind }) => withPass || kind !== "pass")
    .sort((a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9) || a.i - b.i);
  // Verdicts that never ran (skipped, not reached) fold into one quiet
  // badge per kind (`2 not reached`), their names on hover.
  const quiet = (kind) => sorted.filter((x) => x.kind === kind);
  const fold = (kind, word) => {
    const xs = quiet(kind);
    if (!xs.length) return [];
    if (xs.length === 1)
      return [
        {
          kind,
          label: badgeLabel(xs[0].e, { withRun }),
          reason: badgeReason(xs[0].e),
        },
      ];
    return [
      {
        kind,
        label: `${xs.length} ${word}`,
        reason: xs
          .map((x) => `${nameOf(x.e)}: ${badgeReason(x.e) ?? word}`)
          .join("\n"),
      },
    ];
  };
  return [
    ...sorted
      .filter((x) => x.kind !== "skipped" && x.kind !== "not_reached")
      .map(({ e, kind }) => ({
        kind,
        label: badgeLabel(e, { withRun }),
        reason: badgeReason(e),
      })),
    ...fold("skipped", "skipped"),
    ...fold("not_reached", "not reached"),
  ].sort((a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9));
}

/** `prompt 1,212 · completion 96 · cached 1,024` for an LLM span, else null. */
export function tokenSplitText(span) {
  if (!span || span.observation_type !== "llm") return null;
  const p = Number(span.prompt_tokens) || 0;
  const c = Number(span.completion_tokens) || 0;
  if (!p && !c) return null;
  const cached = Number(span.span_attributes?.[CACHE_READ_ATTR]) || 0;
  return [
    `prompt ${fmt(p)}`,
    `completion ${fmt(c)}`,
    ...(cached > 0 ? [`cached ${fmt(cached)}`] : []),
  ].join(" · ");
}

/**
 * Failing verdict names per graph node (DiffGraph `_subtitle`): spans map
 * to nodes through `nodeToSpanIds`. `{nodeId: "✕ order_found, refund_amount"}`.
 */
export function failingByNode(tree, nodeToSpanIds) {
  const bySpan = new Map();
  const walk = (e) => {
    const id = e?.observation_span?.id;
    const fails = stepBadgesOf(e).filter((b) => b.kind === "fail");
    if (id && fails.length)
      bySpan.set(
        id,
        (e.eval_scores ?? [])
          .filter((x) => verdictKind(x) === "fail")
          .map(nameOf),
      );
    (e?.children ?? []).forEach(walk);
  };
  (tree ?? []).forEach(walk);
  const out = {};
  Object.entries(nodeToSpanIds ?? {}).forEach(([node, ids]) => {
    const names = [
      ...new Set((ids ?? []).flatMap((id) => bySpan.get(id) ?? [])),
    ];
    if (names.length) out[node] = `✕ ${names.join(", ")}`;
  });
  return out;
}
