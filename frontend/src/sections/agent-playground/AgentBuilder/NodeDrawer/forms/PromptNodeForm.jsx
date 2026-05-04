import React, { startTransition } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Divider } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useFormContext, useWatch } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import CreateResponseSchema from "src/components/custom-model-options/CreateResponseSchema";
import PromptNameRow from "src/sections/agent-playground/components/PromptNameRow";
import ModelSelectionRow from "src/sections/agent-playground/components/ModelSelectionRow";
import OutputToolsRow from "src/sections/agent-playground/components/OutputToolsRow";
import ModelParametersPopover from "src/sections/agent-playground/components/ModelParametersPopover";
import PromptMessageRow from "../../../components/PromptMessageRow";
import VariableAccessInfo from "../../../components/VariableAccessInfo";
import { enqueueSnackbar } from "notistack";
import { usePromptNodeForm } from "./usePromptNodeForm";
import { mapPatchResponseToStoreData } from "./promptNodeFormUtils";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
} from "../../../store";
import usePartialNodeUpdate from "../../hooks/usePartialNodeUpdate";
import { useSaveDraftContext } from "../../saveDraftContext";
import useConnectedNodeVariables from "../../../hooks/useConnectedNodeVariables";
import { useGetPromptVersionsInfinite } from "src/api/agent-playground/agent-playground";
import NodeDrawerSkeleton from "../NodeDrawerSkeleton";
import { PORT_DIRECTION } from "../../../utils/constants";

export default function PromptNodeForm({ nodeId }) {
  const queryClient = useQueryClient();
  const { handleSubmit, watch, setValue } = useFormContext();
  const templateFormat = watch("templateFormat") || "mustache";
  const {
    control,
    modelConfig,
    isModelSelected,
    isUnsupportedOutputFormat,
    responseFormatMenuItems,
    modelParameters,
    updateSliderParameter,
    updateBooleanParameter,
    updateDropdownParameter,
    updateReasoningSliderParameter,
    updateReasoningDropdownParameter,
    updateShowReasoningProcess,
    showCreateSchema,
    setShowCreateSchema,
    isParamsPopoverOpen,
    paramsAnchorEl,
    handleParamsClick,
    handleParamsClose,
    handleModelChange,
    handleToolsApply,
    buildPayload,
    responseFormatField,
    isLoadingQueries,
  } = usePromptNodeForm();

  const { updateNodeData, clearSelectedNode, clearValidationErrorNode } =
    useAgentPlaygroundStoreShallow((state) => ({
      updateNodeData: state.updateNodeData,
      clearSelectedNode: state.clearSelectedNode,
      clearValidationErrorNode: state.clearValidationErrorNode,
    }));
  const { partialUpdate, isPending } = usePartialNodeUpdate();
  const { ensureDraft } = useSaveDraftContext();

  const {
    dropdownOptions,
    validateVariable,
    isLoading: isLoadingVariables,
  } = useConnectedNodeVariables(nodeId);

  // Check if prompt versions are still loading (React Query deduplicates with PromptNameRow)
  const promptTemplateId = useWatch({ name: "prompt_template_id" });
  const { isLoading: isLoadingVersions } =
    useGetPromptVersionsInfinite(promptTemplateId);

  const isFormLoading =
    isLoadingQueries || isLoadingVariables || isLoadingVersions;

  if (isFormLoading) return <NodeDrawerSkeleton />;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        height: "100%",
      }}
    >
      {/* Unsupported output format error */}
      {isUnsupportedOutputFormat && (
        <Alert severity="error" sx={{ mb: 1 }}>
          This prompt uses an unsupported output format. Only text-based prompts
          are supported in the agent builder.
        </Alert>
      )}

      {/* Prompt Name Row */}
      <PromptNameRow control={control} />

      {/* Model Selection Row */}
      <ModelSelectionRow
        modelConfig={modelConfig}
        isModelSelected={isModelSelected}
        onModelChange={handleModelChange}
        onParamsClick={handleParamsClick}
        disabled={isUnsupportedOutputFormat}
      />

      {/* Output Type, Tools, and Template Format Row */}
      <OutputToolsRow
        control={control}
        isModelSelected={isModelSelected}
        responseFormatMenuItems={responseFormatMenuItems}
        onCreateSchema={() => setShowCreateSchema(true)}
        modelConfig={modelConfig}
        onToolsApply={handleToolsApply}
        disabled={isUnsupportedOutputFormat}
        templateFormat={templateFormat}
        onTemplateFormatChange={(v) => setValue("templateFormat", v, { shouldDirty: true })}
      />

      {/* Model Parameters Popover */}
      <ModelParametersPopover
        open={isParamsPopoverOpen}
        anchorEl={paramsAnchorEl}
        onClose={handleParamsClose}
        modelParameters={modelParameters}
        onSliderChange={updateSliderParameter}
        onBooleanChange={updateBooleanParameter}
        onDropdownChange={updateDropdownParameter}
        onReasoningSliderChange={updateReasoningSliderParameter}
        onReasoningDropdownChange={updateReasoningDropdownParameter}
        onShowReasoningProcessChange={updateShowReasoningProcess}
      />

      <Divider />

      {/* Variable access info */}
      <VariableAccessInfo />

      {/* Messages Section with drag and drop sorting */}
      <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        <PromptMessageRow
          control={control}
          name="messages"
          disabled={false}
          showAddButton={true}
          dropdownOptions={dropdownOptions}
          mentionEnabled
          variableValidator={validateVariable}
          jinjaMode={templateFormat === "jinja"}
        />
      </Box>

      {/* Save Button - always at bottom */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          pt: 1.5,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        <LoadingButton
          type="submit"
          variant="outlined"
          size="small"
          loading={isPending}
          disabled={isUnsupportedOutputFormat}
          onClick={handleSubmit(async (data) => {
            // Check name uniqueness against other nodes
            const otherNodes = useAgentPlaygroundStore
              .getState()
              .nodes.filter((n) => n.id !== nodeId);
            const nameExists = otherNodes.some(
              (n) => n.data?.label === data.name,
            );
            if (nameExists) {
              enqueueSnackbar("A node with this name already exists", {
                variant: "error",
              });
              return;
            }

            // Capture old label before updating for variable propagation
            const currentNode = useAgentPlaygroundStore
              .getState()
              .nodes.find((n) => n.id === nodeId);
            const oldLabel = currentNode?.data?.label || nodeId;
            const outputLabel = currentNode?.data?.ports?.find(
              (p) => p.direction === PORT_DIRECTION.OUTPUT,
            )?.display_name;

            const payload = buildPayload(data);
            const nodeUpdate = {
              label: data.name || nodeId,
              config: {
                version: data.version,
                prompt_version_id: data.prompt_version_id,
                prompt_template_id: data.prompt_template_id,
                templateFormat: data.templateFormat || "mustache",
                modelConfig: data.modelConfig,
                messages: data.messages,
                payload,
              },
            };

            clearValidationErrorNode(nodeId);
            const prevData = currentNode?.data;

            // Always apply optimistic update first
            updateNodeData(nodeId, nodeUpdate);

            const draftResult = await ensureDraft({ skipDirtyCheck: true });

            if (draftResult === false) {
              if (prevData) updateNodeData(nodeId, prevData);
              return;
            }

            if (draftResult !== "created") {
              // Already a draft — fire individual PATCH
              let apiResponse;
              try {
                apiResponse = await partialUpdate(nodeId, nodeUpdate);
              } catch {
                if (prevData) updateNodeData(nodeId, prevData);
                enqueueSnackbar("Failed to save prompt", { variant: "error" });
                return;
              }

              const storeData = apiResponse
                ? mapPatchResponseToStoreData(apiResponse)
                : nodeUpdate;
              updateNodeData(nodeId, storeData);
            }

            // BOTH paths: propagate label rename
            const newLabel = data.name || nodeId;
            if (oldLabel !== newLabel) {
              startTransition(() => {
                useAgentPlaygroundStore
                  .getState()
                  .propagateVariableRename(
                    nodeId,
                    oldLabel,
                    newLabel,
                    outputLabel,
                    outputLabel,
                  );
              });
            }

            queryClient.invalidateQueries({
              queryKey: ["agent-playground", "node-detail"],
            });
            clearSelectedNode();
          })}
          sx={{
            fontWeight: "fontWeightMedium",
          }}
        >
          Save prompt
        </LoadingButton>
      </Box>

      {/* Create Response Schema Modal */}
      <CreateResponseSchema
        open={showCreateSchema}
        onClose={() => {
          setShowCreateSchema(false);
        }}
        setValue={(value) => {
          responseFormatField.onChange(value);
        }}
      />
    </Box>
  );
}

PromptNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
