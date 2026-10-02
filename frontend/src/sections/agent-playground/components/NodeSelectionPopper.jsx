import { Paper, Popper, ClickAwayListener, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useRef, useState } from "react";
import { AGENT_NODE, NODE_TYPES } from "../utils/constants";
import {
  useGetNodeTemplates,
  useGetReferenceableGraphs,
} from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";
import NodeCard from "./NodeCard";
import PromptNodePopper from "./PromptNodePopper";
import { enqueueSnackbar } from "notistack";
import useAddNodeOptimistic from "../AgentBuilder/hooks/useAddNodeOptimistic";

export default function NodeSelectionPopper({
  open,
  anchorEl,
  onClose,
  onNodeSelect,
}) {
  const [promptPopperOpen, setPromptPopperOpen] = useState(false);
  const promptAnchorRef = useRef(null);

  const { addNode } = useAddNodeOptimistic();

  const { currentAgent } = useAgentPlaygroundStoreShallow((state) => ({
    currentAgent: state.currentAgent,
  }));
  const { data: referenceableGraphs = [] } = useGetReferenceableGraphs(
    currentAgent?.id,
  );

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

  const handleNodeClick = useCallback(
    (nodeId, nodeTemplateId) => {
      if (nodeId === NODE_TYPES.LLM_PROMPT) {
        return;
      }
      if (onNodeSelect) {
        onNodeSelect(nodeId, nodeTemplateId);
      } else {
        addNode({
          type: nodeId,
          position: undefined,
          node_template_id: nodeTemplateId,
        });
      }
      handleMainClose();
    },
    [addNode, handleMainClose, onNodeSelect],
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
    </>
  );
}

NodeSelectionPopper.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onNodeSelect: PropTypes.func,
};
