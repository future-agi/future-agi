import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Stack,
  TablePagination,
  Tooltip,
  Typography,
} from "@mui/material";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import DataTable from "src/components/data-table/DataTable";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { useMetricKpi } from "./api";
import MetricValueCell from "./MetricValueCell";
import {
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

function MetricReadCell({ metric, scope, window, freshness = false }) {
  const query = useMetricKpi({
    scope: { ...scope, projectId: metric.project_id },
    metricId: metric.id,
    // current_version can be a newer draft; reads pin the ACTIVE definition.
    version: metric.definition?.version,
    params: window,
    enabled: Boolean(metric.definition && window),
  });
  if (!metric.definition) {
    return (
      <Typography variant="body2" color="text.secondary">
        {freshness ? "No active definition" : "Not calculated yet"}
      </Typography>
    );
  }
  const loading = query.readState === "loading";
  const error = !["ready", "loading"].includes(query.readState);
  const data = query.readState === "ready" ? query.data : undefined;
  if (!freshness) {
    return (
      <Tooltip
        title={
          data?.effective_window
            ? `Effective window: ${formatMetricWindow(data.effective_window)}`
            : ""
        }
      >
        <Box sx={{ width: "100%" }}>
          <MetricValueCell
            source="point"
            value={summaryPoint(data?.current)}
            loading={loading}
            error={error}
            valueMode={metric.value_mode}
          />
        </Box>
      </Tooltip>
    );
  }
  if (loading)
    return (
      <Typography variant="body2" color="text.secondary">
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
      <Stack spacing={0.5} alignItems="flex-start">
        <Typography variant="caption" color="text.secondary">
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
    <Stack spacing={0.5}>
      <Tooltip title={formatMetricTime(provenance?.updated_at)}>
        <Typography variant="body2" color="text.secondary" tabIndex={0}>
          {updated === "Unavailable"
            ? "Freshness unavailable"
            : `Updated ${updated}`}
        </Typography>
      </Tooltip>
      {recalculating && (
        <Typography variant="caption" color="text.secondary">
          Recalculating · {done} of {total} days
        </Typography>
      )}
      {metric.value_mode === "on_demand" && (
        <Typography variant="caption" color="text.secondary">
          On demand
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
  onSelect,
  toolbar,
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
  const projectNames = useQuery({
    queryKey: ["metric-list-projects", scope, projectIds],
    enabled: projectIds.length > 0,
    staleTime: 30000,
    retry: false,
    placeholderData: undefined,
    meta: { errorHandled: true },
    queryFn: async ({ signal }) => {
      const names = {};
      const remaining = new Set(projectIds);
      for (let pageNumber = 0; remaining.size > 0; pageNumber += 1) {
        const page = await readObserveProjectPage({
          signal,
          params: { page_number: pageNumber, page_size: 50, name: "" },
        });
        signal.throwIfAborted();
        for (const project of page.rows) {
          if (remaining.has(project.id) && project.name) {
            names[project.id] = project.name;
            remaining.delete(project.id);
          }
        }
        if (pageNumber + 1 >= page.totalPages) break;
      }
      return names;
    },
  });
  const columns = [
    {
      accessorKey: "name",
      header: "Name",
      minSize: 220,
      meta: { flex: 2 },
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
            whiteSpace: "nowrap",
            overflow: "hidden",
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "text.primary",
              outlineOffset: -2,
            },
          }}
        >
          <Box
            component="span"
            sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {item.name}
          </Box>
        </Button>
      ),
    },
    {
      id: "value",
      header: "Value · Last 7 days",
      minSize: 320,
      meta: { flex: 2 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <MetricReadCell metric={item} scope={scope} window={window} />
      ),
    },
    {
      id: "updated",
      header: "Updated",
      minSize: 230,
      meta: { flex: 1 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <MetricReadCell metric={item} scope={scope} window={window} freshness />
      ),
    },
    {
      id: "version",
      header: "Version",
      size: 80,
      enableSorting: false,
      cell: ({ row: { original: item } }) =>
        item.definition?.version != null
          ? `v${item.definition.version}`
          : "Draft",
    },
    {
      accessorKey: "project_id",
      header: "Project",
      minSize: 180,
      meta: { flex: 1 },
      enableSorting: false,
      cell: ({ getValue }) => {
        const name =
          (!projectNames.isError && projectNames.data?.[getValue()]) ||
          (projectNames.isPending
            ? "Loading project…"
            : "Project name unavailable");
        return (
          <Tooltip title={name}>
            <Typography
              component="span"
              variant="body2"
              noWrap
              sx={{ display: "block", width: "100%", fontSize: 13 }}
            >
              {name}
            </Typography>
          </Tooltip>
        );
      },
    },
  ];
  const firstUse =
    !loading &&
    !error &&
    metrics.length === 0 &&
    pagination.page === 0 &&
    !scope.projectId;
  return (
    <Stack
      spacing={2}
      sx={{ flex: 1, minHeight: 0 }}
      aria-label="Saved metrics"
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <FormSearchField
          placeholder="Search this page"
          inputProps={{ "aria-label": "Search metrics on this page" }}
          searchQuery={search}
          onChange={(event) => setSearch(event.target.value)}
          sx={{ width: 250, "& .MuiOutlinedInput-root": { height: "30px" } }}
        />
        {toolbar}
      </Stack>
      {error ? (
        <Alert
          severity="error"
          action={
            <Button color="inherit" onClick={() => definitions.refetch()}>
              Retry list
            </Button>
          }
        >
          Metrics could not be loaded. Try again.
        </Alert>
      ) : loading ? (
        <Box
          role="status"
          sx={{ py: 8, textAlign: "center", color: "text.secondary" }}
        >
          Loading metrics…
        </Box>
      ) : firstUse ? (
        <Box sx={{ py: 8, textAlign: "center" }}>
          <Typography variant="h6">No metrics yet</Typography>
          <Typography variant="body2" color="text.secondary">
            Create a metric to track an outcome across conversations, spans or
            users.
          </Typography>
        </Box>
      ) : (
        <Box sx={{ flex: 1, minHeight: 300, overflow: "auto" }}>
          <DataTable
            columns={columns}
            data={rows}
            rowHeight={80}
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
      {!loading && !error && (
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
            "& .MuiTablePagination-toolbar": { minHeight: 44, px: 2 },
            "& .MuiTablePagination-selectLabel, & .MuiTablePagination-displayedRows":
              { fontSize: 14 },
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
  onSelect: PropTypes.func.isRequired,
  toolbar: PropTypes.node,
  pagination: PropTypes.object,
  onPageChange: PropTypes.func,
  onPageSizeChange: PropTypes.func,
  disabled: PropTypes.bool,
};
