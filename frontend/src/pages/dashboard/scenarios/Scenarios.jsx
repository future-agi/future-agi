/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import React, { useCallback, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";

import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import DeleteScenarioDialog from "src/components/scenarios/DeleteScenarioDialog";
import EditScenarioDialog from "src/components/scenarios/EditScenarioDialog";
import ScenarioActionMenu from "src/components/scenarios/ScenariosActionMenu";
import { getChipConfig } from "src/components/scenarios/CustomCellRenderers/ChipCellRenderer";

import { useAuthContext } from "src/auth/hooks";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

import SimulationScenarioEmptyScreen from "./SimulationScenarioEmptyScreen";

// ── Cell renderers ──

function ChipCell({ getValue }) {
  const value = getValue();
  if (!value) return null;
  const config = getChipConfig(value);
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.25,
        px: 1.5,
        py: 0.25,
      }}
    >
      {config.icon && (
        <SvgColor
          src={config.icon}
          sx={{ width: 16, height: 16, color: "text.primary" }}
        />
      )}
      <Typography variant="caption" sx={{ fontWeight: 500 }}>
        {config.label}
      </Typography>
    </Box>
  );
}

function CreatedAtCell({ getValue }) {
  const value = getValue();
  if (!value) {
    return (
      <Typography variant="body2" sx={{ fontSize: 13, color: "text.disabled" }}>
        -
      </Typography>
    );
  }
  try {
    return (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {formatDistanceToNow(new Date(value), { addSuffix: true })}
      </Typography>
    );
  } catch {
    return null;
  }
}

// ── Component ──

function Scenarios() {
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState(""); // "" = All types
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scenarioToDelete, setScenarioToDelete] = useState(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [scenarioToEdit, setScenarioToEdit] = useState(null);

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const { data, isLoading } = useQuery({
    queryKey: ["scenarios", page, pageSize, debouncedSearchQuery, typeFilter],
    queryFn: () =>
      axios.get(endpoints.scenarios.list, {
        params: {
          page: page + 1,
          limit: pageSize,
          search: debouncedSearchQuery,
          ...(typeFilter && { agent_type: typeFilter }),
        },
      }),
    select: (d) => d.data,
    keepPreviousData: true,
  });

  const items = useMemo(() => data?.results ?? [], [data]);
  const total = data?.count ?? 0;

  const showEmptyScreen =
    !isLoading &&
    data &&
    total === 0 &&
    debouncedSearchQuery === "" &&
    !typeFilter;

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.id) return;
      trackEvent(Events.scenarioNameClicked, { [PropertyName.id]: row.id });
      navigate(`/dashboard/simulate/scenarios/${row.id}`);
    },
    [navigate],
  );

  const handleAddScenario = useCallback(() => {
    trackEvent(Events.scenarioAddClicked, { [PropertyName.click]: true });
    navigate("/dashboard/simulate/scenarios/create");
  }, [navigate]);

  const handleEditScenario = useCallback((scenario) => {
    setScenarioToEdit(scenario);
    setEditDialogOpen(true);
  }, []);

  const handleDeleteScenario = useCallback((scenario) => {
    setScenarioToDelete(scenario);
    setDeleteDialogOpen(true);
  }, []);

  const handleMutationSuccess = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["scenarios"] });
  }, [queryClient]);

  const canManage =
    RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role] &&
    RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role];

  const columns = useMemo(() => {
    const cols = [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 2 },
        minSize: 260,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{ fontWeight: 500, color: "text.primary" }}
          >
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "agent_type",
        accessorKey: "agent_type",
        header: "Agent Type",
        size: 170,
        enableSorting: false,
        cell: ChipCell,
      },
      {
        id: "dataset_rows",
        accessorKey: "dataset_rows",
        header: "No of Datapoints",
        size: 140,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {getValue() ?? "-"}
          </Typography>
        ),
      },
      {
        id: "scenario_type",
        accessorKey: "scenario_type",
        header: "Scenario Type",
        size: 150,
        enableSorting: false,
        cell: ChipCell,
      },
      {
        id: "created_at",
        accessorKey: "created_at",
        header: "Created At",
        size: 170,
        enableSorting: false,
        cell: CreatedAtCell,
      },
    ];

    if (canManage) {
      cols.push({
        id: "actions",
        accessorKey: "id",
        header: "",
        size: 80,
        enableSorting: false,
        cell: ({ row }) => (
          <Box
            onClick={(e) => e.stopPropagation()}
            sx={{ display: "flex", alignItems: "center" }}
          >
            <ScenarioActionMenu
              scenario={row.original}
              onEdit={handleEditScenario}
              onDelete={handleDeleteScenario}
            />
          </Box>
        ),
      });
    }

    return cols;
  }, [canManage, handleEditScenario, handleDeleteScenario]);

  return (
    <>
      <Helmet>
        <title>Scenarios</title>
      </Helmet>

      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {showEmptyScreen ? (
          <SimulationScenarioEmptyScreen />
        ) : (
          <>
            {/* Header */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 2,
              }}
            >
              <Stack spacing={0.25}>
                <Typography
                  color="text.primary"
                  typography="m2"
                  fontWeight="fontWeightSemiBold"
                >
                  Scenarios
                </Typography>
                <Typography
                  typography="s1"
                  color="text.primary"
                  fontWeight="fontWeightRegular"
                >
                  Define the test cases, customer profiles, and conversation
                  flows that your AI agent will encounter during simulations
                </Typography>
              </Stack>
              <Box sx={{ display: "flex", gap: 1.5 }}>
                <Button
                  variant="outlined"
                  size="small"
                  sx={{
                    color: "text.primary",
                    borderColor: "divider",
                    px: 1.5,
                    height: 38,
                  }}
                  startIcon={
                    <SvgColor src="/assets/icons/ic_docs_single.svg" />
                  }
                  component="a"
                  href="https://docs.futureagi.com/docs/simulation/concepts/scenarios"
                  target="_blank"
                >
                  View Docs
                </Button>
                <Button
                  variant="contained"
                  color="primary"
                  sx={{ px: 3, borderRadius: "4px", height: 38 }}
                  startIcon={
                    <Iconify
                      icon="octicon:plus-24"
                      sx={{ width: 20, height: 20 }}
                    />
                  }
                  disabled={
                    !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                  }
                  onClick={handleAddScenario}
                >
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    Add Scenario
                  </Typography>
                </Button>
              </Box>
            </Box>

            {/* Search */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Stack direction="row" spacing={1.5} alignItems="center">
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
                <TextField
                  select
                  size="small"
                  value={typeFilter}
                  onChange={(e) => {
                    setTypeFilter(e.target.value);
                    setPage(0); // reset to first page when the filter changes
                  }}
                  SelectProps={{ displayEmpty: true }}
                  sx={{
                    minWidth: 140,
                    "& .MuiOutlinedInput-root": { height: "30px" },
                  }}
                >
                  <MenuItem value="">All types</MenuItem>
                  <MenuItem value={AGENT_TYPES.VOICE}>Voice</MenuItem>
                  <MenuItem value={AGENT_TYPES.CHAT}>Chat</MenuItem>
                </TextField>
              </Stack>
            </Box>

            {/* Table */}
            <DataTable
              columns={columns}
              data={items}
              isLoading={isLoading}
              rowCount={total}
              onRowClick={handleRowClick}
              getRowId={(row) => row.id}
              rowHeight={44}
              emptyMessage="No scenarios found"
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
          </>
        )}
      </Box>

      <DeleteScenarioDialog
        open={deleteDialogOpen}
        onClose={() => {
          setDeleteDialogOpen(false);
          setScenarioToDelete(null);
        }}
        scenario={scenarioToDelete}
        onDeleteSuccess={handleMutationSuccess}
      />

      <EditScenarioDialog
        open={editDialogOpen}
        onClose={() => {
          setEditDialogOpen(false);
          setScenarioToEdit(null);
        }}
        scenario={scenarioToEdit}
        onEditSuccess={handleMutationSuccess}
      />
    </>
  );
}

export default Scenarios;
