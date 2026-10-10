import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";
import { useDeleteSavedView, classifySavedViewError } from "src/api/project/saved-views";
import { getRequestErrorMessage } from "src/utils/errorUtils";

export default function DeleteViewDialog({ view, projectId, projectName = "this project", onClose, onDeleted }) {
  const { mutate, isPending } = useDeleteSavedView(projectId);
  const [error, setError] = useState(null);
  const shared = view?.visibility === "project";
  const remove = () => {
    if (!view?.can_delete || isPending) return;
    setError(null);
    mutate({ id: view.id, expected_revision: view.revision }, {
      onSuccess: () => { onDeleted?.(view.id); onClose(); },
      onError: (err) => {
        const { kind } = classifySavedViewError(err);
        setError(kind === "conflict" ? "This view changed in another browser. Refresh before deleting."
          : kind === "unavailable_record" ? "This view is unavailable"
          : getRequestErrorMessage(err, "Could not delete this view. Please retry."));
      },
    });
  };
  return <Dialog open={Boolean(view)} onClose={isPending ? undefined : onClose} maxWidth="xs" fullWidth aria-labelledby="delete-view-title">
    <DialogTitle id="delete-view-title">Delete “{view?.name}”?</DialogTitle>
    <DialogContent>
      <DialogContentText>This view will be permanently removed{shared ? ` for everyone in ${projectName}` : ""}. This action cannot be undone.</DialogContentText>
      {shared && <Alert severity="warning" sx={{ mt: 2 }}>Shared with {projectName} — teammates will lose this view.</Alert>}
      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
    </DialogContent>
    <DialogActions>
      <Button onClick={onClose} disabled={isPending}>Cancel</Button>
      <Button onClick={remove} variant="contained" color="error" disabled={isPending || !view?.can_delete}>{isPending ? "Deleting…" : error ? "Retry" : "Delete"}</Button>
    </DialogActions>
  </Dialog>;
}
DeleteViewDialog.propTypes = { view: PropTypes.object, projectId: PropTypes.string, projectName: PropTypes.string, onClose: PropTypes.func.isRequired, onDeleted: PropTypes.func };
