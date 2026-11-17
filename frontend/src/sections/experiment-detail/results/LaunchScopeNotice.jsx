import React from "react";
import PropTypes from "prop-types";
import { Alert, Link, Stack } from "@mui/material";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";
import { paths } from "src/routes/paths";

export function ResultsClassicLinks({ experimentId }) {
  const { search, hash } = useLocation();
  return <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap" }}>
    <Link component={RouterLink} to={{ pathname: paths.dashboard.experimentClassic(experimentId), search, hash }}>Classic data</Link>
    <Link component={RouterLink} to={{ pathname: paths.dashboard.experimentClassic(experimentId, "summary"), search, hash }}>Classic summary</Link>
  </Stack>;
}
ResultsClassicLinks.propTypes = { experimentId: PropTypes.string.isRequired };

// Scope disclosure is never used as a substitute for a failed core API read.
export default function LaunchScopeNotice() {
  if (!isLaunchBuild()) return null;
  const deferred = [
    ["exp.results.paths", "Paths"],
    ["exp.results.traces", "Traces"],
    ["exp.leaderboards", "workspace Leaderboards"],
    ["exp.new.runSettings", "custom run settings for new experiments"],
    ["exp.results.iterateDock", "Iterate and quick tests"],
  ].filter(([id]) => !isSurfaceShown(id)).map(([, label]) => label);
  return <Alert severity="info" data-testid="results-launch-scope" sx={{ mx: 1 }}>
    Data, Matrix, Summary and Compare show recorded results and saved run settings.
    {deferred.length ? ` Not available here: ${deferred.join(", ")}.` : ""}
    {" Task side-by-side, classification, Explore workbench, custom columns, derived metrics, Best of N, match-by-key, evaluator-version impact, advanced re-score, add-to-dataset, result reviews and score overrides are not included. Voice/live-session controls and simulated uploads are not available."}
  </Alert>;
}
