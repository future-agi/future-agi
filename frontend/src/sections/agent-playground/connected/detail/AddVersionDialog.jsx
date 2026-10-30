import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import { Button, TextField, Typography } from "@mui/material";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useChangeVersion } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";

/**
 * A new version of a built-in agent from an edited prompt. The same prompt
 * again makes no version: the server answers with the one that already stands
 * for it.
 */
export default function AddVersionDialog({ agent, open, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agent.id);
  const [prompt, setPrompt] = useState(agent.description || "");
  const next = `v${(agent.versionCount || 0) + 1}`;

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
    <ConfirmDialog
      open={open}
      onClose={() => (change.isPending ? null : onClose())}
      closeDisabled={change.isPending}
      title={`Add version ${next}`}
      content={
        <>
          <Typography
            sx={{ typography: "s2_1", color: "text.secondary", mb: 1.5 }}
          >
            Edit the prompt; the new version is checked and then becomes the one
            in production.
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={6}
            label="Prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            inputProps={{ "data-testid": "add-version-prompt" }}
          />
        </>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={change.isPending || !prompt.trim()}
          onClick={submit}
        >
          {`Save as ${next}`}
        </Button>
      }
    />
  );
}

AddVersionDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
