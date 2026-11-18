import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
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

export default function EvalsNodeForm({ nodeId }) {
  const { control } = useFormContext();
  const [openEvaluationDialog, setOpenEvaluationDialog] = useState(false);
  const [selectedEvalItem, setSelectedEvalItem] = useState(null);

  const { setSelectedGroup, setCurrentTab } = useEvaluationContext();

  const { getNodeById, updateNodeData } = useAgentPlaygroundStoreShallow(
    (state) => ({
      getNodeById: state.getNodeById,
      updateNodeData: state.updateNodeData,
    }),
  );
  const { saveDraft } = useSaveDraftContext();

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

  const handleAddEvaluation = async (newEvaluation) => {
    const currentEvaluators = getCurrentEvaluators();

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

      setEvaluators((prev) => [...prev, ...cleanedNew]);
    } else {
      setEvaluators((prev) => {
        let updated = [...prev];

        if (newEvaluation?.previousId) {
          updated = updated.filter(
            (item) => item.eval_id !== newEvaluation.previous_id,
          );
        }
        if (newEvaluation?.removableId) {
          updated = updated.filter(
            (item) => item.id !== newEvaluation.removableId,
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
      });
    }
    saveDraft();
    setOpenEvaluationDialog(false);
    resetEvalStore();
    setSelectedEvalItem(null);
  };

  // Handle edit evaluation
  const handleEditEvalItem = (evalConfig) => {
    setSelectedEvalItem(evalConfig);
    setSelectedGroup(null);
    setCurrentTab("evals");
    setOpenEvaluationDialog(true);
  };

  // Handle delete evaluation
  const handleRemoveEvaluation = (evalId) => {
    setEvaluators((prev) =>
      prev.filter((item) => {
        const targetId = item.eval_id;
        return targetId !== evalId;
      }),
    );
    saveDraft();
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
                key={evalItem.eval_id || evalItem.id}
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
            startIcon={
              <SvgColor
                src="/assets/icons/components/ic_add.svg"
                width="20px"
              />
            }
          >
            Add Eval
          </Button>
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
        scenarioColumnConfig={[]}
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
