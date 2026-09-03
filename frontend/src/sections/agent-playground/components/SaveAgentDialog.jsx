import React, { useEffect, useMemo, useState } from "react";
import ModalWrapper from "../../../components/ModalWrapper/ModalWrapper";
import { useAgentPlaygroundStoreShallow } from "../store";
import { z } from "zod";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { Box, CircularProgress, Stack, Tab, Tabs } from "@mui/material";
import Iconify from "src/components/iconify";
import { useQueryClient } from "@tanstack/react-query";
import {
  useSaveDraftVersion,
  useGetGraphVersions,
  useGetVersionDetail,
} from "src/api/agent-playground/agent-playground";
import { enqueueSnackbar } from "notistack";
import { buildVersionPayload } from "../utils/versionPayloadUtils";
import { VERSION_STATUS } from "../utils/constants";
import { validateGraphForSave } from "../utils/workflowValidation";
import useWorkflowExecution from "../hooks/useWorkflowExecution";
import useCanEditAgent from "../hooks/useCanEditAgent";
import SaveAgentChangelogTab from "./SaveAgentChangelogTab";
import SaveAgentCodeTab from "./SaveAgentCodeTab";
import {
  buildAgentDefinitionFileName,
  classifySaveChanges,
  flattenGraphVersions,
  pickBaselineVersion,
  toComparableSnapshot,
} from "../utils/saveAgentDiff";

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
  const { canEditAgent } = useCanEditAgent();
  const [activeTab, setActiveTab] = useState("changelog");
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

  useEffect(() => {
    if (openSaveAgentDialog) {
      setActiveTab("changelog");
    }
  }, [openSaveAgentDialog]);

  const queryClient = useQueryClient();

  const { data: versionsData, isLoading: isVersionsLoading } =
    useGetGraphVersions(currentAgent?.id, {
      enabled: openSaveAgentDialog && !!currentAgent?.id,
    });

  const versions = useMemo(
    () => flattenGraphVersions(versionsData),
    [versionsData],
  );

  const baseline = useMemo(
    () => pickBaselineVersion(versions, currentAgent),
    [versions, currentAgent],
  );

  const { data: baselineDetail, isLoading: isBaselineLoading } =
    useGetVersionDetail(currentAgent?.id, baseline?.id, {
      enabled: openSaveAgentDialog && !!currentAgent?.id && !!baseline?.id,
    });

  const currentVersionMeta = useMemo(
    () => versions.find((version) => version.id === currentAgent?.version_id),
    [versions, currentAgent?.version_id],
  );

  const saveDiff = useMemo(() => {
    const currentSnapshot = toComparableSnapshot({ nodes, edges });
    const previousSnapshot = baselineDetail
      ? toComparableSnapshot(baselineDetail)
      : { nodes: [], connections: [] };
    return classifySaveChanges({
      previousSnapshot,
      currentSnapshot,
      occurredAt: {
        previous: baseline?.updated_at || baseline?.created_at || null,
        current:
          currentVersionMeta?.updated_at ||
          currentVersionMeta?.created_at ||
          null,
      },
    });
  }, [nodes, edges, baselineDetail, baseline, currentVersionMeta]);

  const fileName = buildAgentDefinitionFileName(currentAgent?.name);
  const isDiffLoading =
    openSaveAgentDialog &&
    (isVersionsLoading || (!!baseline?.id && isBaselineLoading));

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
    if (!canEditAgent) return;
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

  const agentTitle = currentAgent?.name
    ? `Save ${currentAgent.name} agent`
    : "Save Agent";

  return (
    <ModalWrapper
      open={openSaveAgentDialog}
      onClose={() => {
        setPendingRunAfterSave(false);
        setOpenSaveAgentDialog(false);
      }}
      title={agentTitle}
      subTitle="Review the details below, and save the agent."
      actionBtnTitle={pendingRunAfterSave ? "Save & Run" : "Save agent"}
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
      modalWidth="920px"
    >
      <form noValidate onSubmit={handleSubmit(handleSaveAgent)}>
        <Stack direction="column" gap={2}>
          <FormTextFieldV2
            label="Version"
            fieldName="versionName"
            control={control}
            size="small"
            fullWidth
            required
            disabled
          />
          <FormTextFieldV2
            label="Commit message"
            fieldName="commitMessage"
            control={control}
            size="small"
            fullWidth
            disabled={isSavingAgent}
          />
          <Tabs
            value={activeTab}
            onChange={(_event, value) => setActiveTab(value)}
            textColor="primary"
            data-testid="save-agent-diff-tabs"
            sx={{ minHeight: 36, borderBottom: 1, borderColor: "divider" }}
          >
            <Tab
              value="changelog"
              label="Changelog"
              icon={<Iconify icon="solar:document-text-bold" width={16} />}
              iconPosition="start"
              data-testid="save-agent-tab-changelog"
              sx={{ minHeight: 36, textTransform: "none" }}
            />
            <Tab
              value="code"
              label="Code"
              icon={<Iconify icon="solar:code-bold" width={16} />}
              iconPosition="start"
              data-testid="save-agent-tab-code"
              sx={{ minHeight: 36, textTransform: "none" }}
            />
          </Tabs>
          <Box sx={{ minHeight: 220 }}>
            {isDiffLoading ? (
              <Stack
                alignItems="center"
                justifyContent="center"
                sx={{ py: 6 }}
                data-testid="save-agent-diff-loading"
              >
                <CircularProgress size={24} />
              </Stack>
            ) : activeTab === "changelog" ? (
              <SaveAgentChangelogTab entries={saveDiff.entries} />
            ) : (
              <SaveAgentCodeTab
                fileName={fileName}
                currentJson={saveDiff.currentJson}
                aligned={saveDiff.aligned}
                totals={saveDiff.totals}
                perNode={saveDiff.perNode}
                hasBaseline={saveDiff.hasBaseline}
              />
            )}
          </Box>
        </Stack>
        {/* Hidden submit button to enable Enter key submission */}
        <button type="submit" style={{ display: "none" }} />
      </form>
    </ModalWrapper>
  );
}
