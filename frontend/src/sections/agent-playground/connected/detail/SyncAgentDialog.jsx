import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useAgentVersion, useChangeVersion } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  PROMPT_HELP,
  PROMPT_MAX,
  changeBody,
  initialValues,
  nameProblem,
  phoneProblem,
  promptProblem,
} from "./syncAgentValues";

function SpeaksFirst({ checked, onChange }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 1.75,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          Agent speaks first
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          Turn on if your agent greets first. The simulator waits for it before
          replying.
        </Typography>
      </Box>
      <Switch
        size="small"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        inputProps={{ "aria-label": "Agent speaks first" }}
      />
    </Stack>
  );
}

SpeaksFirst.propTypes = {
  checked: PropTypes.bool.isRequired,
  onChange: PropTypes.func.isRequired,
};

function SyncForm({ agent, kind, start, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agent.id);
  const [values, setValues] = useState(start);
  const [touched, setTouched] = useState({});
  const set = (name) => (value) => setValues((v) => ({ ...v, [name]: value }));
  const touch = (name) => () => setTouched((t) => ({ ...t, [name]: true }));

  const problems = {
    prompt: promptProblem(values.prompt),
    phoneNumber: kind === "phone" ? phoneProblem(values.phoneNumber) : null,
    agentName: kind === "livekit" ? nameProblem(values.agentName) : null,
  };
  const body = changeBody(kind, start, values);
  const changed = Boolean(body.prompt !== undefined || body.settings);
  const valid = !Object.values(problems).some(Boolean);
  const next = `v${(agent.versionCount || 0) + 1}`;
  const shown = (name) => (touched[name] ? problems[name] : null);

  const submit = () => {
    setTouched({ prompt: true, phoneNumber: true, agentName: true });
    if (!valid || !changed) return;
    change.mutate(body, {
      onSuccess: (data) => {
        onClose();
        const label = data.version.version_name_display;
        enqueueSnackbar(
          data.up_to_date
            ? `${label} already matches your agent`
            : `${label} created; environments using this agent will offer a rebuild`,
          { variant: data.up_to_date ? "info" : "success" },
        );
      },
      onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
    });
  };

  return (
    <ConfirmDialog
      open
      onClose={() => (change.isPending ? null : onClose())}
      closeDisabled={change.isPending}
      title="Sync your agent"
      content={
        <Stack spacing={2}>
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            We can&apos;t read this agent from where it runs. Change these to
            match your live agent, and saving makes {next}.
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={6}
            maxRows={14}
            label="System prompt"
            required
            value={values.prompt}
            onChange={(e) => set("prompt")(e.target.value)}
            onBlur={touch("prompt")}
            error={Boolean(shown("prompt"))}
            helperText={
              shown("prompt") ||
              `${PROMPT_HELP[kind]} ${values.prompt.length.toLocaleString()} / ${PROMPT_MAX.toLocaleString()}`
            }
            inputProps={{ "data-testid": "sync-agent-prompt" }}
          />
          {kind === "phone" && (
            <TextField
              fullWidth
              label="Phone number"
              required
              placeholder="+14155550100"
              value={values.phoneNumber}
              onChange={(e) => set("phoneNumber")(e.target.value)}
              onBlur={touch("phoneNumber")}
              error={Boolean(shown("phoneNumber"))}
              helperText={
                shown("phoneNumber") ||
                "The platform calls this number, so calls are always inbound."
              }
              inputProps={{ "data-testid": "sync-agent-phone" }}
            />
          )}
          {kind === "livekit" && (
            <TextField
              fullWidth
              label="LiveKit agent name"
              required
              placeholder="returns-line-agent"
              value={values.agentName}
              onChange={(e) => set("agentName")(e.target.value)}
              onBlur={touch("agentName")}
              error={Boolean(shown("agentName"))}
              helperText={
                shown("agentName") ||
                "The name your worker registers with; each call dispatches it into the room."
              }
              inputProps={{ "data-testid": "sync-agent-name" }}
            />
          )}
          <SpeaksFirst
            checked={values.speaksFirst}
            onChange={set("speaksFirst")}
          />
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={change.isPending || !changed || !valid}
          onClick={submit}
        >
          {`Save as ${next}`}
        </Button>
      }
    />
  );
}

SyncForm.propTypes = {
  agent: PropTypes.object.isRequired,
  kind: PropTypes.oneOf(["phone", "livekit"]).isRequired,
  start: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default function SyncAgentDialog({ agent, onClose }) {
  const version = useAgentVersion(agent.id, agent.activeVersionId);
  if (version.isLoading || version.isError) {
    return (
      <ConfirmDialog
        open
        onClose={onClose}
        title="Sync your agent"
        content={
          version.isError ? (
            <Typography sx={{ typography: "s2_1", color: "error.main" }}>
              Couldn&apos;t load this agent&apos;s current version. Close and
              try again.
            </Typography>
          ) : (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          )
        }
      />
    );
  }
  return (
    <SyncForm
      key={agent.activeVersionId}
      agent={agent}
      kind={agent.source}
      start={initialValues(version.data?.config)}
      onClose={onClose}
    />
  );
}

SyncAgentDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};
