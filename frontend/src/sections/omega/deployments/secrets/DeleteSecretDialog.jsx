import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { afterFailure } from "../lib/secretForm";
import { useRevokeSecret } from "./useSecrets";

/**
 * Delete (EE "revoke") a secret after its name is typed. Running revisions
 * keep the value they started with; any later start of a deployment that
 * picks this name fails loudly with EE's own message, and the revision that
 * was serving keeps serving.
 */
export default function DeleteSecretDialog({ secret: open, onClose }) {
  // The last secret shown stays on screen while the dialog fades out.
  const last = useRef(open);
  if (open) last.current = open;
  const secret = open ?? last.current;
  const [typed, setTyped] = useState("");
  const revoke = useRevokeSecret();
  const used = secret?.bindingCount ?? 0;

  const close = () => {
    if (revoke.isPending) return;
    setTyped("");
    revoke.reset();
    onClose();
  };
  const confirm = () =>
    revoke.mutate(
      { id: secret.credentialId },
      {
        onSuccess: () => {
          setTyped("");
          revoke.reset();
          onClose(true);
        },
      },
    );

  return (
    <Dialog open={Boolean(open)} onClose={close} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m3", overflowWrap: "anywhere" }}>
        Delete {secret?.name}?
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <Typography typography="s1" color="text.secondary">
          {used > 0
            ? `It is used in ${used === 1 ? "1 place" : `${used} places`}. Revisions running now keep their value, but the next start of anything that picks it (a deploy, rollback or token rotation) fails until another secret is picked.`
            : "Nothing uses it yet."}{" "}
          This cannot be undone. Type the name to confirm.
        </Typography>
        <TextField
          size="small"
          autoFocus
          value={typed}
          disabled={revoke.isPending}
          onChange={(e) => setTyped(e.target.value)}
          inputProps={{
            "aria-label": "Secret name to confirm",
            "data-testid": "delete-secret-confirm-name",
            spellCheck: false,
            autoComplete: "off",
          }}
          InputProps={{ sx: { fontFamily: "monospace" } }}
        />
        {revoke.error && (
          <Alert
            severity="error"
            variant="outlined"
            data-testid="delete-secret-error"
          >
            {revoke.error.message}{" "}
            {afterFailure(revoke.error, {
              refused: "The secret is unchanged.",
              unknown:
                "If it did not go through, the secret is unchanged. Deleting again is safe.",
            })}
          </Alert>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={close} disabled={revoke.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="error"
          disabled={typed !== secret?.name || revoke.isPending}
          onClick={confirm}
          data-testid="confirm-delete-secret"
        >
          {revoke.isPending ? "Deleting…" : "Delete"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

DeleteSecretDialog.propTypes = {
  secret: PropTypes.shape({
    credentialId: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    bindingCount: PropTypes.number,
  }),
  onClose: PropTypes.func.isRequired,
};
