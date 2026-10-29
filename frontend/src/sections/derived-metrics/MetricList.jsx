import PropTypes from "prop-types";
import { useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
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
import axios, { endpoints } from "src/utils/axios";
import { formatMetricTime } from "./metricTime";
import { verifyResultIdentity } from "./useCommittedMetricResults";

// A bounded list page reads the latest run of each visible metric. There is no
// aggregate-list API: never infer a value from a current editable definition.
export default function MetricList({
  definitions,
  scope,
  onSelect,
  pagination = { page: 0, pageSize: 25 },
  onPageChange,
  onPageSizeChange,
  disabled,
}) {
  const [search, setSearch] = useState("");
  const metrics = definitions.data?.results || [];
  const projectIds = [
    ...new Set(metrics.map((metric) => metric.project_id)),
  ].sort();
  const projectNames = useQuery({
    queryKey: ["metric-list-projects", scope, projectIds],
    enabled: projectIds.length > 0,
    staleTime: 30000,
    retry: false,
    queryFn: async ({ signal }) => {
      const names = {};
      const remaining = new Set(projectIds);
      // This catalog is independent of the editor's searched/paginated picker.
      // Stop as soon as the visible metrics' projects have all been resolved.
      for (let pageNumber = 0; remaining.size > 0; pageNumber += 1) {
        const page = await readObserveProjectPage({
          signal,
          params: { page_number: pageNumber, page_size: 50, name: "" },
        });
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
  const latest = useQueries({
    queries: metrics.map((metric) => ({
      queryKey: ["metric-list-latest", scope, metric.id],
      staleTime: 30000,
      retry: false,
      queryFn: async ({ signal }) => {
        const page = (
          await axios.get(endpoints.derivedMetrics.runs(metric.id), {
            signal,
            params: { limit: 1 },
          })
        ).data;
        const run = page.results[0];
        if (!run) return { run: null };
        if (run.metric_id !== metric.id)
          throw new Error("Run identity unavailable");
        if (!run.result_id) return { run, result: null };
        const resultPage = verifyResultIdentity(
          (
            await axios.get(endpoints.derivedMetrics.results(run.id), {
              signal,
              params: {
                rollup: "overall",
                limit: 1,
                include_uncertainty: false,
              },
            })
          ).data,
          run.result_id,
        );
        return {
          run,
          result:
            resultPage.results.find((item) => item.group_key === "") || null,
        };
      },
    })),
  });
  const rows = metrics.map((metric, index) => ({
    ...metric,
    latest: latest[index],
  }));
  const columns = [
    {
      accessorKey: "name",
      header: "Name",
      minSize: 220,
      meta: { flex: 2 },
      enableSorting: false,
      cell: ({ row: { original: item } }) => (
        <Button
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(item);
          }}
          onKeyDown={(event) => event.stopPropagation()}
          onKeyUp={(event) => event.stopPropagation()}
          title={item.name}
          sx={{
            justifyContent: "flex-start",
            textAlign: "left",
            width: "100%",
            minWidth: 0,
            height: "auto",
            p: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            fontSize: 13,
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
      header: "Latest value",
      size: 120,
      enableSorting: false,
      cell: ({ row: { original: item } }) => {
        const { data, isError, isPending } = item.latest;
        if (isError) return "Unavailable";
        if (isPending) return "Loading…";
        if (!data?.run) return "Not run";
        if (!data.result)
          return (
            <Tooltip title="No committed value">
              <span>No result</span>
            </Tooltip>
          );
        return data.result.value == null
          ? "No value"
          : new Intl.NumberFormat(undefined, {
              maximumSignificantDigits: 6,
            }).format(data.result.value);
      },
    },
    {
      id: "run",
      header: "Last run / status",
      size: 250,
      enableSorting: false,
      cell: ({ row: { original: item } }) =>
        item.latest.isError ? (
          <Button
            onClick={(e) => {
              e.stopPropagation();
              item.latest.refetch();
            }}
          >
            Retry latest run
          </Button>
        ) : item.latest.data?.run ? (
          `${item.latest.data.run.created_at ? formatMetricTime(item.latest.data.run.created_at) : item.latest.data.run.as_of ? `Captured ${formatMetricTime(item.latest.data.run.as_of)}` : "Time unavailable"} · ${item.latest.data.run.status}`
        ) : item.latest.isPending ? (
          "Loading…"
        ) : (
          "Not run"
        ),
    },
    {
      accessorKey: "current_version",
      header: "Version",
      size: 70,
      enableSorting: false,
      cell: ({ getValue }) => `v${getValue()}`,
    },
    {
      accessorKey: "project_id",
      header: "Project",
      minSize: 200,
      meta: { flex: 1 },
      enableSorting: false,
      cell: ({ getValue }) => {
        const name =
          projectNames.data?.[getValue()] ||
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
  return (
    <Stack
      spacing={2}
      sx={{ flex: 1, minHeight: 0 }}
      aria-label="Saved metrics"
    >
      <FormSearchField
        placeholder="Search this page"
        inputProps={{ "aria-label": "Search metrics on this page" }}
        searchQuery={search}
        onChange={(event) => setSearch(event.target.value)}
        sx={{
          width: 250,
          maxWidth: "100%",
          "& .MuiOutlinedInput-root": { height: "30px" },
        }}
      />
      {definitions.isError && (
        <Alert severity="error">
          Definitions could not be loaded.{" "}
          <Button onClick={() => definitions.refetch()}>Retry</Button>
        </Alert>
      )}
      {!definitions.isError &&
      !definitions.isLoading &&
      metrics.length === 0 &&
      pagination.page === 0 ? (
        <Box sx={{ py: 8, textAlign: "center" }}>
          <Typography variant="h6">No metrics yet</Typography>
          <Typography variant="body2" color="text.secondary">
            Create a metric to summarize evaluation results or numeric span
            fields.
          </Typography>
        </Box>
      ) : (
        <Box
          sx={{
            flex: 1,
            minHeight: 300,
            overflow: "auto",
          }}
        >
          <DataTable
            columns={columns}
            data={rows.filter((item) =>
              item.name.toLowerCase().includes(search.toLowerCase()),
            )}
            isLoading={definitions.isLoading}
            emptyMessage={
              search
                ? "No metrics match your search"
                : "No metrics on this page"
            }
            onRowClick={disabled ? undefined : onSelect}
          />
        </Box>
      )}
      <TablePagination
        component="div"
        rowsPerPageOptions={[10, 25, 50]}
        rowsPerPage={pagination.pageSize}
        page={pagination.page}
        // Cursor responses do not contain a total. Never present a lower bound as an exact count.
        count={
          definitions.data?.next_cursor ||
          (pagination.page > 0 && metrics.length === 0)
            ? -1
            : pagination.page * pagination.pageSize + metrics.length
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
          disabled: disabled || definitions.isFetching || pagination.page === 0,
        }}
        nextIconButtonProps={{
          "aria-label": "Next page",
          disabled:
            disabled ||
            definitions.isFetching ||
            !definitions.data?.next_cursor,
        }}
        labelDisplayedRows={({ from, to, count }) =>
          pagination.page > 0 && metrics.length === 0
            ? "No metrics on this page"
            : `${from}–${to} of ${count === -1 ? `more than ${to}` : count}`
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
    </Stack>
  );
}
MetricList.propTypes = {
  definitions: PropTypes.object.isRequired,
  scope: PropTypes.string.isRequired,
  onSelect: PropTypes.func.isRequired,
  pagination: PropTypes.object,
  onPageChange: PropTypes.func,
  onPageSizeChange: PropTypes.func,
  disabled: PropTypes.bool,
};
