import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { MAX_COMPARE_RUNS, parseRunsParam } from "./results/useResultsUrlState";
import {
  COMPARE_CHECK_FAILED,
  comparisonKeyOf,
  groupUnnamed,
  inOneComparisonGroup,
  setUpsDifferLine,
} from "./matrix/comparableRuns";

/** The catalog is newest first; only the server can prove plan compatibility. */
export function useExperimentRunHistory(runs = []) {
  const [params, setParams] = useSearchParams();
  const groups = useMemo(() => {
    const byPlan = new Map();
    const result = [];
    runs.forEach((run) => {
      // Older cached catalogs did not include this field. Preserve their
      // existing behavior; an explicitly unavailable plan (null) stands alone.
      // The Matrix and the lineage ribbon group runs by the same key.
      const key = comparisonKeyOf(run);
      let group = byPlan.get(key);
      if (!group || group.length === MAX_COMPARE_RUNS) {
        group = [];
        byPlan.set(key, group);
        result.push(group);
      }
      group.push(run);
    });
    return result;
  }, [runs]);
  const requested = params.get("history");
  const selected =
    groups.find((group) => group.some((run) => run.run_id === requested)) ??
    groups[0];
  const select = (runId) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("history", runId);
      // These selections may name runs/examples/evaluators from the old plan.
      [
        "runs",
        "baseline",
        "cand",
        "row",
        "run",
        "vs",
        "trial",
        "filters",
        "bmetric",
        "wmetric",
      ].forEach((key) => next.delete(key));
      return next;
    });
  return { groups, runs: selected ?? [], select };
}

/**
 * The runs the Data tab compares: the runs a link names (`runs=`, as a
 * dataset row's Compare side by side opens), in the link's order and only
 * those in this experiment's catalog, else the Run history group. `linked`
 * is null when the link names no runs. The results read lines up only runs
 * of one comparison group, so named runs the catalog puts in more than one,
 * or whose group it does not name, are never read together: `linked` stays
 * null, the Run history group is compared, and `apart` holds the named runs
 * so the page can say why. The
 * page header reads the same answer while the Data tab is open.
 */
export function useExperimentComparison(catalog) {
  const history = useExperimentRunHistory(catalog);
  const [params] = useSearchParams();
  const requested = params.get("runs");
  const named = useMemo(() => {
    const ids = parseRunsParam(requested);
    if (!ids.length) return null;
    const known = catalog ?? [];
    return ids
      .map((id) => known.find((run) => run.run_id === id))
      .filter(Boolean);
  }, [catalog, requested]);
  const apart = named && !inOneComparisonGroup(named) ? named : null;
  const linked = apart ? null : named;
  // After a refused link, a Run history group that is unproven too (records
  // without comparison_group) is read by its newest run alone, so the
  // refused runs are never read together.
  const fallback =
    apart && !inOneComparisonGroup(history.runs)
      ? history.runs.slice(0, 1)
      : history.runs;
  return { history, runs: linked ?? fallback, linked, apart };
}

const runName = (run) =>
  run.label || run.name || (run.ordinal ? `#${run.ordinal}` : run.run_id);

export default function ExperimentRunHistory({
  history,
  selectedRuns,
  apartRuns,
}) {
  if (history.groups.length < 2 && !selectedRuns && !apartRuns) return null;
  return (
    <Box sx={{ px: 2, py: 1 }}>
      <Box component="label">
        Run history{" "}
        <select
          aria-label="Run history"
          value={selectedRuns ? "selected" : history.runs[0]?.run_id ?? ""}
          onChange={(event) => history.select(event.target.value)}
        >
          {selectedRuns && (
            <option value="selected" disabled>
              Selected runs ({selectedRuns.length})
            </option>
          )}
          {history.groups.map((group) => (
            <option key={group[0].run_id} value={group[0].run_id}>
              {group.length === 1 ? "Run " : "Runs "}
              {group.map(runName).join(", ")}
            </option>
          ))}
        </select>
      </Box>
      <Typography variant="body2" color="text.secondary">
        {apartRuns
          ? groupUnnamed(apartRuns)
            ? COMPARE_CHECK_FAILED
            : setUpsDifferLine(apartRuns.map(runName))
          : selectedRuns
            ? "Showing the runs selected in the link. Choose a history group to change the comparison."
            : "Showing up to eight runs with the same immutable examples, trials and evaluators. Earlier runs remain available in Run history."}
      </Typography>
    </Box>
  );
}

ExperimentRunHistory.propTypes = {
  selectedRuns: PropTypes.arrayOf(PropTypes.object),
  apartRuns: PropTypes.arrayOf(PropTypes.object),
  history: PropTypes.shape({
    groups: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.object)).isRequired,
    runs: PropTypes.arrayOf(PropTypes.object).isRequired,
    select: PropTypes.func.isRequired,
  }).isRequired,
};
