import React, { useEffect } from "react";
import ModalWrapper from "../../../components/ModalWrapper/ModalWrapper";
import { useAgentPlaygroundStoreShallow } from "../store";
import { z } from "zod";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { Stack } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useSaveDraftVersion } from "src/api/agent-playground/agent-playground";
import { enqueueSnackbar } from "notistack";
import { buildVersionPayload } from "../utils/versionPayloadUtils";
import { VERSION_STATUS } from "../utils/constants";
import { validateGraphForSave } from "../utils/workflowValidation";
import useWorkflowExecution from "../hooks/useWorkflowExecution";

const formSchema = z.object({
  versionName: z.string().min(1, "Version name is required"),
  commitMessage: z.string().optional().default(""),
});

export default function SaveAgentDialog() {
  const {
    openSaveAgentDialog,
    setOpenSaveAgentDialog,
    currentAgent,
    nodes,
    edges,
    updateVersion,
    loadVersion,
    setValidationErrorNodeIds,
    clearValidationErrors,
    pendingRunAfterSave,
    setPendingRunAfterSave,
  } = useAgentPlaygroundStoreShallow((state) => ({
    openSaveAgentDialog: state.openSaveAgentDialog,
    setOpenSaveAgentDialog: state.setOpenSaveAgentDialog,
    currentAgent: state.currentAgent,
    nodes: state.nodes,
    edges: state.edges,
    updateVersion: state.updateVersion,
    loadVersion: state.loadVersion,
    setValidationErrorNodeIds: state.setValidationErrorNodeIds,
    clearValidationErrors: state.clearValidationErrors,
    pendingRunAfterSave: state.pendingRunAfterSave,
    setPendingRunAfterSave: state.setPendingRunAfterSave,
  }));
  const { runWorkflow } = useWorkflowExecution();
  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {
      versionName: currentAgent?.version_name || "",
      commitMessage: "",
    },
  });

  const {
    control,
    handleSubmit,
    formState: { isValid },
    reset,
    getValues,
  } = form;

  useEffect(() => {
    reset({
      versionName: currentAgent?.version_name || "",
      commitMessage: "",
    });
  }, [currentAgent, reset]);

  const queryClient = useQueryClient();
  const dialogCopy = pendingRunAfterSave
    ? {
        title: "Save and run",
        subTitle:
          "This saves the starter prompt as the first version, then runs one scenario.",
        action: "Save and run",
        versionLabel: "Version name",
        notesLabel: "Notes (optional)",
      }
    : {
        title: "Save agent",
        subTitle: "Save the current agent version.",
        action: "Save",
        versionLabel: "Version name",
        notesLabel: "Notes (optional)",
      };

  const { mutate: saveAgent, isPending: isSavingAgent } = useSaveDraftVersion({
    onSuccess: (data) => {
      const result = data.data.result;
      // Reload canvas so node IDs sync with backend UUIDs
      loadVersion(result);
      // Update store: mark as active (not draft) + sync URL via history.replaceState
      updateVersion(result.id, result.version_number, {
        is_draft: false,
        version_status: VERSION_STATUS.ACTIVE,
      });
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-versions", currentAgent?.id],
      });
      queryClient.invalidateQueries({
        queryKey: [
          "agent-playground",
          "version-detail",
          currentAgent?.id,
          currentAgent?.version_id,
        ],
      });
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph", currentAgent?.id],
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-versions-infinite"],
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-version-detail"],
      });
      setOpenSaveAgentDialog(false);
      reset({
        versionName: `Version ${result.version_number}` || "",
        commitMessage: "",
      });
      if (pendingRunAfterSave) {
        setPendingRunAfterSave(false);
        runWorkflow();
      }
    },
    onError: (error) => {
      const errorMessage =
        typeof error?.result === "string"
          ? error.result
          : "Failed to save agent version";
      enqueueSnackbar(errorMessage, { variant: "error" });
      if (pendingRunAfterSave) {
        setPendingRunAfterSave(false);
      }
    },
  });

  const handleSaveAgent = () => {
    clearValidationErrors();

    const validationResult = validateGraphForSave(nodes, edges);

    if (!validationResult.valid) {
      if (validationResult.invalidNodeIds.length > 0) {
        setValidationErrorNodeIds(validationResult.invalidNodeIds);
      }

      setOpenSaveAgentDialog(false);

      const message = validationResult.hasCycle
        ? validationResult.errors[0].message
        : validationResult.invalidNodeIds.length === 1
          ? "Node not configured"
          : `${validationResult.invalidNodeIds.length} nodes are not configured`;

      enqueueSnackbar(message, { variant: "error" });
      return;
    }

    const { commitMessage } = getValues();
    const isDraft = currentAgent?.is_draft ?? true;

    const payload = isDraft
      ? {
          status: VERSION_STATUS.ACTIVE,
          ...(commitMessage && { commit_message: commitMessage }),
        }
      : buildVersionPayload(nodes, edges, {
          status: VERSION_STATUS.ACTIVE,
          commitMessage,
        });

    saveAgent({
      graphId: currentAgent?.id,
      versionId: currentAgent?.version_id,
      payload,
    });
  };

  return (
    <ModalWrapper
      open={openSaveAgentDialog}
      onClose={() => {
        setPendingRunAfterSave(false);
        setOpenSaveAgentDialog(false);
      }}
      title={dialogCopy.title}
      subTitle={dialogCopy.subTitle}
      actionBtnTitle={dialogCopy.action}
      actionBtnProps={{
        size: "small",
        onClick: handleSaveAgent,
      }}
      cancelBtnProps={{
        size: "small",
      }}
      actionBtnSx={{
        minWidth: "90px",
      }}
      cancelBtnSx={{
        minWidth: "90px",
      }}
      isValid={isValid}
      isLoading={isSavingAgent}
    >
      <form noValidate onSubmit={handleSubmit(handleSaveAgent)}>
        <Stack direction="column" gap={2}>
          <FormTextFieldV2
            label={dialogCopy.versionLabel}
            fieldName="versionName"
            control={control}
            size="small"
            fullWidth
            required
            disabled
          />
          <FormTextFieldV2
            label={dialogCopy.notesLabel}
            fieldName="commitMessage"
            control={control}
            size="small"
            fullWidth
            disabled={isSavingAgent}
          />
        </Stack>
        {/* Hidden submit button to enable Enter key submission */}
        <button type="submit" style={{ display: "none" }} />
      </form>
    </ModalWrapper>
  );
}
