import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import axios from "src/utils/axios";
import {
  FOLDERS_URL,
  folderUrl,
  folderErrorSentence,
  useRefreshFolders,
} from "./realFolderApi";

const TITLES = {
  create: "Create folder",
  rename: "Rename folder",
  move: "Move folder",
  remove: "Remove folder",
};

export default function RealFolderActionDialog({
  action,
  folder,
  onClose,
  onDone,
}) {
  const [path, setPath] = useState(folder?.path ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useRefreshFolders();
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (action === "create") await axios.post(FOLDERS_URL, { path });
      else if (action === "remove") await axios.delete(folderUrl(folder.id));
      else await axios.patch(folderUrl(folder.id), { path });
      await refresh();
      onDone?.({
        message:
          action === "remove"
            ? "Folder removed. Datasets kept."
            : "Folder saved",
        variant: "success",
        folderChange: { action, from: folder?.path, to: path },
      });
      onClose();
    } catch (err) {
      setError(folderErrorSentence(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onClose={busy ? undefined : onClose}
      fullWidth
      maxWidth="xs"
      aria-labelledby="folder-action-title"
    >
      <form onSubmit={submit}>
        <DialogTitle id="folder-action-title">{TITLES[action]}</DialogTitle>
        <DialogContent>
          {action === "remove" ? (
            <Typography>
              Remove {folder.path} and its subfolders? Datasets will move to No
              folder. No datasets will be deleted.
            </Typography>
          ) : (
            <TextField
              autoFocus
              fullWidth
              margin="dense"
              label="Folder path"
              value={path}
              disabled={busy}
              onChange={(event) => setPath(event.target.value)}
              helperText="Use / for subfolders, for example support/billing. Empty folders are kept."
            />
          )}
          {error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="contained"
            color={action === "remove" ? "error" : "primary"}
            disabled={busy || (action !== "remove" && !path.trim())}
          >
            {action === "remove" ? "Remove folder" : "Save folder"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

RealFolderActionDialog.propTypes = {
  action: PropTypes.oneOf(["create", "rename", "move", "remove"]).isRequired,
  folder: PropTypes.shape({ id: PropTypes.string, path: PropTypes.string }),
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
