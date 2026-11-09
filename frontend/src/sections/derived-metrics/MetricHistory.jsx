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
  Tooltip,
  Typography,
} from "@mui/material";
import { useMetricHistory } from "./api";
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

export default function MetricHistory({ metric, scope }) {
  const [cursors, setCursors] = useState([null]);
  const [page, setPage] = useState(0);
  const query = useMetricHistory({
    scope,
    metricId: metric?.id,
    enabled: Boolean(metric),
    params: { limit: 30, ...(cursors[page] && { before: cursors[page] }) },
  });
  if (!metric) return null;
  // B18 has no snapshot implementation. Even a serializer's grain support bit
  // is not proof the action exists. Do not call its placeholder write endpoint.
  const snapshotReason = metric.snapshot_supported
    ? "Snapshots are not available yet. No snapshot will be saved."
    : metric.snapshot_unsupported_reason ||
      "Snapshots are not available for this metric yet.";
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
            Definition versions and recalculations, newest first.
          </Typography>
        </Box>
        <Tooltip title={snapshotReason} describeChild>
          <span tabIndex={0}>
            <Button color="inherit" variant="outlined" disabled>
              Save snapshot
            </Button>
          </span>
        </Tooltip>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        {snapshotReason}
      </Typography>
      <MetricReadNotice query={query} label="History" />
      {query.data &&
        (query.data.results.length ? (
          <TableContainer>
            <Table size="small" aria-label="Metric history">
              <TableHead>
                <TableRow>
                  {["Event", "Created (UTC)", "Status", "History progress"].map(
                    (label) => (
                      <TableCell key={label}>{label}</TableCell>
                    ),
                  )}
                </TableRow>
              </TableHead>
              <TableBody>
                {query.data.results.map((entry, index) => {
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
};
