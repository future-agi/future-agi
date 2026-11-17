import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";

import { afterFailure, secretValueProblem } from "../lib/secretForm";
import SecretValueField from "./SecretValueField";
import { useRotateSecret } from "./useSecrets";

/**
 * Replace a secret's value (EE "rotate"). The vault swaps the value in one
 * step when it confirms the save: until then the old value is in force, and a
 * refused save changes nothing. Revisions already running keep the value they
 * started with; every activation (deploy, rollback, token rotation) resolves
 * the vault again (EE project-deployments/pipeline/activate.ts).
 */
export default function ReplaceSecretDialog({ secret: open, onClose }) {
  // The last secret shown stays on screen while the dialog fades out.
  const last = useRef(open);
  if (open) last.current = open;
  const secret = open ?? last.current;
  const [value, setValue] = useState("");
  const [tried, setTried] = useState(false);
  const rotate = useRotateSecret();
  const problem = secretValueProblem(value);

  const close = () => {
    if (rotate.isPending) return;
    setValue("");
    setTried(false);
    rotate.reset();
    onClose();
  };
  const save = (event) => {
    event.preventDefault();
    setTried(true);
    if (problem || rotate.isPending) return;
    rotate.mutate(
      { id: secret.credentialId, value },
      {
        onSuccess: () => {
          setValue("");
          setTried(false);
          rotate.reset();
          onClose(true);
        },
      },
    );
  };

  return (
    <Dialog open={Boolean(open)} onClose={close} maxWidth="xs" fullWidth>
      <form onSubmit={save} noValidate data-testid="replace-secret-form">
        <DialogTitle sx={{ typography: "m3", overflowWrap: "anywhere" }}>
          Replace the value of {secret?.name}
        </DialogTitle>
        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 2 }}
        >
          <Typography typography="s2" color="text.secondary">
            The current value stays in force until the new one is saved.
            Revisions already running keep the value they started with; a
            revision reads the new one when it next starts (a deploy, a rollback
            or an access-token rotation).
          </Typography>
          <SecretValueField
            label="New value"
            testId="replace-secret-value"
            value={value}
            disabled={rotate.isPending}
            onChange={setValue}
            error={tried ? problem : null}
          />
          {rotate.error && (
            <Alert
              severity="error"
              variant="outlined"
              data-testid="replace-secret-error"
            >
              {rotate.error.message}{" "}
              {afterFailure(rotate.error, {
                refused: "The current value is still in force.",
                unknown:
                  "If it did not go through, the current value is still in force. Saving again is safe.",
              })}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button color="inherit" onClick={close} disabled={rotate.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="contained"
            disabled={rotate.isPending}
            data-testid="confirm-replace-secret"
          >
            {rotate.isPending ? "Saving…" : "Replace value"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

ReplaceSecretDialog.propTypes = {
  secret: PropTypes.shape({
    credentialId: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
  }),
  onClose: PropTypes.func.isRequired,
};
