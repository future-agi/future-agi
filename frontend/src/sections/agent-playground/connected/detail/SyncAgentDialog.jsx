import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box,
  CircularProgress,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import ClosableDialog from "src/components/custom-dialog/closable-dialog";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useAgentVersion, useChangeVersion } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { LabeledField } from "./bits";
import {
  PROMPT_HELP,
  PROMPT_MAX,
  changeBody,
  initialValues,
  nameProblem,
  phoneProblem,
  promptProblem,
} from "./syncAgentValues";

const nextLabel = (agent) => `v${(agent.versionCount || 0) + 1}`;

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
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
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
  const next = nextLabel(agent);
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
    <ModalWrapper
      open
      onClose={onClose}
      isLoading={change.isPending}
      title={`Add version ${next}`}
      actionBtnTitle={`Save as ${next}`}
      isValid={changed && valid}
      onSubmit={submit}
      modalWidth="560px"
    >
      <Stack spacing={2}>
        <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
          We can&apos;t read this agent from where it runs. Change these to
          match your live agent, and saving makes {next}.
        </Typography>
        <LabeledField
          id="sync-agent-prompt"
          label="System prompt"
          required
          error={shown("prompt")}
          helper={`${PROMPT_HELP[kind]} ${values.prompt.length.toLocaleString()} / ${PROMPT_MAX.toLocaleString()}`}
        >
          <TextField
            id="sync-agent-prompt"
            size="small"
            fullWidth
            multiline
            minRows={6}
            maxRows={14}
            value={values.prompt}
            onChange={(e) => set("prompt")(e.target.value)}
            onBlur={touch("prompt")}
            error={Boolean(shown("prompt"))}
            inputProps={{ "data-testid": "sync-agent-prompt" }}
          />
        </LabeledField>
        {kind === "phone" && (
          <LabeledField
            id="sync-agent-phone"
            label="Phone number"
            required
            error={shown("phoneNumber")}
            helper="The platform calls this number, so calls are always inbound."
          >
            <TextField
              id="sync-agent-phone"
              size="small"
              fullWidth
              placeholder="+14155550100"
              value={values.phoneNumber}
              onChange={(e) => set("phoneNumber")(e.target.value)}
              onBlur={touch("phoneNumber")}
              error={Boolean(shown("phoneNumber"))}
              inputProps={{ "data-testid": "sync-agent-phone" }}
            />
          </LabeledField>
        )}
        {kind === "livekit" && (
          <LabeledField
            id="sync-agent-name"
            label="LiveKit agent name"
            required
            error={shown("agentName")}
            helper="The name your worker registers with; each call dispatches it into the room."
          >
            <TextField
              id="sync-agent-name"
              size="small"
              fullWidth
              placeholder="returns-line-agent"
              value={values.agentName}
              onChange={(e) => set("agentName")(e.target.value)}
              onBlur={touch("agentName")}
              error={Boolean(shown("agentName"))}
              inputProps={{ "data-testid": "sync-agent-name" }}
            />
          </LabeledField>
        )}
        <SpeaksFirst
          checked={values.speaksFirst}
          onChange={set("speaksFirst")}
        />
      </Stack>
    </ModalWrapper>
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
      <ClosableDialog
        open
        onClose={onClose}
        fullWidth
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 400 } }}
      >
        <Box sx={{ p: 2 }}>
          <Typography sx={{ typography: "m2", fontWeight: 600, pr: 4 }}>
            {`Add version ${nextLabel(agent)}`}
          </Typography>
          {version.isError ? (
            <Typography
              sx={{ typography: "s2_1", color: "status.danger.fg", mt: 1 }}
            >
              Couldn&apos;t load this agent&apos;s current version. Close and
              try again.
            </Typography>
          ) : (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          )}
        </Box>
      </ClosableDialog>
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
