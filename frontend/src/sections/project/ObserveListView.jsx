import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { Alert, Box, Button, Chip, Typography, useTheme } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { addHours, formatDistanceToNow, differenceInHours } from "date-fns";
import React, {
  useEffect,
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router";
import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";
import { useDebounce } from "src/hooks/use-debounce";
import PropTypes from "prop-types";
import { DataTable, DataTablePagination } from "src/components/data-table";
import EmptyState from "src/components/empty-state";
import { quickstartTracing } from "src/components/empty-state/quickstarts";
import VolumeBarChart from "./VolumeBarChart";
import TagEditor from "./TagEditor";
import { buildProjectListApiFilters } from "./common";
import { toValidDate } from "src/utils/format-time";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import axios, { endpoints } from "src/utils/axios";

// ── Helpers ──

const LOAD_ERROR_MESSAGE = "Could not load projects";
const EMPTY_MESSAGE = "No projects found";

const SORT_FIELD_MAP = {
  name: "name",
  issues: "issues",
  last_active: "updated_at",
};

// Every new workspace is given a sample project with traces at sign-up
// (create_demo_traces_and_spans in futureagi/accounts/user_onboard.py). Its
// project record has source "demo" (ProjectSourceChoices.DEMO); the user's own
// projects have another source. list_projects rows do not carry source, so a
// short list that holds every project is checked against each project's
// record, read the way the project page reads it.
const DEMO_PROJECT_SOURCE = "demo";
const MAX_PROJECTS_TO_CHECK = 5;

// Ids to check when a page holds every project there is and only a few.
const projectIdsToCheck = (projectPage) =>
  projectPage?.totalRows > 0 &&
  projectPage.totalRows <= MAX_PROJECTS_TO_CHECK &&
  projectPage.rows.length === projectPage.totalRows
    ? projectPage.rows.map((project) => project.id)
    : [];

// While only sample projects are listed, the list reads itself again so the
// user's first project replaces the code without a reload: every 5 s for the
// first five minutes, then every 30 s.
const FIRST_PROJECT_CHECK_MS = 5_000;
const FIRST_PROJECT_SLOW_CHECK_MS = 30_000;
const FIRST_PROJECT_FAST_CHECKS_FOR_MS = 5 * 60_000;

function getHealthColor(lastActive, theme) {
  const parsed = toValidDate(lastActive);
  if (!parsed) return theme.palette.text.disabled;
  const hours = differenceInHours(new Date(), parsed);
  if (hours < 1) return theme.palette.success.main;
  if (hours < 24) return theme.palette.warning.main;
  return theme.palette.text.disabled;
}

// last_active comes from the hourly trace rollup: it is the start of the hour
// that holds the newest trace, which can be anywhere in that hour. So it is
// measured from the end of the hour. updated_at (used when there is no
// activity yet) is an exact time.
function describeLastActive(lastActive, updatedAt) {
  const hourStart = toValidDate(lastActive);
  if (hourStart) {
    const hourEnd = addHours(hourStart, 1);
    if (hourEnd > new Date()) return { at: hourEnd, label: "This hour" };
    return {
      at: hourEnd,
      label: formatDistanceToNow(hourEnd, { addSuffix: true }),
    };
  }
  const updated = toValidDate(updatedAt);
  if (!updated) return null;
  return {
    at: updated,
    label: formatDistanceToNow(updated, { addSuffix: true }),
  };
}

// ── API ──

// ── Component ──

const ObserveListView = forwardRef(
  (
    {
      searchQuery = "",
      onSelectionChanged,
      setSelectedRowsData,
      filters = null,
      onFirstRunChange,
    },
    ref,
  ) => {
    const navigate = useNavigate();
    const theme = useTheme();

    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState(25);
    const [sorting, setSorting] = useState([{ id: "last_active", desc: true }]);
    const [rowSelection, setRowSelection] = useState({});
    // When the list was first seen holding only sample projects (null: not).
    const [onlySamplesSince, setOnlySamplesSince] = useState(null);

    useImperativeHandle(ref, () => ({
      clearSelection: () => {
        setRowSelection({});
        setSelectedRowsData?.([]);
      },
    }));

    const debouncedSearch = useDebounce(searchQuery.trim(), 500);

    const sortBy = sorting[0]
      ? SORT_FIELD_MAP[sorting[0].id] || "updated_at"
      : "updated_at";
    const sortOrder = sorting[0]?.desc ? "desc" : "asc";

    const apiFilters = useMemo(
      () => buildProjectListApiFilters(filters),
      [filters],
    );

    useEffect(() => {
      setPage(0);
    }, [debouncedSearch, apiFilters]);

    const unfiltered = !debouncedSearch && !(filters?.length > 0);

    const {
      data: apiData,
      isLoading,
      isFetching,
      isPlaceholderData,
      isError,
      error,
      refetch,
    } = useQuery({
      queryKey: [
        "observe-projects",
        {
          search: debouncedSearch,
          page,
          pageSize,
          sortBy,
          sortOrder,
          apiFilters,
        },
      ],
      queryFn: ({ signal }) =>
        readObserveProjectPage({
          signal,
          params: {
            name: debouncedSearch || null,
            page_number: page,
            page_size: pageSize,
            sort_by: sortBy,
            sort_direction: sortOrder,
            ...(apiFilters && { filters: apiFilters }),
          },
        }),
      retry: false,
      placeholderData: keepPreviousData,
      staleTime: 30_000,
      // Only sample projects so far: keep re-reading the list, as the setup
      // page does, so the user's first trace shows without a reload.
      refetchInterval:
        onlySamplesSince === null
          ? false
          : () =>
              Date.now() - onlySamplesSince < FIRST_PROJECT_FAST_CHECKS_FOR_MS
                ? FIRST_PROJECT_CHECK_MS
                : FIRST_PROJECT_SLOW_CHECK_MS,
    });

    const items = apiData?.rows || [];
    const total = apiData?.totalRows ?? 0;
    const rowIdsKey = JSON.stringify(items.map((item) => item.id));

    // Not while an earlier search's rows stand in for this page.
    const idsToCheck =
      unfiltered && !isPlaceholderData ? projectIdsToCheck(apiData) : [];
    const projectSources = useQueries({
      queries: idsToCheck.map((projectId) => ({
        // Same read and cache entry as the project page (useGetProjectDetails).
        queryKey: ["project-detail", projectId],
        queryFn: () =>
          axios.get(endpoints.project.projectExperimentDetail(projectId)),
        select: (response) => response?.data?.result?.source,
        staleTime: 60_000,
      })),
    });
    // A record that is still loading or could not be read counts as the
    // user's own project.
    const listsOnlySamples =
      idsToCheck.length > 0 &&
      projectSources.every((read) => read.data === DEMO_PROJECT_SOURCE);
    // While only sample projects are listed, each is tagged as one. Not in
    // a list that also holds the user's own projects: there the sample is
    // just another row, and the tag would come and go with list size.
    const sampleIdsKey = listsOnlySamples ? idsToCheck.join(",") : "";

    useEffect(() => {
      setOnlySamplesSince((since) =>
        listsOnlySamples ? since ?? Date.now() : null,
      );
    }, [listsOnlySamples]);

    useEffect(() => {
      // Only a settled response for this query can bound the current page.
      if (!apiData || isFetching || isPlaceholderData || isError) return;
      const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
      if (page > lastPage) {
        setPage((current) => Math.min(current, lastPage));
      }
    }, [
      apiData,
      isFetching,
      isPlaceholderData,
      isError,
      total,
      pageSize,
      page,
    ]);

    const handleRowSelectionChange = useCallback(
      (sel, selected) => {
        setRowSelection(sel);
        setSelectedRowsData?.(selected.map((row) => row.id));
      },
      [setSelectedRowsData],
    );

    const columns = useMemo(
      () => [
        {
          id: "name",
          accessorKey: "name",
          header: "Project",
          meta: { flex: 1 },
          minSize: 140,
          cell: ({ getValue, row }) => (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                minWidth: 0,
              }}
            >
              <Typography
                variant="body2"
                noWrap
                sx={{ fontWeight: 500, fontSize: 12 }}
              >
                {getValue()}
              </Typography>
              {sampleIdsKey.split(",").includes(row.original?.id) && (
                <Chip
                  label="Sample"
                  size="small"
                  variant="outlined"
                  sx={{ flexShrink: 0, height: 20, fontSize: 11 }}
                />
              )}
            </Box>
          ),
        },
        {
          // id matches the data field so the grid's value accessor resolves
          // (DataTable keys getValue off `id`); avoids reaching into row.original.
          id: "issues",
          accessorKey: "issues",
          header: "Alerts",
          size: 80,
          enableSorting: false,
          cell: ({ getValue }) => {
            const count = getValue() ?? 0;
            if (count === 0) {
              return (
                <Typography
                  variant="body2"
                  sx={{ fontSize: 12, color: "text.disabled" }}
                >
                  —
                </Typography>
              );
            }
            return (
              <Chip
                label={count}
                size="small"
                sx={{
                  bgcolor: (t) => alpha(t.palette.error.main, 0.1),
                  color: "error.main",
                }}
              />
            );
          },
        },
        {
          id: "volume",
          accessorKey: "last_30_days_vol",
          header: "Volume (30d)",
          size: 200,
          enableSorting: false,
          cell: ({ row }) =>
            row.original.activity_query_complete === false ? (
              <Typography variant="body2" color="text.disabled">
                Unavailable
              </Typography>
            ) : (
              <Box sx={{ width: "100%", overflow: "hidden" }}>
                <VolumeBarChart
                  dailyVolume={row.original.daily_volume || []}
                  height={22}
                />
              </Box>
            ),
        },
        {
          id: "tags",
          accessorKey: "tags",
          header: "Tags",
          size: 150,
          enableSorting: false,
          cell: ({ row }) => <TagEditor projectId={row.original.id} />,
        },
        {
          // id matches the data field so getValue() resolves; fall back to
          // updated_at only when there's no activity yet. last_active is an
          // hour start, not a trace time (see describeLastActive).
          id: "last_active",
          accessorKey: "last_active",
          header: "Last Active",
          size: 160,
          enableSorting: false,
          cell: ({ getValue, row }) => {
            if (row.original.activity_query_complete === false) {
              return (
                <Typography variant="body2" color="text.disabled">
                  Unavailable
                </Typography>
              );
            }
            // Validity-aware fallback: an unparseable last_active must not win over a valid updated_at.
            const lastActive = describeLastActive(
              getValue(),
              row.original?.updated_at,
            );
            if (!lastActive) return null;
            const color = getHealthColor(lastActive.at, theme);
            return (
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: color,
                    flexShrink: 0,
                  }}
                />
                <Typography variant="body2" noWrap sx={{ fontSize: 12 }}>
                  {lastActive.label}
                </Typography>
              </Box>
            );
          },
        },
      ],
      [theme, sampleIdsKey],
    );

    /* Nothing traced yet — not a search or filter that matched nothing. */
    const isFirstRun =
      !isLoading &&
      !isError &&
      total === 0 &&
      !debouncedSearch &&
      !(filters?.length > 0);
    /* Only the sign-up sample is listed: the user has not traced yet either. */
    const onlyDemoProject = !isLoading && !isError && listsOnlySamples;
    const sampleNote =
      items.length === 1
        ? `${items[0].name} above is a sample project.`
        : "The projects above are sample projects.";
    useEffect(() => {
      onFirstRunChange?.(isFirstRun);
    }, [isFirstRun, onFirstRunChange]);

    if (isFirstRun) {
      return (
        <EmptyState
          icon="solar:routing-2-linear"
          title="No traces yet"
          description="Instrument your app once and every LLM call, tool call and agent step shows up here as a trace, grouped by project."
          docsHref="https://docs.futureagi.com/docs/observe"
          quickstart={quickstartTracing("my-project")}
        />
      );
    }

    const table = (
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        sorting={sorting}
        onSortingChange={setSorting}
        rowSelection={rowSelection}
        selectionScope={[
          page,
          pageSize,
          searchQuery,
          debouncedSearch,
          apiFilters,
          rowIdsKey,
        ]}
        onRowSelectionChange={handleRowSelectionChange}
        onRowClick={(row) =>
          navigate(`/dashboard/observe/${row.id}/llm-tracing`)
        }
        getRowId={(row) => row.id}
        enableSelection
        rowHeight={ROW_HEIGHT.default}
        emptyMessage={isError ? "" : EMPTY_MESSAGE}
      />
    );

    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 1,
          // The first-trace code sits under the sample project's row, so the
          // page scrolls instead of squeezing the list.
          overflow: onlyDemoProject ? "auto" : "hidden",
          minHeight: 0,
        }}
      >
        {isError && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => refetch()}>
                Retry
              </Button>
            }
          >
            {getRequestErrorMessage(error, LOAD_ERROR_MESSAGE)}
          </Alert>
        )}
        {onlyDemoProject ? (
          // Sized to its rows (and a scrollbar) so the sample project stays in
          // view above the code.
          <Box
            sx={{
              flexShrink: 0,
              height:
                TABLE_HEADER_HEIGHT.default +
                items.length * ROW_HEIGHT.default +
                16,
            }}
          >
            {table}
          </Box>
        ) : (
          table
        )}
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
        {onlyDemoProject && (
          // The sample project above has traces, so this does not say "No
          // traces yet" or wait for a first trace: it asks for the user's own.
          // The page title carries "Send your first trace", so the code card
          // under it is just the quickstart.
          <EmptyState
            icon="solar:routing-2-linear"
            title="Send your first trace"
            description={`${sampleNote} Instrument your app once and every LLM call, tool call and agent step shows up here as a trace, grouped by project.`}
            docsHref="https://docs.futureagi.com/docs/observe"
            quickstart={{
              ...quickstartTracing("my-project"),
              title: "Quickstart",
              waiting: undefined,
            }}
            sx={{ flexGrow: 0, flexShrink: 0, pt: 3 }}
          />
        )}
      </Box>
    );
  },
);

ObserveListView.displayName = "ObserveListView";

ObserveListView.propTypes = {
  searchQuery: PropTypes.string,
  onFirstRunChange: PropTypes.func,
  onSelectionChanged: PropTypes.func,
  setSelectedRowsData: PropTypes.func,
  filters: PropTypes.array,
};

export default ObserveListView;
