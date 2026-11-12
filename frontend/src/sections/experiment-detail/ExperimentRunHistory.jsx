import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { MAX_COMPARE_RUNS } from "./results/useResultsUrlState";
import { comparisonKeyOf } from "./matrix/comparableRuns";

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
  const selected = groups.find((group) => group.some((run) => run.run_id === requested))
    ?? groups[0];
  const select = (runId) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    next.set("history", runId);
    // These selections may name runs/examples/evaluators from the old plan.
    ["baseline", "cand", "row", "run", "vs", "trial", "filters", "bmetric", "wmetric"]
      .forEach((key) => next.delete(key));
    return next;
  });
  return { groups, runs: selected ?? [], select };
}

export default function ExperimentRunHistory({ history }) {
  if (history.groups.length < 2) return null;
  return (
    <Box sx={{ px: 2, py: 1 }}>
      <Box component="label">
        Run history{" "}
        <select
          aria-label="Run history"
          value={history.runs[0]?.run_id ?? ""}
          onChange={(event) => history.select(event.target.value)}
        >
          {history.groups.map((group) => (
            <option key={group[0].run_id} value={group[0].run_id}>
              {group.length === 1 ? "Run " : "Runs "}
              {group.map((run) => run.label || run.name || (run.ordinal ? `#${run.ordinal}` : run.run_id)).join(", ")}
            </option>
          ))}
        </select>
      </Box>
      <Typography variant="body2" color="text.secondary">
        Showing up to eight runs with the same immutable examples, trials and evaluators.
        Earlier runs remain available in Run history.
      </Typography>
    </Box>
  );
}

ExperimentRunHistory.propTypes = {
  history: PropTypes.shape({
    groups: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.object)).isRequired,
    runs: PropTypes.arrayOf(PropTypes.object).isRequired,
    select: PropTypes.func.isRequired,
  }).isRequired,
};
