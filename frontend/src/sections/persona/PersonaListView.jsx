import {
  Avatar,
  Box,
  Button,
  IconButton,
  MenuItem,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import Iconify from "src/components/iconify";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { ObjectFilterPanel } from "src/components/filter";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useBulkDeletePersonas,
  useGetPersonasPaginated,
} from "src/api/persona/persona";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PersonaCreateEditDrawer from "./PersonaCreateEdit/PersonaCreateEditDrawer";
import DuplicatePersonas from "./PersonaCreateEdit/DuplicatePersonas";
import PersonaInfoDrawer from "./PersonaInfo/PersonaInfoDrawer";
import PersonasBulkActionsBar from "./components/PersonasBulkActionsBar";
import PersonasBulkDeleteDialog from "./components/PersonasBulkDeleteDialog";
import PersonasColumnsPopover from "./components/PersonasColumnsPopover";
import { extractTagsFromPersona } from "./common";

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
  lockedFilters = null,
  toolbarActions = null,
  /* Sits under a page header + tabs: the toolbar takes the shared 12px top
     padding so it lines up with its sibling tab. */
  framed = false,
}) => {
  const { role } = useAuthContext();
  const canCreate = RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];

  const pickLockedValue = (v) => (Array.isArray(v) ? v[0] : v) ?? null;
  const lockedSimulationType =
    pickLockedValue(lockedFilters?.simulation_type) ??
    (isSelectable ? personaCreateEditType : null);
  const lockedType = pickLockedValue(lockedFilters?.type);

  // State
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [typeFilter, setTypeFilter] = useState(lockedType);
  const [simulationFilter, setSimulationFilter] =
    useState(lockedSimulationType);
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
  const [duplicateDialog, setDuplicateDialog] = useState({
    open: false,
    persona: null,
  });

  const debouncedSearch = useDebounce(searchQuery.trim(), 400);

  const { data, isLoading, isFetching } = useGetPersonasPaginated({
    page: page + 1,
    pageSize,
    search: debouncedSearch || null,
    type: lockedType ?? typeFilter,
    simulationType: lockedSimulationType ?? simulationFilter,
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
            sx={{ fontWeight: 500, fontSize: "12px" }}
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
              sx={{ fontSize: "12px", color: "text.secondary" }}
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
            <Box
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                color: "text.secondary",
              }}
            >
              <Iconify
                icon={
                  isVoice
                    ? "solar:microphone-3-bold"
                    : "solar:chat-round-line-bold"
                }
                width={12}
              />
              <Typography variant="body2" sx={{ fontSize: "12px" }}>
                {isVoice ? "Voice" : "Chat"}
              </Typography>
            </Box>
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
              <Typography
                variant="body2"
                noWrap
                sx={{ fontSize: "12px", color: "text.primary" }}
              >
                {displayed.join(" · ")}
              </Typography>
              {remaining > 0 && (
                <Typography
                  variant="body2"
                  sx={{
                    fontSize: "12px",
                    color: "text.tertiary",
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`+${remaining}`}
                </Typography>
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
                    fontSize: "12px",
                    fontWeight: 600,
                  }}
                >
                  {isSystem ? (
                    <Iconify icon="solar:shield-check-bold" width={14} />
                  ) : (
                    getInitials(name)
                  )}
                </Avatar>
                <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
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
              <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
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
              size: 88,
              enableSorting: false,
              cell: ({ row }) => {
                const persona = row.original;
                const canEdit = !persona?.isDefault && canCreate;
                if (!canCreate) return null;
                return (
                  <Box
                    sx={{ display: "flex", alignItems: "center", gap: 0.25 }}
                  >
                    <IconButton
                      size="small"
                      aria-label={`Duplicate persona ${persona?.name || ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setDuplicateDialog({ open: true, persona });
                      }}
                      sx={{ color: "text.secondary" }}
                    >
                      <Iconify icon="solar:copy-linear" width={16} />
                    </IconButton>
                    {canEdit && (
                      <IconButton
                        size="small"
                        aria-label={`Edit persona ${persona?.name || ""}`}
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
                    )}
                  </Box>
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
    const fields = [];
    if (!lockedType) {
      fields.push({
        value: "type",
        label: "Category",
        type: "enum",
        choices: ["prebuilt", "custom"],
        operators: ["is"],
        single: true,
      });
    }
    if (!lockedSimulationType) {
      fields.push({
        value: "simulation_type",
        label: "Agent Type",
        type: "enum",
        choices: [AGENT_TYPES.VOICE, AGENT_TYPES.CHAT],
        choiceLabels: {
          [AGENT_TYPES.VOICE]: "Voice",
          [AGENT_TYPES.CHAT]: "Chat",
        },
        operators: ["is"],
        single: true,
      });
    }
    return fields;
  }, [lockedType, lockedSimulationType]);

  const currentFilters = useMemo(() => {
    const f = {};
    if (!lockedType && typeFilter) f.type = [typeFilter];
    if (!lockedSimulationType && simulationFilter)
      f.simulation_type = [simulationFilter];
    return Object.keys(f).length ? f : null;
  }, [typeFilter, simulationFilter, lockedType, lockedSimulationType]);

  const activeFilterCount =
    (lockedType ? 0 : typeFilter ? 1 : 0) +
    (lockedSimulationType ? 0 : simulationFilter ? 1 : 0);

  const handleFilterApply = useCallback(
    (result) => {
      if (!result) {
        setTypeFilter(lockedType ?? null);
        setSimulationFilter(lockedSimulationType ?? null);
        setPage(0);
        return;
      }
      const pickFirst = (v) => (Array.isArray(v) ? v[0] : v) || null;
      if (Array.isArray(result)) {
        const flat = {};
        for (const t of result) {
          const val = Array.isArray(t.value)
            ? t.value
            : t.value
              ? [t.value]
              : [];
          if (val.length) flat[t.field] = val;
        }
        setTypeFilter(lockedType ?? pickFirst(flat.type));
        setSimulationFilter(
          lockedSimulationType ?? pickFirst(flat.simulation_type),
        );
      } else {
        setTypeFilter(lockedType ?? pickFirst(result.type));
        setSimulationFilter(
          lockedSimulationType ?? pickFirst(result.simulation_type),
        );
      }
      setPage(0);
    },
    [lockedType, lockedSimulationType],
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
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {/* Toolbar: search, segmented toggle, filter selects, spacer, actions */}
      <ListToolbar sx={framed ? undefined : { pt: 0 }}>
        <FormSearchField
          size="small"
          placeholder="Search personas"
          sx={{ width: 280 }}
          searchQuery={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
            setPage(0);
          }}
        />
        {!lockedType && (
          <ToggleButtonGroup
            exclusive
            size="small"
            value={typeFilter ?? "all"}
            onChange={(_, v) => {
              if (!v) return;
              setTypeFilter(v === "all" ? null : v);
              setPage(0);
            }}
          >
            {QUICK_FILTERS.map((f) => (
              <ToggleButton key={f.label} value={f.value ?? "all"}>
                {f.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
        {!lockedSimulationType && (
          <TextField
            select
            size="small"
            value={simulationFilter ?? "all"}
            onChange={(e) => {
              setSimulationFilter(
                e.target.value === "all" ? null : e.target.value,
              );
              setPage(0);
            }}
            inputProps={{ "aria-label": "Agent type" }}
            sx={{ minWidth: 140 }}
          >
            {SIMULATION_FILTERS.map((f) => (
              <MenuItem key={f.label} value={f.value ?? "all"}>
                {f.label}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:filter-linear" width={16} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
          onClick={(e) => setFilterAnchorEl(e.currentTarget)}
          sx={{
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
            borderColor: hiddenColumns.length > 0 ? "primary.main" : "divider",
            color: hiddenColumns.length > 0 ? "primary.main" : "text.secondary",
          }}
        >
          Columns
        </Button>

        <Box sx={{ flex: 1 }} />

        {!isSelectable && selectedItems.length > 0 ? (
          <PersonasBulkActionsBar
            selectedCount={selectedItems.length}
            deletableCount={deletableSelected.length}
            onDelete={() => setDeleteDialogOpen(true)}
            onCancel={handleCancelSelection}
          />
        ) : (
          <>
            {toolbarActions}
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!canCreate}
              startIcon={<Iconify icon="solar:add-linear" width={16} />}
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
            >
              Create persona
            </Button>
          </>
        )}
      </ListToolbar>

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
          typeFilter === "custom"
            ? "You haven't created any custom personas yet"
            : typeFilter === "prebuilt"
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
      <ObjectFilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        filterFields={filterFields}
        currentFilters={currentFilters}
        onApply={handleFilterApply}
        aiPlaceholder="e.g. 'show custom voice personas'"
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
      <DuplicatePersonas
        open={duplicateDialog.open}
        personaId={duplicateDialog.persona?.id}
        onClose={() => setDuplicateDialog({ open: false, persona: null })}
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
  framed: PropTypes.bool,
  isSelectable: PropTypes.bool,
  lockedFilters: PropTypes.object,
  selectedPersonas: PropTypes.array,
  onToggleSelect: PropTypes.func,
  onCreatePersona: PropTypes.func,
  personaCreateEditType: PropTypes.string,
  // Extra buttons shown just before Create persona (hidden while rows are selected).
  toolbarActions: PropTypes.node,
};

export default PersonaListView;
