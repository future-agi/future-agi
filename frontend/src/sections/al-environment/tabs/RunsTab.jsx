import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import Pane from "../parts/Pane";
import LegacyRuns from "./runs/LegacyRuns";
import RunDetail from "./runs/RunDetail";
import RunList from "./runs/RunList";

/**
 * Runs, at whichever of three states this session is in: a list of simulations, one of them
 * opened, or — for a session written before a run was a folder — the older per-scenario records.
 *
 * Presentational on purpose. Which run is open is the parent's state, so the tab can be rendered
 * from a fixture, and running lives on the composer chip rather than here: a button that starts
 * a suite does not belong on the page you read the results from.
 */
const RunsTab = ({ runs, selectedRunId, onSelectRun, run, legacyRuns = [] }) => {
  if (runs.length > 0) {
    // The parent loads the detail; until it arrives the list is still the truthful thing to show.
    if (selectedRunId && run) return <RunDetail run={run} onBack={() => onSelectRun(null)} />;
    return <RunList runs={runs} selectedRunId={selectedRunId} onSelectRun={onSelectRun} />;
  }

  if (legacyRuns.length > 0) return <LegacyRuns runs={legacyRuns} />;

  return (
    <Pane title="Runs">
      <Typography variant="body2" color="text.secondary">
        Nothing has been run yet. A run restores the world, wires the agent&apos;s own tools to
        it, and grades from what is left behind.
      </Typography>
    </Pane>
  );
};

RunsTab.propTypes = {
  runs: PropTypes.array.isRequired,
  selectedRunId: PropTypes.string,
  onSelectRun: PropTypes.func.isRequired,
  run: PropTypes.object,
  legacyRuns: PropTypes.array,
};

export default RunsTab;
