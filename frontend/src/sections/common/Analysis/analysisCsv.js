// CSV of a comparison's Summary (compare page `Download CSV`): one row per
// run with every metric's value and its delta vs the baseline run.
import { getRunStatus } from "src/sections/common/Runs/runStatus";

const esc = (v) => {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const num = (v, d = 4) =>
  typeof v === "number" && Number.isFinite(v) ? Number(v.toFixed(d)) : "";

/** @param analysis R5 result. @returns CSV text (header + one line per run). */
export function analysisCsv(analysis) {
  const metrics = analysis?.metrics ?? [];
  const runs = analysis?.runs ?? [];
  const header = [
    "run",
    "name",
    "status",
    "baseline",
    "rows",
    "errored_rows",
    "flaky_rows",
    "success_rate",
    "success_tries_passed",
    "success_tries",
    "reliable_all_tries",
    "cost_per_task_usd",
    "time_per_task_ms",
    "passes_every_eval",
    // L7c g6 (sum:export): pass/fail deltas also in points, rounded once
    // to the 0.1 the All runs table shows (from the same R5 delta).
    ...metrics.flatMap((m) =>
      m.output_type === "pass_fail"
        ? [`${m.key}`, `${m.key}:delta`, `${m.key}:delta_pts`]
        : [`${m.key}`, `${m.key}:delta`],
    ),
  ];
  const lines = runs.map((r) => {
    const cells = [
      r.label,
      r.name,
      // the capability contract: the run's real outcome (the one derivation), never its
      // stamp: a run whose rows all errored exports Failed, a partial one
      // PartialCompleted.
      r.status == null ? "" : getRunStatus(r).key,
      r.run_id === analysis.baseline_run_id ? "yes" : "",
      r.in_view,
      r.errored_rows,
      r.flaky_rows,
      num(r.success_rate, 6),
      r.success_pass ?? "",
      r.success_n ?? "",
      num(r.reliable_rate, 6),
      num(r.cost_per_row, 6),
      num(r.latency_p50_ms, 0),
      num(r.pass_rate),
      ...metrics.flatMap((m) => {
        const p = m.per_run?.find((x) => x.run_id === r.run_id);
        const v = m.vs_baseline?.find((x) => x.run_id === r.run_id);
        const pts =
          typeof v?.delta === "number" && Number.isFinite(v.delta)
            ? Math.round(v.delta * 1000) / 10
            : "";
        return m.output_type === "pass_fail"
          ? [num(p?.value, 6), num(v?.delta, 6), pts]
          : [num(p?.value, 6), num(v?.delta, 6)];
      }),
    ];
    return cells.map(esc).join(",");
  });
  return [header.map(esc).join(","), ...lines].join("\n");
}

/**
 * CSV of the tasks that got worse vs the baseline, one line per (run, task):
 * the `Tasks that got worse` scope of `Download CSV` (AS#19).
 * @param analysis R5 result
 * @param {{run: object, base: object, worseIds: string[]}[]} items summaryVerdicts()
 */
export function worseTasksCsv(analysis, items, rowPreviews = {}) {
  // L7c g6 (sum:export): every line names its row and input: the rows the
  // caller read (`rowPreviews`, example id -> {row_order, input_preview}),
  // else the worklist's own items.
  const worklist = analysis?.worklist ?? {};
  const listed = [
    ...(worklist.items ?? []),
    ...Object.values(worklist.tabs ?? {}).flatMap((t) => t?.items ?? []),
  ];
  const previews = {
    ...Object.fromEntries(listed.map((i) => [i.example_id, i])),
    ...rowPreviews,
  };
  const header = ["run", "baseline", "row_id", "row", "input_preview"];
  const lines = [];
  (items ?? []).forEach(({ run, base, worseIds }) =>
    (worseIds ?? []).forEach((id) => {
      const p = previews[id];
      lines.push(
        [run.label, base.label, id, p?.row_order ?? "", p?.input_preview ?? ""]
          .map(esc)
          .join(","),
      );
    }),
  );
  return [header.map(esc).join(","), ...lines].join("\n");
}

/** Data lines in a CSV text (header excluded). */
export function csvDataLines(text) {
  if (!text) return 0;
  return Math.max(
    0,
    String(text)
      .split("\n")
      .filter((l) => l !== "").length - 1,
  );
}

/** `support-agent-golden-set-2026-10-15.csv` (slug + local date). */
export function csvFileName(base, date = new Date()) {
  const slug =
    String(base ?? "summary")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "summary";
  const d = date instanceof Date ? date : new Date(date);
  const pad = (n) => String(n).padStart(2, "0");
  return `${slug}-${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}.csv`;
}

/** `812 B`, `4.2 KB`, `1.2 MB` for the saved-file toast. */
export function fileSizeText(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round((b / 1024) * 10) / 10} KB`;
  return `${Math.round((b / (1024 * 1024)) * 10) / 10} MB`;
}

/** First text value of a row's inputs, one line, at most 140 characters. */
const firstText = (inputs) => {
  const values =
    inputs && typeof inputs === "object" ? Object.values(inputs) : [inputs];
  const v = values.find((x) => typeof x === "string" && x.trim()) ?? "";
  const one = String(v).replace(/\s+/g, " ").trim();
  return one.length > 140 ? `${one.slice(0, 139)}…` : one;
};

/**
 * L7c g6: `{example_id: {row_order, input_preview}}` from R3 result rows
 * (`rows[]` of GET …/results/), for worseTasksCsv.
 */
export function rowPreviewsOf(rows) {
  return Object.fromEntries(
    (rows ?? []).map((r) => [
      r.example_id,
      { row_order: r.row_order ?? "", input_preview: firstText(r.inputs) },
    ]),
  );
}
