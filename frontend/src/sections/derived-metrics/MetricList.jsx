import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Stack,
  TablePagination,
  Tooltip,
  Typography,
} from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import DataTable from "src/components/data-table/DataTable";
import ListToolbar from "src/components/list-toolbar";
import EmptyState from "src/components/empty-state";
import StatusBadge from "src/components/status-badge";
import ContentSkeleton from "src/components/content-skeleton";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useMetricKpi } from "./api";
import {
  calculatedThroughCopy,
  calculationPausedCopy,
  forwardPendingCopy,
  windowNotCalculatedYetCopy,
} from "./copy";
import MetricValueCell from "./MetricValueCell";
import { firstCalculationBadge } from "./metricCalculationState";
import { isMetricFirstUse } from "./metricPresentation";
import {
  metricProjectLabel,
  useMetricProjectNames,
} from "./useMetricProjectNames";
import {
  coveragePending,
  coverageWindow,
  formatMetricTime,
  formatMetricWindow,
  formatRelativeMetricTime,
} from "./metricTime";

// The list shows the value and interval. Coverage diagnostics remain available
// on the detail page; omitting them here keeps the native table readable.
function summaryPoint(point) {
  if (!point) return undefined;
  return {
    value: point.value,
    point_status: point.point_status,
    value_mode: point.value_mode,
    weighted: point.weighted,
    rate_eligible: point.rate_eligible,
    unit: point.unit,
    value_format: point.value_format,
    uncertainty: point.uncertainty,
  };
}

function stopActivationKey(event) {
  // Let the grid keep arrow-key navigation; only isolate button activation.
  if (event.key === "Enter" || event.key === " ") event.stopPropagation();
}

// One line per cell line; a list row holds at most two.
const line = {
  display: "block",
  minWidth: 0,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

function MetricReadCell({ metric, scope, window, freshness = false }) {
  // No full 15-minute period exists yet for a metric that started with no
  // history: the server cannot read less, so no read is sent.
  const waiting = Boolean(window) && coveragePending(window, metric.definition);
  const query = useMetricKpi({
    scope: { ...scope, projectId: metric.project_id },
    metricId: metric.id,
    // current_version can be a newer draft; reads pin the ACTIVE definition.
    version: metric.definition?.version,
    // A metric that started with no history is read from its coverage start.
    params: window && coverageWindow(window, metric.definition),
    enabled: Boolean(metric.definition && window) && !waiting,
  });
  // A paused project calculates nothing new: freshness says so first. Values
  // already calculated still show in the Value column.
  const paused = calculationPausedCopy(metric.calculation_paused);
  if (freshness && paused)
    return (
      <Tooltip title={paused} placement="top-start">
        <span>
          <StatusBadge status="paused" label="Paused" variant="dot" />
        </span>
      </Tooltip>
    );
  // A window past where values are calculated (refreshes due or failing) is
  // "not calculated yet", never a load error.
  const notYet =
    query.error?.code === "window_not_calculated_yet"
      ? windowNotCalculatedYetCopy(
          query.error.calculated_through,
          query.error.reason,
        )
      : "";
  if (waiting || notYet) {
    // Not calculated yet: say when the first values appear, never a load error.
    const pending = notYet || forwardPendingCopy(metric.definition);
    return freshness ? (
      <Tooltip title={pending} placement="top-start">
        <Typography variant="body2" color="text.secondary" sx={line}>
          <span aria-hidden="true">—</span>
          <Box component="span" sx={visuallyHidden}>
            {pending}
          </Box>
        </Typography>
      </Tooltip>
    ) : (
      <Tooltip title={pending} placement="top-start">
        <span>
          <StatusBadge
            status="pending"
            label="Not calculated yet"
            variant="dot"
          />
        </span>
      </Tooltip>
    );
  }
  if (!metric.definition) {
    // The Value column already says "Not calculated yet". Freshness says when
    // the definition was saved, which is all that is known; without a saved
    // time it stays a quiet dash with the reason for assistive tech.
    const savedAt = metric.draft_definition?.created_at;
    const saved = formatRelativeMetricTime(savedAt);
    // Not a tab stop: the exact time is in the cell's own text for
    // assistive tech, and on hover.
    if (freshness && saved !== "Unavailable")
      return (
        <Tooltip
          title={`Saved ${formatMetricTime(savedAt)}. No active definition yet.`}
          placement="top-start"
          describeChild
        >
          <Typography variant="body2" color="text.secondary" sx={line}>
            Saved {saved}
            <Box component="span" sx={visuallyHidden}>
              {`, ${formatMetricTime(savedAt)}. No active definition yet.`}
            </Box>
          </Typography>
        </Tooltip>
      );
    return freshness ? (
      <Tooltip title="No active definition yet" placement="top-start">
        <Typography variant="body2" color="text.secondary" sx={line}>
          <span aria-hidden="true">—</span>
          <Box component="span" sx={visuallyHidden}>
            No active definition
          </Box>
        </Typography>
      </Tooltip>
    ) : (
      <StatusBadge status="pending" label="Not calculated yet" variant="dot" />
    );
  }
  const loading = query.readState === "loading";
  const error = !["ready", "loading"].includes(query.readState);
  const data = query.readState === "ready" ? query.data : undefined;
  if (!freshness) {
    const through = data?.complete_through;
    const partial = Boolean(data?.partial && through);
    return (
      <Tooltip
        title={
          data?.effective_window
            ? `Effective window: ${formatMetricWindow(data.effective_window)}${
                partial ? `. ${calculatedThroughCopy(through)}` : ""
              }`
            : ""
        }
        placement="top-start"
      >
        <Box sx={{ width: "100%", minWidth: 0 }}>
          <MetricValueCell
            source="point"
            value={summaryPoint(data?.current)}
            loading={loading}
            error={error}
            valueMode={metric.value_mode}
            dense
          />
        </Box>
      </Tooltip>
    );
  }
  if (loading)
    return (
      <Typography variant="body2" color="text.secondary" sx={line}>
        {metric.value_mode === "on_demand" ? "On demand" : "Loading freshness…"}
      </Typography>
    );
  if (error) {
    const message =
      {
        unavailable: "Values unavailable",
        busy: "Service busy",
        conflict: "Definition changed — refresh the list",
      }[query.readState] || "Freshness unavailable";
    return (
      <Stack
        direction="row"
        spacing={0.5}
        alignItems="center"
        sx={{ minWidth: 0 }}
      >
        <Typography variant="body2" color="text.secondary" sx={line}>
          {message}
        </Typography>
        <Button
          size="small"
          color="inherit"
          aria-label={`Retry value for ${metric.name}`}
          onClick={(event) => {
            event.stopPropagation();
            void query.refetch();
          }}
          onKeyDown={stopActivationKey}
          onKeyUp={stopActivationKey}
          sx={{ flexShrink: 0 }}
        >
          Retry
        </Button>
      </Stack>
    );
  }
  const provenance = data?.provenance;
  const done = provenance?.backfill_days_done;
  const total = provenance?.backfill_days_total;
  const recalculating =
    Number.isSafeInteger(done) &&
    Number.isSafeInteger(total) &&
    done >= 0 &&
    done < total;
  const updated = formatRelativeMetricTime(provenance?.updated_at);
  return (
    <Stack spacing={0.25} sx={{ minWidth: 0, width: "100%" }}>
      <Stack direction="row" spacing={0.75} sx={{ minWidth: 0 }}>
        <Tooltip title={formatMetricTime(provenance?.updated_at)}>
          <Typography
            variant="body2"
            color="text.secondary"
            tabIndex={0}
            sx={line}
          >
            {updated === "Unavailable"
              ? "Freshness unavailable"
              : `Updated ${updated}`}
          </Typography>
        </Tooltip>
        {metric.value_mode === "on_demand" && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ ...line, flexShrink: 0 }}
          >
            · On demand
          </Typography>
        )}
      </Stack>
      {recalculating && (
        <Typography variant="caption" color="text.secondary" sx={line}>
          Recalculating · {done} of {total} days
        </Typography>
      )}
    </Stack>
  );
}
MetricReadCell.propTypes = {
  metric: PropTypes.object.isRequired,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object,
  freshness: PropTypes.bool,
};

export default function MetricList({
  definitions,
  scope,
  window,
  windowLabel = "Last 7 days",
  onSelect,
  toolbar,
  actions,
  createAction,
  pagination = { page: 0, pageSize: 25 },
  onPageChange,
  onPageSizeChange,
  disabled,
}) {
  const [search, setSearch] = useState("");
  const loading =
    definitions.readState === "loading" ||
    definitions.isLoading ||
    definitions.isFetching;
  const error = definitions.isError;
  // Never leave old workspace/page values looking current during a failed read.
  const metrics = !loading && !error ? definitions.data?.results || [] : [];
  const rows = metrics.filter((metric) =>
    metric.name.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const projectIds = [
    ...new Set(rows.map((metric) => metric.project_id)),
  ].sort();
  const projectNames = useMetricProjectNames(scope, projectIds);
  const columns = [
    {
      accessorKey: "name",
      header: "Name",
      minSize: 240,
      meta: { flex: 1.6 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <Button
          size="small"
          color="inherit"
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(item);
          }}
          onKeyDown={stopActivationKey}
          onKeyUp={stopActivationKey}
          title={item.name}
          sx={{
            justifyContent: "flex-start",
            textAlign: "left",
            width: "100%",
            minWidth: 0,
            px: 0,
            color: "text.primary",
            whiteSpace: "nowrap",
            overflow: "hidden",
            "&:hover": { bgcolor: "transparent", textDecoration: "underline" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "text.primary",
              outlineOffset: -2,
            },
          }}
        >
          {/* Same size as the other cells, and the heaviest text in the row. */}
          <Typography
            component="span"
            variant="body2"
            sx={{
              fontWeight: 500,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {item.name}
          </Typography>
        </Button>
      ),
    },
    {
      id: "value",
      header: `Value · ${windowLabel}`,
      renderHeader: () => (
        <Tooltip
          title={window ? formatMetricWindow(window) : ""}
          placement="top-start"
        >
          <span>Value · {windowLabel}</span>
        </Tooltip>
      ),
      minSize: 220,
      meta: { flex: 1.3 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <MetricReadCell metric={item} scope={scope} window={window} />
      ),
    },
    {
      id: "updated",
      header: "Updated",
      minSize: 180,
      meta: { flex: 1 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <MetricReadCell metric={item} scope={scope} window={window} freshness />
      ),
    },
    {
      accessorKey: "project_id",
      header: "Project",
      minSize: 160,
      meta: { flex: 1 },
      enableSorting: false,
      cell: ({ getValue }) => {
        const name = metricProjectLabel(projectNames, getValue());
        return (
          <Tooltip title={name}>
            <Typography
              component="span"
              variant="body2"
              color="text.secondary"
              noWrap
              sx={{ display: "block", width: "100%" }}
            >
              {name}
            </Typography>
          </Tooltip>
        );
      },
    },
    {
      id: "version",
      header: "Version",
      size: 132,
      enableSorting: false,
      // A saved metric without an active version is waiting for its first
      // calculation; the badge says so in the header's words, shortened.
      cell: ({ row: { original: item } }) =>
        item.definition?.version != null ? (
          <Typography component="span" variant="body2" color="text.secondary">
            {`v${item.definition.version}`}
          </Typography>
        ) : (
          <Tooltip
            title={firstCalculationBadge(item.draft_definition).label}
            placement="top-start"
          >
            <Box component="span" sx={{ display: "inline-flex", minWidth: 0 }}>
              <StatusBadge
                {...firstCalculationBadge(item.draft_definition, {
                  short: true,
                })}
                variant="dot"
              />
            </Box>
          </Tooltip>
        ),
    },
  ];
  const firstUse = isMetricFirstUse(definitions, pagination, scope);
  return (
    <Stack sx={{ flex: 1, minHeight: 0 }} aria-label="Saved metrics">
      <ListToolbar>
        <FormSearchField
          placeholder="Search this page"
          inputProps={{ "aria-label": "Search metrics on this page" }}
          searchQuery={search}
          onChange={(event) => setSearch(event.target.value)}
          sx={{ width: 250, "& .MuiOutlinedInput-root": { height: "30px" } }}
        />
        {toolbar}
        <Box sx={{ flex: 1 }} />
        {actions}
        {!firstUse && createAction}
      </ListToolbar>
      {error ? (
        <Alert
          severity="error"
          sx={{ mt: 1 }}
          action={
            <Button color="inherit" onClick={() => definitions.refetch()}>
              Retry list
            </Button>
          }
        >
          Metrics could not be loaded. Try again.
        </Alert>
      ) : loading ? (
        <Box role="status" aria-live="polite" sx={{ pt: 1 }}>
          <Box component="span" sx={visuallyHidden}>
            Loading metrics…
          </Box>
          <ContentSkeleton
            variant="rows"
            rows={5}
            role="presentation"
            label=""
          />
        </Box>
      ) : firstUse ? (
        <EmptyState
          icon="solar:graph-up-linear"
          title="No metrics yet"
          description="Create a metric to track an outcome across conversations, spans or users."
          action={createAction}
        />
      ) : (
        <Box sx={{ flex: 1, minHeight: 240, overflow: "auto", pt: 1 }}>
          <DataTable
            columns={columns}
            data={rows}
            rowHeight={ROW_HEIGHT.twoLine}
            emptyMessage={
              search.trim()
                ? "No metrics match your search"
                : scope.projectId && pagination.page === 0
                  ? "No metrics in this project"
                  : "No metrics on this page"
            }
            onRowClick={disabled ? undefined : onSelect}
          />
        </Box>
      )}
      {!loading && !error && !firstUse && (
        <TablePagination
          component="div"
          rowsPerPageOptions={[10, 25, 50]}
          rowsPerPage={pagination.pageSize}
          page={pagination.page}
          count={
            definitions.data?.next_cursor || pagination.page > 0
              ? -1
              : metrics.length
          }
          onPageChange={(_, page) =>
            onPageChange?.(page, definitions.data?.next_cursor)
          }
          onRowsPerPageChange={(event) =>
            onPageSizeChange?.(Number(event.target.value))
          }
          SelectProps={{
            native: true,
            inputProps: { "aria-label": "Rows per page" },
            disabled,
          }}
          backIconButtonProps={{
            "aria-label": "Previous page",
            disabled: disabled || pagination.page === 0,
          }}
          nextIconButtonProps={{
            "aria-label": "Next page",
            disabled: disabled || !definitions.data?.next_cursor,
          }}
          labelDisplayedRows={({ from, to, count }) =>
            pagination.page > 0 && metrics.length === 0
              ? "No metrics on this page"
              : count === -1
                ? `${metrics.length} ${metrics.length === 1 ? "metric" : "metrics"} on this page${definitions.data?.next_cursor ? " · More available" : ""}`
                : `${from}–${to} of ${count}`
          }
          sx={{
            borderTop: 1,
            borderColor: "divider",
            flexShrink: 0,
            color: "text.secondary",
            "& .MuiTablePagination-toolbar": { minHeight: 44, px: 1 },
            "& .MuiTablePagination-selectLabel, & .MuiTablePagination-displayedRows, & .MuiTablePagination-select":
              { typography: "s1" },
          }}
        />
      )}
    </Stack>
  );
}
MetricList.propTypes = {
  definitions: PropTypes.object.isRequired,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object,
  windowLabel: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  toolbar: PropTypes.node,
  actions: PropTypes.node,
  createAction: PropTypes.node,
  pagination: PropTypes.object,
  onPageChange: PropTypes.func,
  onPageSizeChange: PropTypes.func,
  disabled: PropTypes.bool,
};
