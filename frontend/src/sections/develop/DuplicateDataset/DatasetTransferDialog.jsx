import { LoadingButton } from "@mui/lab";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  TextField,
  Typography as MuiTypography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { refusalSentence } from "src/sections/develop-detail/DataTab/Versions/classicWrites";
import axios, { endpoints } from "src/utils/axios";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useWorkspacesList } from "src/api/workspaces/list";
import {
  postDatasetTransfer,
  transferPlanNotes,
  transferPlanSentence,
  writableWorkspaces,
} from "./datasetTransferApi";

/**
 * The same MUI component, including this theme's legacy variants (typography.js).
 * Keep this type local: no change to other callers or MUI's global declarations.
 * @typedef {Omit<import("@mui/material").TypographyProps, "variant"> & {variant?: import("@mui/material").TypographyProps["variant"] | "m2" | "s2"}} TransferTypographyProps
 */
const Typography = /** @type {React.ComponentType<TransferTypographyProps>} */ (
  MuiTypography
);

/** @typedef {{at: number, asked: {name: string, toWorkspaceId: string | null}}} PreviewRequest */

/**
 * Duplicate on the real backend with the transfer capability: copy here
 * (the classic clone), or copy into another workspace of this organization
 * after an optional write-free preview. Moving is not offered: the backend
 * refuses every move between workspaces. History, split and experiment
 * options are not offered either: the backend has no versions yet.
 */
const DatasetTransferDialog = ({ open, onClose, refreshGrid, selected }) => {
  const queryClient = useQueryClient();
  // WorkspaceProvider supplies a two-ID callback; the context's default is a no-op.
  /** @type {{currentWorkspaceId: string | null, switchWorkspace: (newWorkspaceId: string, oldWorkspaceId?: string | null) => Promise<void>}} */
  const { currentWorkspaceId, switchWorkspace } = useWorkspace();
  const { data: workspaceRows, fetchNextPage } = useWorkspacesList({
    enabled: open,
  });
  const workspaces = useMemo(
    () => writableWorkspaces(workspaceRows),
    [workspaceRows],
  );
  // The workspace list comes 100 at a time and only its first page loads by
  // itself. While the dialog is open, load the remaining pages one after
  // another, so every workspace you can add datasets to is offered, whatever
  // page the list puts it on. A failing page ends the loading (the next
  // opening tries again); cancelRefetch: false waits for a list request
  // already in flight instead of cancelling it.
  const listArrived = workspaceRows !== undefined;
  useEffect(() => {
    if (!open || !listArrived) return undefined;
    let live = true;
    const loadNextPage = () =>
      fetchNextPage({ cancelRefetch: false }).then((list) => {
        if (live && list?.hasNextPage && !list.isError) loadNextPage();
      });
    loadNextPage();
    return () => {
      live = false;
    };
  }, [open, listArrived, fetchNextPage]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState(null);
  const [plan, setPlan] = useState(null);
  const [refusal, setRefusal] = useState(null);
  // Every option change, and closing, starts a new revision. A preview
  // answer is shown only while the options it was asked about are still on
  // screen; a late answer for earlier options is dropped.
  const revision = useRef(0);

  const datasetName = selected?.name ?? selected?.datasetName ?? "";
  const toWorkspace = target ?? currentWorkspaceId ?? null;
  const toName =
    workspaces.find((w) => w.id === toWorkspace)?.name ?? "this workspace";
  const crossing = Boolean(toWorkspace && toWorkspace !== currentWorkspaceId);
  const options = { toWorkspaceId: toWorkspace };

  const preview = useMutation({
    /** @param {PreviewRequest} variables */
    mutationFn: ({ asked }) =>
      postDatasetTransfer(selected?.id, { ...asked, dryRun: true }),
    onSuccess: (result, { at }) => {
      if (at !== revision.current) return;
      setRefusal(null);
      setPlan(result);
    },
    onError: (error, { at }) => {
      if (at !== revision.current) return;
      setPlan(null);
      setRefusal(refusalSentence(error, "The preview failed. Try again."));
    },
  });

  const handleClose = () => {
    revision.current += 1;
    setName("");
    setTarget(null);
    setPlan(null);
    setRefusal(null);
    preview.reset();
    onClose();
  };

  const submit = useMutation({
    mutationFn: () => {
      if (crossing) {
        return postDatasetTransfer(selected?.id, { ...options, name });
      }
      const form = new FormData();
      if (name.trim()) form.append("new_dataset_name", name.trim());
      return axios
        .post(endpoints.develop.cloneDataset(selected?.id), form)
        .then((res) => res.data?.result ?? null);
    },
    onSuccess: (result) => {
      if (crossing) {
        const where = result?.to_workspace?.name ?? toName;
        const to = result?.to_workspace?.id ?? toWorkspace;
        enqueueSnackbar(`Copied to ${where}`, {
          variant: "success",
          action: () => (
            <Button
              size="small"
              color="inherit"
              onClick={() => switchWorkspace(to, currentWorkspaceId)}
              sx={{ fontWeight: 600 }}
            >
              Switch workspace
            </Button>
          ),
        });
      } else {
        enqueueSnackbar(
          `Duplicated “${datasetName}” as “${result?.dataset_name ?? "a copy"}”`,
          { variant: "success" },
        );
      }
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      handleClose();
      refreshGrid?.();
    },
    onError: (error) => {
      setRefusal(refusalSentence(error, "The dataset was not copied."));
    },
  });

  // Any change makes the last preview and the last refusal stale, and so
  // does any preview answer still on its way.
  const changed = (fn) => (event) => {
    revision.current += 1;
    setPlan(null);
    setRefusal(null);
    fn(event.target.value);
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0, gap: "2px" }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight="fontWeightMedium"
              color="text.primary"
              variant="m2"
            >
              {datasetName ? `Duplicate “${datasetName}”` : "Duplicate dataset"}
            </Typography>
            <IconButton onClick={handleClose} aria-label="Close">
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Copy rows and input columns here or into another workspace." />
        </DialogTitle>
        <Box
          component="form"
          onSubmit={(event) => {
            event.preventDefault();
            setRefusal(null);
            submit.mutate();
          }}
        >
          <DialogContent
            sx={{
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              paddingTop: 2,
            }}
          >
            <TextField
              autoFocus
              size="small"
              label="Name"
              placeholder={datasetName ? `Copy of ${datasetName}` : ""}
              value={name}
              onChange={changed(setName)}
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            {workspaces.length > 1 && (
              <TextField
                select
                size="small"
                label="To workspace"
                value={toWorkspace ?? ""}
                onChange={changed(setTarget)}
                fullWidth
              >
                {workspaces.map((w) => (
                  <MenuItem key={w.id} value={w.id}>
                    {w.name}
                    {w.id === currentWorkspaceId ? " (this workspace)" : ""}
                  </MenuItem>
                ))}
              </TextField>
            )}
            {plan && !refusal && (
              <Alert severity="info" icon={false} data-testid="transfer-plan">
                {transferPlanSentence(plan)}
                {transferPlanNotes(plan).map((note) => (
                  <Typography
                    key={note}
                    variant="s2"
                    component="p"
                    sx={{ marginTop: 0.5 }}
                  >
                    {note}
                  </Typography>
                ))}
              </Alert>
            )}
            {refusal && <Alert severity="error">{refusal}</Alert>}
          </DialogContent>
          <DialogActions sx={{ padding: 0, marginTop: 3 }}>
            <Button onClick={handleClose} variant="outlined">
              <Typography
                variant="s2"
                fontWeight="fontWeightSemiBold"
                color="text.secondary"
              >
                Cancel
              </Typography>
            </Button>
            {crossing && (
              <LoadingButton
                variant="outlined"
                loading={preview.isPending}
                disabled={submit.isPending}
                onClick={() => {
                  setRefusal(null);
                  preview.mutate({
                    at: revision.current,
                    asked: { ...options, name },
                  });
                }}
              >
                <Typography variant="s2" fontWeight="fontWeightSemiBold">
                  Check first
                </Typography>
              </LoadingButton>
            )}
            <LoadingButton
              variant="contained"
              color="primary"
              loading={submit.isPending}
              disabled={preview.isPending}
              type="submit"
            >
              <Typography variant="s2" fontWeight="fontWeightSemiBold">
                Copy
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DatasetTransferDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.any,
};

export default DatasetTransferDialog;
