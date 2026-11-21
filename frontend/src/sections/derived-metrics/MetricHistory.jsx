import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import { ClosableDialog } from "src/components/custom-dialog";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useMetricHistory, useDeleteMetricSnapshot } from "./api";
import { metricActionMessage } from "./metricPageValues";
import { snapshotStateCopy, windowNotCalculatedCopy } from "./copy";
import { attemptState, groupHistoryRows } from "./metricCalculationState";
import { formatMetricTime, formatMetricTimeRange } from "./metricTime";
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
  staging: "Staging",
  not_started: "Not started",
};

// Why a capture did not save, from the snapshot's error code. Expired has its
// own state; an unknown code says only that it failed.
const failedCopy = {
  window_not_calculated: `Not saved. ${windowNotCalculatedCopy}`,
  window_not_calculated_yet:
    "Not saved. This window was not calculated yet. Try again after the next calculation.",
  capture_failed: "Not saved. The capture did not finish. Try again.",
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
        spacing={3}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            History
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Snapshots, definition versions and recalculations, newest first.
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            component="p"
            sx={{ mt: 0.5 }}
          >
            Snapshots keep the captured result, even after source retention.
            Explicit data erasure can change them.
          </Typography>
        </Box>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
          disabled={query.readState === "loading"}
          onClick={() => query.refetch()}
        >
          Refresh history
        </Button>
      </Stack>
      <MetricReadNotice query={query} label="History" />
      {query.data &&
        (query.data.results.length ? (
          <TableContainer>
            <Table size="small" aria-label="Metric history">
              <TableHead>
                <TableRow>
                  {["Event", "Time (UTC)", "Status", "Details"].map((label) => (
                    <TableCell key={label}>{label}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {groupHistoryRows(query.data.results).map((row, index) => {
                  if (row.kind === "attempts")
                    return row.entries.length > 1 ? (
                      <AttemptGroupRows
                        key={`attempts:${row.entries[0].created_at}:${index}`}
                        entries={row.entries}
                        metric={metric}
                      />
                    ) : (
                      <EntryRow
                        key={`recalculation:${row.entries[0].created_at}:${index}`}
                        entry={row.entries[0]}
                        metric={metric}
                      />
                    );
                  const { entry } = row;
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
                  return (
                    <EntryRow
                      key={`${entry.kind}:${entry.created_at}:${index}`}
                      entry={entry}
                      metric={metric}
                    />
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
            <EmptyState
              variant="no-results"
              icon="solar:history-linear"
              title="No history yet"
              description="Snapshots, versions and recalculations appear here."
            />
          </Box>
        ))}
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="flex-end"
      >
        <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
          Page {page + 1}
        </Typography>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          disabled={page === 0 || query.readState === "loading"}
          onClick={() => setPage(page - 1)}
        >
          Newer events
        </Button>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
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

const timeCell = {
  whiteSpace: "nowrap",
  color: "text.secondary",
  fontVariantNumeric: "tabular-nums",
};
// The column header names UTC, so the cells do not repeat it.
const columnTime = (value) => formatMetricTime(value, { utcSuffix: false });

function entryBadge(entry) {
  if (entry.kind === "recalculation") return attemptState(entry.status);
  // A status without a label of its own keeps its words, humanized by the
  // product status map, never the raw token.
  return {
    status: entry.status || "unavailable",
    label: statusLabels[entry.status],
  };
}

// Recalculation rows never report progress; versions do once calculating.
// The dash is not a tab stop: its hidden words already reach assistive
// tech, and a long History page would otherwise add a stop per row.
function EntryDetails({ definition }) {
  const progress =
    Number.isSafeInteger(definition?.backfill_days_done) &&
    Number.isSafeInteger(definition?.backfill_days_total) &&
    definition.backfill_days_total > 0;
  if (progress)
    return (
      <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
        {`${definition.backfill_days_done} of ${definition.backfill_days_total} days`}
      </TableCell>
    );
  return (
    <TableCell sx={{ color: "text.secondary" }}>
      <Tooltip
        title="Progress not reported"
        placement="top-start"
        describeChild
      >
        <Box component="span">
          <span aria-hidden="true">—</span>
          <Box component="span" sx={visuallyHidden}>
            Progress not reported
          </Box>
        </Box>
      </Tooltip>
    </TableCell>
  );
}
EntryDetails.propTypes = { definition: PropTypes.object };

function EntryRow({ entry, metric, nested = false }) {
  const definition = entry.definition;
  const active = definition?.id === metric.active_version;
  return (
    <TableRow hover sx={{ height: ROW_HEIGHT.default }}>
      <TableCell sx={nested ? { pl: 6 } : undefined}>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ minWidth: 0 }}
        >
          <Iconify
            icon={
              entry.kind === "version"
                ? "solar:document-text-linear"
                : entry.kind === "recalculation"
                  ? "solar:refresh-linear"
                  : "solar:camera-linear"
            }
            width={16}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <Typography variant="body2" component="span">
            {entry.kind === "version"
              ? `Definition v${definition?.version ?? "—"}`
              : entry.kind === "recalculation"
                ? "Recalculation"
                : "Snapshot unavailable"}
          </Typography>
          {active && entry.kind === "version" && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                px: 0.75,
                borderRadius: 0.5,
                bgcolor: "action.hover",
              }}
            >
              Active definition
            </Typography>
          )}
        </Stack>
      </TableCell>
      <TableCell sx={timeCell}>{columnTime(entry.created_at)}</TableCell>
      <TableCell>
        <StatusBadge {...entryBadge(entry)} variant="dot" />
      </TableCell>
      <EntryDetails definition={definition} />
    </TableRow>
  );
}
EntryRow.propTypes = {
  entry: PropTypes.object.isRequired,
  metric: PropTypes.object.isRequired,
  nested: PropTypes.bool,
};

// Repeated attempts in one state read as one row: how many, over which
// times, in that state. Expanding lists each attempt.
function AttemptGroupRows({ entries, metric }) {
  const [open, setOpen] = useState(false);
  const badge = attemptState(entries[0].status);
  const label = `${entries.length} recalculations`;
  return (
    <>
      <TableRow hover sx={{ height: ROW_HEIGHT.default }}>
        <TableCell>
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="center"
            sx={{ minWidth: 0, ml: -0.5 }}
          >
            <IconButton
              size="small"
              aria-expanded={open}
              aria-label={`${open ? "Hide" : "Show"} ${label}`}
              onClick={() => setOpen(!open)}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={16}
              />
            </IconButton>
            <Typography variant="body2" component="span">
              {label}
            </Typography>
          </Stack>
        </TableCell>
        <TableCell sx={timeCell}>
          {formatMetricTimeRange(
            entries.at(-1).created_at,
            entries[0].created_at,
          )}
        </TableCell>
        <TableCell>
          <StatusBadge {...badge} variant="dot" />
        </TableCell>
        <EntryDetails />
      </TableRow>
      {open &&
        entries.map((entry, index) => (
          <EntryRow
            key={`${entry.created_at}:${index}`}
            entry={entry}
            metric={metric}
            nested
          />
        ))}
    </>
  );
}
AttemptGroupRows.propTypes = {
  entries: PropTypes.array.isRequired,
  metric: PropTypes.object.isRequired,
};

// One label per snapshot, checked in this order: a deletion wins over every
// other state, then expiry, then an erasure change, then the capture status.
function snapshotState(snapshot) {
  if (snapshot.deleted_at)
    return snapshot.cleanup_status === "done"
      ? { status: "deleted", role: "neutral", label: "Deleted" }
      : { status: "pending", role: "neutral", label: "Cleanup pending" };
  if (snapshot.error === "expired")
    return { status: "expired", label: "Expired" };
  if (snapshot.changed_at)
    return {
      status: "changed",
      role: "warning",
      label: "Changed by data erasure",
    };
  if (snapshot.status === "complete")
    return { status: "complete", label: "Saved" };
  if (snapshot.status === "running")
    return { status: "running", label: "Capturing" };
  return {
    status: snapshot.status || "unavailable",
    label: statusLabels[snapshot.status] || "Unavailable",
  };
}

// Every fact that applies, in the same order, so the label's own note comes
// first. An expiry or an erasure change is still stated after a deletion.
function snapshotNotes(snapshot) {
  return [
    snapshot.deleted_at && snapshot.cleanup_status !== "done"
      ? `Deletion requested ${formatMetricTime(snapshot.deleted_at)}. Its stored results are being removed.`
      : "",
    snapshot.error === "expired" ? snapshotStateCopy("expired") : "",
    snapshot.changed_at
      ? snapshotStateCopy("changed", {
          date: formatMetricTime(snapshot.changed_at),
          removedCount: snapshot.removed_unit_count,
        })
      : "",
    snapshot.status === "failed" && Object.hasOwn(failedCopy, snapshot.error)
      ? failedCopy[snapshot.error]
      : "",
  ].filter(Boolean);
}

function SnapshotWindow({ snapshot, ...other }) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      component="div"
      sx={{ fontVariantNumeric: "tabular-nums" }}
      {...other}
    >
      {formatMetricTime(snapshot.window_start)} –{" "}
      {formatMetricTime(snapshot.window_end)}
    </Typography>
  );
}
SnapshotWindow.propTypes = { snapshot: PropTypes.object.isRequired };

function SnapshotRow({ snapshot, scope, metricId, canWrite }) {
  const [confirm, setConfirm] = useState(false);
  const remove = useDeleteMetricSnapshot({
    scope,
    metricId,
    snapshotId: snapshot.id,
  });
  const state = snapshotState(snapshot);
  const notes = snapshotNotes(snapshot);
  const titleId = `delete-snapshot-${snapshot.id}`;
  // Names which captured window each row's Delete snapshot button removes.
  const windowId = `snapshot-window-${snapshot.id}`;
  return (
    <TableRow hover sx={{ height: ROW_HEIGHT.default }}>
      <TableCell>
        <Stack
          direction="row"
          spacing={1}
          alignItems="flex-start"
          sx={{ minWidth: 0 }}
        >
          <Iconify
            icon="solar:camera-linear"
            width={16}
            sx={{ color: "text.disabled", flexShrink: 0, mt: 0.25 }}
          />
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" component="div">
              Snapshot · v{snapshot.metric_version ?? "—"}
            </Typography>
            <SnapshotWindow snapshot={snapshot} id={windowId} />
          </Box>
        </Stack>
      </TableCell>
      <TableCell sx={timeCell}>
        {columnTime(snapshot.captured_at || snapshot.created_at)}
      </TableCell>
      <TableCell>
        <StatusBadge
          status={state.status}
          role={state.role}
          label={state.label}
          variant="dot"
        />
        {notes.length > 0 && (
          <Stack spacing={0.25} sx={{ mt: 0.5, maxWidth: 360 }}>
            {notes.map((note) => (
              <Typography
                key={note}
                component="div"
                variant="caption"
                color="text.secondary"
              >
                {note}
              </Typography>
            ))}
          </Stack>
        )}
      </TableCell>
      <TableCell>
        {snapshot.deleted_at ? (
          <Typography variant="body2" color="text.secondary">
            No longer available
          </Typography>
        ) : (
          // The wrapper is focusable while the button is disabled, so the
          // reason is reachable by keyboard, as on Save snapshot.
          <Tooltip
            title={
              canWrite
                ? ""
                : "A workspace member role is required to delete snapshots."
            }
            describeChild
          >
            <span tabIndex={canWrite ? undefined : 0}>
              <Button
                color="inherit"
                size="small"
                startIcon={
                  <Iconify icon="solar:trash-bin-trash-linear" width={16} />
                }
                aria-describedby={windowId}
                disabled={!canWrite || remove.isPending}
                onClick={() => {
                  remove.reset();
                  setConfirm(true);
                }}
              >
                Delete snapshot
              </Button>
            </span>
          </Tooltip>
        )}
        <ClosableDialog
          open={confirm}
          onClose={remove.isPending ? undefined : () => setConfirm(false)}
          maxWidth="xs"
          fullWidth
          aria-labelledby={titleId}
        >
          <DialogTitle id={titleId}>Delete snapshot?</DialogTitle>
          <DialogContent>
            <Stack spacing={1.5}>
              <Typography variant="body2" color="text.secondary">
                This permanently removes this snapshot’s frozen inputs and
                results. The live metric is not deleted.
              </Typography>
              <Box
                sx={{
                  p: 1.5,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  Captured window
                </Typography>
                <SnapshotWindow
                  snapshot={snapshot}
                  variant="body2"
                  color="text.primary"
                />
              </Box>
              {remove.isError && (
                <Alert severity="error">
                  {metricActionMessage(
                    remove.error,
                    "The snapshot was not deleted.",
                  )}
                </Alert>
              )}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button
              color="inherit"
              variant="outlined"
              size="small"
              disabled={remove.isPending}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </Button>
            <Button
              color="error"
              variant="contained"
              size="small"
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
        </ClosableDialog>
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
