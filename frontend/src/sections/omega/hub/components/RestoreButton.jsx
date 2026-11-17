import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";

import { useEntryAction } from "../api/queries";

// On a tombstone, maintainers can restore the entry (SPEC §3.3) with a note.
export default function RestoreButton({ publisher, name }) {
  const [note, setNote] = useState("");
  const restore = useEntryAction(publisher, name);
  return (
    <Box sx={{ maxWidth: 560, mx: "auto", width: "100%", display: "flex", gap: 1, alignItems: "flex-start", flexDirection: "column" }}>
      <TextField size="small" fullWidth label="Private note for the restore" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button
        variant="outlined"
        size="small"
        disabled={!note.trim() || restore.isPending}
        onClick={() => restore.mutate({ action: "restore", note: note.trim() })}
        data-testid="hub-restore-button"
      >
        Restore this entry
      </Button>
      {restore.isError && <Alert severity="error">{restore.error.message}</Alert>}
    </Box>
  );
}

RestoreButton.propTypes = { publisher: PropTypes.string.isRequired, name: PropTypes.string.isRequired };
