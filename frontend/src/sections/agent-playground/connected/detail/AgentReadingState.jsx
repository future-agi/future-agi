import PropTypes from "prop-types";
import { useMemo } from "react";
import { useSnackbar } from "notistack";
import { Alert, Button, CircularProgress } from "@mui/material";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import {
  pollWhileChecking,
  toVersion,
  useRetryVersion,
} from "src/api/agents/agentDetail";
import { useFoundCheck } from "src/api/agents/agentConnect";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { STEP_LABEL, failureText } from "./versionLabels";

/**
 * Where the newest version's reading stands, above the tabs: reading, failed
 * with Retry, or waiting for "Check what we found" on an agent connected on
 * its own. Nothing when the newest version is checked and confirmed.
 */
export default function AgentReadingState({ agent }) {
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const list = useAgentDefinitionVersions({
    selectedAgentId: agent.id,
    refetchInterval: pollWhileChecking,
  });
  const retry = useRetryVersion(agent.id);
  const foundCheck = useFoundCheck(agent.id);
  const newest = useMemo(() => {
    const first = (list.data?.pages?.[0]?.results ?? []).map(toVersion);
    return first.find((v) => v.check !== "discarded") ?? null;
  }, [list.data]);

  if (!newest) return null;

  if (newest.check === "pending") {
    return (
      <Alert
        severity="info"
        icon={<CircularProgress size={14} />}
        data-testid="agent-reading-state"
        sx={{ mb: 1.5 }}
      >
        {`${STEP_LABEL[newest.checkStep] || "Checking"} ${newest.label}`}
        {newest.checkStep === "reading"
          ? ": its tools, prompt and model are read from the code."
          : "."}
      </Alert>
    );
  }

  if (newest.check === "failed" && newest.checkFailure) {
    const canRetry = canEdit && newest.checkFailure.retryable;
    return (
      <Alert
        severity="warning"
        data-testid="agent-reading-state"
        sx={{ mb: 1.5 }}
        action={
          canRetry ? (
            <Button
              size="small"
              color="inherit"
              disabled={retry.isPending}
              onClick={() =>
                retry.mutate(
                  { versionId: newest.id },
                  {
                    onSuccess: () =>
                      enqueueSnackbar(`${newest.label} is being read again`, {
                        variant: "info",
                      }),
                    onError: (e) =>
                      enqueueSnackbar(errorMessage(e), { variant: "error" }),
                  },
                )
              }
            >
              Retry
            </Button>
          ) : null
        }
      >
        {`Couldn't read ${newest.label}: ${failureText(newest.checkFailure)}`}
      </Alert>
    );
  }

  if (agent.foundCheck?.required && newest.check !== "failed") {
    return (
      <Alert
        severity="info"
        icon={false}
        data-testid="agent-found-check"
        sx={{ mb: 1.5 }}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!canEdit || foundCheck.isPending}
            onClick={() =>
              foundCheck.mutate(undefined, {
                onSuccess: () =>
                  enqueueSnackbar("Thanks, the agent can be built from now", {
                    variant: "success",
                  }),
                onError: (e) =>
                  enqueueSnackbar(errorMessage(e), { variant: "error" }),
              })
            }
          >
            Looks right
          </Button>
        }
      >
        Check the entry, prompt, tools and model on the Definition tab, then
        confirm them to build an environment from this agent.
      </Alert>
    );
  }
  return null;
}

AgentReadingState.propTypes = { agent: PropTypes.object.isRequired };
