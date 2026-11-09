import { Alert, AlertTitle, Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import { isExperimentReadUnavailable } from "src/api/develop/experiment-read-policy";
import RunExperimentFormSkeleton from "./RunExperimentFormSkeleton";

/** Never initialize editable cards from an incomplete source snapshot. */
export default function ExperimentSourceGate({
  enabled = true,
  detail,
  configs,
  children,
}) {
  const failed =
    enabled && (detail.isError ? detail : configs.isError ? configs : null);
  const ready = !enabled || Boolean(detail.data && configs.data);
  const unavailable = isExperimentReadUnavailable(failed?.error);
  return (
    <>
      {failed && (
        <Box sx={{ p: 3 }}>
          <Alert
            severity="warning"
            action={
              <Button
                color="inherit"
                disabled={failed.isFetching}
                onClick={() => failed.refetch()}
              >
                Retry
              </Button>
            }
          >
            <AlertTitle>
              {unavailable
                ? "Experiment unavailable"
                : "Could not load experiment"}
            </AlertTitle>
            {unavailable
              ? "This experiment or its run configuration is unavailable in this workspace. Close this panel or retry after access is restored."
              : "The experiment could not be loaded. Automatic retries have stopped. Please try again."}
          </Alert>
        </Box>
      )}
      {/* Keep an already initialized draft mounted during background refreshes. */}
      {ready ? children : failed ? null : <RunExperimentFormSkeleton />}
    </>
  );
}

ExperimentSourceGate.propTypes = {
  enabled: PropTypes.bool,
  detail: PropTypes.object.isRequired,
  configs: PropTypes.object.isRequired,
  children: PropTypes.node,
};
