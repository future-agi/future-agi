import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { hubPaths } from "../constants";
import { useEntryAction } from "../api/queries";
import { splitRef } from "../lib/format";
import { useInstallPath } from "../lib/installs";

// Move or rename an entry (SPEC §3.4): likes, installs, picks and versions
// move with it; the old reference stays reserved and redirects. Installs are
// named only once entries can be installed (lib/installs.js).
export default function MoveDialog({ open, onClose, entry }) {
  const installPath = useInstallPath();
  const [toPublisher, setToPublisher] = useState("");
  const [toName, setToName] = useState("");
  const [note, setNote] = useState("");
  const navigate = useNavigate();
  const move = useEntryAction(entry.publisher.handle, entry.name);
  const submit = async () => {
    let result;
    try {
      result = await move.mutateAsync({
        action: "move",
        toPublisher: toPublisher.trim() || undefined,
        toName: toName.trim() || undefined,
        note: note.trim(),
      });
    } catch {
      return; // The dialog shows move.error.
    }
    onClose();
    const next = splitRef(result?.ref);
    if (next.publisher && next.name) navigate(hubPaths.entry(next.publisher, next.name), { replace: true });
  };
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Move or rename</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}>
        <Typography typography="s2" color="text.secondary">
          {`${installPath ? "Likes, installs, picks" : "Likes, picks"} and versions move with ${entry.ref}. The old reference redirects forever and can never be reused.`}
        </Typography>
        <TextField size="small" label="New publisher (optional)" value={toPublisher} onChange={(e) => setToPublisher(e.target.value)} />
        <TextField size="small" label="New name (optional)" value={toName} onChange={(e) => setToName(e.target.value)} />
        <TextField
          label="Note (the publisher's request; for a transfer, the receiving owner's consent)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          multiline
          minRows={3}
        />
        {move.isError && <Alert severity="error">{move.error.message}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={move.isPending || !note.trim() || (!toPublisher.trim() && !toName.trim())}
          onClick={submit}
        >
          Move
        </Button>
      </DialogActions>
    </Dialog>
  );
}

MoveDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  entry: PropTypes.object.isRequired,
};
