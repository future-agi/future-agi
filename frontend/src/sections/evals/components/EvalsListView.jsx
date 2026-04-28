import { Avatar, Box, Button, Chip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { enqueueSnackbar } from "notistack";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import {
  getStorage,
  setStorage,
  removeStorage,
} from "src/hooks/use-local-storage";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";

import {
  useBulkDeleteEvals,
  useEvalsList,
  useEvalsListCharts,
} from "../hooks/useEvalsList";
import BulkActionsBar from "./BulkActionsBar";
import BulkDeleteDialog from "./BulkDeleteDialog";
import DisplayOptionsPopover, {
  DEFAULT_COLUMN_ORDER,
} from "./DisplayOptionsPopover";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import EvalTypeBadge from "./EvalTypeBadge";
import VolumeBarChart from "src/sections/project/VolumeBarChart";
import ErrorRateSparkline from "./ErrorRateSparkline";
import TypeBadge from "./TypeBadge";
import VersionBadge from "./VersionBadge";
import { EVAL_TAGS, TAG_LOOKUP } from "../constant";

// ── TagsCell ──
// Renders tags in a single non-wrapping row, showing as many as fit in
// the available cell width. Overflow collapses into a `+N` chip with a
// CustomTooltip listing the hidden tag labels.
const TAG_GAP_PX = 4; // matches sx gap: 0.5 → 4px
const PLUS_CHIP_RESERVE_PX = 44; // rough reservation for the "+N" chip

const TagsCell = ({ tags }) => {
  const containerRef = useRef(null);
  const measureRef = useRef(null);
  const [visibleCount, setVisibleCount] = useState(tags?.length || 0);

  const allTags = useMemo(() => tags || [], [tags]);

  const recompute = useCallback(() => {
    if (!containerRef.current || !measureRef.current) return;
    const containerWidth = containerRef.current.clientWidth;
    const chips = Array.from(measureRef.current.children);
    if (!chips.length) {
      setVisibleCount(0);
      return;
    }

    // Try fitting progressively. Reserve space for the "+N" chip unless
    // every tag fits.
    let used = 0;
    let fit = 0;
    for (let i = 0; i < chips.length; i++) {
      const w = chips[i].getBoundingClientRect().width;
      const withGap = i === 0 ? w : w + TAG_GAP_PX;
      const wouldOverflow =
        used + withGap >
        (i === chips.length - 1
          ? containerWidth
          : containerWidth - PLUS_CHIP_RESERVE_PX);
      if (wouldOverflow) break;
      used += withGap;
      fit = i + 1;
    }
    // Always show at least one tag if the container has any room.
    if (fit === 0 && chips.length > 0) fit = 1;
    setVisibleCount(fit);
  }, []);

  useEffect(() => {
    recompute();
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => recompute());
    ro.observe(el);
    return () => ro.disconnect();
  }, [recompute, allTags]);

  if (!allTags.length) return null;

  const visible = allTags.slice(0, visibleCount);
  const hidden = allTags.slice(visibleCount);

  return (
    <Box
      ref={containerRef}
      sx={{
        display: "flex",
        gap: 0.5,
        flexWrap: "nowrap",
        overflow: "hidden",
        width: "100%",
        alignItems: "center",
      }}
    >
      {/* Hidden measurement row — renders all tags off-screen so we can
          read their natural widths. */}
      <Box
        ref={measureRef}
        aria-hidden
        sx={{
          position: "absolute",
          visibility: "hidden",
          pointerEvents: "none",
          display: "flex",
          gap: 0.5,
          left: -9999,
          top: -9999,
        }}
      >
        {allTags.map((tag) => {
          const tagDef = TAG_LOOKUP[tag];
          return (
            <Chip
              key={`measure-${tag}`}
              icon={
                tagDef?.icon ? (
                  <Iconify icon={tagDef.icon} width={12} />
                ) : undefined
              }
              label={tagDef?.label || tag}
              size="small"
              variant="outlined"
              sx={{ fontSize: "11px", height: 22 }}
            />
          );
        })}
      </Box>

      {visible.map((tag) => {
        const tagDef = TAG_LOOKUP[tag];
        return (
          <Chip
            key={tag}
            icon={
              tagDef?.icon ? (
                <Iconify icon={tagDef.icon} width={12} />
              ) : undefined
            }
            label={tagDef?.label || tag}
            size="small"
            variant="outlined"
            sx={{ fontSize: "11px", height: 22, flexShrink: 0 }}
          />
        );
      })}

      {hidden.length > 0 && (
        <CustomTooltip
          show
          title={hidden.map((t) => TAG_LOOKUP[t]?.label || t).join(", ")}
          arrow
          size="small"
        >
          <Chip
            label={`+${hidden.length}`}
            size="small"
            variant="outlined"
            sx={{
              fontSize: "11px",
              height: 22,
              cursor: "default",
              flexShrink: 0,
            }}
          />
        </CustomTooltip>
      )}
    </Box>
  );
};

TagsCell.propTypes = {
  tags: PropTypes.arrayOf(PropTypes.string),
};

// ── Static filter fields ──
const STATIC_FILTER_FIELDS = [
  {
    value: "eval_type",
    label: "Eval Type",
    type: "enum",
    choices: ["agent", "llm", "code"],
  },
  {
    value: "output_type",
    label: "Output Type",
    type: "enum",
    choices: ["pass_fail", "percentage", "deterministic"],
  },
  {
    value: "template_type",
    label: "Type",
    type: "enum",
    choices: ["single", "composite"],
  },
  {
    value: "tags",
    label: "Tags",
    type: "enum",
    choices: EVAL_TAGS.map((t) => t.value),
  },
];

// ── Helpers ──

const AVATAR_COLORS = [
  "#7C4DFF",
  "#FF6B6B",
  "#5BE49B",
  "#FFB547",
  "#36B5FF",
  "#FF85C0",
  "#00BFA6",
  "#8C9EFF",
];

function getAvatarColor(name) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

const OUTPUT_TYPE_LABELS = {
  pass_fail: "Pass/fail",
  percentage: "Percentage",
  deterministic: "Choices",
};

const COLUMN_VISIBILITY_STORAGE_KEY = "evals-list-column-visibility";
const COLUMN_ORDER_STORAGE_KEY = "evals-list-column-order";

// Merge stored order with the canonical default — drop unknown ids, append
// any new fields that were added after the user persisted their order.
const mergeColumnOrder = (stored) => {
  if (!Array.isArray(stored) || stored.length === 0)
    return DEFAULT_COLUMN_ORDER;
  const valid = stored.filter((id) => DEFAULT_COLUMN_ORDER.includes(id));
  const missing = DEFAULT_COLUMN_ORDER.filter((id) => !valid.includes(id));
  return [...valid, ...missing];
};

// Sort field map — frontend column id → backend sort key
const SORT_FIELD_MAP = {
  name: "name",
  lastUpdated: "updated_at",
  created_by_name: "created_at",
};

// ── Component ──

const EvalsListView = () => {
  const navigate = useNavigate();

  // State
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [sorting, setSorting] = useState([{ id: "lastUpdated", desc: true }]);
  const [rowSelection, setRowSelection] = useState({});
  const [filters, setFilters] = useState(null);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [displayAnchorEl, setDisplayAnchorEl] = useState(null);
  const [columnVisibility, setColumnVisibility] = useState(
    () => getStorage(COLUMN_VISIBILITY_STORAGE_KEY) || {},
  );
  const [columnOrder, setColumnOrder] = useState(() =>
    mergeColumnOrder(getStorage(COLUMN_ORDER_STORAGE_KEY)),
  );
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const debouncedSearch = useDebounce(searchQuery.trim(), 500);

  // Derive API params
  const ownerFilter = filters?.owner_not
    ? (filters.owner_not === "system" ? "user" : "all")
    : (filters?.owner || "all");
  // `filters.name` from the dropdown is an exact-match list; a single typed
  // value falls back to `search` (fuzzy name__icontains). Multi-select is
  // sent as `filters.names` (name__in).
  const filterSearch = useMemo(() => {
    if (!filters) return null;
    if (filters.search) return filters.search;
    if (typeof filters.name === "string" && filters.name) return filters.name;
    return null;
  }, [filters]);
  const apiFilters = useMemo(() => {
    if (!filters) return null;
    const f = {};
    if (filters.eval_type) f.eval_type = filters.eval_type;
    if (filters.eval_type_not) f.eval_type_not = filters.eval_type_not;
    if (filters.output_type) f.output_type = filters.output_type;
    if (filters.output_type_not) f.output_type_not = filters.output_type_not;
    if (filters.template_type) f.template_type = filters.template_type;
    if (filters.tags) f.tags = filters.tags;
    if (filters.created_by) f.created_by = filters.created_by;
    if (filters.created_by_not) f.created_by_not = filters.created_by_not;
    if (Array.isArray(filters.name) && filters.name.length > 0) {
      f.names = filters.name;
    } else if (Array.isArray(filters.names) && filters.names.length > 0) {
      f.names = filters.names;
    }
    return Object.keys(f).length > 0 ? f : null;
  }, [filters]);

  // Keep the filter-panel state as a single "Created By" field for display/edit
  // even though backend-facing state may include both owner scope + created_by.
  const panelFilters = useMemo(() => {
    if (!filters) return null;
    const f = { ...filters };

    // Reconstruct negated owner display from created_by_not + owner_not
    if (Array.isArray(filters.created_by_not) && filters.created_by_not.length > 0) {
      const negated = [...filters.created_by_not];
      if (filters.owner_not === "system") negated.push("System");
      f.owner_not = negated;
    } else if (filters.owner_not === "system") {
      f.owner_not = ["System"];
    }

    if (Array.isArray(filters.created_by) && filters.created_by.length > 0) {
      f.owner = [...filters.created_by];
    } else if (filters.owner === "system") {
      f.owner = ["System"];
    } else if (filters.owner === "user" || filters.owner === "all") {
      delete f.owner;
    }

    delete f.created_by;
    delete f.created_by_not;
    return f;
  }, [filters]);

  // Map TanStack sorting to API params
  const sortBy = sorting[0]
    ? SORT_FIELD_MAP[sorting[0].id] || "updated_at"
    : "updated_at";
  const sortOrder = sorting[0]?.desc ? "desc" : "asc";

  // Data fetching
  const { data, isLoading } = useEvalsList({
    page,
    pageSize,
    search: debouncedSearch || filterSearch || null,
    ownerFilter,
    filters: apiFilters,
    sortBy,
    sortOrder,
  });

  const bulkDelete = useBulkDeleteEvals();

  const items = data?.items || [];
  const total = data?.total || 0;

  // Fetch all eval names for filter dropdowns
  const { data: allEvalNames } = useQuery({
    queryKey: ["evals", "all-names"],
    queryFn: async () => {
      const { data: resp } = await axios.post(
        endpoints.develop.eval.getEvalNames,
        {},
      );
      return resp?.result || resp || [];
    },
    staleTime: 60 * 1000,
  });

  // Accumulate creators seen across renders so active filters don't shrink
  // the dropdown. Without this, filtering by one creator removes the others
  // from the list (since they're derived from the currently-loaded page).
  const creatorsRef = useRef(new Set());
  const [creatorsVersion, setCreatorsVersion] = useState(0);
  useEffect(() => {
    let changed = false;
    for (const item of items) {
      const c = item?.created_by_name;
      if (c && !creatorsRef.current.has(c)) {
        creatorsRef.current.add(c);
        changed = true;
      }
    }
    if (changed) setCreatorsVersion((v) => v + 1);
  }, [items]);

  const filterFields = useMemo(() => {
    const evalNames = (allEvalNames || [])
      .map((e) => e.name)
      .filter(Boolean)
      .sort();
    const creators = [...creatorsRef.current].sort();
    return [
      { value: "name", label: "Name", type: "enum", choices: evalNames },
      { value: "owner", label: "Created By", type: "enum", choices: creators },
      ...STATIC_FILTER_FIELDS,
    ];
    // creatorsVersion is the reactivity bump — the real data is in the ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allEvalNames, creatorsVersion]);

  // Fetch 30-day charts separately (ClickHouse, async)
  const templateIds = useMemo(() => items.map((item) => item.id), [items]);
  const { data: chartsData, isLoading: chartsLoading } =
    useEvalsListCharts(templateIds);

  // Selected rows
  const selectedItems = useMemo(() => {
    return Object.keys(rowSelection)
      .filter((key) => rowSelection[key])
      .map((key) => items[parseInt(key, 10)])
      .filter(Boolean);
  }, [rowSelection, items]);

  // Column definitions — TanStack format
  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Evaluation Name",
        meta: { flex: 1.5 },
        minSize: 200,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "thirtyDayChart",
        accessorKey: "id",
        header: "30 day chart",
        size: 160,
        enableSorting: false,
        cell: ({ row }) => {
          const chartInfo = chartsData?.[row.original.id];
          if (chartsLoading)
            return (
              <Box
                sx={{
                  py: 0.5,
                  width: "100%",
                  height: 32,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  animation: "pulse 1.5s ease-in-out infinite",
                  "@keyframes pulse": {
                    "0%,100%": { opacity: 0.4 },
                    "50%": { opacity: 0.7 },
                  },
                }}
              />
            );
          return (
            <Box sx={{ width: "100%", overflow: "hidden" }}>
              <VolumeBarChart
                dailyVolume={chartInfo?.chart?.map((d) => d.value) || []}
                height={22}
              />
            </Box>
          );
        },
      },
      {
        id: "thirtyDayErrorRate",
        accessorKey: "id",
        header: "30 day error rate",
        size: 160,
        enableSorting: false,
        cell: ({ row }) => {
          const chartInfo = chartsData?.[row.original.id];
          if (chartsLoading)
            return (
              <Box
                sx={{
                  py: 0.5,
                  width: "100%",
                  height: 32,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  animation: "pulse 1.5s ease-in-out infinite",
                  "@keyframes pulse": {
                    "0%,100%": { opacity: 0.4 },
                    "50%": { opacity: 0.7 },
                  },
                }}
              />
            );
          return (
            <Box sx={{ width: "100%", overflow: "hidden" }}>
              <ErrorRateSparkline
                dailyValues={chartInfo?.error_rate?.map((d) => d.value) || []}
                height={22}
              />
            </Box>
          );
        },
      },
      {
        id: "evalType",
        accessorKey: "eval_type",
        header: "Eval Type",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => <EvalTypeBadge type={getValue()} />,
      },
      {
        id: "tags",
        accessorKey: "tags",
        header: "Tags",
        size: 200,
        enableSorting: false,
        cell: ({ getValue }) => <TagsCell tags={getValue()} />,
      },
      {
        id: "createdByName",
        accessorKey: "created_by_name",
        header: "Created By",
        size: 150,
        cell: ({ getValue }) => {
          const name = getValue() || "Unknown";
          const isSystem = name === "System";
          return (
            <CustomTooltip show title={name} placement="top" arrow size="small">
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Avatar
                  sx={{
                    width: 24,
                    height: 24,
                    fontSize: "10px",
                    fontWeight: 700,
                    bgcolor: isSystem
                      ? "action.selected"
                      : getAvatarColor(name),
                    color: isSystem ? "text.secondary" : "common.white",
                  }}
                >
                  {isSystem ? (
                    <Iconify icon="solar:shield-check-bold" width={14} />
                  ) : (
                    getInitials(name)
                  )}
                </Avatar>
                <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>
                  {name}
                </Typography>
              </Box>
            </CustomTooltip>
          );
        },
      },
      {
        id: "lastUpdated",
        accessorKey: "last_updated",
        header: "Last updated",
        size: 120,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val) return null;
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return null;
          }
        },
      },
      {
        id: "currentVersion",
        accessorKey: "current_version",
        header: "Versions",
        size: 90,
        enableSorting: false,
        cell: ({ getValue, row }) => {
          const version = getValue();
          const isDraft = row.original.is_draft || version === "draft";
          if (isDraft || !version) return null;
          return <VersionBadge version={version} />;
        },
      },
      {
        id: "outputType",
        accessorKey: "output_type",
        header: "Output Type",
        size: 120,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>
            {OUTPUT_TYPE_LABELS[getValue()] || getValue()}
          </Typography>
        ),
      },
      {
        id: "templateType",
        accessorKey: "template_type",
        header: "Type",
        size: 110,
        enableSorting: false,
        cell: ({ getValue }) => <TypeBadge type={getValue()} />,
      },
    ],
    [chartsData, chartsLoading],
  );

  // Apply user-defined order to the columns array. The `name` column is
  // locked and always rendered first; selection column (if any) stays too.
  const orderedColumns = useMemo(() => {
    const byId = new Map(columns.map((c) => [c.id, c]));
    const nameCol = byId.get("name");
    const ordered = columnOrder.map((id) => byId.get(id)).filter(Boolean);
    const seen = new Set(ordered.map((c) => c.id));
    if (nameCol) seen.add("name");
    const leftover = columns.filter((c) => !seen.has(c.id) && c.id !== "name");
    return [...(nameCol ? [nameCol] : []), ...ordered, ...leftover];
  }, [columns, columnOrder]);

  // Hidden columns — convert array to TanStack visibility map
  const hiddenColumns = useMemo(
    () => Object.keys(columnVisibility).filter((k) => !columnVisibility[k]),
    [columnVisibility],
  );

  const handleToggleColumn = useCallback((field) => {
    setColumnVisibility((prev) => {
      const next = {
        ...prev,
        [field]: prev[field] === false ? true : false,
      };
      setStorage(COLUMN_VISIBILITY_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const handleResetColumns = useCallback(() => {
    removeStorage(COLUMN_VISIBILITY_STORAGE_KEY);
    removeStorage(COLUMN_ORDER_STORAGE_KEY);
    setColumnVisibility({});
    setColumnOrder(DEFAULT_COLUMN_ORDER);
  }, []);

  const handleReorderColumns = useCallback((nextOrder) => {
    setColumnOrder(nextOrder);
    setStorage(COLUMN_ORDER_STORAGE_KEY, nextOrder);
  }, []);

  const handleColumnVisibilityChange = useCallback((updater) => {
    setColumnVisibility((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      setStorage(COLUMN_VISIBILITY_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const activeFilterCount = useMemo(() => {
    if (!panelFilters) return 0;
    return Object.keys(panelFilters).filter(
      (k) => k !== "_tokens" && panelFilters[k],
    ).length;
  }, [panelFilters]);

  const handleCancelSelection = useCallback(() => {
    setRowSelection({});
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    // System-owned evals cannot be deleted — filter them out and warn
    // the user if any were in the selection.
    const systemItems = selectedItems.filter((item) => item.owner === "system");
    const deletable = selectedItems.filter((item) => item.owner !== "system");

    if (systemItems.length > 0) {
      enqueueSnackbar(
        deletable.length > 0
          ? `${systemItems.length} system eval${
              systemItems.length > 1 ? "s" : ""
            } cannot be deleted and ${
              systemItems.length > 1 ? "were" : "was"
            } skipped.`
          : "System evals cannot be deleted.",
        { variant: "warning" },
      );
    }

    if (deletable.length === 0) {
      setDeleteDialogOpen(false);
      handleCancelSelection();
      return;
    }

    const ids = deletable.map((item) => item.id);
    await bulkDelete.mutateAsync(ids);
    setDeleteDialogOpen(false);
    handleCancelSelection();
  }, [selectedItems, bulkDelete, handleCancelSelection]);
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
        {/* Left: search + filters */}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <FormSearchField
            size="small"
            placeholder="Search"
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
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="mage:filter" width={16} />}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
            onClick={(e) => setFilterAnchorEl(e.currentTarget)}
            sx={{
              textTransform: "none",
              fontSize: "13px",
              height: "32px",
              borderColor: activeFilterCount > 0 ? "primary.main" : "divider",
              color: activeFilterCount > 0 ? "primary.main" : "text.secondary",
            }}
          >
            Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:list-check-bold" width={16} />}
            onClick={(e) => setDisplayAnchorEl(e.currentTarget)}
            sx={{
              textTransform: "none",
              fontSize: "13px",
              height: "32px",
              borderColor:
                hiddenColumns.length > 0 ? "primary.main" : "divider",
              color:
                hiddenColumns.length > 0 ? "primary.main" : "text.secondary",
            }}
          >
            Columns
          </Button>
        </Box>

        {/* Right: bulk actions or create button */}
        <Box>
          {selectedItems.length > 0 ? (
            <BulkActionsBar
              selectedCount={selectedItems.length}
              onDelete={() => setDeleteDialogOpen(true)}
              onCancel={handleCancelSelection}
            />
          ) : (
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon="mingcute:add-line" width={18} />}
              onClick={() => navigate("/dashboard/evaluations/create")}
              sx={{ px: 2.5, typography: "body2", textTransform: "none" }}
            >
              Create evals
            </Button>
          )}
        </Box>
      </Box>

      {/* Quick tag filters */}
      <Box
        sx={{
          display: "flex",
          gap: 0.5,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {EVAL_TAGS.map((tag) => {
          const activeTagValues = /** @type {string[]} */ (filters?.tags || []);
          const tagValues = tag.match || [tag.value];
          const isActive = tagValues.some((v) => activeTagValues.includes(v));
          return (
            <Chip
              key={tag.value}
              icon={<Iconify icon={tag.icon} width={14} />}
              label={tag.label}
              size="small"
              variant={isActive ? "filled" : "outlined"}
              color={isActive ? "primary" : "default"}
              onClick={() => {
                if (isActive) {
                  const toRemove = new Set(tagValues);
                  setFilters((prev) => {
                    const safe = prev || {};
                    const remaining = /** @type {string[]} */ (
                      safe.tags || []
                    ).filter((/** @type {string} */ v) => !toRemove.has(v));
                    if (!remaining.length) {
                      const next = { ...safe };
                      delete next.tags;
                      return Object.keys(next).length ? next : null;
                    }
                    return { ...safe, tags: remaining };
                  });
                } else {
                  setFilters((prev) => {
                    const safe = prev || {};
                    return {
                      ...safe,
                      tags: [
                        .../** @type {string[]} */ (safe.tags || []),
                        ...tagValues,
                      ],
                    };
                  });
                }
                setPage(0);
              }}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          );
        })}
        {filters?.tags && (
          <Chip
            label="Clear"
            size="small"
            variant="outlined"
            onDelete={() => {
              setFilters((prev) => {
                const next = { ...prev };
                delete next.tags;
                return next;
              });
              setPage(0);
            }}
            sx={{ fontSize: "11px", height: 26 }}
          />
        )}
      </Box>

      {/* Table */}
      <DataTable
        columns={orderedColumns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        sorting={sorting}
        onSortingChange={setSorting}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        columnVisibility={columnVisibility}
        onColumnVisibilityChange={handleColumnVisibilityChange}
        onRowClick={(row) => navigate(`/dashboard/evaluations/${row.id}`)}
        getRowId={(row) => row.id}
        enableSelection
        emptyMessage="No evaluations found"
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

      {/* Filter panel */}
      <FilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        filterFields={filterFields}
        currentFilters={panelFilters}
        onApply={(result) => {
          // FilterPanel Basic tab returns {field: [values]}, Query tab returns [{field, op, value}]
          if (!result) {
            setFilters(null);
            setPage(0);
            return;
          }
          if (Array.isArray(result)) {
            // Query tab — convert token array to flat object
            const flat = {};
            for (const t of result) {
              const val = Array.isArray(t.value)
                ? t.value
                : t.value
                  ? [t.value]
                  : [];
              if (!val.length) continue;
              if (t.field === "owner") {
                const isNeg = t.operator === "is_not" || t.operator === "not_equals";
                const hasSystem = val.some((v) => v.toLowerCase() === "system");
                const userNames = val.filter(
                  (v) => v.toLowerCase() !== "system",
                );
                if (isNeg) {
                  if (hasSystem) flat.owner_not = "system";
                  if (userNames.length) flat.created_by_not = userNames;
                } else {
                  if (hasSystem && !userNames.length) flat.owner = "system";
                  else if (!hasSystem && userNames.length) flat.owner = "user";
                  if (userNames.length) flat.created_by = userNames;
                }
              } else if (t.field === "name") {
                // Single free-text entry → fuzzy `search`; everything else
                // (including single dropdown pick) → exact match via `name`.
                // The `apiFilters` memo handles the final name → names/search
                // transform.
                if (t.operator === "contains" && val.length === 1) {
                  flat.search = val[0];
                } else {
                  flat.name = val;
                }
              } else {
                const isNeg = t.operator === "is_not" || t.operator === "not_equals";
                const key = isNeg ? `${t.field}_not` : t.field;
                flat[key] = val;
              }
            }
            setFilters(Object.keys(flat).length > 0 ? flat : null);
          } else {
            // Basic tab — already a flat object {field: [values]}.
            // Leave `flat.name` alone (may be string or array) — `apiFilters`
            // decides between `names` (exact multi) and `search` (fuzzy one).
            const flat = { ...result };
            const isNegOwner = Boolean(flat.owner_not);
            const rawOwner = flat.owner_not || flat.owner;
            if (rawOwner) {
              const vals = Array.isArray(rawOwner) ? rawOwner : [rawOwner];
              const hasSystem = vals.some((v) => v.toLowerCase() === "system");
              const userNames = vals.filter((v) => v.toLowerCase() !== "system");
              delete flat.owner;
              delete flat.owner_not;
              if (isNegOwner) {
                if (hasSystem) flat.owner_not = "system";
                if (userNames.length) flat.created_by_not = userNames;
              } else {
                if (hasSystem && !userNames.length) flat.owner = "system";
                else if (!hasSystem && userNames.length) flat.owner = "user";
                if (userNames.length) flat.created_by = userNames;
              }
            }
            setFilters(Object.keys(flat).length > 0 ? flat : null);
          }
          setPage(0);
        }}
        aiPlaceholder="e.g. 'show agent evals tagged Red Teaming'"
      />

      {/* Display options popover */}
      <DisplayOptionsPopover
        anchorEl={displayAnchorEl}
        open={Boolean(displayAnchorEl)}
        onClose={() => setDisplayAnchorEl(null)}
        hiddenColumns={hiddenColumns}
        onToggleColumn={handleToggleColumn}
        onReset={handleResetColumns}
        columnOrder={columnOrder}
        onReorderColumns={handleReorderColumns}
      />

      {/* Delete confirmation */}
      <BulkDeleteDialog
        open={deleteDialogOpen}
        count={selectedItems.length}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteDialogOpen(false)}
        isLoading={bulkDelete.isLoading}
      />
    </Box>
  );
};

export default EvalsListView;
