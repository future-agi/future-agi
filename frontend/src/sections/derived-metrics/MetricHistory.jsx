import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Typography,
} from "@mui/material";
import { useMetricHistory, useDeleteMetricSnapshot } from "./api";
import { metricReadMessage } from "./metricPageValues";
import { snapshotStateCopy } from "./copy";
import { formatMetricTime } from "./metricTime";
import { MetricReadNotice } from "./MetricStatusLine";

const statusLabels = {
  pending: "Pending",
  queued: "Queued",
  building: "Recalculating",
  running: "Recalculating",
  in_progress: "Recalculating",
  complete: "Complete",
  completed: "Complete",
  committed: "Committed",
  failed: "Failed",
  cancelled: "Cancelled",
  superseded: "Superseded",
  not_started: "Not started",
};

export default function MetricHistory({ metric, scope, canWrite = false }) {
  const [cursors, setCursors] = useState([null]);
  const [page, setPage] = useState(0);
  const query = useMetricHistory({
    scope,
    metricId: metric?.id,
    enabled: Boolean(metric),
    params: { limit: 30, ...(cursors[page] && { before: cursors[page] }) },
  });
  if (!metric) return null;
  return (
    <Stack spacing={2}>
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
      >
        <Box>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            History
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Snapshots, definition versions and recalculations, newest first.
          </Typography>
        </Box>
        <Button
          color="inherit"
          disabled={query.readState === "loading"}
          onClick={() => query.refetch()}
        >
          Refresh history
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        Snapshots keep the captured result, even after source retention.
        Explicit data erasure can change them.
      </Typography>
      <MetricReadNotice query={query} label="History" />
      {query.data &&
        (query.data.results.length ? (
          <TableContainer>
            <Table size="small" aria-label="Metric history">
              <TableHead>
                <TableRow>
                  {["Event", "Created (UTC)", "Status", "Details"].map(
                    (label) => (
                      <TableCell key={label}>{label}</TableCell>
                    ),
                  )}
                </TableRow>
              </TableHead>
              <TableBody>
                {query.data.results.map((entry, index) => {
                  if (entry.kind === "snapshot" && entry.snapshot)
                    return (
                      <SnapshotRow
                        key={entry.snapshot.id}
                        snapshot={entry.snapshot}
                        scope={scope}
                        metricId={metric.id}
                        canWrite={canWrite}
                      />
                    );
                  const definition = entry.definition;
                  const progress =
                    Number.isSafeInteger(definition?.backfill_days_done) &&
                    Number.isSafeInteger(definition?.backfill_days_total) &&
                    definition.backfill_days_total > 0;
                  return (
                    <TableRow
                      key={`${entry.kind}:${entry.created_at}:${index}`}
                    >
                      <TableCell>
                        {entry.kind === "version"
                          ? `Definition v${definition?.version ?? "—"}`
                          : entry.kind === "recalculation"
                            ? "Recalculation"
                            : "Snapshot unavailable"}
                        {definition?.id === metric.active_version && (
                          <Typography
                            variant="caption"
                            component="div"
                            color="text.secondary"
                          >
                            Active definition
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {formatMetricTime(entry.created_at)}
                      </TableCell>
                      <TableCell>
                        {statusLabels[entry.status] ||
                          entry.status ||
                          "Unavailable"}
                      </TableCell>
                      <TableCell>
                        {progress
                          ? `${definition.backfill_days_done} of ${definition.backfill_days_total} days`
                          : "Not reported"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Typography color="text.secondary">No history yet.</Typography>
        ))}
      <Stack direction="row" spacing={2} alignItems="center">
        <Button
          color="inherit"
          disabled={page === 0 || query.readState === "loading"}
          onClick={() => setPage(page - 1)}
        >
          Newer events
        </Button>
        <Typography variant="caption" color="text.secondary">
          Page {page + 1}
        </Typography>
        <Button
          color="inherit"
          disabled={!query.data?.next_cursor}
          onClick={() => {
            setCursors([...cursors.slice(0, page + 1), query.data.next_cursor]);
            setPage(page + 1);
          }}
        >
          Older events
        </Button>
      </Stack>
    </Stack>
  );
}
MetricHistory.propTypes = {
  metric: PropTypes.object,
  scope: PropTypes.object.isRequired,
  canWrite: PropTypes.bool,
};

function SnapshotRow({ snapshot, scope, metricId, canWrite }) {
  const [confirm, setConfirm] = useState(false);
  const remove = useDeleteMetricSnapshot({
    scope,
    metricId,
    snapshotId: snapshot.id,
  });
  const label = snapshot.deleted_at
    ? snapshot.cleanup_status === "done"
      ? "Deleted"
      : "Cleanup pending"
    : snapshot.error === "expired"
      ? "Expired"
      : snapshot.changed_at
        ? "Changed by data erasure"
        : snapshot.status === "complete"
          ? "Saved"
          : snapshot.status === "running"
            ? "Capturing"
            : statusLabels[snapshot.status] || "Unavailable";
  return (
    <TableRow>
      <TableCell>
        <Typography variant="body2" fontWeight={600}>
          Snapshot · v{snapshot.metric_version}
        </Typography>
        <Typography variant="caption" color="text.secondary" component="div">
          {formatMetricTime(snapshot.window_start)} –{" "}
          {formatMetricTime(snapshot.window_end)} (UTC)
        </Typography>
      </TableCell>
      <TableCell>
        {formatMetricTime(snapshot.captured_at || snapshot.created_at)}
      </TableCell>
      <TableCell>
        {label}
        {snapshot.changed_at && (
          <Typography component="div" variant="caption" color="text.secondary">
            {snapshotStateCopy("changed", {
              date: formatMetricTime(snapshot.changed_at),
              removedCount: snapshot.removed_unit_count,
            })}
          </Typography>
        )}
        {snapshot.error === "expired" && (
          <Typography component="div" variant="caption" color="text.secondary">
            {snapshotStateCopy("expired")}
          </Typography>
        )}
      </TableCell>
      <TableCell>
        {snapshot.deleted_at ? (
          "No longer available"
        ) : (
          <Button
            color="inherit"
            disabled={!canWrite || remove.isPending}
            onClick={() => setConfirm(true)}
          >
            Delete snapshot
          </Button>
        )}
        <Dialog
          open={confirm}
          onClose={remove.isPending ? undefined : () => setConfirm(false)}
          maxWidth="sm"
          fullWidth
          aria-labelledby={`delete-snapshot-${snapshot.id}`}
        >
          <DialogTitle id={`delete-snapshot-${snapshot.id}`}>
            Delete snapshot?
          </DialogTitle>
          <DialogContent>
            <Typography variant="body2">
              This permanently removes this snapshot’s frozen inputs and
              results. The live metric is not deleted.
            </Typography>
            {remove.isError && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {metricReadMessage(remove.error, "Snapshot deletion")}
              </Alert>
            )}
          </DialogContent>
          <DialogActions>
            <Button
              color="inherit"
              disabled={remove.isPending}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </Button>
            <Button
              color="error"
              variant="contained"
              disabled={remove.isPending}
              onClick={async () => {
                try {
                  await remove.mutateAsync();
                  setConfirm(false);
                } catch {
                  /* Keep the confirmation and its error visible. */
                }
              }}
            >
              {remove.isPending ? "Deleting…" : "Delete snapshot"}
            </Button>
          </DialogActions>
        </Dialog>
      </TableCell>
    </TableRow>
  );
}
SnapshotRow.propTypes = {
  snapshot: PropTypes.object.isRequired,
  scope: PropTypes.object.isRequired,
  metricId: PropTypes.string.isRequired,
  canWrite: PropTypes.bool.isRequired,
};
