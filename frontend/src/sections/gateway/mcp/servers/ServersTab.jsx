import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { GatewayEmptyState, GatewayFilterBar } from "../../components/frame";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import {
  useMCPStatus,
  useMCPTools,
  useRemoveMCPServer,
} from "../hooks/useMCPConfig";
import FilterSelect from "../components/FilterSelect";
import McpIcon from "../components/McpIcon";
import { hasStoredCredential, stripSecrets } from "../utils";
import AddMCPServerDialog from "./AddMCPServerDialog";
import ServerCatalog from "./ServerCatalog";
import ServerDrawer from "./ServerDrawer";
import ToolsGrid from "./ToolsGrid";
import { TOOL_STATS_COLUMNS } from "./toolStats";
import {
  MCP_RUNTIME_UNAVAILABLE,
  MCP_SAVED_SERVER_NOTICE,
} from "../runtimeStrings";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import {
  AUTH_FILTERS,
  STATUS_FILTERS,
  filterServers,
  serverRows,
} from "./catalog";

const EMPTY_FILTERS = { search: "", status: "", auth: "" };

// The org's MCP servers as a catalog: a card per server with its health,
// auth and tools, filters for connection status and auth, a detail drawer,
// and the connect flow (Add server, with Test connection). A server's
// stored secret never reaches the page: the dialog gets its config with
// every secret removed, plus whether one is set.
const ServersTab = ({ gatewayId, runtimeOff = false }) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    data: config,
    isLoading: configLoading,
    isError,
    refetch,
  } = useGatewayConfig(gatewayId);
  const { data: mcpStatus, isError: statusError } = useMCPStatus(gatewayId);
  const {
    data: mcpTools,
    isLoading: toolsLoading,
    isError: toolsError,
  } = useMCPTools(gatewayId, { enabled: !runtimeOff });
  // Adding, editing and removing a server write the org's gateway config:
  // Owner or Admin only (useGatewayAdmin). Reading stays open.
  const { canEdit } = useGatewayAdmin();

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [openId, setOpenId] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editServer, setEditServer] = useState(null);
  // The server whose card's Remove was pressed; kept while the confirmation
  // closes, so its text does not change on the way out.
  const [removeId, setRemoveId] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const removeMutation = useRemoveMCPServer();

  // This API serves no credential vault (agentcc/urls_mcp_credentials.py has
  // no routes), so a server's auth is read from its inline config only.
  // While the status read fails, a card's health says the status is unknown,
  // as the banner does, not "Not connected".
  const rows = useMemo(
    () =>
      serverRows(config?.mcp, runtimeOff ? undefined : mcpStatus, [], {
        statusRead: !statusError,
      }).map((row) =>
        runtimeOff
          ? {
              ...row,
              runtimeOff: true,
              toolCount: null,
              health:
                row.config?.enabled === false
                  ? row.health
                  : { tone: "neutral", label: MCP_RUNTIME_UNAVAILABLE },
            }
          : row,
      ),
    [config, mcpStatus, statusError, runtimeOff],
  );
  const shown = useMemo(() => filterServers(rows, filters), [rows, filters]);
  const open = rows.find((row) => row.id === openId) || null;

  const openAdd = () => {
    if (!canEdit) return;
    setEditServer(null);
    setDialogOpen(true);
  };

  // ?add=1 (the Overview's "Add server") opens the add dialog once, for an
  // Owner or Admin.
  const wantsAdd = searchParams.get("add") === "1";
  useEffect(() => {
    if (!wantsAdd) return;
    if (canEdit) {
      setEditServer(null);
      setDialogOpen(true);
    }
    const next = new URLSearchParams(searchParams);
    next.delete("add");
    setSearchParams(next, { replace: true });
  }, [wantsAdd, searchParams, setSearchParams, canEdit]);

  const openEdit = (row) => {
    if (!canEdit) return;
    setEditServer({
      serverId: row.id,
      config: stripSecrets(row.config),
      hasCredential: hasStoredCredential(row.config),
      storedHeader: row.auth.header,
    });
    setDialogOpen(true);
  };

  const askRemove = (row) => {
    if (!canEdit) return;
    setRemoveId(row.id);
    setConfirmRemove(true);
  };

  const remove = () => {
    if (!canEdit || !confirmRemove || removeMutation.isPending) return;
    const serverId = removeId;
    removeMutation.mutate(
      { gatewayId, serverId },
      {
        onSuccess: () => {
          enqueueSnackbar(`Server "${serverId}" removed`, {
            variant: "success",
          });
          setConfirmRemove(false);
          if (openId === serverId) setOpenId(null);
        },
        onError: () => {
          enqueueSnackbar("Failed to remove server", { variant: "error" });
          setConfirmRemove(false);
        },
      },
    );
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setEditServer(null);
  };

  const set = (key) => (value) =>
    setFilters((prev) => ({ ...prev, [key]: value }));
  const filtered = Boolean(filters.search || filters.status || filters.auth);
  const empty = !configLoading && !isError && rows.length === 0;

  let body;
  if (isError) {
    body = (
      <GatewayEmptyState
        icon="monitoring"
        title="Servers could not be loaded"
        description="The organization's gateway config did not load. Try again."
        actionLabel="Try again"
        onAction={() => refetch?.()}
      />
    );
  } else if (configLoading) {
    body = (
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", lg: "1fr 1fr 1fr" },
          gap: 1.5,
        }}
      >
        {[0, 1, 2].map((key) => (
          <Skeleton key={key} variant="rounded" height={132} />
        ))}
      </Box>
    );
  } else if (empty) {
    body = (
      <GatewayEmptyState
        icon="mcp"
        title="No MCP servers yet"
        description={
          runtimeOff
            ? MCP_SAVED_SERVER_NOTICE
            : "Add an MCP server's URL. The gateway connects to it, serves its tools to your agents through one endpoint and records every call."
        }
        actionLabel={!runtimeOff && canEdit ? "Add server" : undefined}
        onAction={!runtimeOff && canEdit ? openAdd : undefined}
      />
    );
  } else if (shown.length === 0 && filtered) {
    body = (
      <GatewayEmptyState
        variant="no-results"
        title="No servers match"
        actionLabel="Clear filters"
        onAction={() => setFilters(EMPTY_FILTERS)}
      />
    );
  } else {
    body = (
      <ServerCatalog
        rows={shown}
        onOpen={setOpenId}
        onEdit={openEdit}
        onRemove={askRemove}
        canEdit={canEdit}
      />
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {(runtimeOff || !empty) && !isError && (
        <GatewayFilterBar
          search={filters.search}
          onSearchChange={set("search")}
          searchPlaceholder="Search servers"
          actions={
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                variant="contained"
                color="primary"
                size="small"
                startIcon={<McpIcon name="plus" size={16} />}
                onClick={openAdd}
                disabled={!canEdit}
              >
                Add server
              </Button>
            </GatewayAdminOnly>
          }
        >
          <FilterSelect
            label="Status"
            value={filters.status}
            onChange={set("status")}
            options={STATUS_FILTERS}
          />
          <FilterSelect
            label="Auth"
            value={filters.auth}
            onChange={set("auth")}
            options={AUTH_FILTERS}
          />
        </GatewayFilterBar>
      )}

      {body}

      {!empty && !isError && !configLoading && (
        <Typography typography="s2" color="text.secondary">
          {runtimeOff
            ? MCP_SAVED_SERVER_NOTICE
            : `${shown.length} of ${rows.length} ${rows.length === 1 ? "server" : "servers"}. Each server's tools reach agents as <server>__<tool> at the organization's MCP endpoint; virtual servers narrow them per agent.`}
        </Typography>
      )}

      {(runtimeOff || !empty) && !isError && (
        <Box sx={{ mt: 1 }}>
          <ToolsGrid
            runtimeOff={runtimeOff}
            tools={mcpTools || []}
            isLoading={toolsLoading}
            isError={toolsError}
            // A saved list (MCP off) keeps no current stats.
            statsColumns={runtimeOff ? undefined : TOOL_STATS_COLUMNS}
          />
        </Box>
      )}

      <ServerDrawer
        runtimeOff={runtimeOff}
        row={open}
        tools={mcpTools || []}
        toolsLoading={toolsLoading}
        toolsError={toolsError}
        gatewayId={gatewayId}
        onClose={() => setOpenId(null)}
        onEdit={openEdit}
        canEdit={canEdit}
      />

      <AddMCPServerDialog
        runtimeOff={runtimeOff}
        canEdit={canEdit}
        open={dialogOpen}
        onClose={closeDialog}
        gatewayId={gatewayId}
        editServer={editServer}
      />

      <ConfirmDialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        onConfirm={remove}
        title="Remove MCP server"
        message={`Remove "${removeId}" from the saved server configuration and revoke its stored credentials?`}
        confirmLabel="Remove server"
        isLoading={removeMutation.isPending}
      />
    </Box>
  );
};

ServersTab.propTypes = {
  runtimeOff: PropTypes.bool,
  gatewayId: PropTypes.string,
};

export default ServersTab;
