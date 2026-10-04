import React from "react";
import PropTypes from "prop-types";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";

export default function ShareViewDialog({ view, projectName = "this project", dirty = false, pending = false, error, onClose, onConfirm, onSaveFirst }) {
  const shared = view?.visibility === "project";
  return <Dialog open={Boolean(view)} onClose={pending ? undefined : onClose} maxWidth="xs" fullWidth aria-labelledby="share-view-title">
    <DialogTitle id="share-view-title">{shared ? `Make “${view?.name}” personal?` : `Share “${view?.name}” with ${projectName}?`}</DialogTitle>
    <DialogContent>
      <DialogContentText>{shared
        ? `Members of ${projectName} will lose access to this view the next time they refresh. Their current screens are not changed.`
        : dirty
          ? "You have unsaved changes. Sharing publishes the last saved version of this view, not your current edits."
          : `Members of ${projectName} will see this view's saved filters and display settings. Sharing does not change what data they can access.`}</DialogContentText>
      {dirty && !shared && <Alert severity="info" sx={{ mt: 2 }}>Sharing does not change data access.{view?.updated_at && ` Saved version: ${new Date(view.updated_at).toLocaleString()}`}</Alert>}
      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
    </DialogContent>
    <DialogActions>
      <Button onClick={onClose} disabled={pending}>Cancel</Button>
      {dirty && !shared && <Button onClick={onSaveFirst ?? onClose} disabled={pending}>Save changes first</Button>}
      <Button onClick={onConfirm} variant="contained" disabled={pending}>{shared ? "Make personal" : dirty ? "Share saved version" : "Share"}</Button>
    </DialogActions>
  </Dialog>;
}
ShareViewDialog.propTypes = { view: PropTypes.object, projectName: PropTypes.string, dirty: PropTypes.bool, pending: PropTypes.bool, error: PropTypes.string, onClose: PropTypes.func.isRequired, onConfirm: PropTypes.func.isRequired, onSaveFirst: PropTypes.func };
