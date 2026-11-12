import React, { useCallback } from "react";
import PropTypes from "prop-types";
import { useParams, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useChangeSet, useChangeSetStore } from "./changeSetStore";
import SaveVersionDialog from "./SaveVersionDialog";
import { followSavedRows } from "../DataGrid/newRowsReveal";
import { useVersionDialogs } from "./versionDialogsStore";
import { pendingCountsText, pendingTotal } from "./versionText";

/**
 * PendingChangesGate (PRODUCT-SPEC §3.5; owned by L3).
 *
 * Frozen API:
 *   const gate = usePendingChangesGate();
 *   gate("structural" | "experiment", proceed);
 * With unsaved ops in the dataset's change-set, `gate` opens the
 * interstitial and calls `proceed` only after the user chooses; with none it
 * calls `proceed()` right away.
 *   structural  "You have 3 unsaved changes."
 *               [Save version & continue] [Discard] [Cancel]
 *   experiment  "… Experiments run on a saved version."
 *               [Save version & continue] [Use v6 without them] [Cancel]
 * Run Prompt, Evaluate and generated-column actions never call it.
 *
 * `<PendingChangesGate/>` (default export) hosts the interstitial and the
 * Save version dialog; it is mounted once per dataset page in
 * DevelopDetailView so every tab can gate and save.
 */
export function usePendingChangesGate(datasetIdArg) {
  const { dataset } = useParams();
  const datasetId = datasetIdArg ?? dataset;
  const openGate = useVersionDialogs((s) => s.openGate);
  return useCallback(
    (kind, proceed) => {
      const ops = useChangeSetStore.getState().getOps(datasetId);
      if (!ops.length) {
        proceed?.();
        return;
      }
      openGate(
        datasetId,
        kind === "experiment" ? "experiment" : "structural",
        proceed,
      );
    },
    [datasetId, openGate],
  );
}

/** Opens Save version for the current dataset (bar, gate, shortcuts). */
export function useOpenSaveVersion(datasetIdArg) {
  const { dataset } = useParams();
  const datasetId = datasetIdArg ?? dataset;
  const openSave = useVersionDialogs((s) => s.openSave);
  return useCallback(
    (opts) => openSave(datasetId, opts),
    [datasetId, openSave],
  );
}

const GateDialog = ({ gate, onClose, onSaveFirst }) => {
  const cs = useChangeSet(gate.datasetId);
  const { data: versions } = useDatasetVersions(gate.datasetId);
  const latest = latestOf(versions);
  const { refreshGrid } = useDevelopDetailContext();
  const total = pendingTotal(cs.counts);
  const experiment = gate.kind === "experiment";

  const run = () => {
    onClose();
    gate.proceed?.();
  };

  return (
    <Dialog
      open
      maxWidth={false}
      onClose={onClose}
      PaperProps={{ sx: { width: 520, maxWidth: "calc(100vw - 32px)" } }}
    >
      <Box sx={{ p: 2 }}>
        <DialogTitle sx={{ p: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography typography="m2" fontWeight="fontWeightBold">
              {experiment ? "Save your changes first?" : "Unsaved changes"}
            </Typography>
            <IconButton onClick={onClose} aria-label="Close">
              <Iconify icon="mdi:close" />
            </IconButton>
          </Stack>
        </DialogTitle>
        <DialogContent sx={{ p: 0, mt: 1, color: "text.secondary" }}>
          <Typography typography="s1" color="text.primary">
            You have {total} unsaved change{total === 1 ? "" : "s"}.
            {experiment
              ? " Experiments run on a saved version."
              : " This action saves its own version, so save or discard them first."}
          </Typography>
          <Typography typography="s2" color="text.secondary" sx={{ mt: 0.75 }}>
            {pendingCountsText(cs.counts)}
          </Typography>
        </DialogContent>
        <DialogActions
          sx={{
            p: 0,
            mt: 3,
            gap: 1,
            "& .MuiButton-root": { whiteSpace: "nowrap", ml: "0 !important" },
          }}
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            sx={{ px: 2, mr: "auto" }}
          >
            Cancel
          </Button>
          {experiment ? (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={run}
            >
              Use v{latest?.number ?? ""} without them
            </Button>
          ) : (
            <Button
              size="small"
              variant="outlined"
              color="error"
              onClick={() => {
                cs.discard();
                refreshGrid?.();
                run();
              }}
            >
              Discard
            </Button>
          )}
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onSaveFirst}
          >
            Save version & continue
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

GateDialog.propTypes = {
  gate: PropTypes.object.isRequired,
  onClose: PropTypes.func,
  onSaveFirst: PropTypes.func,
};

const PendingChangesGate = () => {
  const { save, gate, openSave, closeSave, closeGate } = useVersionDialogs();
  const { refreshGrid } = useDevelopDetailContext();
  const [, setSearchParams] = useSearchParams();

  const openHistory = useCallback(() => {
    const p = new URLSearchParams(window.location.search);
    p.set("tab", "data");
    p.set("history", "1");
    p.delete("version");
    setSearchParams(p);
  }, [setSearchParams]);

  const onSaved = useCallback(
    (version, result) => {
      const datasetId = save?.datasetId;
      const after = save?.onSaved;
      useChangeSetStore.getState().discard(datasetId);
      // IT-R2 G5 P4: a `New (6)` grid view follows its rows into the version.
      followSavedRows(datasetId, result?.row_id_map);
      closeSave();
      refreshGrid?.();
      const label = version?.tags?.length
        ? `Saved v${version.number} · ${version.tags[0]}`
        : `Saved v${version?.number ?? ""}`;
      enqueueSnackbar(label, {
        variant: "success",
        action: (id) => (
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Button
              size="small"
              color="primary"
              onClick={() => {
                closeSnackbar(id);
                openHistory();
              }}
              sx={{ fontWeight: 600 }}
            >
              View history
            </Button>
            <IconButton
              size="small"
              aria-label="Dismiss"
              onClick={() => closeSnackbar(id)}
              sx={{ p: 0.5, color: "text.secondary" }}
            >
              <Iconify width={18} icon="mingcute:close-line" />
            </IconButton>
          </Stack>
        ),
      });
      // IT-R1 G5 P1: the raw save result carries `row_id_map` (temp id ->
      // saved id), so a caller can follow its pending rows into the version.
      after?.(version, result);
    },
    [save, closeSave, refreshGrid, openHistory],
  );

  return (
    <>
      {gate && (
        <GateDialog
          gate={gate}
          onClose={closeGate}
          onSaveFirst={() => {
            const { datasetId, proceed } = gate;
            closeGate();
            // L5 G3: the continuation gets the save result (row_id_map).
            openSave(datasetId, {
              onSaved: (version, result) => proceed?.(result),
            });
          }}
        />
      )}
      {save && (
        <SaveVersionDialog
          open
          datasetId={save.datasetId}
          onClose={closeSave}
          onSaved={onSaved}
        />
      )}
    </>
  );
};

export default PendingChangesGate;
