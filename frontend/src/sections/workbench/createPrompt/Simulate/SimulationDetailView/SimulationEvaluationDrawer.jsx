import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";

import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { chatEvalColumns } from "src/components/run-tests/common";

import SimulationEvaluationPage from "./SimulationEvaluationPage";
import { useSimulationDetailContext } from "./context/SimulationDetailContext";

const SimulationEvaluationDrawer = ({ open, onClose, onSuccess }) => {
  const { id: promptTemplateId } = useParams();
  const queryClient = useQueryClient();
  const { simulation, refetchSimulation } = useSimulationDetailContext();

  const simulationId = simulation?.id;
  const [pickerOpen, setPickerOpen] = useState(false);
  // Non-null when editing an existing eval config — drives `initialEval` on
  // the picker (jumps to config step) and switches the save bridge to the
  // update endpoint.
  const [editingEvalItem, setEditingEvalItem] = useState(null);

  // Prefer snake_case (post-middleware removal) but fall back to camelCase so
  // this component is resilient to either response shape.
  const scenariosDetail =
    simulation?.scenarios_detail ?? simulation?.scenariosDetail ?? [];

  // Build eval columns from scenario column configs. Same shape as before:
  // [{ id, name, type }] merged with the chatEvalColumns base set.
  const evalColumns = useMemo(() => {
    const scenarioColumns = scenariosDetail.reduce((acc, detail) => {
      const columnConfig =
        detail?.dataset_column_config ?? detail?.datasetColumnConfig ?? {};
      Object.entries(columnConfig).forEach(([key, value]) => {
        if (!acc.find((col) => col.id === key)) {
          acc.push({
            id: key,
            name: value?.name || key,
            type: value?.type || "string",
          });
        }
      });
      return acc;
    }, []);
    return [...chatEvalColumns, ...scenarioColumns];
  }, [scenariosDetail]);

  const existingEvals =
    simulation?.simulate_eval_configs_detail ??
    simulation?.simulateEvalConfigsDetail ??
    simulation?.evals_detail ??
    simulation?.evalsDetail ??
    [];

  const { mutateAsync: addEvalsAsync } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.runTests.addEvals(simulationId), payload),
  });

  const { mutateAsync: updateEvalAsync } = useMutation({
    mutationFn: ({ evalConfigId, payload }) =>
      axios.post(
        endpoints.runTests.updateSimulateEval(simulationId, evalConfigId),
        payload,
      ),
  });

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["simulation-detail", promptTemplateId, simulationId],
    });
    refetchSimulation?.();
  }, [queryClient, promptTemplateId, simulationId, refetchSimulation]);

  // EvalPicker emits per-binding overrides (data_injection, agent_mode,
  // check_internet, tools, knowledge_bases, summary, etc.) at the top level
  // of `evalConfig`. The runtime engine reads them from `config.run_config`,
  // so re-nest before save. Mirrors the shared EvaluationDrawer.
  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      if (!simulationId) return;
      const editing = editingEvalItem;
      const isComposite = evalConfig.templateType === "composite";

      const runConfig = {};
      if (!isComposite) {
        if (evalConfig.model) runConfig.model = evalConfig.model;
        if (evalConfig.agent_mode) runConfig.agent_mode = evalConfig.agent_mode;
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
      if (evalConfig.data_injection)
        runConfig.data_injection = evalConfig.data_injection;
      if (evalConfig.error_localizer_enabled !== undefined)
        runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;

      const payload = {
        template_id: evalConfig.templateId,
        name: evalConfig.name,
        model: evalConfig.model,
        mapping: evalConfig.mapping || {},
        config: {
          ...(evalConfig.config || {}),
          ...(Object.keys(runConfig).length ? { run_config: runConfig } : {}),
        },
        // Top-level field — the simulate update view writes
        // SimulateEvalConfig.error_localizer separately from run_config.
        error_localizer: !!runConfig.error_localizer_enabled,
        filters: {},
      };
      try {
        if (editing?.id) {
          await updateEvalAsync({
            evalConfigId: editing.id,
            payload,
          });
          enqueueSnackbar("Eval updated successfully", { variant: "success" });
        } else {
          await addEvalsAsync({ evaluations_config: [payload] });
          enqueueSnackbar("Eval added successfully", { variant: "success" });
        }
        handleRefresh();
        setEditingEvalItem(null);
      } catch (error) {
        enqueueSnackbar(error?.response?.data?.error || "Failed to save eval", {
          variant: "error",
        });
        // Rethrow so EvalPickerConfigFull keeps the user on the config step.
        throw error;
      }
    },
    [
      addEvalsAsync,
      updateEvalAsync,
      handleRefresh,
      simulationId,
      editingEvalItem,
    ],
  );

  const handleEditEvaluation = useCallback((evalItem) => {
    if (!evalItem) return;
    setEditingEvalItem(evalItem);
    setPickerOpen(true);
  }, []);

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        variant="temporary"
        onClose={onClose}
        PaperProps={{
          sx: (theme) => ({
            width: 720,
            maxWidth: "95vw",
            height: "100vh",
            position: "fixed",
            zIndex: 10,
            boxShadow: theme.customShadows?.drawer || theme.shadows[16],
            borderRadius: "0px !important",
            backgroundColor: "background.paper",
          }),
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <SimulationEvaluationPage
          onClose={onClose}
          onSuccess={onSuccess}
          onAddEvaluation={() => {
            setEditingEvalItem(null);
            setPickerOpen(true);
          }}
          onEditEvaluation={handleEditEvaluation}
        />
      </Drawer>

      <EvalPickerDrawer
        open={pickerOpen}
        onClose={() => {
          setPickerOpen(false);
          setEditingEvalItem(null);
        }}
        source="simulation"
        sourceId={simulationId || ""}
        sourceColumns={evalColumns}
        existingEvals={editingEvalItem ? [] : existingEvals}
        onEvalAdded={handleEvalAdded}
        initialEval={
          editingEvalItem
            ? {
                id: editingEvalItem.template_id || editingEvalItem.templateId,
                template_id:
                  editingEvalItem.template_id || editingEvalItem.templateId,
                name: editingEvalItem.name,
                mapping: editingEvalItem.mapping || {},
                config: editingEvalItem.config || {},
                run_config: editingEvalItem.config?.run_config || {},
              }
            : null
        }
      />
    </>
  );
};

SimulationEvaluationDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSuccess: PropTypes.func.isRequired,
};

export default SimulationEvaluationDrawer;
