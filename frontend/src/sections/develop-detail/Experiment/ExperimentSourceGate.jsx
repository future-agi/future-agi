import { Alert, AlertTitle, Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import { isExperimentReadUnavailable } from "src/api/develop/experiment-read-policy";
import RunExperimentFormSkeleton from "./RunExperimentFormSkeleton";

/** V2's saved configuration lists are complete; canonical runs are projections.
 * Prototype target-shaped runs still need their supplementary R1 configuration.
 */
export function hasSavedExperimentConfigs(detail) {
  return (
    (detail?.experiment_type ?? "llm") === "llm" &&
    Array.isArray(detail?.prompt_configs) &&
    Array.isArray(detail?.agent_configs) &&
    !(detail?.runs ?? []).some((run) => run?.target_type)
  );
}

/** Never initialize editable cards from an incomplete source snapshot. */
export default function ExperimentSourceGate({
  enabled = true,
  detail,
  configs,
  children,
}) {
  const savedConfigs = hasSavedExperimentConfigs(detail.data);
  // The supplementary configs count only once the detail has loaded
  // without saved configs. A launch build refuses them at once, so judging
  // them earlier flashed "Experiment unavailable" until the detail arrived.
  const failed =
    enabled &&
    (detail.isError
      ? detail
      : detail.data && !savedConfigs && configs.isError
        ? configs
        : null);
  const ready =
    !enabled || Boolean(detail.data && (savedConfigs || configs.data));
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
