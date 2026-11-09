import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../../context/useGatewayContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { useApiKeys } from "../../keys/hooks/useApiKeys";
import { useMCPStatus, useMCPTools } from "../hooks/useMCPConfig";
import McpIcon from "../components/McpIcon";
import { mcpVerdict } from "../MCPStatusBanner";
import VirtualServerDrawer from "./VirtualServerDrawer";
import {
  accessSummary,
  mcpEndpoint,
  virtualServerRows,
  virtualServerStatus,
} from "./virtualServers";

const ROW_HEIGHT = 40;

function StatusCell({ data }) {
  if (!data?.status) return null;
  return (
    <GatewayStatusChip tone={data.status.tone} label={data.status.label} />
  );
}

StatusCell.propTypes = {
  data: PropTypes.shape({
    status: PropTypes.shape({
      tone: PropTypes.string,
      label: PropTypes.string,
    }),
  }),
};

// Virtual servers: named MCP endpoints, /mcp/v/<slug>, each serving a chosen
// set of the org's servers and tools to chosen keys (org config
// mcp.virtual_servers, composed by the gateway). Rows open the editor.
export default function VirtualServersTab({ gatewayId }) {
  const { canEdit, disabledReason } = useGatewayAdmin();
  const { gateway } = useGatewayContext();
  const baseUrl = gateway?.baseUrl || "";
  const {
    data: config,
    isLoading,
    isError,
    refetch,
  } = useGatewayConfig(gatewayId);
  const { data: tools } = useMCPTools(gatewayId);
  // Until a tool list has loaded (still loading, or the first read failed)
  // a row's tool count means nothing; a later failed refetch keeps the last.
  const toolsKnown = tools != null;
  const { data: mcpStatus, isError: statusError } = useMCPStatus(gatewayId);
  const { data: keys } = useApiKeys(gatewayId);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(null); // { slug } or { slug: null }

  const mcp = config?.mcp;
  // The same verdict as the page's chip and banner, so a row never reads
  // "MCP on" while the banner says no tool can be called.
  const verdictKind = mcpVerdict({
    status: mcpStatus,
    isError: statusError,
    mcpConfig: config ? mcp || {} : undefined,
  }).kind;
  const rows = useMemo(
    () =>
      virtualServerRows(mcp, tools || [], baseUrl).map((row) => ({
        ...row,
        status: virtualServerStatus({ kind: verdictKind }, row.candidateCount, {
          toolsKnown,
        }),
      })),
    [mcp, tools, baseUrl, verdictKind, toolsKnown],
  );
  const shown = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((row) =>
      [row.title, row.slug, row.id, row.description, ...row.members]
        .join(" ")
        .toLowerCase()
        .includes(term),
    );
  }, [rows, search]);
  const serverIds = useMemo(
    () => Object.keys(mcp?.servers || {}).sort(),
    [mcp],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Name",
        colId: "title",
        field: "title",
        minWidth: 160,
        flex: 1.2,
      },
      { headerName: "Slug", colId: "slug", field: "slug", minWidth: 130 },
      {
        headerName: "Servers",
        colId: "members",
        valueGetter: ({ data }) => data.members.join(", "),
        minWidth: 180,
        flex: 1.3,
      },
      {
        headerName: "Tool candidates",
        colId: "tools",
        field: "candidateCount",
        headerTooltip:
          "Uncomposed inventory, not the served tool count. The gateway resolves names and collisions.",
        minWidth: 150,
        flex: 0.7,
      },
      {
        headerName: "Who can use it",
        colId: "access",
        valueGetter: ({ data }) => accessSummary(data.access),
        minWidth: 190,
        flex: 1.2,
      },
      {
        headerName: "Endpoint",
        colId: "endpoint",
        field: "endpoint",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 260,
        flex: 1.8,
      },
      {
        headerName: "Status",
        colId: "status",
        valueGetter: ({ data }) => data.status?.label,
        cellRenderer: StatusCell,
        sortable: false,
        minWidth: 120,
        flex: 0.7,
      },
      {
        headerName: "Not enforced",
        colId: "notEnforced",
        valueGetter: ({ data }) =>
          data.notEnforced.map((item) => item.label).join(", ") || "\u2014",
        tooltipValueGetter: ({ data }) =>
          data.notEnforced
            .map((item) => `${item.label}: ${item.detail}`)
            .join("\n"),
        minWidth: 190,
        flex: 1,
      },
    ],
    [],
  );

  const editingSlug = editing?.slug || null;
  const editingEntry = editingSlug ? mcp?.virtual_servers?.[editingSlug] : null;
  const openNew = () => {
    if (canEdit) setEditing({ slug: null });
  };
  const defaultEndpoint = mcpEndpoint(baseUrl, "");

  let body;
  if (isError) {
    body = (
      <GatewayEmptyState
        icon="monitoring"
        title="Virtual servers could not be loaded"
        description="The organization's gateway config did not load. Try again."
        actionLabel="Try again"
        onAction={() => refetch()}
      />
    );
  } else if (!isLoading && rows.length === 0) {
    body = (
      <GatewayEmptyState
        icon="mcp"
        title="No virtual servers yet"
        description="A virtual server is its own MCP endpoint with a chosen set of servers, tools and keys: give each agent only the tools it needs."
        actionLabel="New virtual server"
        onAction={openNew}
        actionDisabled={!canEdit}
      />
    );
  } else if (!isLoading && shown.length === 0) {
    body = (
      <GatewayEmptyState
        variant="no-results"
        title="No virtual servers match"
        actionLabel="Clear search"
        onAction={() => setSearch("")}
      />
    );
  } else {
    body = (
      <GatewayGrid
        rowData={isLoading ? undefined : shown}
        loading={isLoading}
        columnDefs={columnDefs}
        stateKey="gateway-mcp-virtual-servers"
        getRowId={(params) => params.data.slug}
        onRowClicked={(event) => setEditing({ slug: event.data.slug })}
        rowStyle={{ cursor: "pointer" }}
        height={Math.min(
          Math.max(ROW_HEIGHT * Math.max(shown.length, 3) + 42, 200),
          560,
        )}
      />
    );
  }

  const empty = !isLoading && !isError && rows.length === 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {!canEdit && (
        <Typography color="text.secondary">{disabledReason}</Typography>
      )}
      {!empty && (
        <GatewayFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search virtual servers"
          actions={
            <Button
              size="small"
              variant="contained"
              color="primary"
              startIcon={<McpIcon name="plus" size={16} />}
              onClick={openNew}
              disabled={!canEdit}
            >
              New virtual server
            </Button>
          }
        />
      )}

      {body}

      {defaultEndpoint && rows.length > 0 && (
        <Typography typography="s2" color="text.secondary">
          {`A client of ${defaultEndpoint} gets the virtual server bound to its key, else the organization default, else every server the organization's access default allows.`}
        </Typography>
      )}

      <VirtualServerDrawer
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        slug={editingSlug}
        entry={editingEntry}
        gatewayId={gatewayId}
        baseUrl={baseUrl}
        serverIds={serverIds}
        tools={tools || []}
        keys={keys || []}
        takenSlugs={rows.map((row) => row.slug)}
      />
    </Box>
  );
}

VirtualServersTab.propTypes = {
  gatewayId: PropTypes.string,
};
