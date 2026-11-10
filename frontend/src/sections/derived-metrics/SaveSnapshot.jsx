import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { useCreateMetricSnapshot } from "./api";
import { formatMetricTime, metricBucketWindow } from "./metricTime";
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
  // The request keeps the raw selection; the server rounds it the same way.
  const saved = metricBucketWindow(request);
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
            size="small"
            startIcon={<Iconify icon="solar:camera-linear" width={16} />}
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
      <ClosableDialog
        open={Boolean(request)}
        onClose={save.isPending ? undefined : () => setRequest(null)}
        fullWidth
        maxWidth="sm"
        aria-labelledby="save-snapshot-title"
      >
        <DialogTitle id="save-snapshot-title">Save snapshot</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5}>
            <Typography variant="body2" color="text.secondary">
              Capture the committed result for this window. Later recalculations
              will not change it.
            </Typography>
            <Box
              sx={{
                p: 1.5,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                component="div"
              >
                Window
              </Typography>
              {/* What the server saves, as its History row will show it.
                  formatMetricTime names UTC on each end. */}
              <Typography
                variant="body2"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {formatMetricTime(saved.window_start)} –{" "}
                {formatMetricTime(saved.window_end)}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                component="div"
                sx={{ mt: 0.5 }}
              >
                Your selected range, with both ends rounded down to 15-minute
                UTC buckets.
              </Typography>
            </Box>
            <Typography variant="caption" color="text.secondary">
              Uses the active definition at capture time. Saved snapshots remain
              until deleted. Data erasure can remove contributions and mark a
              snapshot changed.
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
            variant="outlined"
            size="small"
            disabled={save.isPending}
            onClick={() => setRequest(null)}
          >
            Cancel
          </Button>
          <Button
            color="inherit"
            variant="contained"
            size="small"
            disabled={save.isPending}
            onClick={submit}
          >
            {save.isPending ? "Queuing…" : "Save snapshot"}
          </Button>
        </DialogActions>
      </ClosableDialog>
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
