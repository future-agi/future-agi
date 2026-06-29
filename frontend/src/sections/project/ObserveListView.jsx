import { Box, Chip, Typography, useTheme } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { formatDistanceToNow, differenceInHours } from "date-fns";
import React, {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import PropTypes from "prop-types";
import { DataTable, DataTablePagination } from "src/components/data-table";
import VolumeBarChart from "./VolumeBarChart";
import TagEditor from "./TagEditor";

// ── Helpers ──

const SORT_FIELD_MAP = {
  name: "name",
  issues: "issues",
  last_active: "updated_at",
};

function getHealthColor(lastActive, theme) {
  if (!lastActive) return theme.palette.text.disabled;
  const hours = differenceInHours(new Date(), new Date(lastActive));
  if (hours < 1) return theme.palette.success.main;
  if (hours < 24) return theme.palette.warning.main;
  return theme.palette.text.disabled;
}

// ── API ──

const fetchObserveProjects = async (params) => {
  const { data } = await axios.get(endpoints.project.projectObserveList, {
    params,
  });
  return data;
};

// ── Component ──

const ObserveListView = forwardRef(
  (
    {
      searchQuery = "",
      onSelectionChanged,
      setSelectedRowsData,
      filters = null,
    },
    ref,
  ) => {
    const navigate = useNavigate();
    const theme = useTheme();

    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState(25);
    const [sorting, setSorting] = useState([{ id: "last_active", desc: true }]);
    const [rowSelection, setRowSelection] = useState({});

    useImperativeHandle(ref, () => ({
      clearSelection: () => setRowSelection({}),
    }));

    const debouncedSearch = useDebounce(searchQuery.trim(), 500);

    const sortBy = sorting[0]
      ? SORT_FIELD_MAP[sorting[0].id] || "updated_at"
      : "updated_at";
    const sortOrder = sorting[0]?.desc ? "desc" : "asc";

    // Build the operator-based filters payload via the canonical contract
    // builder (same one every other list uses), so the project list speaks the
    // full { column_id, filter_config: { filter_type, filter_op, filter_value } }
    // shape and gets the builder's operator validation.
    const apiFilters = useMemo(() => {
      if (!filters?.length) return null;
      const out = filters
        .filter((f) => f.value != null && f.value !== "")
        .map((f) =>
          buildApiFilterFromPanelRow({
            field: f.field, // "name" | "tags"
            operator: f.operator || "contains", // "contains" | "equals"
            value: f.value,
          }),
        );
      return out.length ? JSON.stringify(out) : null;
    }, [filters]);

    const { data: apiData, isLoading } = useQuery({
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
      queryFn: () =>
        fetchObserveProjects({
          name: debouncedSearch || null,
          page_number: page,
          page_size: pageSize,
          sort_by: sortBy,
          sort_direction: sortOrder,
          project_type: "observe",
          ...(apiFilters && { filters: apiFilters }),
        }),
      keepPreviousData: true,
      staleTime: 30_000,
    });

    const items = apiData?.result?.table || [];
    const total = apiData?.result?.metadata?.total_rows || 0;

    const handleRowSelectionChange = useCallback(
      (sel) => {
        setRowSelection(sel);
        if (setSelectedRowsData) {
          const ids = Object.keys(sel)
            .filter((k) => sel[k])
            .map((k) => items[parseInt(k, 10)]?.id)
            .filter(Boolean);
          setSelectedRowsData(ids);
        }
      },
      [items, setSelectedRowsData],
    );

    const columns = useMemo(
      () => [
        {
          id: "name",
          accessorKey: "name",
          header: "Project",
          meta: { flex: 1 },
          minSize: 140,
          cell: ({ getValue }) => (
            <Typography
              variant="body2"
              noWrap
              sx={{ fontWeight: 500, fontSize: 13 }}
            >
              {getValue()}
            </Typography>
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
                  sx={{ fontSize: 13, color: "text.disabled" }}
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
                  height: 20,
                  fontSize: 11,
                  fontWeight: 600,
                  bgcolor: (t) => alpha(t.palette.error.main, 0.1),
                  color: "error.main",
                  "& .MuiChip-label": { px: 0.75 },
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
          cell: ({ row }) => (
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
          // updated_at only when there's no activity yet.
          id: "last_active",
          accessorKey: "last_active",
          header: "Last Active",
          size: 160,
          enableSorting: false,
          cell: ({ getValue, row }) => {
            const val = getValue() || row.original?.updated_at;
            const color = getHealthColor(val, theme);
            if (!val) return null;
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
                <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
                  {formatDistanceToNow(new Date(val), { addSuffix: true })}
                </Typography>
              </Box>
            );
          },
        },
      ],
      [theme],
    );

    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 1,
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading}
          rowCount={total}
          sorting={sorting}
          onSortingChange={setSorting}
          rowSelection={rowSelection}
          onRowSelectionChange={handleRowSelectionChange}
          onRowClick={(row) =>
            navigate(`/dashboard/observe/${row.id}/llm-tracing`)
          }
          getRowId={(row) => row.id}
          enableSelection
          rowHeight={44}
          emptyMessage="No projects found"
        />
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
      </Box>
    );
  },
);

ObserveListView.displayName = "ObserveListView";

ObserveListView.propTypes = {
  searchQuery: PropTypes.string,
  onSelectionChanged: PropTypes.func,
  setSelectedRowsData: PropTypes.func,
  filters: PropTypes.array,
};

export default ObserveListView;
