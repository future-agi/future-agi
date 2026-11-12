import { useEffect } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useAuthContext } from "src/auth/hooks";
import { isSurfaceShown } from "src/launch/launchSwitch";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import NewExperimentButton from "../Experiment/NewExperimentButton";
import { useDatasetOriginStore } from "../states";

// How often the first-run action reads the dataset again while its rows are
// still being generated or processed.
const ROWS_POLL_MS = 3000;

const pageResult = (response) => response?.data?.result;
const hasRows = (result) => Boolean(result?.table?.length);
// A synthetic dataset whose rows are still being generated (the Data grid's
// rule, DevelopDataV2 getRows).
const isGenerating = (result) =>
  Boolean(result?.synthetic_dataset) &&
  (result?.synthetic_dataset_percentage ?? 100) !== 100;
const rowsArriving = (result) =>
  isGenerating(result) ||
  (Boolean(result?.is_processing_data) && !hasRows(result));

// The first-run action when the caller passes no onRunExperiment (the classic
// Experiments tab passes none): the dataset's own New experiment entry, with
// its role, run-access and pending-changes gates.
// - It reads this dataset's first page itself: the Data grid's page-0 query,
//   which the dataset header also reads. The read runs again on mount, on
//   window focus and on invalidation, and every few seconds while rows are
//   still arriving. A dataset with no rows, or a synthetic dataset whose rows
//   are still being generated, cannot start an experiment.
// - A disabled action says why, under the button. The row reason waits for
//   this mount's read, so a cached page never shows an old reason.
// - Scenario pages (?fromScenario=true) mount neither the New experiment
//   drawer nor the pending-changes dialog (DevelopDetailView), and a build
//   without the classic drawer has neither: no action there.
function FirstExperimentAction() {
  const { dataset } = useParams();
  const [searchParams] = useSearchParams();
  const hosted =
    isSurfaceShown("exp.classic") &&
    searchParams.get("fromScenario") !== "true";
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const access = useDatasetAccess(hosted ? dataset : undefined);
  const setProcessingComplete = useDatasetOriginStore(
    (state) => state.setProcessingComplete,
  );
  const { data, isFetchedAfterMount } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", {
      enabled: hosted && Boolean(dataset),
      staleTime: 0,
      refetchOnWindowFocus: true,
      refetchInterval: (query) =>
        rowsArriving(pageResult(query.state.data)) ? ROWS_POLL_MS : false,
      // A failed read only keeps the action off; no toast on this tab.
      meta: { errorHandled: true },
    }),
  );
  // The Data grid sets this flag from the same read. Keeping it in step lets
  // the dataset header, which reads this page and the flag, agree with this
  // action.
  useEffect(() => {
    const result = pageResult(data);
    if (hosted && result?.synthetic_dataset)
      setProcessingComplete(!isGenerating(result));
  }, [hosted, data, setProcessingComplete]);

  if (!hosted) return null;
  const result = pageResult(data);
  const ready = hasRows(result) && !isGenerating(result);
  let reason = null;
  if (!canEdit) reason = "Viewers can't run experiments";
  else if (!access.can.run) reason = access.reason("run");
  else if (result && isFetchedAfterMount && !ready)
    reason = rowsArriving(result)
      ? "Rows are still processing"
      : "Add rows first";
  return (
    <Stack alignItems="center" spacing={0.75}>
      <NewExperimentButton placement="experiments" disabled={!ready} />
      {reason ? (
        <Typography
          data-testid="first-experiment-reason"
          sx={{ fontSize: 12, lineHeight: "18px", color: "text.secondary" }}
        >
          {reason}
        </Typography>
      ) : null}
    </Stack>
  );
}

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
        action={
          onRunExperiment ? (
            <Button
              color="primary"
              variant="contained"
              onClick={onRunExperiment}
            >
              Run Experiment
            </Button>
          ) : (
            <FirstExperimentAction />
          )
        }
      />
    </Box>
  );
};

ExperimentTabEmptyState.propTypes = {
  sdkRunShown: PropTypes.bool,
  onRunExperiment: PropTypes.func,
};

export default ExperimentTabEmptyState;
