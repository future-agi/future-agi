import PropTypes from "prop-types";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { settledMetricRead, useCreateMetricSnapshot } from "./api";
import { forwardPendingCopy, metricCountCopy } from "./copy";
import { formatMetricValue } from "./formatMetricValue";
import {
  coveragePending,
  formatMetricClock,
  formatMetricTime,
  metricBucketWindow,
} from "./metricTime";
import { metricActionMessage } from "./metricPageValues";
import { pointValueState } from "./unitValueState";

// The value the Overview already read for this window, labelled with when it
// was read. The snapshot itself keeps the committed result at capture time.
function CurrentValue({ read }) {
  const current = read?.data?.current;
  const state = pointValueState(current);
  const metadata = current ? { ...current, value_type: "number" } : null;
  // Rounded as the Overview headline it quotes; unrounded on hover.
  const value =
    state.state === "value"
      ? `${current.weighted ? "≈ " : ""}${formatMetricValue(
          current.value,
          metadata,
          undefined,
          { display: true },
        )}`
      : null;
  const unrounded = value ? formatMetricValue(current.value, metadata) : null;
  const counts = value ? metricCountCopy(current, current.unit_noun) : [];
  return (
    <Box sx={{ mt: 1.25 }}>
      <Typography variant="caption" color="text.secondary" component="div">
        Current value for this window
      </Typography>
      {read ? (
        <>
          <Typography
            variant="body2"
            color={value ? "text.primary" : "text.secondary"}
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            <Box component="span" title={unrounded ?? undefined}>
              {value ?? state.label}
            </Box>
            {counts[0] && (
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" "}
                · {counts[0]}
              </Box>
            )}
          </Typography>
          <Typography variant="caption" color="text.secondary" component="div">
            As read at {formatMetricClock(read.readAt)}. The snapshot keeps the
            committed result when it is captured.
          </Typography>
        </>
      ) : (
        <Typography variant="body2" color="text.secondary">
          Not available. Open Overview to read it.
        </Typography>
      )}
    </Box>
  );
}
CurrentValue.propTypes = { read: PropTypes.object };

export default function SaveSnapshot({
  metric,
  scope,
  window,
  canWrite,
  onSaved,
}) {
  const client = useQueryClient();
  const [request, setRequest] = useState(null);
  // What the dialog shows. It outlives the request so the closing dialog
  // keeps its window instead of flashing "Unavailable".
  const [shown, setShown] = useState(null);
  const save = useCreateMetricSnapshot({ scope, metricId: metric?.id });
  // The request keeps the raw selection; the server rounds it the same way.
  const saved = metricBucketWindow(shown?.request);
  const reason = !canWrite
    ? "A workspace member role is required to save snapshots."
    : !metric?.definition
      ? "A completed active definition is required."
      : ["span", "user"].includes(metric.definition.level)
        ? `Snapshots are not available for ${metric.definition.level} metrics.`
        : !metric.snapshot_supported
          ? metric.snapshot_unsupported_reason ||
            "Snapshots are not available for this metric."
          : coveragePending(window, metric.definition)
            ? `Nothing to save yet. ${forwardPendingCopy(metric.definition)}`
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
              const next = { ...window, idempotency_key: crypto.randomUUID() };
              setRequest(next);
              setShown({
                request: next,
                // The same read the Overview made for this window, if any.
                read: settledMetricRead(client, "kpi", {
                  scope,
                  metricId: metric.id,
                  version: metric.definition.version,
                  params: window,
                }),
              });
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
              {shown && <CurrentValue read={shown.read} />}
            </Box>
            <Typography variant="caption" color="text.secondary">
              Uses the active definition at capture time. Saved snapshots remain
              until deleted. Data erasure can remove contributions and mark a
              snapshot changed.
            </Typography>
            {save.isError && (
              <Alert severity="error">
                {metricActionMessage(save.error, "The snapshot was not saved.")}
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
