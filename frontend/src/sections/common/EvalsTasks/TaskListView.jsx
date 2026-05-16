import {
  alpha,
  Box,
  Button,
  Chip,
  IconButton,
  Popover,
  Tooltip,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import _ from "lodash";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import DeleteConfirmation from "./DeleteConfirmation";

// ── Status Config ──

const STATUS_CONFIG = {
  pending: {
    paletteColor: "warning",
    icon: "solar:clock-circle-linear",
  },
  running: {
    paletteColor: "info",
    icon: "svg-spinners:ring-resize",
  },
  completed: {
    paletteColor: "success",
    icon: "solar:check-circle-linear",
  },
  failed: {
    paletteColor: "error",
    icon: "solar:close-circle-linear",
  },
  paused: {
    paletteColor: "default",
    icon: "solar:pause-circle-linear",
  },
};

const StatusBadge = ({ status }) => {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const palColor = config.paletteColor;
  return (
    <Chip
      label={_.capitalize(status)}
      size="small"
      color={palColor}
      variant="outlined"
      icon={<Iconify icon={config.icon} width={14} />}
      sx={{
        fontWeight: 500,
        fontSize: "12px",
        height: 24,
        "& .MuiChip-icon": { ml: 0.5 },
      }}
    />
  );
};

StatusBadge.propTypes = {
  status: PropTypes.string,
};

// ── Hover Popover (shared) ──

const HoverChipList = ({ items, label, emptyText }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);

  if (!items?.length) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Typography variant="caption" color="text.disabled">
          {emptyText}
        </Typography>
      </Box>
    );
  }

  const firstItem =
    typeof items[0] === "string"
      ? items[0]
      : items[0].name || items[0].eval_template_name;
  const remaining = items.length - 1;

  const chipStyles = {
    backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.1),
    color: "primary.main",
    borderRadius: "4px",
    fontWeight: 500,
    fontSize: "12px",
    height: 22,
    "& .MuiChip-label": { px: 0.75 },
  };

  return (
    <>
      <Box
        onMouseEnter={(e) => setAnchorEl(e.currentTarget)}
        onMouseLeave={() => setAnchorEl(null)}
        sx={{ display: "flex", alignItems: "center", height: "100%", gap: 0.5 }}
      >
        <Chip label={firstItem} size="small" sx={chipStyles} />
        {remaining > 0 && (
          <Typography
            variant="caption"
            sx={{ color: "text.secondary", fontSize: "12px", pl: 0.5 }}
          >
            +{remaining} other{remaining > 1 ? "s" : ""}
          </Typography>
        )}
      </Box>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        sx={{ pointerEvents: "none" }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        disableRestoreFocus
        PaperProps={{
          sx: {
            pointerEvents: "auto",
            p: 1.5,
            maxWidth: 320,
            maxHeight: 280,
            overflowY: "auto",
            boxShadow: "-5px 5px 10px rgba(0,0,0,0.1)",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          },
        }}
      >
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ display: "block", mb: 1, color: "text.primary" }}
        >
          Added {label} ({items.length})
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
          {items.map((item, idx) => {
            const text =
              typeof item === "string"
                ? item
                : item.name || item.eval_template_name || "—";
            return (
              <Chip
                key={idx}
                label={text}
                size="small"
                sx={{
                  ...chipStyles,
                  alignSelf: "flex-start",
                  maxWidth: "100%",
                }}
              />
            );
          })}
        </Box>
      </Popover>
    </>
  );
};

HoverChipList.propTypes = {
  items: PropTypes.array,
  label: PropTypes.string,
  emptyText: PropTypes.string,
};

// ── Eval Chips ──

const EvalChips = ({ evals }) => (
  <HoverChipList items={evals} label="Evals" emptyText="None" />
);

EvalChips.propTypes = {
  evals: PropTypes.array,
};

// ── Filter Chips ──

const buildFilterChips = (filtersApplied) => {
  if (!filtersApplied) return [];
  const chips = [];

  const dateRange = filtersApplied.date_range || filtersApplied.dateRange;
  if (dateRange?.length === 2) {
    const [start, end] = dateRange;
    const fmt = (d) => {
      try {
        return new Date(d).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          year: "2-digit",
        });
      } catch {
        return d;
      }
    };
    chips.push(`Date: ${fmt(start)} → ${fmt(end)}`);
  }
  const observationTypes =
    filtersApplied.observation_type || filtersApplied.observationType;
  if (observationTypes?.length) {
    observationTypes.forEach((t) => chips.push(`Type: ${t}`));
  }
  const spanAttributeFilters =
    filtersApplied.span_attributes_filters ||
    filtersApplied.spanAttributesFilters;
  if (spanAttributeFilters?.length) {
    spanAttributeFilters.forEach((f) => {
      const key = f.key || f.field || f.name;
      const op = f.operator || f.op || "=";
      const val = f.value ?? "";
      chips.push(`${key} ${op} ${val}`);
    });
  }
  if (filtersApplied.project_id) {
    chips.push(`Project: ${filtersApplied.project_id.slice(0, 8)}…`);
  }
  return chips;
};

const FilterSummary = ({ filtersApplied }) => {
  const chips = buildFilterChips(filtersApplied);
  return (
    <HoverChipList
      items={chips}
      label="Filters"
      emptyText="No Filters applied"
    />
  );
};

FilterSummary.propTypes = {
  filtersApplied: PropTypes.object,
};

// ── Main Component ──

const TaskListView = ({
  observeId = null,
  onCreateTask,
  onRowClick,
  _onEditTask,
  refreshKey,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [sorting, setSorting] = useState([{ id: "created_at", desc: true }]);
  const [rowSelection, setRowSelection] = useState({});
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const debouncedSearch = useDebounce(searchQuery.trim(), 500);

  // Fetch task list
  const apiEndpoint = observeId
    ? endpoints.project.getEvalTaskList
    : endpoints.project.getEvalTasksWithProjectName;

  const { data, isLoading, refetch } = useQuery({
    queryKey: [
      "eval-tasks",
      observeId,
      page,
      pageSize,
      debouncedSearch,
      sorting,
      refreshKey,
    ],
    queryFn: async () => {
      const params = {
        page: page + 1,
        page_size: pageSize,
      };
      if (observeId) params.project_id = observeId;
      if (debouncedSearch) params.search = debouncedSearch;

      // Map tanstack column IDs (camelCase) → backend column IDs (snake_case).
      // Backend expects snake_case sort keys; the DataTable column IDs are camelCase
      // for display consistency.
      const SORT_FIELD_MAP = {
        name: "name",
        projectName: "project_name",
        samplingRate: "sampling_rate",
        status: "status",
        created_at: "created_at",
        last_run: "last_run",
      };
      const rawSortId = sorting[0]?.id || "created_at";
      const sortField = SORT_FIELD_MAP[rawSortId] || rawSortId;
      const sortDir = sorting[0]?.desc ? "desc" : "asc";
      params.sort_by = sortField;
      params.sort_order = sortDir;

      const { data: resp } = await axios.get(apiEndpoint(), { params });
      return resp?.result;
    },
    keepPreviousData: true,
  });

  const items = useMemo(
    () =>
      data?.table ||
      data?.tasks ||
      data?.results ||
      data?.data ||
      (Array.isArray(data) ? data : []),
    [data],
  );
  const total =
    data?.metadata?.total_count ||
    data?.total ||
    data?.total_count ||
    items.length;

  // Pause/Resume mutations
  const { mutate: pauseTask } = useMutation({
    mutationFn: (taskId) => axios.post(endpoints.project.pauseEvalTask(taskId)),
    onSuccess: () => refetch(),
  });

  const { mutate: resumeTask } = useMutation({
    mutationFn: (taskId) =>
      axios.post(endpoints.project.resumeEvalTask(taskId)),
    onSuccess: () => refetch(),
  });

  // Delete mutation
  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      const ids = Array.isArray(deleteTarget) ? deleteTarget : [deleteTarget];
      await axios.post(endpoints.project.markEvalsDeleted(), {
        eval_task_ids: ids.map((r) => r.id || r),
      });
      refetch();
      setDeleteTarget(null);
      setRowSelection({});
    } finally {
      setDeleteLoading(false);
    }
  }, [deleteTarget, refetch]);

  // Selected rows
  const selectedItems = useMemo(() => {
    return Object.keys(rowSelection)
      .filter((key) => rowSelection[key])
      .map((key) => items[parseInt(key, 10)])
      .filter(Boolean);
  }, [rowSelection, items]);

  // Columns
  const columns = useMemo(() => {
    const cols = [
      {
        id: "name",
        accessorKey: "name",
        header: "Task Name",
        meta: { flex: 1.2 },
        minSize: 180,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {getValue()}
          </Typography>
        ),
      },
    ];

    // Show project name only when not filtered by project
    if (!observeId) {
      cols.push({
        id: "projectName",
        accessorKey: "project_name",
        header: "Project",
        size: 150,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>
            {getValue() || "—"}
          </Typography>
        ),
      });
    }

    cols.push(
      {
        id: "evalsApplied",
        accessorKey: "evals_applied",
        header: "Eval Metrics",
        size: 200,
        enableSorting: false,
        cell: ({ getValue }) => <EvalChips evals={getValue()} />,
      },
      {
        id: "filtersApplied",
        accessorKey: "filters_applied",
        header: "Filters",
        size: 180,
        enableSorting: false,
        cell: ({ getValue }) => <FilterSummary filtersApplied={getValue()} />,
      },
      {
        id: "samplingRate",
        accessorKey: "sampling_rate",
        header: "Sampling",
        size: 90,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: "13px" }}>
            {getValue()}%
          </Typography>
        ),
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 140,
        cell: ({ getValue, row }) => {
          const status = getValue()?.toLowerCase();
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <StatusBadge status={status} />
              {status === "running" && (
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    pauseTask(row.original.id);
                  }}
                  sx={{ p: 0.25 }}
                >
                  <Iconify
                    icon="solar:pause-circle-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              )}
              {status === "paused" && (
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    resumeTask(row.original.id);
                  }}
                  sx={{ p: 0.25 }}
                >
                  <Iconify
                    icon="solar:play-circle-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              )}
            </Box>
          );
        },
      },
      {
        id: "created_at",
        accessorKey: "created_at",
        header: "Created",
        size: 110,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val) return null;
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return null;
          }
        },
      },
      {
        id: "last_run",
        accessorKey: "last_run",
        header: "Last Run",
        size: 110,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val)
            return (
              <Typography variant="caption" color="text.disabled">
                —
              </Typography>
            );
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return null;
          }
        },
      },
    );

    return cols;
  }, [observeId, pauseTask, resumeTask]);

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {/* Top Controls */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <FormSearchField
            size="small"
            placeholder="Search tasks..."
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
            searchQuery={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setPage(0);
            }}
          />
        </Box>

        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {selectedItems.length > 0 && (
            <>
              <Typography variant="caption" color="text.secondary">
                {selectedItems.length} selected
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="error"
                startIcon={
                  <Iconify icon="solar:trash-bin-trash-linear" width={16} />
                }
                onClick={() => setDeleteTarget(selectedItems)}
                sx={{ textTransform: "none", fontSize: "12px", height: 32 }}
              >
                Delete
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => setRowSelection({})}
                sx={{ textTransform: "none", fontSize: "12px", height: 32 }}
              >
                Cancel
              </Button>
            </>
          )}
          {selectedItems.length === 0 && (
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon="mingcute:add-line" width={18} />}
              onClick={onCreateTask}
              sx={{ px: 2.5, typography: "body2", textTransform: "none" }}
            >
              Create Task
            </Button>
          )}
        </Box>
      </Box>

      {/* Table */}
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        sorting={sorting}
        onSortingChange={setSorting}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        onRowClick={(row) => onRowClick?.(row)}
        getRowId={(row) => row.id}
        enableSelection
        emptyMessage="No tasks found"
      />

      {/* Pagination */}
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

      {/* Delete confirmation */}
      {deleteTarget && (
        <DeleteConfirmation
          open={Boolean(deleteTarget)}
          title={`Delete ${Array.isArray(deleteTarget) ? deleteTarget.length : 1} task(s)?`}
          content="This action cannot be undone. The task(s) and their logs will be permanently removed."
          onClose={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
          isLoading={deleteLoading}
        />
      )}
    </Box>
  );
};

TaskListView.propTypes = {
  observeId: PropTypes.string,
  onCreateTask: PropTypes.func,
  onRowClick: PropTypes.func,
  _onEditTask: PropTypes.func,
  refreshKey: PropTypes.any,
};

export default TaskListView;
