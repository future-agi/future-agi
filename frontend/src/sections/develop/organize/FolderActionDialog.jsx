// Rename, move or remove a virtual folder (W9-SCOPE §5 A6): one PUT per
// dataset in the folder's subtree (a prefix rewrite), in sequence, with
// progress; a folder can't move into its own subfolder; Remove moves its
// datasets up to the parent. The toast offers Undo: each dataset back in
// its own previous folder.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import {
  invalidateDatasetLists,
  saveInSequence,
  sequenceToast,
} from "src/api/develop/dataset-org";
import { leafOf, parentOf, removeFolderCopy, undoChanges } from "./folderTree";
import { doneMessage, folderPlan, folderTarget } from "./folderActions";

const TITLES = {
  rename: "Rename folder",
  move: "Move folder",
  remove: "Remove folder",
};

export default function FolderActionDialog({
  open,
  action,
  path,
  datasets,
  onClose,
  onDone,
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState(null);
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!open) return;
    setValue(action === "rename" ? leafOf(path) : parentOf(path));
    setError(null);
    setProgress(null);
  }, [open, action, path]);
  const plan = useMemo(
    () => folderPlan(action, datasets, path, value),
    [action, datasets, path, value],
  );
  const byId = useMemo(
    () => Object.fromEntries((datasets ?? []).map((d) => [d.id, d])),
    [datasets],
  );

  const submit = async () => {
    if (plan.problem) return setError(plan.problem);
    setError(null);
    const to = Object.fromEntries(plan.changes.map((c) => [c.id, c.to]));
    let done = 0;
    setProgress({ done, total: plan.changes.length });
    const result = await saveInSequence(
      plan.changes.map((c) => ({ id: c.id, name: byId[c.id]?.name })),
      (d) => {
        done += 1;
        setProgress({ done, total: plan.changes.length });
        return { folder: to[d.id] };
      },
    );
    invalidateDatasetLists(queryClient);
    setProgress(null);
    const toast = result.failed.length
      ? sequenceToast(result, "Moved")
      : {
          variant: "success",
          message: doneMessage(action, path, value, result.saved.length),
        };
    if (!result.saved.length) {
      setError(result.failed[0]?.error ?? toast.message);
      return undefined;
    }
    const savedIds = new Set(result.saved.map((d) => d.id));
    const back = undoChanges(plan.changes.filter((c) => savedIds.has(c.id)));
    onDone?.({
      ...toast,
      undo: async () => {
        const res = await saveInSequence(
          back.map((c) => ({ id: c.id, name: byId[c.id]?.name, to: c.to })),
          (d) => ({ folder: d.to }),
        );
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
          {`${TITLES[action] ?? "Folder"} ${path}`}
        </Typography>
      </DialogTitle>
      <DialogContent>
        {action === "remove" ? (
          <Typography typography="s1">
            {removeFolderCopy(datasets, path)}
          </Typography>
        ) : (
          <TextField
            autoFocus
            fullWidth
            size="small"
            label={
              action === "rename" ? "New name" : "Move into (empty = top level)"
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            helperText={
              action === "move" && !plan.problem
                ? `Becomes ${folderTarget(action, path, value)}`
                : " "
            }
            sx={{ mt: 1 }}
          />
        )}
        {(error || (plan.problem && value !== "" && action !== "remove")) && (
          <Typography
            role="alert"
            typography="s2"
            color="error.main"
            sx={{ mt: 1 }}
          >
            {error ?? plan.problem}
          </Typography>
        )}
        {progress && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mt: 1 }}
            aria-live="polite"
          >
            {`Saving ${progress.done} of ${progress.total}…`}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={Boolean(progress)}
          color={action === "remove" ? "error" : "primary"}
        >
          {action === "remove"
            ? "Remove folder"
            : action === "rename"
              ? "Rename"
              : "Move"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

FolderActionDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  action: PropTypes.oneOf(["rename", "move", "remove"]).isRequired,
  path: PropTypes.string.isRequired,
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      folder: PropTypes.string,
    }),
  ).isRequired,
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
