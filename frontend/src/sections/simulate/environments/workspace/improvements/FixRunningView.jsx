import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import FixSteps from "./FixSteps";
import TrialChart from "./TrialChart";
import TrialsTable from "./TrialsTable";

// Screen 2: a running fix. Everything here comes from the one read the page
// polls; nothing is estimated.
export default function FixRunningView({ fix, runTestId }) {
  const trials = fix.candidates || [];
  const max = fix.plan?.max_candidates;
  return (
    <Box data-testid="fix-running-view" sx={{ px: 3, py: 2.5 }}>
      <FixSteps fix={fix} runTestId={runTestId} />
      <TrialChart candidates={trials} />
      {trials.length > 0 && (
        <>
          <Typography sx={{ typography: "s1", fontWeight: 600, mb: 1 }}>
            {max
              ? `Trials (${trials.length} of up to ${max})`
              : `Trials (${trials.length})`}
          </Typography>
          <TrialsTable fix={fix} />
        </>
      )}
    </Box>
  );
}

FixRunningView.propTypes = {
  fix: PropTypes.object.isRequired,
  runTestId: PropTypes.string,
};
