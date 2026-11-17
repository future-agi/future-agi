import React, { useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import Alert from "@mui/material/Alert";

import { usePathMutation } from "../api/queries";
import SandboxIcon from "../components/SandboxIcon";
import { fileErrorMessage, joinPath, parentOf } from "../lib/files";
import FileNameDialog from "./FileNameDialog";
import MoveDialog from "./MoveDialog";

/**
 * The actions on one row of the Files list: Download (files), Rename, Move to
 * folder and Delete (when the viewer may change files). Delete always asks
 * first, and says when a folder takes everything in it along. Rename and Move
 * are one sandbox move (EE's POST .../move), to a new name in the same folder
 * or to the same name in another folder.
 */
export default function FileRowMenu({
  sandboxId,
  entry,
  path,
  canEdit,
  moveTargets = [],
  listFolder,
  onDownload,
  onChanged,
}) {
  const [anchor, setAnchor] = useState(null);
  const [dialog, setDialog] = useState(null);
  const close = () => setAnchor(null);
  const move = usePathMutation(
    sandboxId,
    path,
    (api, to) => api.movePath(sandboxId, path, to),
    {
      onSuccess: (moved) => {
        setDialog(null);
        onChanged?.({ kind: "moved", from: path, to: moved.to });
      },
    },
  );
  const remove = usePathMutation(
    sandboxId,
    path,
    (api) =>
      api.deletePath(sandboxId, path, { recursive: Boolean(entry.directory) }),
    {
      onSuccess: () => {
        setDialog(null);
        onChanged?.({ kind: "deleted", path });
      },
    },
  );
  if (!canEdit && entry.directory) return null;
  const open = (kind) => {
    close();
    move.reset();
    remove.reset();
    setDialog(kind);
  };
  return (
    <>
      <IconButton
        size="small"
        aria-label={`Actions for ${entry.name}`}
        data-testid="file-actions"
        onClick={(event) => {
          event.stopPropagation();
          setAnchor(event.currentTarget);
        }}
      >
        <SandboxIcon name="more" size={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        onClick={(event) => event.stopPropagation()}
      >
        {!entry.directory && (
          <MenuItem
            onClick={() => {
              close();
              onDownload(path);
            }}
            data-testid="file-action-download"
          >
            <ListItemIcon>
              <SandboxIcon name="download" size={16} />
            </ListItemIcon>
            Download
          </MenuItem>
        )}
        {canEdit && (
          <MenuItem
            onClick={() => open("rename")}
            data-testid="file-action-rename"
          >
            <ListItemIcon>
              <SandboxIcon name="edit" size={16} />
            </ListItemIcon>
            Rename
          </MenuItem>
        )}
        {canEdit && (
          <MenuItem onClick={() => open("move")} data-testid="file-action-move">
            <ListItemIcon>
              <SandboxIcon name="folder" size={16} />
            </ListItemIcon>
            Move to folder…
          </MenuItem>
        )}
        {canEdit && (
          <MenuItem
            onClick={() => open("delete")}
            sx={{ color: "error.main" }}
            data-testid="file-action-delete"
          >
            <ListItemIcon sx={{ color: "inherit" }}>
              <SandboxIcon name="trash" size={16} />
            </ListItemIcon>
            Delete
          </MenuItem>
        )}
      </Menu>
      <FileNameDialog
        open={dialog === "rename"}
        title={`Rename ${entry.directory ? "folder" : "file"}`}
        confirmLabel="Rename"
        initial={entry.name}
        pending={move.isPending}
        error={move.error}
        onClose={() => setDialog(null)}
        onSubmit={(name) => move.mutate(joinPath(parentOf(path), name))}
      />
      <MoveDialog
        open={dialog === "move"}
        path={path}
        directory={Boolean(entry.directory)}
        folders={moveTargets}
        pending={move.isPending}
        error={move.error}
        listFolder={listFolder}
        onClose={() => setDialog(null)}
        onSubmit={(folder) => move.mutate(joinPath(folder, entry.name))}
      />
      <Dialog
        open={dialog === "delete"}
        onClose={remove.isPending ? undefined : () => setDialog(null)}
        maxWidth="xs"
      >
        <DialogTitle sx={{ typography: "m3", overflowWrap: "anywhere" }}>
          Delete {entry.name}?
        </DialogTitle>
        <DialogContent>
          <Typography typography="s1" color="text.secondary">
            {entry.directory
              ? "The folder and everything in it are removed from the sandbox. Snapshots already taken are not changed."
              : "The file is removed from the sandbox. Snapshots already taken are not changed."}
          </Typography>
          {remove.error && (
            <Alert severity="error" sx={{ mt: 1.5 }}>
              {fileErrorMessage(remove.error)}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button
            color="inherit"
            onClick={() => setDialog(null)}
            disabled={remove.isPending}
          >
            Cancel
          </Button>
          <LoadingButton
            color="error"
            variant="contained"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
            data-testid="file-delete-confirm"
          >
            Delete
          </LoadingButton>
        </DialogActions>
      </Dialog>
    </>
  );
}

FileRowMenu.propTypes = {
  sandboxId: PropTypes.string.isRequired,
  entry: PropTypes.shape({
    name: PropTypes.string.isRequired,
    directory: PropTypes.bool,
  }).isRequired,
  path: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  // Folders Move offers, relative to /workspace ("" is /workspace itself).
  moveTargets: PropTypes.arrayOf(PropTypes.string),
  // Reads a folder's list (listFiles), for Move's look at its folder.
  listFolder: PropTypes.func,
  onDownload: PropTypes.func.isRequired,
  onChanged: PropTypes.func,
};
