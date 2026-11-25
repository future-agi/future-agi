import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import { Stack, TextField, Typography } from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useAgentVersion, useChangeVersion } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { LabeledField } from "./bits";
import CurrentVersionShell from "./CurrentVersionShell";

const nextLabel = (agent) => `v${(agent.versionCount || 0) + 1}`;

/**
 * A new version of a built-in agent from an edited prompt. The same prompt
 * again makes no version: the server answers with the one that already stands
 * for it.
 */
function AddVersionForm({ agent, open, startPrompt, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agent.id);
  const [prompt, setPrompt] = useState(startPrompt);
  const next = nextLabel(agent);

  const submit = () => {
    change.mutate(
      { kind: "manual", prompt },
      {
        onSuccess: (data) => {
          onClose();
          const label = data.version.version_name_display;
          enqueueSnackbar(
            data.up_to_date
              ? `${label} already has this prompt`
              : `${label} created from the new prompt`,
            { variant: data.up_to_date ? "info" : "success" },
          );
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      isLoading={change.isPending}
      title={`Add version ${next}`}
      actionBtnTitle={`Save as ${next}`}
      isValid={!!prompt.trim()}
      onSubmit={submit}
      modalWidth="720px"
    >
      <Stack spacing={1.5}>
        <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
          Edit the prompt as it now stands. The new version is checked, and then
          you can set it as production.
        </Typography>
        <LabeledField id="add-version-prompt" label="Prompt" required>
          <TextField
            id="add-version-prompt"
            size="small"
            fullWidth
            multiline
            minRows={6}
            maxRows={14}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            inputProps={{ "data-testid": "add-version-prompt" }}
          />
        </LabeledField>
      </Stack>
    </ModalWrapper>
  );
}

AddVersionForm.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  startPrompt: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default function AddVersionDialog({ agent, open, onClose }) {
  const version = useAgentVersion(agent.id, agent.activeVersionId);
  if (agent.activeVersionId && (version.isLoading || version.isError)) {
    return (
      <CurrentVersionShell
        title={`Add version ${nextLabel(agent)}`}
        isError={version.isError}
        onClose={onClose}
      />
    );
  }
  const startPrompt = agent.activeVersionId
    ? version.data?.config?.description || ""
    : agent.description || "";
  return (
    <AddVersionForm
      key={agent.activeVersionId || "none"}
      agent={agent}
      open={open}
      startPrompt={startPrompt}
      onClose={onClose}
    />
  );
}

AddVersionDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
