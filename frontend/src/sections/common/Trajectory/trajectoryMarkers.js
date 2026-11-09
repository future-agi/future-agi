// Milestone markers → span annotations for TraceTreeV2 (R4 lane T). Pure.
//
// Steps are numbered by G0's milestones.js (`step` n = the run's own step);
// both R2 and R4 trace builders write `span_attributes.step = n`, so a step
// maps to its span by that attribute (the flattened-trace index is only a
// fallback: agent container spans make index ≠ n). Never invents numbers.
//   ▲ First wrong step   on the span of G0's firstWrongStep
//   ✗ <check>            on the span a failed check points at
//                        (cause_step_n; else the end state span)
//   ↻ retry 2            on the 2nd, 3rd… run of a tool repeated back to back
import { flattenTrace } from "../Align/normalize";
import { alignLanes, markers } from "../Align/milestones";
import { flattenSpans } from "./graphNodes";

const spanOf = (entry) => entry?.observation_span ?? {};

/** `{[stepN]: spanId}` for a span tree. */
export function spanIdsByStep(spanTree) {
  const out = {};
  flattenSpans(spanTree).forEach(({ span, depth }) => {
    const n = Number(span?.span_attributes?.step);
    if (depth > 0 && Number.isInteger(n) && span.id && !(n in out))
      out[n] = span.id;
  });
  if (Object.keys(out).length) return out;
  flattenTrace({ observation_spans: spanTree }).forEach((s, i) => {
    out[i + 1] = s.id;
  });
  return out;
}

/** The end state span id (tag `end_state` or name `End state`), or null. */
export function endStateSpanId(spanTree) {
  const hit = flattenSpans(spanTree).find(
    ({ span, depth }) =>
      depth > 0 &&
      ((span.tags ?? []).includes("end_state") ||
        String(span.name ?? "").toLowerCase() === "end state"),
  );
  return hit?.span?.id ?? null;
}

/** Name of the span that holds a step (graph node id of First wrong step). */
export function spanNameOfStep(spanTree, stepN) {
  const id = spanIdsByStep(spanTree)[stepN];
  if (!id) return null;
  const hit = flattenSpans(spanTree).find(({ span }) => span.id === id);
  return hit ? spanOf(hit.entry).name ?? null : null;
}

/**
 * @param {object[]} spanTree  `observation_spans`
 * @param {{firstWrongStepN?: number|null, failedChecks?: {key: string,
 *   name?: string, cause_step_n?: number|null}[]}} [marks]
 * @param {{name: string, spanIds: string[]}[]} [retries]  recommendView.retriesOf
 * @returns {{annotations: Object<string, object[]>, highlightSpanIds: string[],
 *   firstWrongSpanId: string|null}}
 */
export function trajectoryMarkers(spanTree, marks = {}, retries = []) {
  const byStep = spanIdsByStep(spanTree);
  const annotations = {};
  const add = (spanId, chip) => {
    if (!spanId) return;
    (annotations[spanId] ??= []).push(chip);
  };
  const fw = marks?.firstWrongStepN;
  const firstWrongSpanId = Number.isInteger(fw) ? byStep[fw] ?? null : null;
  add(firstWrongSpanId, {
    tone: "error",
    label: "First wrong step",
    icon: "mdi:triangle",
  });
  const end = endStateSpanId(spanTree);
  (marks?.failedChecks ?? []).forEach((c) => {
    const at = Number.isInteger(c.cause_step_n) ? byStep[c.cause_step_n] : end;
    add(at ?? end, {
      tone: "error",
      label: c.key ?? c.name,
      icon: "mdi:close",
    });
  });
  const highlightSpanIds = [];
  (retries ?? []).forEach((r) =>
    (r.spanIds ?? []).slice(1).forEach((id, i) => {
      if (!id) return;
      add(id, { tone: "warning", label: `retry ${i + 2}`, icon: "mdi:replay" });
      highlightSpanIds.push(id);
    }),
  );
  return { annotations, highlightSpanIds, firstWrongSpanId };
}

/**
 * One lane's marks from G0 (never our own numbering): First wrong step of
 * the lane alone against its checks, and its failed checks.
 * @param {{id: string, label?: string, steps: object[], checks?: object[]}} lane
 *   `steps` = toLaneSteps output
 * @returns {{firstWrongStepN: number|null, firstWrongStep: object|null,
 *   failedChecks: object[]}}
 */
export function marksOfLane(lane) {
  const steps = lane?.steps ?? [];
  const checks = lane?.checks ?? [];
  const failedChecks = checks.filter((c) => c?.passed === false);
  if (!steps.length || !failedChecks.length)
    return { firstWrongStepN: null, firstWrongStep: null, failedChecks };
  const base = { id: lane.id, label: lane.label ?? lane.id, steps };
  const { rows } = alignLanes(base, [], { mode: "milestone" });
  const m = markers({ rows, lanes: [base], checks: { [lane.id]: checks } });
  return {
    firstWrongStepN: m.firstWrongStep?.stepN ?? null,
    firstWrongStep: m.firstWrongStep ?? null,
    failedChecks,
  };
}
