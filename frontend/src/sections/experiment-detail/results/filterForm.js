// Filter menu model (PRODUCT-SPEC §3.10.5): the menu entries, what each
// asks for, and the pill each one produces. Pure; the menu component only
// renders these.
import {
  ANY_CANDIDATE,
  ANY_RUN,
  NO_SPLIT,
} from "src/sections/common/Results/filterPills";

export const FILTER_ENTRIES = [
  {
    id: "regressed",
    label: "Regressed vs baseline run…",
    pill: "movement",
    fields: ["metric", "run"],
    compare: true,
  },
  {
    id: "improved",
    label: "Improved…",
    pill: "movement",
    fields: ["metric", "run"],
    compare: true,
  },
  {
    id: "changed",
    label: "Changed…",
    pill: "movement",
    fields: ["metric", "run"],
    compare: true,
  },
  {
    id: "fails",
    label: "Eval fails…",
    pill: "fails",
    fields: ["metric", "run"],
  },
  {
    id: "score",
    label: "Score threshold…",
    pill: "score",
    fields: ["metric", "run", "op", "value"],
  },
  { id: "error", label: "Errored", pill: "error", fields: ["run"] },
  { id: "flaky", label: "Flaky", pill: "flaky", fields: ["run"], trials: true },
  { id: "split", label: "Split…", pill: "split", fields: ["splits"] },
  {
    id: "meta",
    label: "Metadata…",
    pill: "meta",
    fields: ["metaKey", "metaValue"],
  },
  {
    id: "human",
    label: "Human label…",
    pill: "human",
    fields: ["humanLabel", "run", "op", "value"],
    human: true,
  },
  {
    id: "pref",
    label: "Preference…",
    pill: "pref",
    fields: ["prefValue", "prefSource"],
    preference: true,
  },
  {
    id: "ops",
    label: "Latency or cost…",
    pill: "latency",
    fields: ["opsKind", "run", "op", "value"],
  },
  {
    id: "tool_error",
    label: "Tool errors…",
    pill: "tool_error",
    fields: ["run"],
    agent: true,
  },
];

export const OPS = [
  { value: "gt", label: ">" },
  { value: "gte", label: "≥" },
  { value: "lt", label: "<" },
  { value: "lte", label: "≤" },
];

/** Why an entry is unavailable in this view, or null. */
export function entryDisabledReason(entry, view) {
  if (entry.compare && view.runCount < 2)
    return "Add a second run to compare against the baseline";
  if (entry.trials && !view.hasTrials) return "No run in view has trials";
  if (entry.human && !view.humanMetrics?.length) return "No human labels yet";
  if (entry.preference && !view.hasPreference) return "No preference votes yet";
  if (entry.agent && !view.hasAgent) return "No agent run in view";
  return null;
}

/** Run options: Any candidate (default with ≥ 2 runs), Any run, each run. */
export function runOptions(runs, baselineId, { candidatesOnly = false } = {}) {
  const multi = runs.length > 1;
  const opts = [];
  if (multi) opts.push({ value: ANY_CANDIDATE, label: "Any candidate" });
  if (!candidatesOnly) opts.push({ value: ANY_RUN, label: "Any run" });
  runs.forEach((r) => {
    if (candidatesOnly && r.run_id === baselineId) return;
    opts.push({
      value: r.run_id,
      label: `${r.label}${r.run_id === baselineId ? " (baseline)" : ""}`,
    });
  });
  return opts;
}

/** Default form values for an entry. */
export function defaultForm(entry, view) {
  const evalMetric =
    entry.id === "score"
      ? view.metrics.find((m) => m.output_type === "score") ?? view.metrics[0]
      : view.metrics[0];
  return {
    metric: evalMetric?.key ?? "",
    run: view.runCount > 1 ? ANY_CANDIDATE : ANY_RUN,
    op: entry.id === "ops" ? "gt" : "lt",
    value: entry.id === "ops" ? "3" : evalMetric?.pass_threshold ?? "0.5",
    splits: [],
    metaKey: view.metadataKeys?.[0] ?? "",
    metaValue: "",
    humanLabel: view.humanMetrics?.[0]?.key ?? "",
    prefValue: view.candidateId ?? "",
    prefSource: "human",
    opsKind: "latency",
  };
}

/** True when the form has what the entry needs. */
export function formComplete(entry, form) {
  return entry.fields.every((f) => {
    if (f === "splits") return form.splits?.length > 0;
    if (f === "value")
      return form.value !== "" && Number.isFinite(Number(form.value));
    if (f === "prefSource" || f === "opsKind") return true;
    return form[f] != null && form[f] !== "";
  });
}

/** The pill an entry + form produce (sent as-is in `filters`). */
export function pillFromForm(entry, form) {
  switch (entry.id) {
    case "regressed":
    case "improved":
    case "changed":
      return {
        type: "movement",
        metric: form.metric,
        dir: entry.id,
        run: form.run,
      };
    case "fails":
      return { type: "fails", metric: form.metric, run: form.run };
    case "score":
      return {
        type: "score",
        metric: form.metric,
        op: form.op,
        value: Number(form.value),
        run: form.run,
      };
    case "error":
    case "flaky":
    case "tool_error":
      return { type: entry.id, run: form.run };
    case "split":
      return { type: "split", values: form.splits };
    case "meta":
      return {
        type: "meta",
        key: form.metaKey,
        op: "eq",
        value: form.metaValue,
      };
    case "human":
      return {
        type: "human",
        label: form.humanLabel,
        op: form.op,
        value: Number(form.value),
        run: form.run,
      };
    case "pref":
      return form.prefValue === "disagree"
        ? { type: "pref_disagree", run: ANY_CANDIDATE }
        : {
            type: "pref",
            run: form.prefValue === "tie" ? ANY_CANDIDATE : form.prefValue,
            value: form.prefValue,
            source: form.prefSource,
          };
    case "ops":
      return {
        type: form.opsKind === "cost" ? "cost" : "latency",
        op: form.op,
        // Latency is entered in seconds and sent in milliseconds.
        value:
          form.opsKind === "cost"
            ? Number(form.value)
            : Math.round(Number(form.value) * 1000),
        run: form.run,
      };
    default:
      return null;
  }
}

export { NO_SPLIT };
