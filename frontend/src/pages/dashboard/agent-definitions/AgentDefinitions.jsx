/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  Divider,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import React, { useCallback, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import DeleteAgentDefinitionDialog from "src/components/agent-definitions/DeleteAgentDefinitionDialog";
import { languageMap } from "src/components/agent-definitions/helper";
import { getChipConfig } from "src/components/scenarios/CustomCellRenderers/ChipCellRenderer";
import SimulationAgentEmptyScreen from "src/sections/agents/SimulationAgentEmptyScreen";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useAuthContext } from "src/auth/hooks";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import {
  Events,
  PropertyName,
  trackEvent,
  handleOnDocsClicked,
} from "src/utils/Mixpanel";
import { camelCaseToTitleCase } from "src/utils/utils";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

// ── Cell renderers (inlined, MUI-native) ──

function AgentNameCell({ row }) {
  const { agent_name: agentName } = row.original;
  return (
    <Typography
      variant="body2"
      noWrap
      sx={{ fontWeight: 500, color: "text.primary" }}
    >
      {agentName}
    </Typography>
  );
}

function AgentTypeCell({ row }) {
  const data = row.original;
  const key =
    data.agent_type === AGENT_TYPES.VOICE
      ? data.inbound
        ? "voice_inbound"
        : "voice_outbound"
      : "chat";
  const config = getChipConfig(key);
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

function LanguagesCell({ getValue }) {
  const value = getValue();
  if (!value || !Array.isArray(value) || value.length === 0) return null;
  const maxVisible = 2;
  const visible = value
    .slice(0, maxVisible)
    .map((lang) => languageMap[lang] || lang);
  const remaining = value.length - maxVisible;
  return (
    <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
      {visible.join(", ")}
      {remaining > 0 ? `, +${remaining}` : ""}
    </Typography>
  );
}

// ── Component ──

function AgentDefinitions() {
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState(""); // "" = All types
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [rowSelection, setRowSelection] = useState({});
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const { data, isLoading } = useQuery({
    queryKey: [
      "agentDefinitions",
      page,
      pageSize,
      debouncedSearchQuery,
      typeFilter,
    ],
    queryFn: () =>
      axios.get(endpoints.agentDefinitions.list, {
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

  // Show the empty screen only when there are truly zero agents (first page,
  // no search). `data` becomes defined after the first fetch.
  const showEmptyScreen =
    !isLoading &&
    data &&
    total === 0 &&
    debouncedSearchQuery === "" &&
    !typeFilter;

  // Selected items — derived from rowSelection index map.
  const selectedItems = useMemo(
    () =>
      Object.keys(rowSelection)
        .filter((k) => rowSelection[k])
        .map((k) => items[parseInt(k, 10)])
        .filter(Boolean),
    [rowSelection, items],
  );

  const columns = useMemo(
    () => [
      {
        id: "agentName",
        accessorKey: "agent_name",
        header: "Agent Name",
        meta: { flex: 2 },
        minSize: 280,
        enableSorting: false,
        cell: AgentNameCell,
      },
      {
        id: "type",
        accessorKey: "agent_type",
        header: "Type",
        size: 170,
        enableSorting: false,
        cell: AgentTypeCell,
      },
      {
        id: "provider",
        accessorKey: "provider",
        header: "Provider",
        size: 140,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {getValue() ? camelCaseToTitleCase(getValue()) : "-"}
          </Typography>
        ),
      },
      {
        id: "contact_number",
        accessorKey: "contact_number",
        header: "Contact Number",
        size: 170,
        enableSorting: false,
        cell: ({ row, getValue }) => {
          const isChat = row.original.agent_type === AGENT_TYPES.CHAT;
          return (
            <Typography variant="body2" sx={{ fontSize: 13 }}>
              {isChat ? "NA" : getValue() || "-"}
            </Typography>
          );
        },
      },
      {
        id: "languages",
        accessorKey: "languages",
        header: "Languages",
        size: 170,
        enableSorting: false,
        cell: LanguagesCell,
      },
      {
        id: "latestVersion",
        accessorKey: "latest_version",
        header: "Version",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {getValue() ? `v${getValue()}` : "-"}
          </Typography>
        ),
      },
    ],
    [],
  );

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.id) return;
      if (!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role]) return;
      trackEvent(Events.agentDefClicked, { [PropertyName.id]: row.id });
      navigate(`${row.id}`, { state: { agentDefinitionId: row.id } });
    },
    [navigate, role],
  );

  const handleAddAgent = useCallback(() => {
    trackEvent(Events.addAgentDefClicked, { [PropertyName.click]: true });
    navigate("create-new-agent-definition");
  }, [navigate]);

  const handleCancelSelection = useCallback(() => setRowSelection({}), []);

  const handleDeleteSuccess = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["agentDefinitions"] });
    setRowSelection({});
    setDeleteDialogOpen(false);
  }, [queryClient]);

  return (
    <>
      <Helmet>
        <title>Agent Definitions</title>
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
          <SimulationAgentEmptyScreen />
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
                  Agent Definitions
                </Typography>
                <Typography
                  typography="s1"
                  color="text.primary"
                  fontWeight="fontWeightRegular"
                >
                  Specifies how your AI agent behaves during voice conversations
                </Typography>
              </Stack>
              <Box sx={{ display: "flex", gap: 2 }}>
                <Button
                  variant="outlined"
                  size="small"
                  sx={{ borderRadius: "4px", height: 38, px: 1 }}
                  onClick={() => {
                    handleOnDocsClicked("agent-definition");
                    window.open(
                      "https://docs.futureagi.com/docs/simulation/concepts/agent-definition",
                      "_blank",
                    );
                  }}
                >
                  <SvgColor
                    src="/assets/icons/agent/docs.svg"
                    sx={{ height: 20, width: 20, mr: 1 }}
                  />
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    View Docs
                  </Typography>
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
                  onClick={handleAddAgent}
                >
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    Create agent definition
                  </Typography>
                </Button>
              </Box>
            </Box>

            {/* Search + bulk bar */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 2,
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
              {selectedItems.length > 0 ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 2,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    px: 2,
                    py: 0.5,
                  }}
                >
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightMedium"
                    color="primary.main"
                  >
                    {selectedItems.length} Selected
                  </Typography>
                  <Divider
                    orientation="vertical"
                    flexItem
                    sx={{ borderRightWidth: 0.25 }}
                  />
                  <Button
                    size="small"
                    startIcon={
                      <SvgColor
                        src="/assets/icons/ic_delete.svg"
                        sx={{
                          width: 20,
                          height: 20,
                          color: "text.disabled",
                        }}
                      />
                    }
                    sx={{ color: "text.secondary" }}
                    onClick={() => setDeleteDialogOpen(true)}
                    disabled={
                      !RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role]
                    }
                  >
                    <Typography
                      typography="s1"
                      fontWeight="fontWeightRegular"
                      color="text.primary"
                    >
                      Delete
                    </Typography>
                  </Button>
                  <Button
                    size="small"
                    sx={{ color: "text.secondary" }}
                    onClick={handleCancelSelection}
                  >
                    <Typography
                      typography="s1"
                      fontWeight="fontWeightRegular"
                      color="text.primary"
                    >
                      Cancel
                    </Typography>
                  </Button>
                </Box>
              ) : null}
            </Box>

            {/* Table */}
            <DataTable
              columns={columns}
              data={items}
              isLoading={isLoading}
              rowCount={total}
              rowSelection={rowSelection}
              onRowSelectionChange={setRowSelection}
              onRowClick={handleRowClick}
              getRowId={(row) => row.id}
              enableSelection
              rowHeight={44}
              emptyMessage="No agent definitions found"
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
          </>
        )}
      </Box>

      <DeleteAgentDefinitionDialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        agents={selectedItems.map((a) => ({
          id: a.id,
          agentName: a.agent_name,
        }))}
        onDeleteSuccess={handleDeleteSuccess}
      />
    </>
  );
}

export default AgentDefinitions;
