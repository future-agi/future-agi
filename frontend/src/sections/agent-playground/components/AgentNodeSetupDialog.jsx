import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Link,
  Stack,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import PropTypes from "prop-types";
import React from "react";
import { paths } from "src/routes/paths";
import { AGENT_NODE_AVAILABILITY } from "../hooks/useAgentNodeAvailability";

// Copy per availability state (PRD r1.1 layout sketch + AC-05). Never names
// hidden agents, counts, IDs or org-wide statements.
const COPY = {
  [AGENT_NODE_AVAILABILITY.LOADING]: {
    title: "Agent node setup",
    body: "Loading eligible agents...",
  },
  [AGENT_NODE_AVAILABILITY.ERROR]: {
    title: "Could not load eligible agents",
    body: "The request for agents you can reference did not complete. Retry to load eligibility again. Nothing has been added to your graph.",
  },
  [AGENT_NODE_AVAILABILITY.FORBIDDEN]: {
    title: "You cannot access eligible agents in this context",
    body: "Your current organization or workspace does not grant access to referenceable agents. Switch context using the existing controls, then retry. Nothing has been added to your graph.",
  },
  [AGENT_NODE_AVAILABILITY.NOT_FOUND]: {
    title: "Current agent unavailable",
    body: "This agent could not be found in the current context, so eligible agents cannot be loaded. Reopen it from the Agent Playground list and try again.",
  },
  [AGENT_NODE_AVAILABILITY.EMPTY]: {
    title: "No eligible agents available here",
    body: "Use another agent in this organization and workspace with an active or inactive version. Draft versions cannot be used, and agents that would create a reference cycle are not eligible. In the new tab, check the same organization and workspace, then create or finish another agent. Return here and refresh. Refresh agents updates choices, not this page.",
  },
  [AGENT_NODE_AVAILABILITY.READY]: {
    title: "Eligible agents are available",
    body: "You can now add an Agent node and pick an agent and version in its form. Nothing has been added yet.",
  },
};

/**
 * Non-mutating setup dialog shown when the user picks the Agent node while
 * no eligible agents are confirmed for the current context (TH-4549).
 *
 * - Refresh agents only refetches eligibility.
 * - Add Agent node is enabled only after a confirmed nonempty result and
 *   performs the originally requested insertion exactly once.
 * - The playground link is a real anchor (new tab, same origin) so a blocked
 *   popup still leaves a link the user can open manually (AC-06).
 */
export default function AgentNodeSetupDialog({
  open,
  status,
  isRefreshing = false,
  isAdding = false,
  onRefresh,
  onAdd,
  onClose,
}) {
  const copy = COPY[status] ?? COPY[AGENT_NODE_AVAILABILITY.ERROR];
  const isLoading = status === AGENT_NODE_AVAILABILITY.LOADING;
  const isReady = status === AGENT_NODE_AVAILABILITY.READY;
  const isEmpty = status === AGENT_NODE_AVAILABILITY.EMPTY;
  const isFailure =
    status === AGENT_NODE_AVAILABILITY.ERROR ||
    status === AGENT_NODE_AVAILABILITY.FORBIDDEN ||
    status === AGENT_NODE_AVAILABILITY.NOT_FOUND;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="agent-node-setup-title"
      data-testid="agent-node-setup-dialog"
      data-status={status}
    >
      <DialogTitle id="agent-node-setup-title">{copy.title}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            {isLoading && <CircularProgress size={18} sx={{ mt: 0.25 }} />}
            <DialogContentText
              data-testid="agent-node-setup-body"
              role={isLoading || isFailure ? "status" : undefined}
            >
              {copy.body}
            </DialogContentText>
          </Stack>
          {isEmpty && (
            <Link
              href={paths.dashboard.agents}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
              variant="body2"
              data-testid="agent-node-setup-open-agents"
            >
              Open Agent Playground in new tab
            </Link>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} color="inherit" size="small">
          Cancel
        </Button>
        {!isLoading && (
          <LoadingButton
            onClick={onRefresh}
            variant={isReady ? "outlined" : "contained"}
            size="small"
            loading={isRefreshing}
            disabled={isAdding}
            data-testid="agent-node-setup-refresh"
          >
            {isFailure ? "Retry" : "Refresh agents"}
          </LoadingButton>
        )}
        {(isEmpty || isReady) && (
          <LoadingButton
            onClick={onAdd}
            variant="contained"
            size="small"
            loading={isAdding}
            disabled={!isReady || isRefreshing}
            data-testid="agent-node-setup-add"
          >
            Add Agent node
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
  isAdding: PropTypes.bool,
  onRefresh: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
