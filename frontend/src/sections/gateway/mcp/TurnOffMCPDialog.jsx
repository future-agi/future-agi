import React, { useId } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";

// Asks before MCP is turned off for the organization. The page saves the
// same org config write "Turn on MCP" makes, with the off value. A failed
// save keeps this dialog open with the API's message, and MCP stays on.
export default function TurnOffMCPDialog({
  open,
  pending = false,
  error,
  onCancel,
  onConfirm,
}) {
  const titleId = useId();
  const bodyId = useId();
  return (
    <Dialog
      open={open}
      onClose={pending ? undefined : onCancel}
      disableEscapeKeyDown={pending}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle id={titleId}>Turn off MCP?</DialogTitle>
      <DialogContent>
        <DialogContentText id={bodyId}>
          Agents and MCP clients lose their tools until MCP is turned on again.
          Servers, credentials and virtual servers are kept.
        </DialogContentText>
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={onConfirm}
          disabled={pending}
        >
          Turn off
        </Button>
      </DialogActions>
    </Dialog>
  );
}

TurnOffMCPDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  pending: PropTypes.bool,
  error: PropTypes.string,
  onCancel: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};
