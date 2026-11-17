// IT-R4 G6 P2: Add run presets — one click adds a whole comparison:
//   Harnesses vs coded agents  every harness and your-code run on the
//                              dataset's latest version
//   All frameworks             the newest run of each framework (your code)
//   Latest of each approach    the newest run of each kind (prompt, agent,
//                              your code, harness, voice …)
// Over the runs NOT in view; finished or not, unlisted runs never. Pure.
import { runKindOf } from "./runKind";
import { provenanceOf } from "./provenanceModel";

const newestFirst = (a, b) =>
  String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")) ||
  (b.experiment_ordinal ?? 0) - (a.experiment_ordinal ?? 0);

const sourceName = (r) => {
  try {
    return provenanceOf(r)?.source?.name ?? null;
  } catch {
    return null;
  }
};

const versionOf = (r) => r?.dataset_version?.number ?? null;

/** One newest run per key, in the order the keys were first met (newest). */
function newestPer(runs, keyOf) {
  const seen = new Map();
  [...runs].sort(newestFirst).forEach((r) => {
    const k = keyOf(r);
    if (k && !seen.has(k)) seen.set(k, r);
  });
  return [...seen.values()];
}

/**
 * @param {object[]} runs  every run of the dataset (`include=config`)
 * @param {string[]} inView  run ids already compared
 * @returns {{id: string, label: string, tooltip: string, ids: string[]}[]}
 *   presets with at least one run to add
 */
export function pickerPresets(runs, inView = []) {
  const listed = (runs ?? []).filter((r) => r && r.listed !== false);
  const offered = listed.filter((r) => !inView.includes(r.run_id));
  const latest = Math.max(
    0,
    ...listed.map((r) => versionOf(r)).filter(Number.isFinite),
  );
  const onLatest = (r) =>
    !latest || versionOf(r) == null || versionOf(r) === latest;

  const harnessCode = offered.filter(
    (r) => ["harness", "code"].includes(runKindOf(r)) && onLatest(r),
  );
  // The newest run of each framework / approach overall; the ones already
  // in view are not added again.
  const out = (list) => list.filter((r) => !inView.includes(r.run_id));
  const frameworks = out(
    newestPer(
      listed.filter((r) => runKindOf(r) === "code"),
      sourceName,
    ),
  );
  const approaches = out(newestPer(listed, (r) => runKindOf(r)));
  const ids = (list) => list.map((r) => r.run_id);
  return [
    {
      id: "harness-vs-code",
      label: "Harnesses vs coded agents",
      tooltip: `Adds every harness and your-code run on dataset v${latest || "latest"}`,
      ids: ids(harnessCode),
    },
    {
      id: "frameworks",
      label: "All frameworks",
      tooltip: "Adds the newest run of each framework",
      ids: ids(frameworks),
    },
    {
      id: "approaches",
      label: "Latest of each approach",
      tooltip:
        "Adds the newest run of each kind of run (prompt, agent, endpoint …)",
      ids: ids(approaches),
    },
  ].filter((p) => p.ids.length);
}
