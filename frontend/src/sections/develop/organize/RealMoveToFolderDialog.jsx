import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
} from "@mui/material";
import {
  folderErrorSentence,
  moveDatasetsToFolder,
  useRealFolderInventory,
  useRefreshFolders,
} from "./realFolderApi";

export default function RealMoveToFolderDialog({ datasets, onClose, onDone }) {
  const [target, setTarget] = useState(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inventory = useRealFolderInventory(true);
  const refresh = useRefreshFolders();
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await moveDatasetsToFolder(datasets, target);
      await refresh();
      onDone?.({
        message: `Moved ${datasets.length} dataset${datasets.length === 1 ? "" : "s"}`,
        variant: "success",
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
      aria-labelledby="move-folder-title"
    >
      <DialogTitle id="move-folder-title">Move to folder</DialogTitle>
      <DialogContent>
        {inventory.isError ? (
          <Alert severity="error">
            {folderErrorSentence(inventory.error)}{" "}
            <Button onClick={() => inventory.refetch()}>Retry</Button>
          </Alert>
        ) : (
          <List aria-label="Destination folders">
            {[
              { id: null, path: "No folder" },
              ...(inventory.data?.folders ?? []),
            ].map((folder) => (
              <ListItemButton
                key={folder.id ?? "none"}
                selected={target === folder.id}
                aria-selected={target === folder.id}
                disabled={busy || inventory.isLoading}
                onClick={() => setTarget(folder.id)}
              >
                <ListItemText primary={folder.path} />
              </ListItemButton>
            ))}
          </List>
        )}
        {error && <Alert severity="error">{error}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={
            busy ||
            target === undefined ||
            inventory.isLoading ||
            inventory.isError
          }
        >
          Move
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RealMoveToFolderDialog.propTypes = {
  datasets: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string }))
    .isRequired,
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
