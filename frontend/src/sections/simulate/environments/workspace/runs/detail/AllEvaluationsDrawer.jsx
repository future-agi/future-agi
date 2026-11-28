import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import { Box, Button, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SavedEvalsList from "src/sections/common/EvaluationDrawer/SavedEvalsList";
import {
  useEnvironmentRunTest,
  useRemoveAppliedEvaluation,
} from "src/api/simulate-environments/environments";
import {
  runAnalyticsKey,
  callDetailKeyPrefix,
  compareRunsKeyPrefix,
  runResultsKey,
} from "src/api/simulate-environments/runEvals";
import SideDrawer from "../../../components/SideDrawer";
import EmptyState from "../../../components/EmptyState";
import ViewerLock from "../../../components/ViewerLock";
import useSimulationAccess from "../../../hooks/useSimulationAccess";
import { WORKSPACE_COPY } from "../../workspace.constants";
import { refusalText } from "../../evals/refusalText";
import {
  EVALS_LOAD_FAILED_TOOLTIP,
  GRADING_TOOLTIP,
  HARNESS_ONLY_TOOLTIP,
  NOT_COMPLETED_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
  PENDING_TOOLTIP,
} from "./allEvaluationsDrawer.constants";
import { LoadingScreen } from "src/components/loading-screen";

const REMOVE_FALLBACK = "Couldn’t remove the evaluation. Try again.";
// A stable empty array for `configs` while the run test's evals load.
const NO_CONFIGS = [];

/**
 * The run page's "All Evaluations" drawer.
 *
 * Every eval bound to the run test is listed here, including the harness's
 * per-scenario checks — those can't be ticked or re-run, because only a call
 * rerun refreshes them, but they can still be removed. The footer runs the
 * ticked rows (every runnable eval when none is ticked) and a row's run icon
 * runs just that row; a row's edit icon edits
 * that eval. Both go to the run page (`onRerun`, `onEdit`), whose one edit form
 * and confirm dialog also serve the table's column menus, and this drawer
 * closes once the grading it asked for is queued.
 */
export default function AllEvaluationsDrawer({
  open,
  onClose,
  env,
  runTestId,
  executionId,
  canRun = false,
  grading = false,
  rerunPending = false,
  editOpen = false,
  onRerun,
  onEdit,
  onAddEvaluations,
  listKey = 0,
}) {
  const queryClient = useQueryClient();
  const access = useSimulationAccess();

  const {
    data: configsData,
    isPending,
    isError,
    error,
    refetch,
  } = useEnvironmentRunTest(runTestId, { enabled: open && Boolean(runTestId) });
  const configs = configsData ?? NO_CONFIGS;

  const removeEval = useRemoveAppliedEvaluation();

  const requestRerun = (list) => onRerun?.(list, { onSuccess: onClose });

  const handleRemove = (config) => {
    removeEval.mutate(
      { id: env?.id, evalConfigId: config.id },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({
            queryKey: runResultsKey(executionId),
          });
          queryClient.invalidateQueries({
            queryKey: runAnalyticsKey(executionId),
          });
          queryClient.invalidateQueries({ queryKey: compareRunsKeyPrefix });
          // Call rows are built from the live catalog, and a drawer keeps
          // its detail for minutes: reopened, it must not list the removed eval.
          queryClient.invalidateQueries({ queryKey: callDetailKeyPrefix });
        },
        onError: (e) =>
          enqueueSnackbar(refusalText(e, REMOVE_FALLBACK), {
            variant: "error",
          }),
      },
    );
  };

  const cannotRunTooltip = grading ? GRADING_TOOLTIP : NOT_COMPLETED_TOOLTIP;

  // The run page's rules for each control, as reasons the shared list shows
  // as tooltips. Empty means allowed.
  const disabledReason = (config, action) => {
    if (action === "add") {
      return access.canEdit ? "" : WORKSPACE_COPY.viewer.evals;
    }
    if (action === "runAll") {
      if (!access.canRun) return WORKSPACE_COPY.viewer.runEvals;
      if (!canRun) return cannotRunTooltip;
      return rerunPending ? PENDING_TOOLTIP : "";
    }
    // The run drawer has no bulk Delete: removals go one at a time, each
    // reporting its own error.
    if (action === "bulkDelete") return "Remove evals one at a time.";
    const runnable = config.regradable === true;
    if (action === "select") return runnable ? "" : HARNESS_ONLY_TOOLTIP;
    if (action === "run") {
      if (!access.canRun) return WORKSPACE_COPY.viewer.runEvals;
      if (!runnable) return HARNESS_ONLY_TOOLTIP;
      if (!canRun) return cannotRunTooltip;
      return rerunPending ? PENDING_TOOLTIP : "";
    }
    if (action === "edit") {
      if (!access.canEdit) return WORKSPACE_COPY.viewer.evals;
      if (config.editable !== true) return NOT_EDITABLE_TOOLTIP;
      if (grading) return GRADING_TOOLTIP;
      if (!canRun) return NOT_COMPLETED_TOOLTIP;
      if (editOpen || rerunPending) return PENDING_TOOLTIP;
      return "";
    }
    if (action === "delete") {
      // The grader copes with a removal mid-run (it drops the pending cell),
      // but the column would vanish from a table that is polling for its
      // scores. Removal waits.
      if (!access.canEdit) return WORKSPACE_COPY.viewer.evals;
      if (grading) return GRADING_TOOLTIP;
      return removeEval.isPending ? "Removing an evaluation…" : "";
    }
    return "";
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      {/* The dataset's All Evaluations list (SavedEvalsList), fed this run's
          evals and the run page's rules for what can run, change or go. */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          p: 2,
          gap: 1,
        }}
      >
        <Typography sx={{ typography: "m2", fontWeight: 600, pr: 4 }}>
          All Evaluations
        </Typography>

        <Box sx={{ flex: 1, minHeight: 0 }}>
          {isPending ? (
            <LoadingScreen compact />
          ) : isError && configsData === undefined ? (
            <EmptyState
              title="Couldn’t load evaluations"
              body={refusalText(error, EVALS_LOAD_FAILED_TOOLTIP)}
              action={
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : configs.length === 0 ? (
            <EmptyState
              title="No evaluations yet"
              body="Add one to grade this run."
              action={
                <ViewerLock
                  locked={!access.canEdit}
                  title={WORKSPACE_COPY.viewer.evals}
                >
                  <Button
                    variant="outlined"
                    size="small"
                    disabled={!access.canEdit}
                    startIcon={<Iconify icon="solar:add-linear" width={14} />}
                    onClick={() => onAddEvaluations?.()}
                  >
                    Add evaluations
                  </Button>
                </ViewerLock>
              }
            />
          ) : (
            <SavedEvalsList
              key={listKey}
              evals={configs}
              hideRunStatus
              disabledReason={disabledReason}
              onAddClick={() => onAddEvaluations?.()}
              onRunEvalClick={(config) => requestRerun([config])}
              onRunSelected={(list) => requestRerun(list)}
              onEditEvalClick={(config) =>
                onEdit?.(config, { onSuccess: onClose })
              }
              onDeleteEvalClick={(config) => handleRemove(config)}
              // The run page asks before any re-grade, from here or a
              // column menu, so the row doesn't ask first.
              confirmRun={false}
              confirmText={{
                deleteLabel: "Remove",
                delete: (config) =>
                  `Remove ${config.name} from this environment? Its column leaves this run's results.`,
              }}
            />
          )}
        </Box>
      </Box>
    </SideDrawer>
  );
}

AllEvaluationsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.shape({ id: PropTypes.string }),
  runTestId: PropTypes.string,
  executionId: PropTypes.string,
  canRun: PropTypes.bool,
  grading: PropTypes.bool,
  // The run page's re-run is on its way, from here or from a column menu.
  // Runs and edits both wait on it: one sent now would grade the old settings.
  rerunPending: PropTypes.bool,
  // The run page's edit form is open.
  editOpen: PropTypes.bool,
  onRerun: PropTypes.func,
  onEdit: PropTypes.func,
  onAddEvaluations: PropTypes.func,
  // Changes on each open, so a reopened list starts unticked.
  listKey: PropTypes.number,
};
