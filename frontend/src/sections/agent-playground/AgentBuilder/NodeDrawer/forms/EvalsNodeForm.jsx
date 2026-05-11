import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Button, MenuItem, Stack, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";
import { resetEvalStore } from "src/sections/evals/store/useEvalStore";
import EvaluationSelectionDialog from "src/components/run-tests/EvaluationSelectionDialog";
import axios, { endpoints } from "src/utils/axios";
import {
  formatGroupMembers,
  getVersionedEvalName,
} from "../../../../../components/run-tests/common";
import { useAgentPlaygroundStoreShallow } from "../../../store";
import { useSaveDraftContext } from "../../saveDraftContext";
import { ShowComponent } from "src/components/show";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";
import EvalListItem from "src/sections/agent-playground/components/EvalListItem";
import usePartialNodeUpdate from "../../hooks/usePartialNodeUpdate";
import { getEvaluatorId } from "../../../utils/evaluationUtils";

const EVAL_INPUT_COLUMNS = [
  { field: "input", headerName: "Input", dataType: "text" },
  { field: "reference", headerName: "Reference", dataType: "text" },
  { field: "context", headerName: "Context", dataType: "text" },
];

export default function EvalsNodeForm({ nodeId }) {
  const { control, getValues, handleSubmit } = useFormContext();
  const [openEvaluationDialog, setOpenEvaluationDialog] = useState(false);
  const [selectedEvalItem, setSelectedEvalItem] = useState(null);

  const { setSelectedGroup, setCurrentTab } = useEvaluationContext();

  const { getNodeById, updateNodeData } = useAgentPlaygroundStoreShallow(
    (state) => ({
      getNodeById: state.getNodeById,
      updateNodeData: state.updateNodeData,
    }),
  );
  const { ensureDraft } = useSaveDraftContext();
  const { partialUpdate, isPending } = usePartialNodeUpdate();

  // Get current evaluators from node data
  const getCurrentEvaluators = useCallback(() => {
    const node = getNodeById(nodeId);
    return node?.data?.evaluators || [];
  }, [getNodeById, nodeId]);

  // Update evaluators in node data
  const setEvaluators = useCallback(
    (updater) => {
      const currentEvaluators = getCurrentEvaluators();
      const newEvaluators =
        typeof updater === "function" ? updater(currentEvaluators) : updater;
      updateNodeData(nodeId, { evaluators: newEvaluators });
    },
    [getCurrentEvaluators, updateNodeData, nodeId],
  );

  const persistEvalNode = useCallback(
    async (evaluators, formValues = getValues()) => {
      const node = getNodeById(nodeId);
      const nodeUpdate = {
        label: formValues.name,
        evaluators,
        config: {
          ...(node?.data?.config || {}),
          evaluators,
          threshold: formValues.threshold ?? 0.5,
          failAction: formValues.failAction || "continue",
          payload: { ports: node?.data?.ports || [] },
        },
      };
      const prevData = node?.data;
      updateNodeData(nodeId, nodeUpdate);
      const draftResult = await ensureDraft({ skipDirtyCheck: true });
      if (draftResult === false) {
        if (prevData) updateNodeData(nodeId, prevData);
        return;
      }
      if (draftResult !== "created") {
        try {
          await partialUpdate(nodeId, nodeUpdate);
        } catch {
          if (prevData) updateNodeData(nodeId, prevData);
        }
      }
    },
    [
      ensureDraft,
      getNodeById,
      getValues,
      nodeId,
      partialUpdate,
      updateNodeData,
    ],
  );

  const handleAddEvaluation = async (newEvaluation) => {
    const currentEvaluators = getCurrentEvaluators();

    let nextEvaluators = currentEvaluators;
    if (newEvaluation?.isGroupEvals) {
      const data = await axios.get(
        `${endpoints.develop.eval.groupEvals}${newEvaluation?.templateId}/`,
      );
      const evalsToAdd = data?.data?.result?.members;
      const formattedEvals =
        formatGroupMembers(newEvaluation, evalsToAdd, currentEvaluators) ?? [];

      const removedSet = new Set(newEvaluation?.removedEvals ?? []);
      const cleanedNew = formattedEvals
        .filter((item) => !removedSet.has(item?.templateId))
        ?.map((item) => ({
          ...item,
          evalGroup: newEvaluation?.templateId,
        }));

      nextEvaluators = [...currentEvaluators, ...cleanedNew];
      setEvaluators(nextEvaluators);
    } else {
      nextEvaluators = (() => {
        let updated = [...currentEvaluators];

        const previousId =
          newEvaluation?.previousId || newEvaluation?.previous_id;
        if (previousId) {
          updated = updated.filter(
            (item) => getEvaluatorId(item) !== previousId,
          );
        }
        if (newEvaluation?.removableId) {
          updated = updated.filter(
            (item) => getEvaluatorId(item) !== newEvaluation.removableId,
          );
        }

        const versionedName = getVersionedEvalName(
          newEvaluation.name,
          updated,
          newEvaluation.templateId,
        );

        const finalEvaluation = { ...newEvaluation, name: versionedName };
        delete finalEvaluation?.previousId;
        return [...updated, finalEvaluation];
      })();
      setEvaluators(nextEvaluators);
    }
    await persistEvalNode(nextEvaluators);
    setOpenEvaluationDialog(false);
    resetEvalStore();
    setSelectedEvalItem(null);
  };

  const onSubmit = handleSubmit((formData) =>
    persistEvalNode(getCurrentEvaluators(), formData),
  );

  // Handle edit evaluation
  const handleEditEvalItem = (evalConfig) => {
    setSelectedEvalItem(evalConfig);
    setSelectedGroup(null);
    setCurrentTab("evals");
    setOpenEvaluationDialog(true);
  };

  // Handle delete evaluation
  const handleRemoveEvaluation = async (evalId) => {
    const nextEvaluators = getCurrentEvaluators().filter((item) => {
      const targetId = getEvaluatorId(item);
      return targetId !== evalId;
    });
    setEvaluators(nextEvaluators);
    await persistEvalNode(nextEvaluators);
  };

  return (
    <>
      <Stack direction="column" gap={2}>
        <FormTextFieldV2
          fullWidth
          size="small"
          control={control}
          fieldName="name"
          label="Node Name"
          required
        />
        <Stack direction="row" gap={1.5}>
          <FormTextFieldV2
            fullWidth
            size="small"
            control={control}
            fieldName="threshold"
            label="Threshold"
            fieldType="number"
            inputProps={{ min: 0, max: 1, step: 0.01 }}
          />
          <FormTextFieldV2
            select
            fullWidth
            size="small"
            control={control}
            fieldName="failAction"
            label="Fail Action"
          >
            <MenuItem value="continue">Continue</MenuItem>
            <MenuItem value="stop">Stop</MenuItem>
            <MenuItem value="route_fallback">Route fallback</MenuItem>
          </FormTextFieldV2>
        </Stack>
        <ShowComponent condition={getCurrentEvaluators().length > 0}>
          <Stack direction="column" gap={1.5}>
            <Typography
              typography="s1_2"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              Evals ({getCurrentEvaluators()?.length})
            </Typography>
            {getCurrentEvaluators().map((evalItem) => (
              <EvalListItem
                key={getEvaluatorId(evalItem) || evalItem.name}
                evalItem={evalItem}
                onEdit={handleEditEvalItem}
                onRemove={handleRemoveEvaluation}
              />
            ))}
          </Stack>
        </ShowComponent>
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-start",
          }}
        >
          <Button
            variant="outlined"
            size="small"
            onClick={() => setOpenEvaluationDialog(true)}
            sx={{
              fontWeight: "fontWeightMedium",
            }}
            startIcon={
              <SvgColor
                src="/assets/icons/components/ic_add.svg"
                sx={{
                  height: "20px",
                  width: "20px",
                }}
              />
            }
          >
            Add Eval
          </Button>
        </Box>
        <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
          <LoadingButton
            type="button"
            size="small"
            variant="outlined"
            loading={isPending}
            onClick={onSubmit}
          >
            Save
          </LoadingButton>
        </Box>
      </Stack>

      {/* Evaluation Selection Dialog */}
      <EvaluationSelectionDialog
        open={openEvaluationDialog}
        onClose={() => {
          setOpenEvaluationDialog(false);
          resetEvalStore();
          setSelectedEvalItem(null);
        }}
        scenarioColumnConfig={EVAL_INPUT_COLUMNS}
        onAddEvaluation={handleAddEvaluation}
        selectedEvalItem={selectedEvalItem}
        datasetId={null} // Since we're not tied to a specific dataset
      />
    </>
  );
}

EvalsNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
