import { useSyncExternalStore } from "react";

// The sibling editor/browser share a form control, but not a context provider.
// Keep preview feedback local to that form's lifetime, outside saved/dirty form
// values and query caches. A new form can never inherit another form's preview.
const previewStores = new WeakMap();
export function useSamplingPreview(control) {
  if (!previewStores.has(control)) {
    let snapshot = null;
    const listeners = new Set();
    previewStores.set(control, {
      getSnapshot: () => snapshot,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      publish: (next) => {
        if (snapshot === next) return;
        snapshot = next;
        listeners.forEach((listener) => listener());
      },
    });
  }
  const store = previewStores.get(control);
  return [
    useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot),
    store.publish,
  ];
}

// Shared invalidation scope; this is not a reservation or a saved task field.
export const SAMPLING_PREVIEW_SCOPE = [
  "project",
  "rowType",
  "filters",
  "runType",
  "startDate",
  "endDate",
  "datePreset",
  "evalsDetails",
  "samplingPolicy",
];

export const samplingUnitNames = (unit) =>
  ({ span: "spans", trace: "traces", session: "sessions", user: "users" })[
    unit
  ] || "custom units";

export const samplingRowNames = (rowType) =>
  ({
    spans: "spans",
    traces: "traces",
    sessions: "sessions",
    voiceCalls: "voice calls",
  })[rowType] || "rows";

export function samplingPreviewSummary(preview, policy, rowType) {
  if (!preview || !policy || !Array.isArray(preview.strata)) return null;
  const countKeys = ["eligible_units", "selected_units", "selected_rows"];
  if (
    !preview.strata.every((cell) =>
      countKeys.every(
        (key) => Number.isSafeInteger(cell[key]) && cell[key] >= 0,
      ),
    )
  )
    return null;
  const [eligible, selected, rows] = countKeys.map((key) =>
    preview.strata.reduce((sum, cell) => sum + cell[key], 0),
  );
  const number = (value) => value.toLocaleString();
  const population = `Preview: ${number(selected)} of ${number(eligible)} ${samplingUnitNames(policy.sampling_unit)}; ${number(rows)} ${samplingRowNames(rowType)}`;
  const evaluators = preview.evaluator_count;
  if (!Number.isSafeInteger(evaluators) || evaluators <= 0)
    return `${population}; select evaluators to estimate runs`;
  // Each selected row runs every configured evaluator. Do not substitute the
  // expected live admission count for membership in this particular preview.
  return `${population} × ${number(evaluators)} evaluator${evaluators === 1 ? "" : "s"} = ${number(rows * evaluators)} runs`;
}
