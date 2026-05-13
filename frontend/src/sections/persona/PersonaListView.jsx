import {
  Avatar,
  Box,
  Button,
  Chip,
  IconButton,
  Tooltip,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import PropTypes from "prop-types";
import { useCallback, useMemo, useState } from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useBulkDeletePersonas,
  useGetPersonasPaginated,
} from "src/api/persona/persona";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PersonaCreateEditDrawer from "./PersonaCreateEdit/PersonaCreateEditDrawer";
import PersonaInfoDrawer from "./PersonaInfo/PersonaInfoDrawer";
import PersonasBulkActionsBar from "./components/PersonasBulkActionsBar";
import PersonasBulkDeleteDialog from "./components/PersonasBulkDeleteDialog";
import PersonasColumnsPopover from "./components/PersonasColumnsPopover";
import { extractTagsFromPersona } from "./common";

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

const getAvatarColor = (name) => {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
};

const getInitials = (name) => {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
};

const QUICK_FILTERS = [
  { value: null, label: "All", icon: "solar:widget-5-bold-duotone" },
  {
    value: "prebuilt",
    label: "Future AGI Built",
    icon: "solar:shield-check-bold-duotone",
  },
  { value: "custom", label: "Custom", icon: "solar:user-id-bold-duotone" },
];

const SIMULATION_FILTERS = [
  { value: null, label: "All Types" },
  { value: AGENT_TYPES.VOICE, label: "Voice", icon: "solar:microphone-3-bold" },
  {
    value: AGENT_TYPES.CHAT,
    label: "Chat",
    icon: "solar:chat-round-line-bold",
  },
];

const PersonaListView = ({
  isSelectable = false,
  selectedPersonas = [],
  onToggleSelect,
  onCreatePersona,
  personaCreateEditType = null,
}) => {
  const { role } = useAuthContext();
  const canCreate = RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];

  // Each filter holds `is` (include) and `is_not` (exclude) buckets so a
  // field can carry both clauses at once.
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [typeFilter, setTypeFilter] = useState({ is: [], is_not: [] });
  const [simulationFilter, setSimulationFilter] = useState(
    isSelectable && personaCreateEditType
      ? { is: [personaCreateEditType], is_not: [] }
      : { is: [], is_not: [] },
  );
  const [rowSelection, setRowSelection] = useState({});
  const [columnVisibility, setColumnVisibility] = useState({});
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [columnsAnchorEl, setColumnsAnchorEl] = useState(null);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);

  const [infoDrawer, setInfoDrawer] = useState({ open: false, persona: null });
  const [editDrawer, setEditDrawer] = useState({
    mode: null,
    persona: null,
    personaCreateEditType: null,
  });

  const debouncedSearch = useDebounce(searchQuery.trim(), 400);

  // Canonical clause shape shared with ComplexFilter / _apply_filters / FilterEngine.
  const filterClauses = useMemo(() => {
    const out = [];
    const push = (columnId, state) => {
      if (state.is.length) {
        out.push({
          column_id: columnId,
          filter_config: { filter_op: "is", filter_value: state.is },
        });
      }
      if (state.is_not.length) {
        out.push({
          column_id: columnId,
          filter_config: { filter_op: "is_not", filter_value: state.is_not },
        });
      }
    };
    push("type", typeFilter);
    push("simulation_type", simulationFilter);
    return out;
  }, [typeFilter, simulationFilter]);

  const { data, isLoading, isFetching } = useGetPersonasPaginated({
    page: page + 1,
    pageSize,
    search: debouncedSearch || null,
    filterClauses,
  });

  const items = useMemo(() => data?.results || [], [data]);
  const total = data?.count || 0;

  const bulkDelete = useBulkDeletePersonas();

  // In picker mode, derive rowSelection from the externally-controlled
  // `selectedPersonas` prop so selection is preserved across pages.
  const effectiveRowSelection = useMemo(() => {
    if (!isSelectable) return rowSelection;
    const selectedIds = new Set(selectedPersonas.map((p) => p.id));
    const map = {};
    items.forEach((item, idx) => {
      if (selectedIds.has(item.id)) map[idx] = true;
    });
    return map;
  }, [isSelectable, selectedPersonas, items, rowSelection]);

  const handleRowSelectionChange = useCallback(
    (next) => {
      if (!isSelectable) {
        setRowSelection(next);
        return;
      }
      const selectedIds = new Set(selectedPersonas.map((p) => p.id));
      items.forEach((item, idx) => {
        const wasSelected = selectedIds.has(item.id);
        const nowSelected = Boolean(next?.[idx]);
        if (wasSelected !== nowSelected) {
          onToggleSelect?.(item, nowSelected);
        }
      });
    },
    [isSelectable, selectedPersonas, items, onToggleSelect],
  );

  const selectedItems = useMemo(
    () =>
      Object.keys(rowSelection)
        .filter((key) => rowSelection[key])
        .map((key) => items[parseInt(key, 10)])
        .filter(Boolean),
    [rowSelection, items],
  );

  const deletableSelected = useMemo(
    () => selectedItems.filter((p) => !p?.isDefault),
    [selectedItems],
  );

  const handleCancelSelection = useCallback(() => {
    setRowSelection({});
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    const ids = deletableSelected.map((p) => p.id);
    if (!ids.length) {
      setDeleteDialogOpen(false);
      return;
    }
    const { deleted, failed } = await bulkDelete.mutateAsync(ids);
    setDeleteDialogOpen(false);
    handleCancelSelection();
    if (deleted > 0) {
      enqueueSnackbar(`${deleted} persona${deleted !== 1 ? "s" : ""} deleted`, {
        variant: "success",
      });
    }
    if (failed.length) {
      enqueueSnackbar(
        `${failed.length} persona${failed.length !== 1 ? "s" : ""} could not be deleted`,
        { variant: "error" },
      );
    }
  }, [deletableSelected, bulkDelete, handleCancelSelection]);

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 1.5 },
        minSize: 220,
        enableSorting: false,
        cell: ({ row }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{ fontWeight: 500, fontSize: "13px" }}
          >
            {row.original?.name}
          </Typography>
        ),
      },
      {
        id: "description",
        accessorKey: "description",
        header: "Description",
        meta: { flex: 2 },
        minSize: 240,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Tooltip title={getValue() || ""} placement="top" arrow>
            <Typography
              variant="body2"
              noWrap
              sx={{ fontSize: "13px", color: "text.secondary" }}
            >
              {getValue() || "—"}
            </Typography>
          </Tooltip>
        ),
      },
      {
        id: "simulationType",
        accessorKey: "simulationType",
        header: "Agent Type",
        size: 110,
        enableSorting: false,
        cell: ({ getValue }) => {
          const type = getValue();
          const isVoice = type === AGENT_TYPES.VOICE;
          return (
            <Chip
              size="small"
              icon={
                <Iconify
                  icon={
                    isVoice
                      ? "solar:microphone-3-bold"
                      : "solar:chat-round-line-bold"
                  }
                  width={12}
                />
              }
              label={isVoice ? "Voice" : "Chat"}
              variant="outlined"
              sx={{
                fontSize: "11px",
                height: 22,
                borderColor: "divider",
              }}
            />
          );
        },
      },
      {
        id: "tags",
        accessorKey: "id",
        header: "Attributes",
        meta: { flex: 1.4 },
        minSize: 200,
        enableSorting: false,
        cell: ({ row }) => {
          const tags = extractTagsFromPersona(row.original);
          if (!tags.length) return null;
          const displayed = tags.slice(0, 2);
          const remaining = tags.length - displayed.length;
          return (
            <Box
              sx={{
                display: "flex",
                gap: 0.5,
                flexWrap: "nowrap",
                overflow: "hidden",
              }}
            >
              {displayed.map((tag) => (
                <Chip
                  key={tag}
                  label={tag}
                  size="small"
                  variant="outlined"
                  sx={{ fontSize: "11px", height: 22, maxWidth: 120 }}
                />
              ))}
              {remaining > 0 && (
                <Chip
                  label={`+${remaining}`}
                  size="small"
                  variant="outlined"
                  sx={{ fontSize: "11px", height: 22 }}
                />
              )}
            </Box>
          );
        },
      },
      {
        id: "createdBy",
        accessorKey: "isDefault",
        header: "Created By",
        size: 170,
        enableSorting: false,
        cell: ({ row }) => {
          const isSystem = row.original?.isDefault;
          const name = isSystem ? "System" : "You";
          return (
            <Tooltip
              title={
                isSystem ? "Future AGI built-in persona" : "Workspace persona"
              }
              placement="top"
              arrow
            >
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
            </Tooltip>
          );
        },
      },
      {
        id: "lastUpdated",
        accessorKey: "updatedAt",
        header: "Last updated",
        size: 140,
        enableSorting: false,
        cell: ({ row }) => {
          const val = row.original?.updatedAt || row.original?.updated_at;
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
      ...(isSelectable
        ? []
        : [
            {
              id: "actions",
              accessorKey: "id",
              header: "",
              size: 56,
              enableSorting: false,
              cell: ({ row }) => {
                const persona = row.original;
                const canEdit = !persona?.isDefault && canCreate;
                if (!canEdit) return null;
                return (
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditDrawer({
                        mode: "edit",
                        persona,
                        personaCreateEditType: persona?.simulationType,
                      });
                    }}
                    sx={{ color: "text.secondary" }}
                  >
                    <Iconify icon="solar:pen-linear" width={16} />
                  </IconButton>
                );
              },
            },
          ]),
    ],
    [canCreate, isSelectable],
  );

  const hiddenColumns = useMemo(
    () => Object.keys(columnVisibility).filter((k) => !columnVisibility[k]),
    [columnVisibility],
  );

  const filterFields = useMemo(() => {
    const fields = [
      {
        value: "type",
        label: "Category",
        type: "enum",
        choices: ["prebuilt", "custom"],
      },
    ];
    if (!(isSelectable && personaCreateEditType)) {
      fields.push({
        value: "simulation_type",
        label: "Agent Type",
        type: "enum",
        choices: [AGENT_TYPES.VOICE, AGENT_TYPES.CHAT],
        // Bridge the legacy API value "text" to the user-facing label
        // "Chat" so the AI filter can resolve "chat personas" → text.
        choiceLabels: {
          [AGENT_TYPES.VOICE]: "Voice",
          [AGENT_TYPES.CHAT]: "Chat",
        },
      });
    }
    return fields;
  }, [isSelectable, personaCreateEditType]);

  // Rows shape preserves the operator dropdown selection across reopens.
  const currentFilters = useMemo(() => {
    const rows = [];
    const push = (field, state) => {
      if (state.is.length) {
        rows.push({ field, operator: "is", value: state.is });
      }
      if (state.is_not.length) {
        rows.push({ field, operator: "is_not", value: state.is_not });
      }
    };
    push("type", typeFilter);
    push("simulation_type", simulationFilter);
    return rows.length ? rows : null;
  }, [typeFilter, simulationFilter]);

  const activeFilterCount =
    (typeFilter.is.length || typeFilter.is_not.length ? 1 : 0) +
    (simulationFilter.is.length || simulationFilter.is_not.length ? 1 : 0);

  const handleFilterApply = useCallback(
    (result) => {
      const lockedSim =
        isSelectable && personaCreateEditType ? personaCreateEditType : null;
      const empty = () => ({ is: [], is_not: [] });
      const lockedSimState = () =>
        lockedSim ? { is: [lockedSim], is_not: [] } : empty();
      if (!result) {
        setTypeFilter(empty());
        setSimulationFilter(lockedSimState());
        setPage(0);
        return;
      }
      // Fresh inner arrays per field; shallow spread would alias them.
      const next = { type: empty(), simulation_type: empty() };
      if (Array.isArray(result)) {
        for (const t of result) {
          if (!next[t.field]) continue;
          const vals = Array.isArray(t.value)
            ? t.value
            : t.value
              ? [t.value]
              : [];
          if (!vals.length) continue;
          const bucket = t.operator === "is_not" ? "is_not" : "is";
          next[t.field][bucket].push(...vals);
        }
      } else {
        // Legacy object shape from FilterPanel — operator absent, assume `is`.
        for (const field of Object.keys(next)) {
          const v = result[field];
          if (Array.isArray(v)) next[field].is = v.filter(Boolean);
          else if (v) next[field].is = [v];
        }
      }
      for (const field of Object.keys(next)) {
        next[field].is = Array.from(new Set(next[field].is));
        next[field].is_not = Array.from(new Set(next[field].is_not));
      }
      setTypeFilter(next.type);
      setSimulationFilter(lockedSim ? lockedSimState() : next.simulation_type);
      setPage(0);
    },
    [isSelectable, personaCreateEditType],
  );

  const handleToggleColumn = useCallback((field) => {
    setColumnVisibility((prev) => ({
      ...prev,
      [field]: prev[field] === false ? true : false,
    }));
  }, []);

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
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <FormSearchField
            size="small"
            placeholder="Search personas"
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
            onClick={(e) => setColumnsAnchorEl(e.currentTarget)}
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

        <Box>
          {!isSelectable && selectedItems.length > 0 ? (
            <PersonasBulkActionsBar
              selectedCount={selectedItems.length}
              deletableCount={deletableSelected.length}
              onDelete={() => setDeleteDialogOpen(true)}
              onCancel={handleCancelSelection}
            />
          ) : (
            <Button
              variant="contained"
              color="primary"
              disabled={!canCreate}
              startIcon={<Iconify icon="mingcute:add-line" width={18} />}
              onClick={() => {
                if (onCreatePersona) {
                  onCreatePersona();
                  return;
                }
                setEditDrawer({
                  mode: "create",
                  persona: null,
                  personaCreateEditType: null,
                });
              }}
              sx={{ px: 2.5, typography: "body2", textTransform: "none" }}
            >
              Create persona
            </Button>
          )}
        </Box>
      </Box>

      {/* Quick filter chips */}
      <Box
        sx={{
          display: "flex",
          gap: 0.75,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {QUICK_FILTERS.map((f) => {
          const isActive =
            typeFilter.is_not.length === 0 &&
            (f.value === null
              ? typeFilter.is.length === 0
              : typeFilter.is.length === 1 && typeFilter.is[0] === f.value);
          return (
            <Chip
              key={f.label}
              icon={<Iconify icon={f.icon} width={14} />}
              label={f.label}
              size="small"
              variant={isActive ? "filled" : "outlined"}
              color={isActive ? "primary" : "default"}
              onClick={() => {
                setTypeFilter({
                  is: f.value ? [f.value] : [],
                  is_not: [],
                });
                setPage(0);
              }}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          );
        })}
        <Box
          sx={{
            width: "1px",
            height: 18,
            bgcolor: "divider",
            mx: 0.5,
          }}
        />
        {SIMULATION_FILTERS.map((f) => {
          const isActive =
            simulationFilter.is_not.length === 0 &&
            (f.value === null
              ? simulationFilter.is.length === 0
              : simulationFilter.is.length === 1 &&
                simulationFilter.is[0] === f.value);
          return (
            <Chip
              key={f.label}
              icon={f.icon ? <Iconify icon={f.icon} width={14} /> : undefined}
              label={f.label}
              size="small"
              variant={isActive ? "filled" : "outlined"}
              color={isActive ? "primary" : "default"}
              onClick={() => {
                setSimulationFilter({
                  is: f.value ? [f.value] : [],
                  is_not: [],
                });
                setPage(0);
              }}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          );
        })}
      </Box>

      {/* Table */}
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading || isFetching}
        rowCount={total}
        rowSelection={effectiveRowSelection}
        onRowSelectionChange={handleRowSelectionChange}
        columnVisibility={columnVisibility}
        onColumnVisibilityChange={setColumnVisibility}
        onRowClick={(row) => {
          if (isSelectable) {
            const isSelected = selectedPersonas.some((p) => p.id === row.id);
            onToggleSelect?.(row, !isSelected);
            return;
          }
          setInfoDrawer({ open: true, persona: row });
        }}
        getRowId={(row) => row.id}
        enableSelection
        emptyMessage={
          typeFilter.is_not.length === 0 &&
          typeFilter.is.length === 1 &&
          typeFilter.is[0] === "custom"
            ? "You haven't created any custom personas yet"
            : typeFilter.is_not.length === 0 &&
                typeFilter.is.length === 1 &&
                typeFilter.is[0] === "prebuilt"
              ? "No prebuilt personas found"
              : "No personas found"
        }
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
        currentFilters={currentFilters}
        onApply={handleFilterApply}
        aiPlaceholder="e.g. 'show custom voice personas'"
        emitRowsFormat
      />

      {/* Columns popover */}
      <PersonasColumnsPopover
        anchorEl={columnsAnchorEl}
        open={Boolean(columnsAnchorEl)}
        onClose={() => setColumnsAnchorEl(null)}
        hiddenColumns={hiddenColumns}
        onToggleColumn={handleToggleColumn}
      />

      {/* Delete confirmation */}
      <PersonasBulkDeleteDialog
        open={deleteDialogOpen}
        count={deletableSelected.length}
        skippedCount={selectedItems.length - deletableSelected.length}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteDialogOpen(false)}
        isLoading={bulkDelete.isPending}
      />

      {/* Drawers */}
      <PersonaCreateEditDrawer
        open={editDrawer.mode !== null}
        onClose={() =>
          setEditDrawer({
            mode: null,
            persona: null,
            personaCreateEditType: null,
          })
        }
        updatePersonaType={(value) =>
          setEditDrawer({
            mode: "create",
            persona: null,
            personaCreateEditType: value,
          })
        }
        editPersona={editDrawer.persona}
        personaCreateEditType={editDrawer.personaCreateEditType}
      />
      <PersonaInfoDrawer
        open={infoDrawer.open}
        persona={infoDrawer.persona || {}}
        onClose={() => setInfoDrawer({ open: false, persona: null })}
      />
    </Box>
  );
};

PersonaListView.propTypes = {
  isSelectable: PropTypes.bool,
  selectedPersonas: PropTypes.array,
  onToggleSelect: PropTypes.func,
  onCreatePersona: PropTypes.func,
  personaCreateEditType: PropTypes.string,
};

export default PersonaListView;
