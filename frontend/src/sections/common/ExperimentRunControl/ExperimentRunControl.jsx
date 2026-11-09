import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  changeRunControl,
  readRunControl,
} from "src/api/develop/experiment-run-control";
import { isSurfaceShown } from "src/launch/launchSwitch";

const LABELS = {
  RUNNING: "Running",
  STOPPING: "Stopping after current work",
  STOPPED: "Stopped",
  COMPLETED: "Completed",
  UNAVAILABLE: "Controls unavailable for this run",
  FAILED: "Failed",
  CANCELED: "Cancelled",
  TERMINATED: "Terminated",
};

export default function ExperimentRunControl({ experimentId }) {
  const [open, setOpen] = useState(false);
  const enabled = isSurfaceShown("exp.runControl");
  if (!enabled) return null;
  return (
    <>
      <Button size="small" onClick={() => setOpen(true)}>
        Manage runs
      </Button>
      {open && (
        <RunControlDialog
          key={experimentId}
          experimentId={experimentId}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

export function RunControlDialog({ experimentId, onClose }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(null);
  const revision = useRef(0);
  const changing = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    let timer;
    const refresh = async () => {
      const current = revision.current;
      try {
        const response = await readRunControl(experimentId, controller.signal);
        if (
          !controller.signal.aborted &&
          !changing.current &&
          current === revision.current
        ) {
          setRuns(response.data.result.runs);
        }
      } catch (e) {
        if (!controller.signal.aborted)
          setError("Could not refresh runs. Close and reopen to retry.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 1500);
      }
    };
    refresh();
    return () => {
      mounted.current = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [experimentId]);

  const change = async (run) => {
    if (changing.current) return;
    changing.current = true;
    revision.current += 1;
    setBusy(run.run_id);
    setError("");
    try {
      const response = await changeRunControl(
        experimentId,
        run.run_id,
        run.status === "RUNNING",
      );
      if (mounted.current)
        setRuns((old) =>
          old.map(
            (item) =>
              response.data.result.runs.find(
                (next) => next.run_id === item.run_id,
              ) || item,
          ),
        );
    } catch (e) {
      if (mounted.current)
        setError(
          e.response?.data?.detail ||
            e.message ||
            "Could not change this run. Try again.",
        );
    } finally {
      changing.current = false;
      revision.current += 1;
      if (mounted.current) setBusy(null);
    }
  };
  return (
    <Dialog
      open
      onClose={busy ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="run-control-title"
    >
      <DialogTitle id="run-control-title">Manage runs</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Stop finishes current work before pausing. Resume continues from
          there; completed results stay unchanged.
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {!runs && !error && (
          <CircularProgress size={24} aria-label="Loading runs" />
        )}
        {runs?.length === 0 && (
          <Typography>No runs in this experiment.</Typography>
        )}
        <Stack spacing={1}>
          {runs?.map((run) => (
            <Box
              key={run.run_id}
              data-testid={`run-control-${run.run_id}`}
              sx={{
                display: "flex",
                gap: 2,
                alignItems: "center",
                py: 1,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography sx={{ overflowWrap: "anywhere" }}>
                  {run.name}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  aria-live="polite"
                >
                  {LABELS[run.status] || run.status}
                </Typography>
              </Box>
              {["RUNNING", "STOPPED", "STOPPING"].includes(run.status) && (
                <Button
                  size="small"
                  disabled={Boolean(busy) || run.status === "STOPPING"}
                  onClick={() => change(run)}
                >
                  {run.status === "STOPPED" ? "Resume" : "Stop"}
                </Button>
              )}
            </Box>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={Boolean(busy)} onClick={onClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ExperimentRunControl.propTypes = { experimentId: PropTypes.string.isRequired };
RunControlDialog.propTypes = {
  experimentId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};
