import React, { useEffect, useRef, useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Divider, IconButton, Stack } from "@mui/material";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ConfirmDialog } from "src/components/custom-dialog";
import NodeConfigurationForm from "./NodeConfigurationForm";
import NodeDrawerSkeleton from "./NodeDrawerSkeleton";
import NodeCard from "../../components/NodeCard";
import SvgColor from "src/components/svg-color";
import { getNodeFormSchema } from "./forms/nodeFormSchemas";
import { NODE_TYPE_CONFIG } from "../../utils/constants";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
} from "../../store";
import { getDefaultValues, mapNodeDetailToNodeData } from "./nodeFormUtils";
import { useGetNodeDetail } from "src/api/agent-playground/agent-playground";
import { useQueryClient } from "@tanstack/react-query";
import PanelErrorBoundary from "../../components/PanelErrorBoundary";

const MIN_WIDTH = 450;
const MAX_WIDTH = 800;

export default function NodeDrawer({
  open,
  onClose,
  node,
  width,
  isResizing,
  onResizeStart,
}) {
  const queryClient = useQueryClient();
  const hadInitialConfigRef = useRef(false);
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);
  const [discardAction, setDiscardAction] = useState(null); // "close" | "switch" | null
  const [activeNode, setActiveNode] = useState(node ?? null);
  const [pendingNode, setPendingNode] = useState(null);

  const { setSelectedNode, updateNodeData, currentAgent, setNodeFormDirty } =
    useAgentPlaygroundStoreShallow((s) => ({
      setSelectedNode: s.setSelectedNode,
      updateNodeData: s.updateNodeData,
      currentAgent: s.currentAgent,
      setNodeFormDirty: s.setNodeFormDirty,
    }));

  // Fetch fresh node detail from API when drawer opens
  const { data: nodeDetailData, isFetching: isLoadingNodeDetail } =
    useGetNodeDetail(
      currentAgent?.id,
      currentAgent?.version_id,
      open ? activeNode?.id : null,
      { meta: { errorHandled: true } },
    );

  // Get schema based on node type
  const schema = useMemo(() => {
    if (!activeNode?.type) return null;
    return getNodeFormSchema(activeNode.type);
  }, [activeNode?.type]);

  const methods = useForm({
    defaultValues: getDefaultValues(node),
    resolver: schema ? zodResolver(schema) : undefined,
  });

  const {
    reset,
    setValue,
    formState: { isDirty },
  } = methods;

  // Sync dirty state to the store so callers (ensureDraft, handleRunWorkflow)
  // can detect unsaved form changes. Tracked whenever the drawer is open.
  useEffect(() => {
    setNodeFormDirty(open && isDirty);
  }, [open, isDirty, setNodeFormDirty]);

  // Clear on unmount so the flag doesn't persist across page navigations.
  useEffect(() => () => setNodeFormDirty(false), [setNodeFormDirty]);

  // After draft creation, the version ID changes and node IDs are remapped.
  // Sync activeNode to the remapped node by matching on label so that
  // useGetNodeDetail and useGetPossibleEdgeMappings use the correct ID.
  useEffect(() => {
    if (!open || !activeNode || !currentAgent?.version_id) return;
    const storeNodes = useAgentPlaygroundStore.getState().nodes;
    const stillExists = storeNodes.some((n) => n.id === activeNode.id);
    if (stillExists) return;
    const remapped = storeNodes.find(
      (n) =>
        n.data?.label === activeNode.data?.label && n.type === activeNode.type,
    );
    if (remapped) setActiveNode(remapped);
  }, [currentAgent?.version_id, open, activeNode]);

  // When API data arrives, merge it with existing node and reset form
  useEffect(() => {
    if (!nodeDetailData || !activeNode) return;

    const enrichedNode = mapNodeDetailToNodeData(nodeDetailData, activeNode);

    if (hadInitialConfigRef.current) {
      // Imported node: form already populated with model/messages from _initialConfig.
      // Only update IDs from the backend — don't overwrite imported data.
      const pt = nodeDetailData.promptTemplate;
      if (pt) {
        setValue("prompt_version_id", pt.promptVersionId || null);
        setValue("prompt_template_id", pt.promptTemplateId || null);
      }
      hadInitialConfigRef.current = false;
    } else {
      const newDefaults = getDefaultValues(enrichedNode);
      reset(newDefaults);
    }

    // Sync store with fresh API data
    updateNodeData(activeNode.id, enrichedNode.data);

    // Invalidate prompt versions cache so PromptNameRow fetches fresh versions
    const pt = nodeDetailData.promptTemplate;
    if (pt?.promptTemplateId) {
      queryClient.invalidateQueries({
        queryKey: ["prompt-versions", pt.promptTemplateId],
      });
    }
  }, [
    nodeDetailData,
    activeNode,
    reset,
    setValue,
    updateNodeData,
    queryClient,
  ]);

  // Keep an "active" node being edited.
  // If drawer is open and user clicks a different node while dirty:
  // - show discard dialog
  // - keep editing the current node until user confirms discard
  useEffect(() => {
    if (!open) {
      // Drawer closed -> clean up transient state
      setShowDiscardDialog(false);
      setDiscardAction(null);
      setPendingNode(null);
      setActiveNode(node ?? null);
      return;
    }

    if (!node) return;

    // First open / first node
    if (!activeNode) {
      setActiveNode(node);
      if (node?.data?._initialConfig) {
        // Imported node: reset to blank defaults first, then apply imported values.
        // keepDefaultValues: true keeps blank as baseline so isDirty = true.
        const blankNode = {
          ...node,
          data: { ...node.data, _initialConfig: undefined },
        };
        reset(getDefaultValues(blankNode));
        reset(getDefaultValues(node), { keepDefaultValues: true });
        updateNodeData(node.id, { _initialConfig: undefined });
        hadInitialConfigRef.current = true;
      } else {
        reset(getDefaultValues(node));
      }
      return;
    }

    // Same node id -> form already has correct data from API
    if (node.id === activeNode.id) {
      return;
    }

    // ID changed but same node (e.g., remapped during active→draft transition).
    // Keep the old activeNode — its ID is still valid for the current version.
    // Updating would cause useGetNodeDetail to fire with new ID + old version → flash.
    if (
      node.data?.label === activeNode.data?.label &&
      node.type === activeNode.type
    ) {
      return;
    }

    // Node id changed while drawer open
    if (isDirty) {
      // Attempted switch while dirty -> confirm discard, and reselect current node
      setPendingNode(node);
      setDiscardAction("switch");
      setShowDiscardDialog(true);

      const storeNodes = useAgentPlaygroundStore.getState().nodes;
      const fullActiveNode =
        storeNodes?.find((n) => n.id === activeNode.id) ?? activeNode;
      setSelectedNode(fullActiveNode);
      return;
    }

    // Not dirty -> switch immediately
    setActiveNode(node);
    if (node?.data?._initialConfig) {
      const blankNode = {
        ...node,
        data: { ...node.data, _initialConfig: undefined },
      };
      reset(getDefaultValues(blankNode));
      reset(getDefaultValues(node), { keepDefaultValues: true });
      updateNodeData(node.id, { _initialConfig: undefined });
      hadInitialConfigRef.current = true;
    } else {
      reset(getDefaultValues(node));
    }
  }, [open, node, activeNode, isDirty, reset, setSelectedNode, updateNodeData]);

  // Handle discard changes
  const handleDiscard = () => {
    setShowDiscardDialog(false);

    if (discardAction === "close") {
      // Discard and close drawer — read fresh node from store (activeNode may be stale)
      if (activeNode) {
        const storeNodes = useAgentPlaygroundStore.getState().nodes;
        const freshNode =
          storeNodes?.find((n) => n.id === activeNode.id) ?? activeNode;
        reset(getDefaultValues(freshNode));
      }
      setDiscardAction(null);
      setPendingNode(null);
      onClose();
      return;
    }

    if (discardAction === "switch" && pendingNode) {
      // Discard and switch to the node user clicked
      const storeNodes = useAgentPlaygroundStore.getState().nodes;
      const fullPendingNode =
        storeNodes?.find((n) => n.id === pendingNode.id) ?? pendingNode;
      setSelectedNode(fullPendingNode);
      setActiveNode(fullPendingNode);
      reset(getDefaultValues(fullPendingNode));

      setDiscardAction(null);
      setPendingNode(null);
      return;
    }

    // Fallback
    setDiscardAction(null);
    setPendingNode(null);
  };

  // Handle continue editing (close dialog, stay on drawer)
  const handleContinue = () => {
    setShowDiscardDialog(false);
    setDiscardAction(null);
    setPendingNode(null);
  };

  const handleClose = () => {
    if (isDirty) {
      setDiscardAction("close");
      setShowDiscardDialog(true);
    } else {
      onClose();
    }
  };

  if (!open || !activeNode) return null;

  return (
    <>
      <Box
        sx={{
          width,
          minWidth: MIN_WIDTH,
          maxWidth: MAX_WIDTH,
          height: "100%",
          bgcolor: "background.paper",
          borderLeft: "1px solid",
          borderColor: "border.default",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          padding: 1.5,
          position: "relative",
          zIndex: 20,
        }}
      >
        {/* Resizer handle */}
        <Box
          onMouseDown={onResizeStart}
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: 4,
            cursor: "col-resize",
            zIndex: 1,
            "&:hover": {
              borderLeft: "2px solid",
              borderColor: "primary.main",
            },
            ...(isResizing && {
              borderLeft: "2px solid",
              borderColor: "primary.main",
            }),
          }}
        />

        <FormProvider {...methods}>
          <Box
            sx={{ height: "100%", display: "flex", flexDirection: "column" }}
          >
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="flex-start"
            >
              <NodeCard node={NODE_TYPE_CONFIG[activeNode?.type]} readOnly />
              <IconButton
                onClick={handleClose}
                sx={{
                  color: "text.primary",
                }}
              >
                <SvgColor
                  src="/assets/icons/ic_close.svg"
                  sx={{
                    height: "20px",
                    width: "20px",
                  }}
                />
              </IconButton>
            </Stack>
            <Divider
              sx={{
                mt: 1.5,
              }}
            />
            {/* Content */}
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                py: 1.5,
              }}
            >
              <PanelErrorBoundary name="NodeConfigurationForm">
                {isLoadingNodeDetail ? (
                  <NodeDrawerSkeleton />
                ) : (
                  <NodeConfigurationForm
                    nodeType={activeNode.type}
                    nodeId={activeNode.id}
                  />
                )}
              </PanelErrorBoundary>
            </Box>
          </Box>
        </FormProvider>
      </Box>

      {/* Discard changes confirmation dialog */}
      <ConfirmDialog
        open={showDiscardDialog}
        onClose={handleContinue}
        title="Unsaved Changes"
        content="You have unsaved changes. Are you sure you want to discard them?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={handleDiscard}
            sx={{ paddingX: "24px" }}
          >
            Discard
          </Button>
        }
      />
    </>
  );
}

NodeDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  node: PropTypes.shape({
    id: PropTypes.string,
    type: PropTypes.string,
    data: PropTypes.object,
  }),
  width: PropTypes.number.isRequired,
  isResizing: PropTypes.bool.isRequired,
  onResizeStart: PropTypes.func.isRequired,
};
