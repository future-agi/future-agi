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
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT } from "src/theme/sizes";
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
  staging: "Staging",
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
        spacing={3}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            History
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Definition versions and recalculations, newest first.
          </Typography>
        </Box>
        <Stack alignItems="flex-end" spacing={0.5} sx={{ flexShrink: 0 }}>
          <Tooltip title={snapshotReason} describeChild>
            <span tabIndex={0}>
              <Button
                color="inherit"
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="solar:camera-linear" width={16} />}
                disabled
              >
                Save snapshot
              </Button>
            </span>
          </Tooltip>
          <Typography variant="caption" color="text.secondary">
            {snapshotReason}
          </Typography>
        </Stack>
      </Stack>
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
                  const active = definition?.id === metric.active_version;
                  return (
                    <TableRow
                      key={`${entry.kind}:${entry.created_at}:${index}`}
                      hover
                      sx={{ height: ROW_HEIGHT.default }}
                    >
                      <TableCell>
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
                      <TableCell
                        sx={{
                          whiteSpace: "nowrap",
                          color: "text.secondary",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {formatMetricTime(entry.created_at)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          status={entry.status || "unavailable"}
                          label={
                            statusLabels[entry.status] ||
                            entry.status ||
                            "Unavailable"
                          }
                          variant="dot"
                        />
                      </TableCell>
                      <TableCell
                        sx={{
                          color: progress ? "text.primary" : "text.secondary",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
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
          <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
            <EmptyState
              variant="no-results"
              icon="solar:history-linear"
              title="No history yet."
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
};
