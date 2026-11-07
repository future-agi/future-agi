import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, DialogActions, DialogContent, DialogTitle, Stack, Typography } from "@mui/material";

import { ClosableDialog } from "src/components/custom-dialog";

export default function DraftConflictDialog({ open, onClose, refetch, onReload, onOverwrite, description = "Your edits have not been saved. Reload the server draft, or replace it with your files using its current revision." }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const resolve = async (overwrite) => {
    setBusy(true);
    setFailure(null);
    try {
      const result = await refetch();
      if (result.isError) throw result.error;
      if (overwrite) {
        const saved = await onOverwrite(result.data?.revision ?? 0);
        if (!saved) {
          setFailure("Couldn't overwrite this draft. Reload the latest revision and try again.");
          return;
        }
      } else {
        onReload(result.data);
      }
      onClose();
    } catch {
      setFailure("Couldn't load the current draft. Your edits are still here. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <ClosableDialog open={open} onClose={() => { if (!busy) onClose(); }} disableEscapeKeyDown={busy} maxWidth="sm" fullWidth>
      <DialogTitle>Draft changed in another tab</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Typography variant="s1">{description}</Typography>
          {failure && <Alert severity="error">{failure}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button variant="outlined" disabled={busy} onClick={() => resolve(false)}>Reload theirs</Button>
        <Button variant="contained" disabled={busy} onClick={() => resolve(true)}>Overwrite with mine</Button>
      </DialogActions>
    </ClosableDialog>
  );
}

DraftConflictDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  refetch: PropTypes.func.isRequired,
  onReload: PropTypes.func.isRequired,
  onOverwrite: PropTypes.func.isRequired,
  description: PropTypes.string,
};
