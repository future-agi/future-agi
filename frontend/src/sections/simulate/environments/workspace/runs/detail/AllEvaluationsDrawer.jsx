import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
import {
  useEnvironmentRunTest,
  useRemoveAppliedEvaluation,
} from "src/api/simulate-environments/environments";
import {
  runAnalyticsKey,
  callDetailKeyPrefix,
  runResultsKey,
} from "src/api/simulate-environments/runEvals";
import SideDrawer from "../../../components/SideDrawer";
import EmptyState from "../../../components/EmptyState";
import { refusalText } from "../../evals/refusalText";
import {
  EVALS_LOAD_FAILED_TOOLTIP,
  GRADING_TOOLTIP,
  HARNESS_ONLY_TOOLTIP,
  NOT_COMPLETED_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
} from "./allEvaluationsDrawer.constants";

const REMOVE_FALLBACK = "Couldn’t remove the evaluation. Try again.";
// A stable empty-array constant: `= []` as a hook default is a fresh
// reference on every render, which would re-run the memos below even when
// the run test's configs have not changed.
const NO_CONFIGS = [];

/**
 * The run page's "All Evaluations" drawer.
 *
 * Every eval bound to the run test is listed here, including the harness's
 * per-scenario checks — those can't be ticked or re-run, because only a call
 * rerun refreshes them, but they can still be removed. The footer runs the
 * ticked rows and a row's run icon runs just that row; a row's edit icon edits
 * that eval. Both go to the run page (`onRerun`, `onEdit`), whose one edit form
 * and confirm dialog also serve the table's column menus. This drawer closes
 * once a re-run it asked for is queued; a saved edit leaves it open, since
 * the person is still managing the run's evals here.
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
}) {
  const queryClient = useQueryClient();
  const [ticked, setTicked] = useState(() => new Set());

  // The run page keeps this drawer mounted while it's closed, so ticks would
  // otherwise still be there on reopening — including on a removed eval that
  // came back under the same id.
  useEffect(() => {
    if (!open) setTicked(new Set());
  }, [open]);

  const {
    data: configsData,
    isPending,
    isError,
    error,
    refetch,
  } = useEnvironmentRunTest(runTestId, { enabled: open && Boolean(runTestId) });
  const configs = configsData ?? NO_CONFIGS;

  const removeEval = useRemoveAppliedEvaluation();

  const runnableIds = useMemo(
    () => configs.filter((c) => c.regradable === true).map((c) => c.id),
    [configs],
  );
  // Filtering by the *current* runnableIds, not by whatever was ticked when a
  // row was removed, is what drops a removed row out of the selection on its
  // own — nothing here needs to react to a remove explicitly.
  const selectedIds = useMemo(
    () => runnableIds.filter((id) => ticked.has(id)),
    [runnableIds, ticked],
  );
  const selectedConfigs = useMemo(
    () => configs.filter((c) => selectedIds.includes(c.id)),
    [configs, selectedIds],
  );

  const allTicked =
    runnableIds.length > 0 && runnableIds.every((id) => ticked.has(id));
  const someTicked = selectedIds.length > 0 && !allTicked;

  const handleSelectAll = () => {
    setTicked(allTicked ? new Set() : new Set(runnableIds));
  };
  const handleToggleRow = (id) => {
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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

  return (
    <SideDrawer open={open} onClose={onClose} width={520}>
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <Box sx={{ p: 3, pb: 1 }}>
          <Typography
            sx={{ typography: "m2", fontWeight: "fontWeightSemiBold", pr: 4 }}
          >
            All Evaluations
          </Typography>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{ mt: 2 }}
          >
            <FormControlLabel
              control={
                <Checkbox
                  checked={allTicked}
                  indeterminate={someTicked}
                  disabled={runnableIds.length === 0}
                  onChange={handleSelectAll}
                />
              }
              label={`Evals (${configs.length})`}
            />
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              onClick={() => onAddEvaluations?.()}
            >
              Add Evaluations
            </Button>
          </Stack>
        </Box>

        <Box sx={{ flex: 1, overflowY: "auto", px: 3 }}>
          {isPending ? (
            <Stack alignItems="center" sx={{ py: 6 }}>
              <CircularProgress size={22} />
            </Stack>
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
            />
          ) : (
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {configs.map((config) => {
                const runnable = config.regradable === true;
                const editable = config.editable === true;
                const editTooltip = !editable
                  ? NOT_EDITABLE_TOOLTIP
                  : grading
                    ? GRADING_TOOLTIP
                    : !canRun
                      ? NOT_COMPLETED_TOOLTIP
                      : "";
                const runTooltip = !runnable
                  ? HARNESS_ONLY_TOOLTIP
                  : !canRun
                    ? cannotRunTooltip
                    : "";
                return (
                  <Stack
                    key={config.id}
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{ py: 1.5 }}
                  >
                    <Tooltip
                      arrow
                      title={!runnable ? HARNESS_ONLY_TOOLTIP : ""}
                    >
                      <span>
                        <Checkbox
                          checked={ticked.has(config.id)}
                          disabled={!runnable}
                          onChange={() => handleToggleRow(config.id)}
                          inputProps={{ "aria-label": `Select ${config.name}` }}
                        />
                      </span>
                    </Tooltip>
                    <Iconify
                      icon="solar:shield-check-linear"
                      width={18}
                      sx={{ color: "text.subtitle", flexShrink: 0 }}
                    />
                    <Typography sx={{ flex: 1, typography: "s1" }} noWrap>
                      {config.name}
                    </Typography>
                    {config.eval_type && (
                      <EvalTypeBadge type={config.eval_type} />
                    )}
                    <Tooltip arrow title={runTooltip}>
                      <span>
                        <IconButton
                          aria-label={`Run ${config.name}`}
                          disabled={!runnable || !canRun || rerunPending}
                          onClick={() => requestRerun([config])}
                        >
                          <Iconify icon="solar:play-circle-linear" width={18} />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Tooltip arrow title={editTooltip}>
                      <span>
                        <IconButton
                          aria-label={`Edit ${config.name}`}
                          disabled={
                            !editable ||
                            grading ||
                            !canRun ||
                            editOpen ||
                            rerunPending
                          }
                          onClick={() => onEdit?.(config)}
                        >
                          <Iconify icon="solar:pen-linear" width={18} />
                        </IconButton>
                      </span>
                    </Tooltip>
                    {/* The grader copes with a removal mid-run (it drops the
                        pending cell), but the column would vanish from a table
                        that is polling for its scores. Removal waits. */}
                    <Tooltip arrow title={grading ? GRADING_TOOLTIP : ""}>
                      <span>
                        <IconButton
                          aria-label={`Remove ${config.name}`}
                          disabled={grading || removeEval.isPending}
                          onClick={() => handleRemove(config)}
                        >
                          <Iconify
                            icon="solar:trash-bin-trash-linear"
                            width={18}
                          />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Stack>
                );
              })}
            </Stack>
          )}
        </Box>

        <Box sx={{ p: 3, borderTop: "1px solid", borderColor: "divider" }}>
          <Tooltip arrow title={!canRun ? cannotRunTooltip : ""}>
            <span style={{ display: "block" }}>
              <Button
                fullWidth
                variant="contained"
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
                disabled={selectedIds.length === 0 || !canRun || rerunPending}
                onClick={() => requestRerun(selectedConfigs)}
              >
                {allTicked && runnableIds.length > 0
                  ? `Run All (${selectedIds.length})`
                  : `Run (${selectedIds.length})`}
              </Button>
            </span>
          </Tooltip>
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
};
