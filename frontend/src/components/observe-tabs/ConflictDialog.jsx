import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";

export default function ConflictDialog({ open, viewName, onClose, onReload, onSaveCopy }) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const close = () => { setConfirmDiscard(false); onClose(); };
  return <Dialog open={open} onClose={close} maxWidth="xs" fullWidth aria-labelledby="conflict-view-title">
    <DialogTitle id="conflict-view-title">{confirmDiscard ? "Discard your unsaved changes?" : "This view changed in another browser"}</DialogTitle>
    <DialogContent><DialogContentText>{confirmDiscard
      ? "Reloading the latest saved version will discard your changes. This cannot be undone."
      : `“${viewName}” was saved from another browser after you opened it. Your unsaved changes here are kept until you choose what to do.`}</DialogContentText></DialogContent>
    <DialogActions>
      <Button onClick={close}>Cancel</Button>
      {!confirmDiscard && <Button onClick={() => { setConfirmDiscard(false); onSaveCopy(); }}>Save as copy</Button>}
      <Button variant="contained" onClick={() => {
        if (confirmDiscard) { setConfirmDiscard(false); onReload(); }
        else setConfirmDiscard(true);
      }}>{confirmDiscard ? "Discard and reload" : "Reload latest (discards your changes)"}</Button>
    </DialogActions>
  </Dialog>;
}
ConflictDialog.propTypes = { open: PropTypes.bool.isRequired, viewName: PropTypes.string, onClose: PropTypes.func.isRequired, onReload: PropTypes.func.isRequired, onSaveCopy: PropTypes.func.isRequired };
