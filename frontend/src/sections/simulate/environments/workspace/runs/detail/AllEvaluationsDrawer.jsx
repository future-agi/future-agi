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
import ConfirmRunEvaluations from "src/sections/common/EvaluationDrawer/ConfirmRunEvaluations";
import {
  useEnvironmentRunTest,
  useRemoveAppliedEvaluation,
} from "src/api/simulate-environments/environments";
import {
  runResultsKey,
  useRunNewEvals,
} from "src/api/simulate-environments/runEvals";
import SideDrawer from "../../../components/SideDrawer";
import EmptyState from "../../../components/EmptyState";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import { EVALS_COPY } from "../../evals/evals.constants";
import { refusalText } from "../../evals/refusalText";

// Only a harness call rerun can refresh a non-regradable row's score, so both
// its checkbox and its run action are locked with the same explanation.
export const HARNESS_ONLY_TOOLTIP = "Only a call rerun refreshes this";
export const NOT_EDITABLE_TOOLTIP = EVALS_COPY.notEditable;
// Shown when a ticked eval has no mapping of its own: on this page that is one
// of the harness's suite evals, whose scores the platform will replace.
export const HARNESS_NOTE =
  "Scores the harness gave will be replaced by the platform's.";

// A failed or unanswered request may still have started grading.
const RUN_FALLBACK = "Grading may not have started. Try again.";
const REMOVE_FALLBACK = "Couldn’t remove the evaluation. Try again.";
const NOT_FINISHED_TOOLTIP = "Available once this run finishes.";
const GRADING_TOOLTIP = "Available once grading finishes.";
// A stable empty-array constant: `= []` as a hook default is a fresh
// reference on every render, which would re-run the memos below even when
// the run test's configs have not changed.
const NO_CONFIGS = [];

/**
 * The run page's "All Evaluations" drawer.
 *
 * Every eval bound to the run test is listed here, including the harness's
 * per-scenario checks — those can't be ticked or re-run, because only a call
 * rerun refreshes them, but they can still be removed. Ticking rows and the
 * footer both open the same confirm dialog as the rest of the product; a
 * single row's run icon opens it pre-filled with just that row.
 *
 * A row's edit icon opens the add flow on that eval's own settings; once it is
 * saved, the same confirm dialog opens for just that eval, so an edit is graded
 * again through the one run path here.
 */
export default function AllEvaluationsDrawer({
  open,
  onClose,
  env,
  runTestId,
  executionId,
  canRun = false,
  grading = false,
  onAddEvaluations,
}) {
  const queryClient = useQueryClient();
  const [ticked, setTicked] = useState(() => new Set());
  // Holds the configs the confirm dialog is about, or null while it's closed.
  const [confirming, setConfirming] = useState(null);
  // The eval whose settings are open for editing, or null.
  const [editing, setEditing] = useState(null);

  // The run page keeps this drawer mounted while it's closed, so ticks, an open
  // edit or a pending confirm would otherwise still be there on reopening —
  // including on a removed eval that came back under the same id.
  useEffect(() => {
    if (open) return;
    setTicked(new Set());
    setEditing(null);
    setConfirming(null);
  }, [open]);

  const {
    data: configsData,
    isPending,
    isError,
    error,
    refetch,
  } = useEnvironmentRunTest(runTestId, { enabled: open && Boolean(runTestId) });
  const configs = configsData ?? NO_CONFIGS;

  const runEvals = useRunNewEvals();
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

  const handleConfirm = (list) => {
    const evalConfigIds = list.map((c) => c.id);
    runEvals.mutate(
      { runTestId, executionId, evalConfigIds },
      {
        onSuccess: (result) => {
          setConfirming(null);
          setTicked(new Set());
          // The endpoint answers 200 even when the async dispatch itself
          // failed (nothing was graded and it can be retried), so that case
          // is told apart by its own sentence, not the status code.
          if (/dispatch failed/i.test(result?.message || "")) {
            enqueueSnackbar(RUN_FALLBACK, { variant: "warning" });
            return;
          }
          const k = evalConfigIds.length;
          enqueueSnackbar(
            `Grading ${k} evaluation${k === 1 ? "" : "s"}. This run updates when grading finishes.`,
            { variant: "success" },
          );
          onClose();
        },
        onError: (e) => {
          enqueueSnackbar(refusalText(e, RUN_FALLBACK), { variant: "error" });
        },
      },
    );
  };

  const handleRemove = (config) => {
    removeEval.mutate(
      { id: env?.id, evalConfigId: config.id },
      {
        onSuccess: () =>
          queryClient.invalidateQueries({
            queryKey: runResultsKey(executionId),
          }),
        onError: (e) =>
          enqueueSnackbar(refusalText(e, REMOVE_FALLBACK), {
            variant: "error",
          }),
      },
    );
  };

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
              body={refusalText(
                error,
                "Couldn’t load this run's evaluations. Try again.",
              )}
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
                      ? NOT_FINISHED_TOOLTIP
                      : "";
                const runTooltip = !runnable
                  ? HARNESS_ONLY_TOOLTIP
                  : !canRun
                    ? NOT_FINISHED_TOOLTIP
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
                          disabled={!runnable || !canRun || runEvals.isPending}
                          onClick={() => setConfirming([config])}
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
                            !editable || grading || !canRun || Boolean(editing)
                          }
                          onClick={() => setEditing(config)}
                        >
                          <Iconify icon="solar:pen-linear" width={18} />
                        </IconButton>
                      </span>
                    </Tooltip>
                    {/* A grader that has not started yet skips a removed eval,
                        so its pending cell on this run would never clear. */}
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
          <Tooltip arrow title={!canRun ? NOT_FINISHED_TOOLTIP : ""}>
            <span style={{ display: "block" }}>
              <Button
                fullWidth
                variant="contained"
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
                disabled={
                  selectedIds.length === 0 || !canRun || runEvals.isPending
                }
                onClick={() => setConfirming(selectedConfigs)}
              >
                {allTicked && runnableIds.length > 0
                  ? `Run All (${selectedIds.length})`
                  : `Run (${selectedIds.length})`}
              </Button>
            </span>
          </Tooltip>
        </Box>
      </Box>

      <AddEvaluationDrawer
        open={Boolean(editing)}
        env={env}
        editingEval={editing}
        onClose={() => setEditing(null)}
        onEdited={(updated) => {
          setEditing(null);
          setConfirming(updated ? [updated] : null);
        }}
      />

      <ConfirmRunEvaluations
        open={Boolean(confirming)}
        onClose={() => setConfirming(null)}
        onConfirm={handleConfirm}
        selectedUserEvalList={confirming || []}
        loading={runEvals.isPending}
        // Only runnable configs ever reach the dialog, so an empty mapping
        // there means one of the harness's built-in suite evals; its score
        // came from the harness and will be replaced by the platform's.
        getNote={(list) =>
          list.some((c) => Object.keys(c.mapping || {}).length === 0)
            ? HARNESS_NOTE
            : null
        }
      />
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
  onAddEvaluations: PropTypes.func,
};
