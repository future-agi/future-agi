import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useCreateMetricSnapshot } from "./api";
import { formatMetricTime } from "./metricTime";
import { metricReadMessage } from "./metricPageValues";

export default function SaveSnapshot({
  metric,
  scope,
  window,
  canWrite,
  onSaved,
}) {
  const [request, setRequest] = useState(null);
  const save = useCreateMetricSnapshot({ scope, metricId: metric?.id });
  const reason = !canWrite
    ? "A workspace member role is required to save snapshots."
    : !metric?.definition
      ? "A completed active definition is required."
      : ["span", "user"].includes(metric.definition.level)
        ? `Snapshots are not available for ${metric.definition.level} metrics.`
        : !metric.snapshot_supported
          ? metric.snapshot_unsupported_reason ||
            "Snapshots are not available for this metric."
          : "";
  const submit = async () => {
    try {
      await save.mutateAsync(request);
      setRequest(null);
      onSaved();
    } catch {
      // Retain the request identity on retry, including ambiguous network errors.
    }
  };
  return (
    <>
      <Tooltip title={reason} describeChild>
        <span tabIndex={reason ? 0 : undefined}>
          <Button
            color="inherit"
            variant="outlined"
            disabled={Boolean(reason) || save.isPending}
            onClick={() => {
              save.reset();
              setRequest({ ...window, idempotency_key: crypto.randomUUID() });
            }}
          >
            Save snapshot
          </Button>
        </span>
      </Tooltip>
      <Dialog
        open={Boolean(request)}
        onClose={save.isPending ? undefined : () => setRequest(null)}
        fullWidth
        maxWidth="sm"
        aria-labelledby="save-snapshot-title"
      >
        <DialogTitle id="save-snapshot-title">Save snapshot</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2">
              Capture the committed result for this window. Later recalculations
              will not change it.
            </Typography>
            <Typography
              variant="body2"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {formatMetricTime(request?.window_start)} –{" "}
              {formatMetricTime(request?.window_end)} (UTC)
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Uses complete 15-minute UTC buckets and the active definition at
              capture time. Saved snapshots remain until deleted. Data erasure
              can remove contributions and mark a snapshot changed.
            </Typography>
            {save.isError && (
              <Alert severity="error">
                {metricReadMessage(save.error, "Snapshot request")}
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button
            color="inherit"
            disabled={save.isPending}
            onClick={() => setRequest(null)}
          >
            Cancel
          </Button>
          <Button
            color="inherit"
            variant="contained"
            disabled={save.isPending}
            onClick={submit}
          >
            {save.isPending ? "Queuing…" : "Save snapshot"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
SaveSnapshot.propTypes = {
  metric: PropTypes.object,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object.isRequired,
  canWrite: PropTypes.bool.isRequired,
  onSaved: PropTypes.func.isRequired,
};
