import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import LoadingButton from "@mui/lab/LoadingButton";

import { fileErrorMessage, nameProblem } from "../lib/files";

/**
 * Ask for one file or folder name (rename, new folder). The name is checked
 * here first; the sandbox's own refusal (for example "already exists") is
 * shown in the dialog, which stays open so the name can be changed.
 */
export default function FileNameDialog({
  open,
  title,
  confirmLabel,
  initial = "",
  pending = false,
  error = null,
  onSubmit,
  onClose,
}) {
  const [name, setName] = useState(initial);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (open) {
      setName(initial);
      setTouched(false);
    }
  }, [open, initial]);
  const problem = nameProblem(name);
  const unchanged = name.trim() === initial;
  return (
    <Dialog
      open={open}
      onClose={pending ? undefined : onClose}
      maxWidth="xs"
      fullWidth
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setTouched(true);
          if (!problem && !unchanged) onSubmit(name.trim());
        }}
      >
        <DialogTitle sx={{ typography: "m3" }}>{title}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onBlur={() => setTouched(true)}
            error={touched && Boolean(problem)}
            helperText={touched && problem ? problem : " "}
            inputProps={{
              "aria-label": "Name",
              "data-testid": "file-name-input",
            }}
            sx={{ mt: 0.5, "& input": { fontFamily: "monospace" } }}
          />
          {error && (
            <Alert severity="error" data-testid="file-name-error">
              {fileErrorMessage(error)}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <LoadingButton
            type="submit"
            variant="contained"
            loading={pending}
            disabled={Boolean(problem) || unchanged}
            data-testid="file-name-submit"
          >
            {confirmLabel}
          </LoadingButton>
        </DialogActions>
      </form>
    </Dialog>
  );
}

FileNameDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  confirmLabel: PropTypes.string.isRequired,
  initial: PropTypes.string,
  pending: PropTypes.bool,
  error: PropTypes.object,
  onSubmit: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
