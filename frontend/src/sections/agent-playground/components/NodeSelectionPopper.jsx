import { Paper, Popper, ClickAwayListener, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useRef, useState } from "react";
import { AGENT_NODE, NODE_TYPES } from "../utils/constants";
import { useGetNodeTemplates } from "src/api/agent-playground/agent-playground";
import NodeCard from "./NodeCard";
import PromptNodePopper from "./PromptNodePopper";
import AgentNodeSetupDialog from "./AgentNodeSetupDialog";
import { enqueueSnackbar } from "notistack";
import useAddNodeOptimistic from "../AgentBuilder/hooks/useAddNodeOptimistic";
import useAgentNodeInsertGuard from "../hooks/useAgentNodeInsertGuard";

export default function NodeSelectionPopper({
  open,
  anchorEl,
  onClose,
  onNodeSelect,
}) {
  const [promptPopperOpen, setPromptPopperOpen] = useState(false);
  const promptAnchorRef = useRef(null);

  const { addNode } = useAddNodeOptimistic();
  const { guardNodeInsert, setupDialogProps } = useAgentNodeInsertGuard();

  // The Agent node is always listed (TH-4549). Whether it can be inserted
  // right now is decided per click by useAgentNodeInsertGuard.
  const { data: templateNodes = [] } = useGetNodeTemplates();
  const nodesList = useMemo(
    () => [...templateNodes, AGENT_NODE],
    [templateNodes],
  );

  const handlePromptExpandClick = useCallback((e) => {
    promptAnchorRef.current = e.currentTarget;
    setPromptPopperOpen(true);
  }, []);

  const handlePromptPopperClose = useCallback(() => {
    setPromptPopperOpen(false);
  }, []);

  const handleMainClose = useCallback(() => {
    setPromptPopperOpen(false);
    onClose();
  }, [onClose]);

  const insertNode = useCallback(
    (nodeId, nodeTemplateId) => {
      if (onNodeSelect) {
        return onNodeSelect(nodeId, nodeTemplateId);
      }
      return addNode({
        type: nodeId,
        position: undefined,
        node_template_id: nodeTemplateId,
      });
    },
    [addNode, onNodeSelect],
  );

  // Always points at the newest insertNode so a *deferred* Agent insert (made
  // later from the setup dialog) runs the parent's current onNodeSelect — which
  // re-reads running/read-only state and the source node at that moment —
  // instead of a closure frozen at click time (TH-4549, PRD R-13). Whether the
  // original target still exists is the caller's call (it owns the source node
  // id); the "+" anchor is hover chrome on edges and says nothing about it.
  const latestInsertRef = useRef(insertNode);
  latestInsertRef.current = insertNode;

  const deferredInsert = useCallback(
    (nodeId, nodeTemplateId) => latestInsertRef.current(nodeId, nodeTemplateId),
    [],
  );

  const handleNodeClick = useCallback(
    (nodeId, nodeTemplateId) => {
      if (nodeId === NODE_TYPES.LLM_PROMPT) {
        return;
      }
      // Eligible agents: inserts immediately, exactly as before. Otherwise the
      // insert is deferred to the setup dialog, which outlives the closed menu
      // and re-validates the target when the user finally adds.
      guardNodeInsert(
        nodeId,
        () => insertNode(nodeId, nodeTemplateId),
        () => deferredInsert(nodeId, nodeTemplateId),
      );
      handleMainClose();
    },
    [guardNodeInsert, handleMainClose, insertNode, deferredInsert],
  );

  return (
    <>
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="right-start"
        sx={{ zIndex: 1300 }}
      >
        <ClickAwayListener
          onClickAway={(e) => {
            if (e.target?.closest?.("[data-prompt-popper]")) return;
            handleMainClose();
          }}
        >
          <Paper
            elevation={3}
            sx={{
              ml: 0.75,
              p: 1.5,
              maxHeight: 400,
              overflowY: "auto",
              backgroundColor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              maxWidth: "250px",
            }}
          >
            <Stack spacing={1}>
              {nodesList.map((node) => (
                <NodeCard
                  key={node.id}
                  node={node}
                  onNodeClick={handleNodeClick}
                  onExpandClick={handlePromptExpandClick}
                  showExpandIcon={true}
                />
              ))}
            </Stack>
          </Paper>
        </ClickAwayListener>
      </Popper>

      <PromptNodePopper
        open={promptPopperOpen}
        anchorEl={promptAnchorRef.current}
        onClose={handlePromptPopperClose}
        onNodeSelect={
          onNodeSelect
            ? async (nodeId, templateId, initialConfig) => {
                await onNodeSelect(nodeId, templateId, initialConfig);
                handleMainClose();
              }
            : async (nodeId, templateId, initialConfig) => {
                try {
                  await addNode({
                    type: nodeId,
                    position: undefined,
                    node_template_id: templateId,
                    name: initialConfig?.name,
                    config: initialConfig,
                  });
                } catch {
                  enqueueSnackbar("Failed to add node", { variant: "error" });
                }
                handleMainClose();
              }
        }
      />

      <AgentNodeSetupDialog {...setupDialogProps} />
    </>
  );
}

NodeSelectionPopper.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onNodeSelect: PropTypes.func,
};
