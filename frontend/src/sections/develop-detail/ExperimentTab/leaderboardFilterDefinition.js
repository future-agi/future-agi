// ComplexFilter definition of the leaderboard: the fields a run can be
// filtered by, with options taken from the runs on this dataset. Pure.
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import {
  TARGET_LABEL,
  kindOfRun,
  modelOf,
  targetVersionOf,
} from "./leaderboardModel";
import { runFilterOptions } from "src/sections/common/Selection/filterToSelected";

const uniqueOptions = (runs, get, label = (v) => v) =>
  [...new Set(runs.map(get).filter((v) => v != null && v !== ""))]
    .sort((a, b) =>
      String(a).localeCompare(String(b), undefined, { numeric: true }),
    )
    .map((v) => ({ value: String(v), label: label(v) }));

const option = (propertyName, propertyId, options) => ({
  propertyName,
  propertyId,
  maxUsage: 1,
  filterType: { type: "option", options },
});

/** ComplexFilter definition from the runs and evals on this dataset. */
export function leaderboardFilterDefinition(runs, metricDefs) {
  const statusLabel = {};
  runs.forEach((r) => {
    const s = getRunStatus(r);
    statusLabel[s.key] = s.label;
  });
  const evals = metricDefs.filter((d) => d.kind === "eval");
  return [
    // R4 (lane Rb, MUST 7): Kind first, under the one Filter.
    // L7c g5 (lb:tags): only the kinds a run can be started with today
    // (prompt, agent, endpoint); no Framework filter (code and harness runs
    // are an absent way to run).
    option("Kind", "kind", uniqueOptions(runs.filter(canRerunHere), kindOfRun)),
    option(
      "Status",
      "status",
      uniqueOptions(
        runs,
        (r) => getRunStatus(r).key,
        (k) => statusLabel[k] ?? k,
      ),
    ),
    option(
      "Target",
      "target",
      uniqueOptions(
        runs,
        (r) => r.target_type,
        (t) => TARGET_LABEL[t] ?? t,
      ),
    ),
    option("Model", "model", uniqueOptions(runs, modelOf)),
    option(
      "Prompt or agent version",
      "target_version",
      uniqueOptions(runs, targetVersionOf),
    ),
    option(
      "Dataset version",
      "dataset_version",
      uniqueOptions(
        runs,
        (r) => r.dataset_version?.number,
        (n) => `v${n}`,
      ),
    ),
    option("Split", "split", [
      { value: "__all__", label: "All rows" },
      ...uniqueOptions(
        runs.flatMap((r) => r.splits ?? []),
        (s) => s.name,
      ),
    ]),
    option(
      "Created by",
      "created_by",
      uniqueOptions(runs, (r) => r.created_by?.name),
    ),
    // W5 C-H1: `Filter to selected` writes this row (the runs it kept).
    option("Run", "run_id", runFilterOptions(runs)),
    // W5 C-H1: offered once some run's experiment carries a tag.
    ...(runs.some((r) => r.tags?.length)
      ? [
          option(
            "Tag",
            "tag",
            uniqueOptions(
              runs.flatMap((r) => (r.tags ?? []).map((name) => ({ name }))),
              (x) => x.name,
            ),
          ),
        ]
      : []),
    option("Has errors", "has_errors", [
      { value: "yes", label: "Yes" },
      { value: "no", label: "No" },
    ]),
    {
      propertyName: "Trials",
      propertyId: "trials",
      maxUsage: 1,
      filterType: { type: "number" },
    },
    ...(evals.length
      ? [
          {
            propertyName: "Evals",
            stringConnector: "is",
            dependents: evals.map((d) => ({
              propertyName: `${d.name} (${d.output_type === "pass_fail" ? "pass rate %" : "avg, 0–1"})`,
              propertyId: `metric:${d.key}`,
              maxUsage: 1,
              filterType: { type: "number" },
            })),
          },
        ]
      : []),
  ];
}
