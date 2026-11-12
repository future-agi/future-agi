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
import { format, formatDistanceToNow } from "date-fns";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import Iconify from "src/components/iconify";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import OverflowCellText from "src/components/data-table/OverflowCellText";
import { ObjectFilterPanel } from "src/components/filter";
import CustomTooltip from "src/components/tooltip";
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
  const [selectedPersonaIds, setSelectedPersonaIds] = useState(() => new Set());
  const [columnVisibility, setColumnVisibility] = useState({});
  const [deleteDialog, setDeleteDialog] = useState(null);
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

  // Catalog selection belongs to persona IDs, not positions in a refreshed
  // page. Only the picker keeps selections across pages (via its parent).
  useEffect(() => {
    if (isSelectable || !data || isLoading || isFetching) return;
    const availableIds = new Set(items.map((item) => item.id));
    setSelectedPersonaIds((previous) => {
      const next = new Set([...previous].filter((id) => availableIds.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [isSelectable, data, items, isLoading, isFetching]);

  // Both catalog and picker selections use the shared table's stable ID keys.
  // The picker still owns its cross-page selection through its parent.
  const effectiveRowSelection = useMemo(() => {
    const selectedIds = isSelectable
      ? new Set(selectedPersonas.map((p) => p.id))
      : selectedPersonaIds;
    const map = {};
    items.forEach((item) => {
      if (selectedIds.has(item.id)) map[item.id] = true;
    });
    return map;
  }, [isSelectable, selectedPersonas, items, selectedPersonaIds]);

  const handleRowSelectionChange = useCallback(
    (next) => {
      if (!isSelectable) {
        setSelectedPersonaIds(
          new Set(
            items.filter((item) => next?.[item.id]).map((item) => item.id),
          ),
        );
        return;
      }
      const selectedIds = new Set(selectedPersonas.map((p) => p.id));
      items.forEach((item) => {
        const wasSelected = selectedIds.has(item.id);
        const nowSelected = Boolean(next?.[item.id]);
        if (wasSelected !== nowSelected) {
          onToggleSelect?.(item, nowSelected);
        }
      });
    },
    [isSelectable, selectedPersonas, items, onToggleSelect],
  );

  const selectedItems = useMemo(
    () => items.filter((item) => selectedPersonaIds.has(item.id)),
    [selectedPersonaIds, items],
  );

  const deletableSelected = useMemo(
    () => selectedItems.filter((p) => !p?.isDefault),
    [selectedItems],
  );

  const handleCancelSelection = useCallback(() => {
    setSelectedPersonaIds(new Set());
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    const ids = deleteDialog?.personas.map((p) => p.id) || [];
    if (!ids.length) {
      setDeleteDialog(null);
      return;
    }
    const { deleted, failed } = await bulkDelete.mutateAsync(ids);
    setDeleteDialog(null);
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
  }, [deleteDialog, bulkDelete, handleCancelSelection]);

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 1.5 },
        // Every column's minimum, the checkbox's 50 included (950 px), fits
        // the 1040 px a 1280 px window leaves the table (1280 - 200 menu -
        // 2 x 16 padding, less the 8 px bar when the rows scroll). At 1024
        // it scrolls sideways under a drawn bar, with the row actions
        // pinned to the right edge.
        // A long name ends in an ellipsis and shows whole on hover or when
        // its cell takes focus.
        minSize: 180,
        enableSorting: false,
        cell: ({ row }) => (
          <OverflowCellText
            text={row.original?.name}
            sx={{ fontWeight: 500, fontSize: "12px" }}
          />
        ),
      },
      {
        id: "description",
        accessorKey: "description",
        header: "Description",
        meta: { flex: 2 },
        minSize: 160,
        enableSorting: false,
        // Cut short, it shows whole on hover or when its cell takes focus.
        cell: ({ getValue }) => (
          <OverflowCellText
            text={getValue() || "—"}
            sx={{ fontSize: "12px", color: "text.secondary" }}
          />
        ),
      },
      {
        id: "simulationType",
        accessorKey: "simulationType",
        header: "Agent Type",
        size: 90,
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
        minSize: 130,
        enableSorting: false,
        cell: ({ row }) => {
          const tags = extractTagsFromPersona(row.original);
          if (!tags.length) return null;
          const displayed = tags.slice(0, 2);
          const remaining = tags.length - displayed.length;
          const every = tags.join(" · ");
          // The first two, ending in an ellipsis where the column is narrow,
          // then how many more there are. With more, every attribute shows
          // on hover of either or when the cell takes focus; without, a cut
          // pair shows whole.
          return (
            <Box
              sx={{
                display: "flex",
                gap: 0.5,
                flexWrap: "nowrap",
                overflow: "hidden",
              }}
            >
              <OverflowCellText
                text={displayed.join(" · ")}
                detail={remaining > 0 ? every : undefined}
                sx={{ fontSize: "12px", color: "text.primary" }}
              />
              {remaining > 0 && (
                <CustomTooltip
                  show
                  title={every}
                  placement="top"
                  arrow
                  size="small"
                  describeChild
                >
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
                </CustomTooltip>
              )}
            </Box>
          );
        },
      },
      {
        id: "createdBy",
        accessorKey: "isDefault",
        header: "Created By",
        size: 110,
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
        // "less than a minute ago", whole.
        size: 150,
        enableSorting: false,
        cell: ({ row }) => {
          const val = row.original?.updatedAt || row.original?.updated_at;
          if (!val) return null;
          try {
            const date = new Date(val);
            // How long ago, whole; the exact time on hover or when the cell
            // takes focus.
            return (
              <OverflowCellText
                text={formatDistanceToNow(date, { addSuffix: true })}
                detail={format(date, "dd-MM-yyyy, h:mm a")}
                sx={{ fontSize: "12px" }}
              />
            );
          } catch {
            return null;
          }
        },
      },
      // Duplicate and Edit, only for someone who can use them: a viewer's
      // empty cells drew an empty pinned strip down the right edge.
      ...(isSelectable || !canCreate
        ? []
        : [
            {
              id: "actions",
              accessorKey: "id",
              header: "",
              // Duplicate and Edit, two 28 px buttons.
              size: 80,
              enableSorting: false,
              cell: ({ row }) => {
                const persona = row.original;
                const canEdit = !persona?.isDefault;
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
      {/* Toolbar: search, segmented toggle, filter selects, actions. It wraps
          when the pane is narrow (1024) instead of squeezing its controls. */}
      <ListToolbar
        useFlexGap
        flexWrap="wrap"
        sx={framed ? undefined : { pt: 0 }}
      >
        <FormSearchField
          size="small"
          placeholder="Search personas"
          sx={{ flex: "1 1 180px", maxWidth: 280 }}
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

        {/* ml: auto keeps the actions right-aligned on whichever line they
            land, which a flex: 1 spacer cannot do once the row wraps. */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            ml: "auto",
            flexShrink: 0,
          }}
        >
          {!isSelectable && selectedItems.length > 0 ? (
            <PersonasBulkActionsBar
              selectedCount={selectedItems.length}
              deletableCount={deletableSelected.length}
              onDelete={() =>
                // Snapshot both the names shown and the IDs submitted so a
                // refresh while confirmation is open cannot change its targets.
                setDeleteDialog({
                  personas: deletableSelected.map(({ id, name }) => ({
                    id,
                    name,
                  })),
                  skippedCount: selectedItems.length - deletableSelected.length,
                })
              }
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
        </Box>
      </ListToolbar>

      {/* Table */}
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading || isFetching}
        rowCount={total}
        rowSelection={effectiveRowSelection}
        selectionScope={
          isSelectable
            ? undefined
            : [
                page,
                pageSize,
                searchQuery,
                debouncedSearch,
                typeFilter,
                simulationFilter,
                lockedType,
                lockedSimulationType,
              ]
        }
        preserveSelectionOnUnmount={isSelectable}
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
        showScrollbar
        pinnedRight={canCreate && !isSelectable ? "actions" : undefined}
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
        open={Boolean(deleteDialog)}
        count={deleteDialog?.personas.length || 0}
        personas={deleteDialog?.personas || []}
        skippedCount={deleteDialog?.skippedCount || 0}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteDialog(null)}
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
