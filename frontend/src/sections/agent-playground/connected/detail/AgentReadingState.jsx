import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  TextField,
} from "@mui/material";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import {
  agentDetailQueryKey,
  pollWhileChecking,
  toVersion,
  useChangeVersion,
  useRetryVersion,
} from "src/api/agents/agentDetail";
import { useFoundCheck } from "src/api/agents/agentConnect";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { LabeledField } from "./bits";
import {
  STEP_LABEL,
  failureText,
  isStalled,
  missingCredentials,
} from "./versionLabels";

function CredentialsForm({ agentId, names }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agentId);
  const [values, setValues] = useState({});
  const complete = names.every((name) => values[name]);

  const submit = (event) => {
    event.preventDefault();
    if (!complete) return;
    change.mutate(
      {
        kind: "credentials",
        secret_values: Object.fromEntries(
          names.map((name) => [name, values[name]]),
        ),
      },
      {
        onSuccess: (data) => {
          setValues({});
          const label = data?.version?.version_name_display;
          enqueueSnackbar(
            label
              ? `${label} created with these credentials and is being checked`
              : "Credentials saved; the agent is being checked again",
            { variant: "success" },
          );
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );
  };

  return (
    <Box
      component="form"
      onSubmit={submit}
      data-testid="agent-credentials-form"
      sx={{ mt: 1.5 }}
    >
      <Stack spacing={1.5}>
        {names.map((name) => (
          <LabeledField key={name} id={`agent-credential-${name}`} label={name}>
            <TextField
              id={`agent-credential-${name}`}
              type="password"
              size="small"
              fullWidth
              autoComplete="off"
              value={values[name] ?? ""}
              onChange={(e) =>
                setValues((current) => ({
                  ...current,
                  [name]: e.target.value,
                }))
              }
            />
          </LabeledField>
        ))}
        <Box>
          <Button
            type="submit"
            size="small"
            variant="contained"
            color="primary"
            disabled={!complete || change.isPending}
          >
            Save credentials
          </Button>
        </Box>
      </Stack>
    </Box>
  );
}

CredentialsForm.propTypes = {
  agentId: PropTypes.string.isRequired,
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
};

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
  const queryClient = useQueryClient();
  const wasPending = useRef(false);
  const check = newest?.check ?? null;
  useEffect(() => {
    if (wasPending.current && check && check !== "pending") {
      queryClient.invalidateQueries({
        queryKey: agentDetailQueryKey(agent.id),
      });
      queryClient.invalidateQueries({
        queryKey: ["agents", "version", agent.id],
      });
      queryClient.invalidateQueries({
        queryKey: ["agents", "stored-agent", agent.id],
      });
    }
    wasPending.current = check === "pending";
  }, [check, agent.id, queryClient]);

  if (!newest) return null;

  const retryButton = (
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
  );

  if (newest.check === "pending" && isStalled(newest)) {
    return (
      <Alert
        severity="warning"
        data-testid="agent-reading-state"
        sx={{ mb: 1.5 }}
        action={canEdit ? retryButton : null}
      >
        {`Checking ${newest.label} is taking longer than it should and may have stopped.`}
      </Alert>
    );
  }

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
    const missing = canEdit ? missingCredentials(newest.checkFailure) : [];
    return (
      <Alert
        severity="warning"
        data-testid="agent-reading-state"
        sx={{ mb: 1.5, "& .MuiAlert-message": { flexGrow: 1 } }}
        action={canRetry ? retryButton : null}
      >
        {`Couldn't read ${newest.label}: ${failureText(newest.checkFailure)}`}
        {missing.length > 0 && (
          <CredentialsForm agentId={agent.id} names={missing} />
        )}
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
