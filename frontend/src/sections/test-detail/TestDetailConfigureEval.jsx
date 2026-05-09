import React, { useCallback, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";

import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import {
  chatEvalColumns,
  voiceEvalColumns,
} from "src/components/run-tests/common";

import { useTestDetailStoreShallow } from "./states";
import { useTestDetail } from "./context/TestDetailContext";
import { AGENT_TYPES } from "../agents/constants";
import { SourceType } from "../scenarios/common";

const TestDetailConfigureEval = () => {
  const { testId } = useParams();
  const queryClient = useQueryClient();

  const { configureEval, setConfigureEval } = useTestDetailStoreShallow(
    (s) => ({
      configureEval: s.configureEval,
      setConfigureEval: s.setConfigureEval,
    }),
  );

  const { data: testData } = useQuery({
    queryKey: ["test-runs-detail", testId],
    queryFn: () => axios.get(endpoints.runTests.detail(testId)),
    select: (data) => data.data,
    enabled: !!testId,
  });

  const { refreshGrid } = useTestDetail();

  const agentType =
    testData?.agent_definition_detail?.agent_type ?? AGENT_TYPES.CHAT;
  const sourceType = testData?.source_type;

  const existingEvals = useMemo(
    () =>
      testData?.simulate_eval_configs_detail ?? testData?.evals_detail ?? [],
    [testData],
  );

  const editingEvalItem = useMemo(() => {
    if (!configureEval?.id) return null;
    return existingEvals.find((e) => e.id === configureEval.id) || null;
  }, [configureEval, existingEvals]);

  const evalColumns = useMemo(() => {
    const base =
      agentType === AGENT_TYPES.CHAT || sourceType === SourceType.PROMPT
        ? chatEvalColumns
        : voiceEvalColumns;
    const scenariosDetail = testData?.scenarios_detail ?? [];
    const scenarioColumns = scenariosDetail.reduce((acc, detail) => {
      const columnConfig = detail?.dataset_column_config ?? {};
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
  }, [agentType, sourceType, testData]);

  const { mutateAsync: updateEvalAsync } = useMutation({
    mutationFn: ({ evalConfigId, payload }) =>
      axios.post(
        endpoints.runTests.updateSimulateEval(testId, evalConfigId),
        payload,
      ),
  });

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["test-runs-detail", testId] });
    refreshGrid?.();
  }, [queryClient, testId, refreshGrid]);

  // Bridge — the new picker returns a camelCase config; the backend expects
  // snake_case. Config Eval is always an edit flow (the user is reconfiguring
  // an existing SimulateEvalConfig bound to a grid column), so we always
  // route through the update endpoint.
  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      if (!testId || !editingEvalItem?.id) return;
      const payload = {
        template_id: evalConfig.templateId,
        name: evalConfig.name,
        model: evalConfig.model,
        mapping: evalConfig.mapping || {},
        config: {
          ...(evalConfig.config || {}),
          ...(evalConfig.data_injection
            ? { run_config: { data_injection: evalConfig.data_injection } }
            : {}),
        },
        error_localizer: evalConfig.error_localizer_enabled || false,
        filters: {},
      };
      try {
        await updateEvalAsync({
          evalConfigId: editingEvalItem.id,
          payload,
        });
        enqueueSnackbar("Eval updated successfully", { variant: "success" });
        handleRefresh();
      } catch (error) {
        enqueueSnackbar(error?.response?.data?.error || "Failed to save eval", {
          variant: "error",
        });
        throw error;
      }
    },
    [testId, editingEvalItem, updateEvalAsync, handleRefresh],
  );

  const onClose = useCallback(() => {
    setConfigureEval(null);
  }, [setConfigureEval]);

  const initialEval = useMemo(() => {
    if (!editingEvalItem) return null;
    const templateId = editingEvalItem.template_id;
    return {
      id: templateId,
      template_id: templateId,
      name: editingEvalItem.name,
      mapping: editingEvalItem.mapping || {},
      config: editingEvalItem.config || {},
      run_config: editingEvalItem.config?.run_config || {},
    };
  }, [editingEvalItem]);

  return (
    <EvalPickerDrawer
      open={Boolean(configureEval?.id) && !!editingEvalItem}
      onClose={onClose}
      source="simulation"
      sourceId={testId || ""}
      sourceColumns={evalColumns}
      existingEvals={[]}
      onEvalAdded={handleEvalAdded}
      initialEval={initialEval}
    />
  );
};

TestDetailConfigureEval.propTypes = {};

export default TestDetailConfigureEval;
