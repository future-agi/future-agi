// Move to folder… (W9-SCOPE §5 A6): pick an existing folder from the tree
// or type a **New folder** path; **Move** sends one PUT per dataset. The
// result carries an Undo that puts each dataset back in its own previous
// folder. Folders are virtual: one exists while it holds a dataset.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import {
  invalidateDatasetLists,
  saveInSequence,
  sequenceToast,
  useSaveDatasetOrg,
} from "src/api/develop/dataset-org";
import { folderProblem, normalizeFolder } from "./orgRules";

export const NEW_FOLDER_HELP =
  "A folder exists while it holds at least one dataset.";

const plural = (n) => `${n} dataset${n === 1 ? "" : "s"}`;

/**
 * The plan for moving datasets to `to`: who moves, and the undo that puts
 * each one back. Pure.
 */
export function planMove(datasets, to) {
  const target = normalizeFolder(to);
  const moving = (datasets ?? []).filter(
    (d) => normalizeFolder(d.folder) !== target,
  );
  const previous = Object.fromEntries(
    moving.map((d) => [d.id, normalizeFolder(d.folder)]),
  );
  return { target, moving, previous };
}

/** `Move 2 datasets to support` (drag label and button). */
export const moveLabel = (n, to) =>
  `Move ${plural(n)} ${to ? `to ${to}` : "out of any folder"}`;

export default function MoveToFolderDialog({
  open,
  datasets,
  folders,
  onClose,
  onDone,
}) {
  const [picked, setPicked] = useState(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState(null);
  const save = useSaveDatasetOrg();
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!open) return;
    setPicked(null);
    setPath("");
    setError(null);
  }, [open]);
  const target = path.trim() ? path : picked ?? null;
  const plan = useMemo(
    () => (target === null ? null : planMove(datasets, target)),
    [datasets, target],
  );
  const submit = async () => {
    if (target === null) return setError("Pick a folder or type a new one.");
    const problem = folderProblem(target);
    if (problem) return setError(problem);
    if (!plan.moving.length)
      return setError(
        `${datasets.length === 1 ? "It is" : "They are"} already ${plan.target ? `in ${plan.target}` : "outside any folder"}.`,
      );
    setError(null);
    const result = await save.mutateAsync({
      datasets: plan.moving,
      patchFor: () => ({ folder: plan.target }),
    });
    const toast = sequenceToast(
      result,
      "Moved",
      plan.target ? ` to ${plan.target}` : " out of any folder",
    );
    if (!result.saved.length) {
      setError(result.failed[0]?.error ?? toast.message);
      return undefined;
    }
    const back = result.saved;
    onDone?.({
      ...toast,
      // Each dataset back in its own previous folder.
      undo: async () => {
        const res = await saveInSequence(back, (d) => ({
          folder: plan.previous[d.id],
        }));
        invalidateDatasetLists(queryClient);
        return res;
      },
    });
    onClose();
    return undefined;
  };
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle sx={{ pb: 1 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {`Move ${plural(datasets?.length ?? 0)} to a folder`}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <List
          dense
          disablePadding
          aria-label="Folders"
          sx={{ maxHeight: 260, overflowY: "auto", mb: 1.5 }}
        >
          <ListItemButton
            selected={picked === "" && !path.trim()}
            onClick={() => {
              setPicked("");
              setPath("");
            }}
          >
            <ListItemText
              primary="No folder"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </ListItemButton>
          {(folders ?? []).map((f) => {
            const depth = f.path.split("/").length - 1;
            return (
              <ListItemButton
                key={f.path}
                selected={picked === f.path && !path.trim()}
                onClick={() => {
                  setPicked(f.path);
                  setPath("");
                }}
                sx={{ pl: 2 + depth * 2 }}
              >
                <ListItemText
                  primary={f.path.split("/").pop()}
                  secondary={`${f.total}`}
                  primaryTypographyProps={{ typography: "s1" }}
                  secondaryTypographyProps={{
                    typography: "s3",
                    component: "span",
                    sx: { ml: 1 },
                  }}
                  sx={{ display: "flex", alignItems: "baseline" }}
                />
              </ListItemButton>
            );
          })}
        </List>
        <TextField
          fullWidth
          size="small"
          label="New folder"
          placeholder="support/billing"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          helperText={NEW_FOLDER_HELP}
        />
        {error && (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1 }}
            role="alert"
          >
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button variant="outlined" color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="contained" onClick={submit} disabled={save.isPending}>
          Move
        </Button>
      </DialogActions>
    </Dialog>
  );
}

MoveToFolderDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      folder: PropTypes.string,
    }),
  ).isRequired,
  folders: PropTypes.arrayOf(
    PropTypes.shape({
      path: PropTypes.string,
      count: PropTypes.number,
      total: PropTypes.number,
    }),
  ),
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
