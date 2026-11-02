import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Collapse,
  FormControlLabel,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
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
import SoonChip from "../../../components/SoonChip";
import { refusalText } from "../../evals/refusalText";
import { EvalMapping } from "../../evals/EvalMappingView";
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
const NO_APPLIED = [];

/**
 * The run page's "All Evaluations" drawer.
 *
 * Every eval bound to the run test is listed here, including the harness's
 * per-scenario checks — those can't be ticked or re-run, because only a call
 * rerun refreshes them, but they can still be removed. The footer runs the
 * ticked rows and a row's run icon runs just that row; a row's edit icon edits
 * that eval. Both go to the run page (`onRerun`, `onEdit`), whose one edit form
 * and confirm dialog also serve the table's column menus, and this drawer
 * closes once the grading it asked for is queued.
 *
 * Ticked rows can also be removed together, after a confirm. A row opens to
 * the eval's description and mapping; both come from the environment's own
 * eval row (`appliedEvals`, matched by config id), since the run test sends
 * neither a description nor labelled inputs.
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
  appliedEvals = NO_APPLIED,
}) {
  const queryClient = useQueryClient();
  const [ticked, setTicked] = useState(() => new Set());
  const [expanded, setExpanded] = useState(() => new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  // The run page keeps this drawer mounted while it's closed, so ticks would
  // otherwise still be there on reopening — including on a removed eval that
  // came back under the same id.
  useEffect(() => {
    if (!open) {
      setTicked(new Set());
      setExpanded(new Set());
      setConfirmBulk(false);
    }
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

  const appliedById = useMemo(
    () => new Map((appliedEvals || []).map((e) => [e?.id, e])),
    [appliedEvals],
  );

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

  const handleToggleExpanded = (id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const requestRerun = (list) => onRerun?.(list, { onSuccess: onClose });

  const refreshRunViews = () => {
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
  };

  const handleRemove = (config) => {
    removeEval.mutate(
      { id: env?.id, evalConfigId: config.id },
      {
        onSuccess: refreshRunViews,
        onError: (e) =>
          enqueueSnackbar(refusalText(e, REMOVE_FALLBACK), {
            variant: "error",
          }),
      },
    );
  };

  // One DELETE per ticked eval, the same call a row's own remove makes.
  // `mutate`'s per-call callbacks only fire for the last of several calls, so
  // each remove is awaited on its own and every refusal is reported by name.
  const handleBulkRemove = async () => {
    const list = selectedConfigs;
    setConfirmBulk(false);
    const results = await Promise.allSettled(
      list.map((config) =>
        removeEval.mutateAsync({ id: env?.id, evalConfigId: config.id }),
      ),
    );
    if (results.some((r) => r.status === "fulfilled")) refreshRunViews();
    results.forEach((r, i) => {
      if (r.status !== "rejected") return;
      enqueueSnackbar(
        `${list[i].name}: ${refusalText(r.reason, REMOVE_FALLBACK)}`,
        { variant: "error" },
      );
    });
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
            {selectedIds.length > 0 ? (
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Button
                  size="small"
                  onClick={() => setTicked(new Set())}
                  sx={{ color: "text.secondary", minWidth: 0 }}
                >
                  Clear
                </Button>
                {/* Same wait as a row's remove: a column vanishing from a
                    table that is polling for its scores. */}
                <CustomTooltip
                  show={grading}
                  arrow
                  size="small"
                  title={GRADING_TOOLTIP}
                >
                  <span>
                    <Button
                      size="small"
                      color="error"
                      disabled={grading || removeEval.isPending}
                      onClick={() => setConfirmBulk(true)}
                      startIcon={
                        <Iconify
                          icon="solar:trash-bin-trash-linear"
                          width={16}
                        />
                      }
                    >
                      {`Remove ${selectedIds.length}`}
                    </Button>
                  </span>
                </CustomTooltip>
              </Stack>
            ) : (
              <Button
                variant="outlined"
                size="small"
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                onClick={() => onAddEvaluations?.()}
              >
                Add Evaluations
              </Button>
            )}
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
                const isOpen = expanded.has(config.id);
                const entry = appliedById.get(config.id);
                return (
                  <Box key={config.id}>
                    <Stack
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
                            inputProps={{
                              "aria-label": `Select ${config.name}`,
                            }}
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
                            <Iconify
                              icon="solar:play-circle-linear"
                              width={18}
                            />
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
                            onClick={() =>
                              onEdit?.(config, { onSuccess: onClose })
                            }
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
                      <IconButton
                        size="small"
                        aria-label={`Show details for ${config.name}`}
                        aria-expanded={isOpen}
                        onClick={() => handleToggleExpanded(config.id)}
                      >
                        <Iconify
                          icon="solar:alt-arrow-down-linear"
                          width={16}
                          sx={{
                            color: "text.subtitle",
                            transform: isOpen ? "rotate(180deg)" : "none",
                            transition: "transform 160ms ease",
                          }}
                        />
                      </IconButton>
                    </Stack>
                    <Collapse in={isOpen} unmountOnExit>
                      <EvalDetails config={config} entry={entry} />
                    </Collapse>
                  </Box>
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
      <ConfirmDialog
        open={confirmBulk}
        onClose={() => setConfirmBulk(false)}
        title={`Remove ${selectedIds.length} eval${selectedIds.length === 1 ? "" : "s"}?`}
        content={
          <Typography component="span" sx={{ typography: "s2" }}>
            New runs won&apos;t be graded on these. Past scores stay.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={handleBulkRemove}
            sx={{ px: 3 }}
          >
            {`Remove ${selectedIds.length}`}
          </Button>
        }
      />
    </SideDrawer>
  );
}

// A row's open body: the eval's description and what fills each variable.
// Both are the environment's eval row when there is one; a run test's own
// config has no description and only its raw mapping.
function EvalDetails({ config, entry }) {
  const blurb = entry?.description || entry?.blurb || "";
  return (
    <Box
      sx={{
        mb: 1.5,
        px: 1.5,
        py: 1.25,
        borderRadius: 1,
        bgcolor: "background.neutral",
      }}
    >
      {blurb ? (
        <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1 }}>
          {blurb}
        </Typography>
      ) : (
        <Box sx={{ mb: 1 }}>
          <SoonChip
            label="Description"
            what="This evaluation's description"
            size="tiny"
          />
        </Box>
      )}
      <EvalMapping item={entry && entry.inputs?.length ? entry : config} />
    </Box>
  );
}

EvalDetails.propTypes = {
  config: PropTypes.object.isRequired,
  entry: PropTypes.object,
};

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
  // The environment's `evaluations.selected[]` rows; an open row reads its
  // description and labelled mapping from the one with its config id.
  appliedEvals: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      description: PropTypes.string,
      inputs: PropTypes.array,
    }),
  ),
};
