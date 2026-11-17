// Which row sets (`show=`) the results read answers in this build. The page
// asks this before it sends a show and before it offers one in the toolbar.
import { DatasetResultsReadQueryParams } from "src/generated/api-contracts/api.zod";
import { isLaunchBuild } from "src/launch/launchSwitch";

/**
 * Whether this build's results read answers `show=<value>`. Prototype builds
 * and unit tests keep every value (launchSwitch); a launch build offers the
 * values the read's published query contract lists. Where a value is not
 * offered, its control stays on screen, turned off with the shared line, and
 * no read asks for it.
 */
export function resultsShowOffered(value) {
  if (!value || value === "all") return true;
  const served =
    DatasetResultsReadQueryParams.shape.show.removeDefault().options;
  return served.includes(value) || !isLaunchBuild();
}

/**
 * Whether this build's results read answers the page's aggregate `<value>`.
 * Average (sent as `mean`) is always offered, and so are the values the page
 * never sends (`run`, each run's own reducer, which is the default, and an
 * old `pass`). Median, N/A rate and the per-try reducers are offered in a
 * prototype build or unit test, and in a launch build only when the read's
 * published query contract lists them. Where one is not offered, it stays on
 * screen, turned off with the shared line; no read asks for it and a link
 * that names it reads as Average.
 */
export function resultsAggOffered(value) {
  if (!value || ["avg", "run", "pass"].includes(value)) return true;
  const served =
    DatasetResultsReadQueryParams.shape.agg.removeDefault().options;
  return served.includes(value) || !isLaunchBuild();
}

/**
 * The kinds of order the results read answers. The first five are sent as
 * they stand. The others start a sort that names a run: `delta` and `score`
 * as `<kind>:<metric>:<run>:asc|desc`, `variance` as
 * `variance:<metric>:<run>`, the others as `<kind>:<run>`.
 */
export const RESULTS_SORT_KINDS = Object.freeze([
  "order",
  "-order",
  "input",
  "-input",
  "regressed",
  "delta",
  "score",
  "latency",
  "cost",
  "variance",
  "flakiness",
]);

// A sort by how far apart a run's tries are names the metric (its key may
// hold colons) and the run; a `variance:<run>`, `variance::<run>` or
// `variance:<metric>:` whose metric or run is empty is not one the read
// answers.
const namesItsParts = (value) => {
  const parts = String(value).split(":");
  if (parts[0] !== "variance") return true;
  return (
    parts.length >= 3 &&
    parts.slice(1, -1).join(":") !== "" &&
    parts.at(-1) !== ""
  );
};

/**
 * Whether this build's results read answers the page's sort `<value>`. The
 * page's own order is always offered. Prototype builds and unit tests keep
 * every sort. A launch build offers what the read's published query contract
 * answers: while the contract lists fixed choices, those values; once it
 * takes the sort as a pattern, the kinds above. Where a sort is not offered,
 * its control stays on screen, turned off with the shared line; no read asks
 * for it and a link that names it reads as the page's order.
 */
export function resultsSortOffered(value) {
  if (!value || value === "order") return true;
  const field = DatasetResultsReadQueryParams.shape.sort;
  const choices = (field.removeDefault?.() ?? field).options;
  const served = Array.isArray(choices)
    ? choices.includes(value)
    : RESULTS_SORT_KINDS.includes(String(value).split(":")[0]) &&
      namesItsParts(value);
  return served || !isLaunchBuild();
}
