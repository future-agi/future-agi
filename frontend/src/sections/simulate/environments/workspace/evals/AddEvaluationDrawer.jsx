import PropTypes from "prop-types";
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Button, CircularProgress, Stack } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import {
  EvalPickerDrawer,
  serializeEvalConfig,
} from "src/sections/common/EvalPicker";
import {
  chatEvalColumns,
  voiceEvalColumns,
} from "src/components/run-tests/common";
import {
  useAddRunEvaluation,
  useAddRunTestEval,
  useEditAppliedEvaluation,
  useEnvironmentRunTest,
} from "src/api/simulate-environments/environments";
import {
  harnessEnvironmentKey,
  harnessEnvironmentQuery,
} from "src/api/simulate-environments/environment";
import { useErrorLocalizationAvailable } from "src/hooks/useErrorLocalization";
import SideDrawer from "../../components/SideDrawer";
import EmptyState from "../../components/EmptyState";
import { EVALS_COPY } from "./evals.constants";
import { gradingCountsSentence } from "./gradingCounts";
import { refusalText } from "./refusalText";

const ADD_FALLBACK = "Couldn’t add the evaluation. Try again.";
const GRADE_FALLBACK = "Couldn’t grade this run. Try again.";
const EDIT_FALLBACK = "Couldn’t update the evaluation. Try again.";
const DETAIL_FALLBACK = EVALS_COPY.addedError;
const NO_INPUTS =
  "This evaluation has no inputs to map, so it can't run in an environment.";
const STALE_LIST =
  "Couldn’t refresh the evaluations already added here, so that list may be out of date.";
// A stable empty array: `= []` as a hook default is a fresh reference on every
// render, which would re-run both `useMemo`s below even when the run test's
// configs have not changed.
const NO_CONFIGS = [];
// The harness can't grade a composite eval on an environment run yet, so the
// picker neither lists nor creates one here.
const SINGLE_EVALS_ONLY = { template_type: ["single"] };

// Where error localization isn't available the picker's toggle is inert and
// always reports it off, so a save would switch off what the environment
// set. The row's own values go back instead — the config's copy too, since
// the server replaces the config whole.
const withRowErrorLocalizer = (body, row) => {
  const { error_localizer: _sent, ...rest } = body;
  const { error_localizer_enabled: _sentEnabled, ...runConfig } =
    rest.config.run_config;
  const saved = row.config?.run_config?.error_localizer_enabled;
  return {
    ...rest,
    ...(typeof row.error_localizer === "boolean" && {
      error_localizer: row.error_localizer,
    }),
    config: {
      ...rest.config,
      run_config: {
        ...runConfig,
        ...(saved !== undefined && { error_localizer_enabled: saved }),
      },
    },
  };
};

/**
 * Adding evaluations to a built environment.
 *
 * People pick from the whole catalogue in the product's own picker and map
 * every input themselves, against the fields of this environment's real calls
 * — the harness's short list and its preset inputs are only what the harness
 * chooses from. The evals are saved on the environment's run test through the
 * same endpoint the simulation page uses. Everything already on it, the
 * harness's own result columns included, sits in the picker's "Added
 * evaluations" box and cannot be picked again.
 *
 * Opened from a run, a new pick also grades that run's finished calls, and
 * each added eval can grade them too.
 *
 * Given `editingEval`, the picker opens straight on that eval's own settings
 * and saving edits it in place. Grading after an edit is the host's call, so
 * nothing is graded here.
 */
export default function AddEvaluationDrawer({
  open,
  env,
  executionId,
  onClose,
  editingEval = null,
  editingEvalId = null,
  onEdited,
}) {
  const envId = env?.id;
  const editMode = Boolean(editingEval || editingEvalId);
  const runMode = Boolean(executionId);
  const queryClient = useQueryClient();
  const detailQuery = useQuery(
    harnessEnvironmentQuery(envId, { enabled: open }),
  );
  const runTestId = detailQuery.data?.overview?.run?.run_test_id || "";
  const sourceColumns =
    detailQuery.data?.overview?.agent_type === "voice"
      ? voiceEvalColumns
      : chatEvalColumns;
  const runTestQuery = useEnvironmentRunTest(runTestId, {
    enabled: open && Boolean(runTestId),
  });
  const configs = runTestQuery.data ?? NO_CONFIGS;
  // The Evaluations tab only has display rows, so it hands over an id and the
  // full row (template, mapping, config) is read from the run test's own list.
  const editTarget = useMemo(
    () =>
      editingEval ??
      (editingEvalId
        ? configs.find((c) => c.id === editingEvalId) ?? null
        : null),
    [editingEval, editingEvalId, configs],
  );
  const addToRunTest = useAddRunTestEval();
  const gradeRun = useAddRunEvaluation();
  const editEval = useEditAppliedEvaluation();
  const errorLocalizationAvailable = useErrorLocalizationAvailable();

  const refreshFailed =
    open && runTestQuery.isError && runTestQuery.data !== undefined;

  // A failed refresh keeps the last list in the picker, and this read opts out
  // of the global error toast, so this is the only sign the list may be stale.
  useEffect(() => {
    if (refreshFailed) enqueueSnackbar(STALE_LIST, { variant: "warning" });
  }, [refreshFailed, runTestQuery.errorUpdatedAt]);

  // The picker needs the run test's own eval list to know what is already
  // bound (nothing already on the run can be picked again), so it must
  // wait on that read too, not just the environment detail — a run test with
  // a big payload can hit the bounded-read wall or a DB hiccup on its own.
  const pending =
    detailQuery.isPending || (Boolean(runTestId) && runTestQuery.isPending);
  const failed = detailQuery.isError
    ? detailQuery
    : runTestId && runTestQuery.isError
      ? runTestQuery
      : null;

  // An eval with no inputs mapped is stored like one of the harness's own
  // result columns and is never graded, so it offers no "Grade this run".
  const addedEvals = useMemo(
    () =>
      configs.map((c) => ({
        id: c.template_id,
        name: c.name,
        canGrade: Object.keys(c.mapping || {}).length > 0,
      })),
    [configs],
  );
  const existingEvals = useMemo(
    () => configs.map((c) => ({ template_id: c.template_id, name: c.name })),
    [configs],
  );

  // Grading is asynchronous; the counts are all there is to show right now.
  const grade = async (name) => {
    const counts = await gradeRun.mutateAsync({ id: envId, executionId, name });
    enqueueSnackbar(
      `${gradingCountsSentence(counts || {})} Reload this run to see the new verdicts.`,
      { variant: "success" },
    );
  };

  // The picker keeps its config step open when this rejects, so a refused add
  // is re-thrown. Grading after a successful add is a second step: its failure
  // leaves the eval added and says which part failed, without re-throwing.
  const addPicked = async (config) => {
    const body = serializeEvalConfig(config);
    if (!Object.keys(body.mapping || {}).length) {
      enqueueSnackbar(NO_INPUTS, { variant: "error" });
      throw new Error(NO_INPUTS);
    }
    try {
      await addToRunTest.mutateAsync({ runTestId, body });
    } catch (error) {
      enqueueSnackbar(refusalText(error, ADD_FALLBACK), { variant: "error" });
      throw error;
    }
    if (!runMode) {
      enqueueSnackbar("Evaluation added", { variant: "success" });
      return;
    }
    try {
      await grade(body.name);
    } catch (error) {
      enqueueSnackbar(
        `Added, but grading this run failed: ${refusalText(error, GRADE_FALLBACK)}`,
        { variant: "error" },
      );
    }
  };

  // The picker keeps its config step open when this rejects, so a refused edit
  // is re-thrown. The name is locked while editing, so it is never a change and
  // is left out rather than sent back to the name-uniqueness check. The
  // environment's edit route refuses a template id outright: switching
  // templates is a remove and an add, not an edit.
  const saveEdit = async (config) => {
    const {
      name: _name,
      template_id: _templateId,
      ...sent
    } = serializeEvalConfig(config);
    const body = errorLocalizationAvailable
      ? sent
      : withRowErrorLocalizer(sent, editTarget);
    if (!Object.keys(body.mapping || {}).length) {
      enqueueSnackbar(NO_INPUTS, { variant: "error" });
      throw new Error(NO_INPUTS);
    }
    let updated;
    try {
      updated = await editEval.mutateAsync({
        id: envId,
        evalConfigId: editTarget.id,
        runTestId,
        body,
      });
    } catch (error) {
      enqueueSnackbar(refusalText(error, EDIT_FALLBACK), { variant: "error" });
      throw error;
    }
    enqueueSnackbar("Evaluation updated", { variant: "success" });
    onEdited?.(updated);
  };

  // `id` stays the template id — the picker loads the template by it — and
  // `userEvalId` keys the picker per eval, so two evals of one template don't
  // share a mounted form. An eval the environment was built with keeps its
  // model and error localizer on the row itself, but the picker only reads
  // them from `run_config`, so they are seeded there; anything already saved
  // in `run_config` wins.
  const initialEval = useMemo(
    () =>
      editTarget
        ? {
            id: editTarget.template_id,
            userEvalId: editTarget.id,
            template_id: editTarget.template_id,
            name: editTarget.name,
            mapping: editTarget.mapping || {},
            config: editTarget.config || {},
            run_config: {
              ...(editTarget.model && { model: editTarget.model }),
              ...(typeof editTarget.error_localizer === "boolean" && {
                error_localizer_enabled: editTarget.error_localizer,
              }),
              ...(editTarget.config?.run_config || {}),
            },
          }
        : null,
    [editTarget],
  );

  const gradeAdded = (addedEval) =>
    grade(addedEval.name).catch((error) =>
      enqueueSnackbar(refusalText(error, GRADE_FALLBACK), { variant: "error" }),
    );

  const handleClose = () => {
    if (envId)
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(envId) });
    onClose?.();
  };

  // Once the run test's evals have been read, a failed background refresh
  // (window focus, the add's own refetch) keeps the last good list rather
  // than tearing down a picker the person may be halfway through.
  const loaded = open && runTestId && runTestQuery.data !== undefined;
  // While editing, the picker never falls back to its add list: a missing or
  // harness-set eval gets its own message instead.
  const editRefusal =
    loaded && editMode
      ? !editTarget
        ? "This evaluation no longer exists."
        : editTarget.editable !== true
          ? EVALS_COPY.notEditable
          : null
      : null;

  if (loaded && !editRefusal) {
    return (
      <EvalPickerDrawer
        open
        onClose={handleClose}
        source="simulation"
        sourceId={runTestId}
        sourceColumns={sourceColumns}
        existingEvals={editMode ? NO_CONFIGS : existingEvals}
        addedEvals={editMode ? null : addedEvals}
        initialEval={initialEval}
        lockedFilters={SINGLE_EVALS_ONLY}
        hideCompositeCreate
        requireInputs
        addedEvalAction={
          runMode && !editMode
            ? {
                label: "Grade this run",
                onClick: gradeAdded,
                busyName: gradeRun.isPending
                  ? gradeRun.variables?.name
                  : undefined,
                disabled: gradeRun.isPending,
                show: (addedEval) => addedEval.canGrade,
              }
            : null
        }
        onEvalAdded={editMode ? saveEdit : addPicked}
      />
    );
  }

  return (
    <SideDrawer open={open} onClose={handleClose} width={560}>
      <Box sx={{ p: 3 }}>
        {editRefusal ? (
          <EmptyState
            icon="solar:danger-triangle-linear"
            title="Can’t edit this evaluation"
            body={editRefusal}
            action={
              <Button variant="outlined" size="small" onClick={handleClose}>
                Back to evaluations
              </Button>
            }
          />
        ) : pending ? (
          <Stack alignItems="center" sx={{ py: 6 }}>
            <CircularProgress size={22} />
          </Stack>
        ) : failed ? (
          <EmptyState
            icon="solar:danger-triangle-linear"
            title="Couldn’t load evaluations"
            body={refusalText(failed.error, DETAIL_FALLBACK)}
            action={
              <Button
                variant="outlined"
                size="small"
                onClick={() => {
                  if (detailQuery.isError) detailQuery.refetch();
                  if (runTestId && runTestQuery.isError) runTestQuery.refetch();
                }}
              >
                Retry
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon="solar:clock-circle-linear"
            title="Not ready yet"
            body="Evaluations can be added once this environment finishes building."
          />
        )}
      </Box>
    </SideDrawer>
  );
}

AddEvaluationDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ id: PropTypes.string }),
  executionId: PropTypes.string,
  onClose: PropTypes.func,
  editingEval: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    template_id: PropTypes.string,
    mapping: PropTypes.object,
    config: PropTypes.object,
    model: PropTypes.string,
    error_localizer: PropTypes.bool,
    editable: PropTypes.bool,
  }),
  editingEvalId: PropTypes.string,
  onEdited: PropTypes.func,
};
