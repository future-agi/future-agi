import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";

import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  EvalPickerDrawer,
  serializeEvalConfig,
} from "src/sections/common/EvalPicker";
import {
  chatEvalColumns,
  voiceEvalColumns,
} from "src/components/run-tests/common";

import { useTestEvaluationStore } from "./states";
import TestEvaluationPage from "./TestEvaluationPage";
import { AGENT_TYPES } from "../agents/constants";
import { SourceType } from "../scenarios/common";

const TestEvaluationDrawer = ({ executionIds, onSuccessOfAdditionOfEvals }) => {
  const { openTestEvaluation, setOpenTestEvaluation } =
    useTestEvaluationStore();
  const { testId } = useParams();
  const queryClient = useQueryClient();

  const runTestDetail = queryClient.getQueryData(["test-runs-detail", testId]);
  const runTestData = runTestDetail?.data;

  // Prefer snake_case (post camelCase-middleware removal) but fall back to
  // camelCase so this component is resilient to either response shape.
  const agentType =
    runTestData?.agent_definition_detail?.agent_type ??
    runTestData?.agentDefinitionDetail?.agentType;
  const sourceType = runTestData?.source_type ?? runTestData?.sourceType;

  const [pickerOpen, setPickerOpen] = useState(false);
  // Non-null when editing an existing eval config — drives `initialEval` on
  // the picker (jumps to config step) and switches the save bridge to the
  // update endpoint.
  const [editingEvalItem, setEditingEvalItem] = useState(null);

  // Build eval columns: base chat/voice columns + per-scenario column configs.
  const evalColumns = useMemo(() => {
    const base =
      agentType === AGENT_TYPES.CHAT || sourceType === SourceType.PROMPT
        ? chatEvalColumns
        : voiceEvalColumns;
    const scenariosDetail =
      runTestData?.scenarios_detail ?? runTestData?.scenariosDetail ?? [];
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
    return [...base, ...scenarioColumns];
  }, [agentType, sourceType, runTestData]);

  const existingEvals =
    runTestData?.simulate_eval_configs_detail ??
    runTestData?.simulateEvalConfigsDetail ??
    runTestData?.evals_detail ??
    runTestData?.evalsDetail ??
    [];

  const { mutateAsync: addEvalsAsync } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.runTests.addEvals(testId), payload),
  });

  const { mutateAsync: updateEvalAsync } = useMutation({
    mutationFn: ({ evalConfigId, payload }) =>
      axios.post(
        endpoints.runTests.updateSimulateEval(testId, evalConfigId),
        payload,
      ),
  });

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["test-runs-detail", testId],
    });
  }, [queryClient, testId]);


  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      if (!testId) return;
      const editing = editingEvalItem;
      const payload = serializeEvalConfig(evalConfig);
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
        throw error;
      }
    },
    [addEvalsAsync, updateEvalAsync, handleRefresh, testId, editingEvalItem],
  );

  const handleEditEvaluation = useCallback((evalItem) => {
    if (!evalItem) return;
    setEditingEvalItem(evalItem);
    setPickerOpen(true);
  }, []);

  const onCloseHandler = useCallback(() => {
    setOpenTestEvaluation(false);
  }, [setOpenTestEvaluation]);

  return (
    <>
      <Drawer
        anchor="right"
        open={openTestEvaluation}
        variant="temporary"
        onClose={onCloseHandler}
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
        <TestEvaluationPage
          onClose={onCloseHandler}
          executionIds={executionIds}
          onSuccessOfAdditionOfEvals={onSuccessOfAdditionOfEvals}
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
        sourceId={testId || ""}
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

TestEvaluationDrawer.propTypes = {
  executionIds: PropTypes.array,
  onSuccessOfAdditionOfEvals: PropTypes.func,
};

export default TestEvaluationDrawer;
