import {
  Box,
  Typography,
  Button,
  IconButton,
  Drawer,
  useTheme,
  Collapse,
} from "@mui/material";
import { Icon } from "@iconify/react";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import SvgColor from "../../../components/svg-color";
import { LoadingButton } from "@mui/lab";
import EvaluationsSelectionGrid from "./EvaluationsSelectionGrid";
import { useEvalsList, getUserEvalListKey } from "./getEvalsList";
import SavedEvalsList from "./SavedEvalsList";
import SavedEvalsSkeleton from "./SavedEvalsSkeleton";
import DeleteEval from "./DeleteEval";
import RunEvals from "./RunEvals";
import CustomEvalsForm from "./CustomEvalsForm";
import { ConfirmDialog } from "../../../components/custom-dialog";
import EvaluationMappingForm from "./EvaluationMappingForm";
import EvaluationProvider from "./context/EvaluationProvider";
import {
  EvaluationContext,
  useEvaluationContext,
} from "./context/EvaluationContext";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import CreateEvaluationGroupDrawer from "./CreateEvaluationGroupDrawer";
import { useSearchParams } from "react-router-dom";
import { resetEvalStore } from "src/sections/evals/store/useEvalStore";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { useRunEvaluationStore } from "src/sections/develop-detail/states";
import { useWorkbenchEvaluationContext } from "src/sections/workbench/createPrompt/Evaluation/context/WorkbenchEvaluationContext";

const EvaluationDrawerChild = ({
  onClose,
  allColumns,
  refreshGrid,
  listComponent,
  setFormIsDirty,
  existingEvals = [],
  requiredColumnIds = "",
}) => {
  const theme = useTheme();
  const { experimentId } = useParams();
  const queryClient = useQueryClient();
  // Safe outside WorkbenchEvaluationProvider — context default returns { versions: [] }
  const { versions: workbenchVersions } = useWorkbenchEvaluationContext();

  const {
    module,
    visibleSection,
    setVisibleSection,
    isDirty,
    setIsDirty,
    actionButtonConfig,
    setCurrentTab,
    setSelectedGroup,
    registerOpenEditForSavedEval,
  } = useEvaluationContext();
  const { id } = actionButtonConfig;
  const [openDeleteEval, setOpenDeleteEval] = useState(null);
  const [selectedEvals, setSelectedEvals] = useState([]);
  const [confirmRunEvaluationsOpen, setConfirmRunEvaluationsOpen] =
    useState(false);
  const [evalPickerOpen, setEvalPickerOpen] = useState(false);
  // When editing an existing eval, pre-select it so the picker opens at config step
  const [editingEval, setEditingEval] = useState(null);

  // Route a saved-eval row into the EvalPicker in edit mode. Shared between
  // the saved-eval list "Edit" button and the column-menu "Edit Eval" entry.
  //
  // `name` is deliberately the saved user-eval *instance* name (not the
  // template name) so the edit form pre-fills the instance's current name —
  // users can then rename the binding. EvalPickerConfigFull's isEditMode
  // branch reads this via evalData.name.
  const openEditForSavedEval = useCallback(
    (evalItem) => {
      const tplId = evalItem.templateId || evalItem.template_id;
      if (!tplId) {
        enqueueSnackbar("Cannot edit: template ID not found. Try refreshing.", {
          variant: "warning",
        });
        return;
      }
      setEditingEval({
        ...evalItem,
        // id is what EvalPickerConfigFull passes to useEvalDetail(templateId)
        id: tplId,
        name: evalItem.name,
        evalType: evalItem.eval_type,
        // Pre-populate the config screen so it renders before the detail
        // API responds. Read run_config from the stored config blob (where
        // the backend persists it) and fall back to a top-level run_config
        // field for callers that pre-shape evalItem differently.
        config: {
          required_keys: evalItem.eval_required_keys || [],
          ...(evalItem.config?.run_config || evalItem.run_config || {}),
          // Map the UserEvalMetric.error_localizer BooleanField to the key
          // EvalPickerConfigFull expects so the toggle shows the saved state.
          ...(evalItem.error_localizer !== undefined
            ? { error_localizer_enabled: evalItem.error_localizer }
            : {}),
        },
        mapping: evalItem.mapping || {},
        outputType: evalItem.output_type,
        // Existing UserEvalMetric id — handleAdd routes via editEval
        // endpoint instead of addEval to avoid duplicate bindings.
        // The column-menu path matches via user_eval_id/userEvalId, so
        // evalItem.id may not be the user-eval id — normalize here.
        userEvalId: evalItem.user_eval_id ?? evalItem.userEvalId ?? evalItem.id,
      });
      setEvalPickerOpen(true);
    },
    [enqueueSnackbar],
  );

  // Share the edit handler with nested list components (e.g. Optimization's
  // AddedEvaluations uses its own SavedEvalsList render path and needs the
  // same edit entry point.)
  useEffect(() => {
    registerOpenEditForSavedEval?.(openEditForSavedEval);
    return () => registerOpenEditForSavedEval?.(null);
  }, [openEditForSavedEval, registerOpenEditForSavedEval]);

  // Intercept: when visibleSection becomes "config", open the new EvalPickerDrawer instead
  useEffect(() => {
    if (visibleSection === "config") {
      setEditingEval(null); // Clear any stale edit state — this opens at the list step
      setEvalPickerOpen(true);
      setVisibleSection("list");
    }
  }, [visibleSection, setVisibleSection]);

  const { data, isLoading } = useEvalsList(
    id,
    {
      eval_type: "user",
    },
    module,
    experimentId,
  );

  useEffect(() => {
    setFormIsDirty(isDirty);
  }, [isDirty]);

  const SavedEvals = data?.evals;

  // When "Edit Eval" is invoked from the data-grid column menu, the column's
  // sourceId is dropped into pendingEditEvalId. Once the saved-evals list
  // loads, locate the matching user eval and route it through the same
  // edit flow as the saved-evals list row action.
  const pendingEditEvalId = useRunEvaluationStore((s) => s.pendingEditEvalId);
  const clearPendingEditEval = useRunEvaluationStore(
    (s) => s.clearPendingEditEval,
  );
  useEffect(() => {
    if (!pendingEditEvalId || !SavedEvals?.length) return;
    // The column-menu "Edit Eval" signal is global (Zustand). Multiple
    // EvaluationDrawer instances (dataset, run-optimization, experiment,
    // etc.) can be mounted simultaneously under the same page and would
    // otherwise race to handle it. The column menu lives on the dataset
    // grid, so the signal is always meant for the dataset drawer.
    // Treat an empty/unset module as "dataset".
    const effectiveModule = module || "dataset";
    if (effectiveModule !== "dataset") return;
    const match = SavedEvals.find(
      (e) =>
        e.id === pendingEditEvalId ||
        e.user_eval_id === pendingEditEvalId ||
        e.userEvalId === pendingEditEvalId,
    );
    if (match) {
      openEditForSavedEval(match);
      clearPendingEditEval();
    }
  }, [
    module,
    pendingEditEvalId,
    SavedEvals,
    openEditForSavedEval,
    clearPendingEditEval,
  ]);

  const _id = useMemo(() => {
    switch (module) {
      case "experiment":
        return experimentId;
      default:
        return id;
    }
  }, [module, id, experimentId]);

  useEffect(() => {
    return () => {
      resetEvalStore();
      setCurrentTab("evals");
      setSelectedGroup(null);
      setVisibleSection("list");
    };
  }, [setCurrentTab, setSelectedGroup, setVisibleSection]);

  // After any eval action (add/run/stop/edit/delete) we need to re-fetch the
  // queries that render eval state. Mirrors ManageExperimentEvalsDrawer's
  // invalidateEvalCaches so the shared drawer and the experiment-specific
  // drawer stay in sync.
  const invalidateEvalCaches = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: getUserEvalListKey(module, id),
    });
    if (module === "experiment" && experimentId) {
      queryClient.invalidateQueries({
        queryKey: ["experiment", experimentId],
      });
      queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
      queryClient.invalidateQueries({
        queryKey: ["experiment", "user-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["develop", "user-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["develop", "previously_configured-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["optimize-develop-column-info"],
      });
    }
  }, [queryClient, module, id, experimentId]);

  return (
    <>
      <Box
        display="flex"
        flexDirection="row"
        height="100%"
        sx={{
          p: theme.spacing(2),
          backgroundColor: theme.palette.background.paper,
          display: "flex",
          flex: 1,
        }}
      >
        <Collapse
          in={visibleSection === "list"}
          orientation="horizontal"
          // unmountOnExit
          sx={{
            display: "flex",
            flexDirection: "column",
            flexGrow: 1,
            gap: theme.spacing(2),

            "& .MuiCollapse-wrapper, & .MuiCollapse-wrapperInner": {
              width: "100%",
              display: "flex",
              flexDirection: "column",
            },
          }}
        >
          {listComponent ? (
            listComponent
          ) : (
            <>
              <Box
                display="flex"
                justifyContent="space-between"
                alignItems="center"
                mb={theme.spacing(1)}
              >
                <Typography fontSize={16} fontWeight={600}>
                  All Evaluations
                </Typography>
                <IconButton
                  onClick={onClose}
                  sx={{ p: 0, color: "text.primary" }}
                >
                  <Icon icon="mingcute:close-line" />
                </IconButton>
              </Box>
              {isLoading ? (
                <Box
                  border="1px solid"
                  borderColor={theme.palette.divider}
                  borderRadius={0.5}
                  p={theme.spacing(1.5)}
                  flexGrow={1}
                  display="flex"
                  flexDirection="column"
                  justifyContent="center"
                  alignItems="center"
                  sx={{ height: "calc(100% - 90px)" }}
                >
                  <SavedEvalsSkeleton />
                </Box>
              ) : !SavedEvals || SavedEvals.length === 0 ? (
                <Box
                  flexGrow={1}
                  alignSelf="stretch"
                  display="flex"
                  flexDirection="column"
                  justifyContent="center"
                  alignItems="center"
                  sx={{ width: "100%", height: "calc(100% - 50px)" }}
                >
                  <Typography fontSize={16} fontWeight="bold">
                    No evaluations added
                  </Typography>
                  <Typography
                    fontSize={12}
                    color={theme.palette.text.disabled}
                    mb={theme.spacing(2)}
                  >
                    Select and configure the evals to run in your dataset
                  </Typography>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    onClick={() => setVisibleSection("config")}
                    startIcon={
                      <SvgColor
                        src="/assets/icons/action_buttons/ic_add.svg"
                        sx={{
                          width: 16,
                          height: 16,
                          color: "inherit",
                        }}
                      />
                    }
                    sx={{ px: theme.spacing(3), fontWeight: 500 }}
                  >
                    Add Evaluations
                  </Button>
                </Box>
              ) : (
                <Box
                  flexGrow={1}
                  display="flex"
                  flexDirection="column"
                  sx={{ height: "calc(100% - 50px)" }}
                >
                  <SavedEvalsList
                    evals={SavedEvals}
                    allColumns={allColumns}
                    onClose={onClose}
                    disableDelete={
                      module === "experiment" &&
                      Array.isArray(SavedEvals) &&
                      SavedEvals.length <= 1
                    }
                    disableDeleteReason="An experiment must have at least one evaluation"
                    onDeleteEvalClick={async (evalItem) => {
                      try {
                        await axios.delete(
                          endpoints.develop.eval.deleteEval(id, evalItem.id),
                          {
                            data: {
                              delete_column: true,
                              ...(module === "experiment" && experimentId
                                ? { experiment_id: experimentId }
                                : {}),
                            },
                          },
                        );
                        enqueueSnackbar(`${evalItem.name} deleted`, {
                          variant: "success",
                        });
                        invalidateEvalCaches();
                        refreshGrid?.(null, true);
                      } catch (err) {
                        enqueueSnackbar(
                          err?.response?.data?.result || "Failed to delete",
                          { variant: "error" },
                        );
                      }
                    }}
                    onRunEvalClick={async (evalItem) => {
                      try {
                        const runEndpoint =
                          module === "workbench"
                            ? endpoints.develop.runPrompt.runEvalsOnMultipleVersions(
                                id,
                              )
                            : endpoints.develop.eval.runEvals(id);
                        const runPayload =
                          module === "workbench"
                            ? {
                                prompt_eval_config_ids: [evalItem.id],
                                version_to_run: workbenchVersions || [],
                              }
                            : {
                                user_eval_ids: [evalItem.id],
                                ...(module === "experiment" && experimentId
                                  ? { experiment_id: experimentId }
                                  : {}),
                              };
                        const res = await axios.post(runEndpoint, runPayload);
                        if (res?.data?.status === false) {
                          enqueueSnackbar(res.data.result || "Failed to run", {
                            variant: "error",
                          });
                          return;
                        }
                        enqueueSnackbar(`${evalItem.name} started`, {
                          variant: "success",
                        });
                        // Immediately refetch so status shows as Running
                        invalidateEvalCaches();
                        refreshGrid?.(null, true);
                      } catch (err) {
                        enqueueSnackbar(
                          err?.response?.data?.result ||
                            "Failed to run evaluation",
                          { variant: "error" },
                        );
                      }
                    }}
                    onStopEvalClick={async (evalItem) => {
                      try {
                        await axios.post(
                          endpoints.develop.eval.stopEval(id, evalItem.id),
                          module === "experiment" && experimentId
                            ? { experiment_id: experimentId }
                            : {},
                        );
                        enqueueSnackbar("User evaluation stopped", {
                          variant: "info",
                        });
                        invalidateEvalCaches();
                        refreshGrid?.(null, true);
                      } catch {
                        enqueueSnackbar("Failed to stop evaluation", {
                          variant: "error",
                        });
                      }
                    }}
                    onEditEvalClick={openEditForSavedEval}
                  />
                </Box>
              )}
              {/* Run All / Cancel buttons removed — actions are now inline
                  in the redesigned SavedEvalsList (per-row Run, bulk Run
                  Selected, and Run All in the sticky bottom bar). */}
            </>
          )}
        </Collapse>
        <Collapse
          in={visibleSection === "config"}
          orientation="horizontal"
          sx={{ height: "100%" }}
          unmountOnExit
        >
          <EvaluationsSelectionGrid
            onClose={() => {
              onClose();
              setVisibleSection("list");
            }}
            theme={theme}
            datasetId={id}
            isEvalsView={false}
          />
        </Collapse>
        <Collapse
          in={visibleSection === "custom"}
          orientation="horizontal"
          unmountOnExit
          timeout={"auto"}
        >
          <CustomEvalsForm onClose={onClose} />
        </Collapse>
        <Collapse
          in={visibleSection === "mapping"}
          orientation="horizontal"
          unmountOnExit
        >
          <EvaluationMappingForm
            onClose={onClose}
            id={id}
            allColumns={allColumns}
            refreshGrid={refreshGrid}
            onBack={() => {
              setVisibleSection("config");
              setIsDirty(false);
            }}
            fullWidth={module === "task"}
            existingEvalsProp={existingEvals}
            requiredColumnIds={requiredColumnIds}
          />
        </Collapse>
        <Collapse
          in={visibleSection === "create-group"}
          orientation="horizontal"
        >
          <CreateEvaluationGroupDrawer
            open={visibleSection === "create-group"}
            handleClose={() => {
              setVisibleSection("config");
            }}
            onBack={() => {
              resetEvalStore();
              setCurrentTab("evals");
              setVisibleSection("config");
            }}
            isEvalsView={false}
          />
        </Collapse>
      </Box>
      <DeleteEval
        open={Boolean(openDeleteEval)}
        setOpen={() => {
          setOpenDeleteEval(null);
        }}
        id={id}
        refreshGrid={refreshGrid}
        userEval={openDeleteEval}
      />
      <RunEvals
        open={confirmRunEvaluationsOpen}
        onClose={() => {
          setConfirmRunEvaluationsOpen(false);
          onClose();
        }}
        userEvals={selectedEvals}
        id={_id}
        refreshGrid={refreshGrid}
      />
      <EvalPickerDrawer
        open={evalPickerOpen}
        onClose={() => {
          setEvalPickerOpen(false);
          setEditingEval(null);
        }}
        initialEval={editingEval}
        source={module || "dataset"}
        sourceId={id || ""}
        sourceColumns={allColumns || []}
        // Experiment evals reference two values that don't exist as real
        // dataset cells — the prompt/agent output and the full prompt chain.
        // Surface them as virtual columns in the variable-mapping dropdown
        // so users can map eval inputs to them (mirrors the legacy
        // ManageExperimentEvalsDrawer.experimentVirtualColumns).
        extraColumns={
          module === "experiment"
            ? [
                { field: "output", headerName: "Output", dataType: "text" },
                {
                  field: "prompt_chain",
                  headerName: "Prompt Chain",
                  dataType: "text",
                },
              ]
            : []
        }
        existingEvals={existingEvals}
        requiredColumnId={requiredColumnIds || ""}
        onEvalAdded={async (evalConfig) => {
          const { handleRun } = actionButtonConfig;
          if (!handleRun) return;

          // Backend `UserEvalSerializer` (model_hub/serializers/eval_runner.py)
          // expects `template_id` (snake_case), not `eval_template`. It also
          // stores runtime overrides under config.run_config — everything the
          // EvalPicker lets the user customize (model / mode / summary / etc.)
          // should be forwarded so eval_runner can apply it at run time.
          const isComposite = evalConfig.templateType === "composite";

          const runConfig = {};
          if (!isComposite) {
            // Single-eval runtime overrides. Composite children each
            // carry their own model/mode/tools — none of this applies
            // at the composite binding level.
            if (evalConfig.model) runConfig.model = evalConfig.model;
            if (evalConfig.agent_mode)
              runConfig.agent_mode = evalConfig.agent_mode;
            if (evalConfig.check_internet !== undefined)
              runConfig.check_internet = !!evalConfig.check_internet;
            if (evalConfig.summary) runConfig.summary = evalConfig.summary;
            if (evalConfig.knowledge_base_id)
              runConfig.knowledge_base_id = evalConfig.knowledge_base_id;
            if (evalConfig.knowledge_bases)
              runConfig.knowledge_bases = evalConfig.knowledge_bases;
            if (evalConfig.tools) runConfig.tools = evalConfig.tools;
            if (evalConfig.pass_threshold !== undefined)
              runConfig.pass_threshold = evalConfig.pass_threshold;
            if (
              evalConfig.choice_scores &&
              Object.keys(evalConfig.choice_scores).length
            )
              runConfig.choice_scores = evalConfig.choice_scores;
          }
          // Data injection applies to both single and composite — the
          // backend resolves it at row-evaluation time.
          if (evalConfig.data_injection)
            runConfig.data_injection = evalConfig.data_injection;
          // Error localizer toggle was previously dropped between
          // EvalPickerConfigFull and the backend. It now flows through
          // for both single and composite bindings.
          if (evalConfig.error_localizer_enabled !== undefined)
            runConfig.error_localizer_enabled =
              !!evalConfig.error_localizer_enabled;

          // Code-eval static params (function_params_schema values).
          // `EvalPickerConfigFull.handleSave` hands them back on
          // evalConfig.params; forward to `config.params` so the backend
          // persists them on UserEvalMetric.config.params and each row's
          // evaluate() call receives them via **kwargs at run time.
          const evalParams =
            evalConfig.params && typeof evalConfig.params === "object"
              ? evalConfig.params
              : {};

          // Build the payload — workbench endpoint expects a different
          // shape than the dataset/task/experiment endpoints.
          let payload;
          if ((module || "dataset") === "workbench") {
            payload = {
              id: evalConfig.templateId,
              name: evalConfig.name,
              mapping: evalConfig.mapping || {},
              model: isComposite ? undefined : evalConfig.model,
              config: {
                params: evalParams,
                ...(Object.keys(runConfig).length
                  ? { run_config: runConfig }
                  : {}),
              },
              error_localizer: runConfig.error_localizer_enabled || false,
              is_run: true,
              version_to_run: workbenchVersions || [],
            };
          } else {
            payload = {
              name: evalConfig.name,
              template_id: evalConfig.templateId,
              model: isComposite ? undefined : evalConfig.model,
              // In the optimization context the optimizer runs evals itself —
              // skip the full-dataset run so adding an eval is near-instant.
              // Dataset adds are also save-only now: the user runs evals
              // manually from the dataset grid rather than auto-running on
              // add, which would otherwise queue work the user didn't ask for.
              run: module !== "run-optimization" && module !== "dataset",
              // Mirror the workbench path: surface error_localizer at the top
              // level so EditAndRunUserEvalView can update eval_metric.error_localizer.
              error_localizer: runConfig.error_localizer_enabled ?? false,
              config: {
                mapping: evalConfig.mapping || {},
                config: isComposite ? {} : evalConfig.config || {},
                ...(Object.keys(evalParams).length
                  ? { params: evalParams }
                  : {}),
                ...(Object.keys(runConfig).length
                  ? { run_config: runConfig }
                  : {}),
              },
              ...(isComposite && evalConfig.compositeWeightOverrides
                ? {
                    composite_weight_overrides:
                      evalConfig.compositeWeightOverrides,
                  }
                : {}),
              ...(isComposite && evalConfig.composite_weight_overrides
                ? {
                    composite_weight_overrides:
                      evalConfig.composite_weight_overrides,
                  }
                : {}),
            };
          }
          // Edit branch: POST directly to /edit_and_run_user_eval/{id} so the
          // existing UserEvalMetric is updated in place rather than duplicated
          // by /add_user_eval (dataset) or /experiments/<id>/add-eval/
          // (experiment). For experiment scope, the backend keys by
          // source_id=experiment_id, so we attach experiment_id to the body
          // — same pattern as the rest of the per-eval endpoints.
          // Workbench + task modules keep their own create-or-update routes.
          const effectiveModule = module || "dataset";
          if (
            evalConfig?.userEvalId &&
            (effectiveModule === "dataset" ||
              effectiveModule === "run-optimization" ||
              effectiveModule === "experiment")
          ) {
            try {
              // `id` in this drawer is always the datasetId for dataset /
              // experiment flows (experiment evals still live under a dataset).
              await axios.post(
                endpoints.develop.eval.editEval(id, evalConfig.userEvalId),
                {
                  ...payload,
                  ...(effectiveModule === "experiment" && experimentId
                    ? { experiment_id: experimentId }
                    : {}),
                },
              );
              queryClient.invalidateQueries({
                queryKey: getUserEvalListKey(module, id),
              });
              if (effectiveModule === "run-optimization") {
                queryClient.invalidateQueries({
                  queryKey: ["optimize-develop-column-info"],
                });
              } else if (effectiveModule === "experiment" && experimentId) {
                queryClient.invalidateQueries({
                  queryKey: ["experiment", experimentId],
                });
                queryClient.invalidateQueries({
                  queryKey: ["experiment-list"],
                });
              }
              refreshGrid?.(null, true);
              setEvalPickerOpen(false);
              setVisibleSection("list");
            } catch (err) {
              // Let EvalPickerDrawer's handleSaveEval catch keep the
              // drawer open on failure.
              throw err;
            }
            return;
          }
          // Non-dataset edit: forward user_eval_id in the payload so the
          // module-specific endpoint can route to its own update path if
          // it supports one.
          if (evalConfig?.userEvalId) {
            payload.user_eval_id = evalConfig.userEvalId;
          }
          // await so errors propagate to EvalPickerDrawer's handleSaveEval
          // catch block — keeps the drawer open on failure.
          await handleRun(payload, () => {
            setEvalPickerOpen(false);
            setVisibleSection("list");
          });
        }}
      />
    </>
  );
};

EvaluationDrawerChild.propTypes = {
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  handleLabelsAdd: PropTypes.func,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  listComponent: PropTypes.node,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
  evalsConfigs: PropTypes.array,
  setFormIsDirty: PropTypes.func,
  setConfirmationModalOpen: PropTypes.func,
  isConfirmationModalOpen: PropTypes.bool,
  openDrawer: PropTypes.bool,
  existingEvals: PropTypes.array,
  requiredColumnIds: PropTypes.string,
};

const ContextConsumer = ({
  context,
  setVisibleSectionRef,
  id,
  showTest,
  showAdd,
  testLabel,
  runLabel,
  handleTest,
  module,
  handleRun,
}) => {
  useEffect(() => {
    if (context) {
      const { setModule, setActionButtonConfig, setVisibleSection } = context;
      if (setVisibleSection) {
        setVisibleSectionRef.current = setVisibleSection;
        setModule(module);
      }
      setActionButtonConfig({
        id,
        showTest,
        showAdd,
        testLabel,
        runLabel,
        handleTest,
        handleRun,
      });
    }
  }, [module, id]);
  return null;
};

ContextConsumer.propTypes = {
  id: PropTypes.string,
  showTest: PropTypes.bool,
  showAdd: PropTypes.bool,
  testLabel: PropTypes.string,
  runLabel: PropTypes.string,
  handleTest: PropTypes.func,
  handleRun: PropTypes.func,
  context: PropTypes.object,
  module: PropTypes.string,
  setVisibleSectionRef: PropTypes.any,
};

const EvaluationDrawer = ({
  open,
  onClose,
  type = "persistent",
  allColumns,
  refreshGrid,
  id,
  experimentEval,
  evalsConfigs,
  setEvalsConfigs,
  hideSaveAndRun,
  module,
  listComponent,
  showTest = true,
  showAdd = true,
  testLabel = "Test",
  runLabel = "Add & Run",
  defaultVisibleSection = "list",
  handleSaveAndRun,
  onSuccess = (_data, _variables) => {},
  handleTest = (_data) => {},
  existingEvals = [],
  requiredColumnIds = "",
}) => {
  const { experimentId } = useParams();
  const setVisibleSectionRef = useRef(null);
  const [formIsDirty, setFormIsDirty] = useState(false);
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const queryClient = useQueryClient();
  const [_, setSearchParam] = useSearchParams();
  const endpoint = useMemo(() => {
    switch (module) {
      case "task":
        return endpoints.project.createEvalTaskConfig();
      case "workbench":
        return endpoints.develop.runPrompt.createOrUpdateEvalConfig(id);
      case "experiment":
        return endpoints.develop.experiment.addEval(experimentId);
      default:
        return endpoints.develop.eval.addEval(id);
    }
  }, [module, id, experimentId]);

  const { mutateAsync } = useMutation({
    mutationFn: (data) => axios.post(endpoint, data),
  });

  // Edit branch: when the picker is opened in edit mode it forwards
  // `user_eval_id` in the payload. We strip it off here and POST to
  // /edit_and_run_user_eval/{id} so the existing UserEvalMetric is
  // updated in place rather than duplicated by /add_user_eval.
  // Other modules (task, workbench, experiment) don't use this flow,
  // so the edit endpoint is dataset-scoped only.
  const { mutateAsync: mutateEditAsync } = useMutation({
    mutationFn: ({ userEvalId, ...data }) =>
      axios.post(endpoints.develop.eval.editEval(id, userEvalId), data),
  });

  const { mutate: handleApplyGroup } = useMutation({
    mutationFn: async (payload) => {
      return axios.post(endpoints.develop.eval.applyEvalGroup, payload);
    },
    onSuccess: (data, variables) => {
      onSuccess(variables, data?.data?.result);
      refreshEvalsList?.();
      resetEvalStore();
    },
  });

  const onSuccessFn = (data, variables, handleSuccess) => {
    handleSuccess?.(data);
    onSuccess?.(data, variables);
    refreshEvalsList?.();
    // When an eval is added in the optimization drawer, refresh the list of
    // evals shown for the selected column. We invalidate by prefix so the
    // dynamic column-id doesn't require a closure update.
    if (module === "run-optimization") {
      queryClient.invalidateQueries({
        queryKey: ["optimize-develop-column-info"],
      });
    }
    if (variables?.run) {
      refreshGrid?.(null, true);
    }
  };

  const handleRun = async (data, handleSuccess, { isGrouped = false } = {}) => {
    if (isGrouped) {
      if (
        module === "dataset" ||
        module === "run-experiment" ||
        module === "run-optimization"
      ) {
        data["filters"]["dataset_id"] = id;
      } else if (module === "experiment") {
        data["filters"]["experiment_id"] = experimentId;
      }
      handleApplyGroup(data, {
        onSuccess: () => {
          handleSuccess();
        },
      });
      return;
    }
    // Edit-existing branch: route to /edit_and_run_user_eval/{id}.
    // Only dataset module currently surfaces edit-from-saved-list +
    // edit-from-column-menu, so we restrict the edit call to that
    // module to avoid hitting the wrong endpoint elsewhere.
    if (data?.user_eval_id && (!module || module === "dataset")) {
      // Strip user_eval_id from the body — mutateEditAsync destructures
      // it into the URL segment and would otherwise send it twice.
      const { user_eval_id: userEvalId, ...body } = data;
      const resp = await mutateEditAsync({ userEvalId, ...body });
      onSuccessFn(resp, data, handleSuccess);
      return;
    }
    const result = await mutateAsync(data);
    onSuccessFn(result, data, handleSuccess);
  };

  const refreshEvalsList = () => {
    queryClient.invalidateQueries({
      queryKey: getUserEvalListKey(module, id),
    });
  };

  const handleClose = () => {
    if (formIsDirty) {
      setOpenConfirmDialog(true);
    } else {
      setVisibleSectionRef.current(defaultVisibleSection);
      onClose();
    }
    setSearchParam((prevSearchParams) => {
      prevSearchParams.delete("groupId");
      return prevSearchParams;
    });
    useRunEvaluationStore.getState().clearPendingEditEval();
    resetEvalStore();
  };

  useEffect(() => {
    return () => {
      resetEvalStore();
    };
  }, []);

  return (
    <EvaluationProvider>
      <EvaluationContext.Consumer>
        {(context) => (
          <ContextConsumer
            context={context}
            setVisibleSectionRef={setVisibleSectionRef}
            id={id}
            showTest={showTest}
            module={module}
            showAdd={showAdd}
            runLabel={runLabel}
            testLabel={testLabel}
            handleTest={handleTest}
            handleRun={handleSaveAndRun ? handleSaveAndRun : handleRun}
          />
        )}
      </EvaluationContext.Consumer>
      <Drawer
        anchor="right"
        open={open}
        variant={type}
        onClose={handleClose}
        PaperProps={{
          sx: (theme) => ({
            width: 650,
            maxWidth: "100vw",
            height: "100vh",
            position: "fixed",
            zIndex: 10,
            boxShadow: theme.customShadows.drawer,
            borderRadius: "0px !important",
            backgroundColor: "background.paper",
          }),
        }}
        ModalProps={{
          BackdropProps: {
            style: {
              backgroundColor: "transparent",
              borderRadius: "0px !important",
            },
          },
        }}
      >
        <EvaluationDrawerChild
          onClose={onClose}
          allColumns={allColumns}
          refreshGrid={refreshGrid}
          experimentEval={experimentEval}
          evalsConfigs={evalsConfigs}
          setEvalsConfigs={setEvalsConfigs}
          hideSaveAndRun={hideSaveAndRun}
          setFormIsDirty={setFormIsDirty}
          setConfirmationModalOpen={setOpenConfirmDialog}
          isConfirmationModalOpen={openConfirmDialog}
          openDrawer={open}
          listComponent={listComponent}
          existingEvals={existingEvals}
          requiredColumnIds={requiredColumnIds}
        />
        <ConfirmDialog
          open={openConfirmDialog}
          onClose={() => setOpenConfirmDialog(false)}
          title="Confirm Action"
          content="Any progress will be lost. Are you sure you want to leave?"
          action={
            <LoadingButton
              variant="contained"
              size="small"
              color="error"
              sx={{
                paddingX: 3,
              }}
              onClick={() => {
                setOpenConfirmDialog(false);
                onClose();
                setVisibleSectionRef.current("list");
              }}
            >
              Close
            </LoadingButton>
          }
        />
      </Drawer>
    </EvaluationProvider>
  );
};

EvaluationDrawer.propTypes = {
  open: PropTypes.bool,
  type: PropTypes.string,
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  onSuccess: PropTypes.func,
  module: PropTypes.string,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  id: PropTypes.string,
  showTest: PropTypes.bool,
  showAdd: PropTypes.bool,
  testLabel: PropTypes.string,
  runLabel: PropTypes.string,
  handleTest: PropTypes.func,
  defaultVisibleSection: PropTypes.string,
  listComponent: PropTypes.node,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
  evalsConfigs: PropTypes.array,
  handleSaveAndRun: PropTypes.func,
  existingEvals: PropTypes.array,
  requiredColumnIds: PropTypes.string,
};

export default EvaluationDrawer;
