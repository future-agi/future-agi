import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import PropTypes from "prop-types";
import React from "react";
import { paths } from "src/routes/paths";
import { AGENT_NODE_AVAILABILITY } from "../hooks/useAgentNodeAvailability";

const COPY = {
  [AGENT_NODE_AVAILABILITY.LOADING]: {
    title: "Checking available agents",
    body: "Looking for agents you can reference from this builder…",
  },
  [AGENT_NODE_AVAILABILITY.ERROR]: {
    title: "Couldn't load available agents",
    body: "The list of agents you can reference failed to load. Try again. If the problem persists, check your access to this workspace.",
  },
  [AGENT_NODE_AVAILABILITY.EMPTY]: {
    title: "No agents available to reference",
    body: "An Agent node runs a published version of another agent from this workspace. Publish or activate a version of another agent, then refresh this list. Nothing has been added to your graph.",
  },
};

/**
 * Non-mutating setup dialog shown when the user picks the Agent node while
 * no eligible agents are available in the current context (TH-4549).
 *
 * Deliberately shows no agent names or counts: the backend already filtered
 * the list for this user's organization/workspace, and this dialog must not
 * hint at anything the user cannot access.
 */
export default function AgentNodeSetupDialog({
  open,
  status,
  isRefreshing = false,
  onRefresh,
  onClose,
}) {
  const copy = COPY[status] ?? COPY[AGENT_NODE_AVAILABILITY.EMPTY];
  const isLoading = status === AGENT_NODE_AVAILABILITY.LOADING;

  const handleOpenAgents = () => {
    window.open(paths.dashboard.agents, "_blank", "noopener,noreferrer");
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="agent-node-setup-title"
      data-testid="agent-node-setup-dialog"
    >
      <DialogTitle id="agent-node-setup-title">{copy.title}</DialogTitle>
      <DialogContent>
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          {isLoading && <CircularProgress size={18} sx={{ mt: 0.25 }} />}
          <DialogContentText data-testid="agent-node-setup-body">
            {copy.body}
          </DialogContentText>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} color="inherit" size="small">
          Cancel
        </Button>
        {status === AGENT_NODE_AVAILABILITY.EMPTY && (
          <Button
            onClick={handleOpenAgents}
            variant="outlined"
            size="small"
            data-testid="agent-node-setup-open-agents"
          >
            Open Agent Playground
          </Button>
        )}
        {!isLoading && (
          <LoadingButton
            onClick={onRefresh}
            variant="contained"
            size="small"
            loading={isRefreshing}
            data-testid="agent-node-setup-refresh"
          >
            {status === AGENT_NODE_AVAILABILITY.ERROR
              ? "Try again"
              : "Refresh agents"}
          </LoadingButton>
        )}
      </DialogActions>
    </Dialog>
  );
}

AgentNodeSetupDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  status: PropTypes.oneOf(Object.values(AGENT_NODE_AVAILABILITY)).isRequired,
  isRefreshing: PropTypes.bool,
  onRefresh: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
