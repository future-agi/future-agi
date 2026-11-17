import React, { createElement, useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomTooltip from "src/components/tooltip";
import { useActivateVersion } from "src/api/agent-playground/versions";
import { invalidateR2Reads } from "src/api/develop/r2";
import { publishVersionIdOf } from "./publishGate";

// Read by screen readers (aria-describedby), not drawn.
const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

const pinRun = (datasetId, runId) =>
  axios.put(endpoints.develop.datasetRuns.baseline(datasetId), {
    run_id: runId,
  });

/**
 * IT-R4 G3 (P3): the fix loop's close. After the full rerun of a draft
 * agent version, one primary `Publish v3.1 and set as baseline` → a
 * confirm that says what changes (`Publish v3.1? It becomes the version
 * new runs use, and #2G becomes the dataset baseline. 7 tasks got better,
 * 2 got worse (T13, T28, within noise).`) → `v3.1 published · #2G is the
 * baseline · Undo`. Undo pins the previous baseline back (the version
 * stays published; the toast's Undo says so on hover).
 * K-3 (BL-06): until the verdict is in the button stays on screen, disabled,
 * and says why (`reason` from publishGate.js: `Waiting for 7 tries · Publish
 * when the verdict is in`), on hover and to screen readers.
 * @param {{datasetId: string, cand: object, baseLabel: string,
 *   baseId: string, version: string, outcome: string, disabled?: boolean,
 *   reason?: string|null, gate?: string, onDone?: Function}} props
 */
export default function PublishAndPin({
  datasetId,
  cand,
  candLabel,
  baseId,
  baseLabel,
  version,
  outcome,
  disabled = false,
  reason = null,
  gate,
  onDone,
}) {
  const reasonId = useId();
  const why = disabled && reason ? reason : null;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const queryClient = useQueryClient();
  const activate = useActivateVersion();
  const versionId = publishVersionIdOf(cand);
  const graphId =
    cand?.config?.agent?.graph_id ?? cand?.target?.graph_id ?? "graph";

  const undo = async () => {
    try {
      await pinRun(datasetId, baseId);
      invalidateR2Reads(queryClient, datasetId);
      enqueueSnackbar(`${baseLabel} is the baseline again`, {
        variant: "success",
      });
    } catch {
      enqueueSnackbar(`Couldn't pin ${baseLabel} back. Try again.`, {
        variant: "error",
      });
    }
  };
  const UndoAction = () =>
    createElement(
      Button,
      {
        size: "small",
        color: "inherit",
        title: `Pin ${baseLabel} back as the baseline (${version} stays published)`,
        sx: { fontWeight: 600 },
        onClick: undo,
      },
      "Undo",
    );

  const publish = async () => {
    setBusy(true);
    try {
      if (versionId) await activate.mutateAsync({ graphId, versionId });
      await pinRun(datasetId, cand.run_id);
      invalidateR2Reads(queryClient, datasetId);
      setOpen(false);
      enqueueSnackbar(`${version} published · ${candLabel} is the baseline`, {
        variant: "success",
        autoHideDuration: 8000,
        action: UndoAction,
      });
      onDone?.();
    } catch {
      enqueueSnackbar(`Couldn't publish ${version}. Try again.`, {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <CustomTooltip show={Boolean(why)} title={why ?? ""} size="small" arrow>
        <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={disabled}
            onClick={() => setOpen(true)}
            startIcon={<Iconify icon="mdi:rocket-launch-outline" width={16} />}
            data-testid="iterate-banner-publish"
            data-gate={gate ?? (disabled ? undefined : "ready")}
            aria-describedby={why ? reasonId : undefined}
            sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
          >
            {`Publish ${version} and set as baseline`}
          </Button>
          {why ? (
            <Box
              component="span"
              id={reasonId}
              data-testid="iterate-banner-publish-reason"
              sx={visuallyHidden}
            >
              {why}
            </Box>
          ) : null}
        </Box>
      </CustomTooltip>
      <Dialog
        open={open}
        onClose={() => !busy && setOpen(false)}
        maxWidth="xs"
        fullWidth
        data-testid="iterate-publish-dialog"
      >
        <DialogTitle
          sx={{ typography: "h6" }}
        >{`Publish ${version}?`}</DialogTitle>
        <DialogContent>
          <Typography typography="s1">
            {`It becomes the version new runs use, and ${candLabel} becomes the dataset baseline. ${outcome}`}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            size="small"
            color="inherit"
            disabled={busy}
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={busy}
            onClick={publish}
            data-testid="iterate-publish-confirm"
          >
            Publish
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

PublishAndPin.propTypes = {
  datasetId: PropTypes.string.isRequired,
  cand: PropTypes.object.isRequired,
  candLabel: PropTypes.string.isRequired,
  baseId: PropTypes.string.isRequired,
  baseLabel: PropTypes.string.isRequired,
  version: PropTypes.string.isRequired,
  outcome: PropTypes.string,
  disabled: PropTypes.bool,
  reason: PropTypes.string,
  gate: PropTypes.string,
  onDone: PropTypes.func,
};
