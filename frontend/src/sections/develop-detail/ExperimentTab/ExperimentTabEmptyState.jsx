import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";

// The Experiments tab with no experiment from the app. When the page was
// opened at an SDK run (its app_url's run= parameter), that run's panel is
// shown above this table, so the first-run call to action would read as if the
// run just made did not exist: a short line says what this table lists.
const ExperimentTabEmptyState = ({ sdkRunShown, onRunExperiment }) => {
  if (sdkRunShown) {
    return (
      <Box
        data-testid="experiment-tab-empty-with-sdk-run"
        sx={{ px: 2, py: 1.5 }}
      >
        <Typography variant="s2" color="text.secondary">
          The SDK run above is shown by its run id. This table lists the
          experiments run in the app; this dataset has none yet.
        </Typography>
      </Box>
    );
  }
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        p: 4,
      }}
    >
      <EmptyLayout
        title="Ready to run your first experiment"
        description="Run your first experiment to test, compare, and improve prompt performance."
        icon="/assets/icons/navbar/ic_experiment.svg"
        action={onRunExperiment ? (
          <Button color="primary" variant="contained" onClick={onRunExperiment}>
            Run Experiment
          </Button>
        ) : undefined}
      />
    </Box>
  );
};

ExperimentTabEmptyState.propTypes = {
  sdkRunShown: PropTypes.bool,
  onRunExperiment: PropTypes.func,
};

export default ExperimentTabEmptyState;
