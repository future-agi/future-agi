import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import Link from "@mui/material/Link";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useApiKeys } from "../../keys/hooks/useApiKeys";
import { formatLatency } from "../../utils/formatters";
import { useMCPTools } from "../hooks/useMCPConfig";
import { useMCPToolCalls } from "../hooks/useMCPToolCalls";
import { useMCPReleaseFeatures } from "../hooks/useMCPReleaseFeatures";
import { useMCPToolCallFeatures } from "../hooks/useMCPToolCallFeatures";
import FilterSelect from "../components/FilterSelect";
import McpIcon from "../components/McpIcon";
import ToolCallDrawer from "./ToolCallDrawer";
import { EXPORT_FORMATS, downloadToolCallExport } from "./exportToolCalls";
import {
  EMPTY_FILTERS,
  LATENCIES,
  OUTCOMES,
  RANGES,
  billingLabel,
  formatCallTime,
  formatMicros,
  hasFilters,
  outcomeOf,
  toolCallParams,
  toolLabel,
  virtualServerNames,
} from "./toolCalls";

const ROW_HEIGHT = 40;

// The Key cell: the key's name, else its id or the caller's kind.
const keyLabel = (keyNames, row) =>
  keyNames[row.api_key_id] || row.api_key_id || row.principal_kind || "—";

function OutcomeCell({ value }) {
  const outcome = outcomeOf(value);
  return <GatewayStatusChip tone={outcome.tone} label={outcome.label} />;
}

OutcomeCell.propTypes = { value: PropTypes.string };

function RunCell({ value }) {
  if (!value) return "—";
  return (
    <Link
      component={RouterLink}
      to={paths.dashboard.gateway.runDetail(value)}
      onClick={(event) => event.stopPropagation()}
      typography="s2"
    >
      {value}
    </Link>
  );
}

RunCell.propTypes = { value: PropTypes.string };

// Every tools/call through the org's MCP endpoints, newest first: who called
// which tool on which server, how it ended, how long it took and what it cost.
// Filters go to the API; a row opens the call's detail.
export default function ToolCallsTab({ gatewayId }) {
  const { explorer, download } = useMCPToolCallFeatures(gatewayId);
  // Unmount the entire explorer (including menu/detail/trace state) on OFF.
  return explorer ? (
    <EnabledToolCallsTab gatewayId={gatewayId} download={download} />
  ) : null;
}

function EnabledToolCallsTab({ gatewayId, download }) {
  const features = useMCPReleaseFeatures(gatewayId);
  // Recorded virtual servers stay readable while the status loads or fails,
  // and after the switch is turned off.
  const virtualServers =
    (features.shown?.virtualServers ?? features.virtualServers) ||
    features.off?.virtualServers === true;
  // A link may open the list on one server (?server=<id>, from a server's
  // detail).
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState(() => ({
    ...EMPTY_FILTERS,
    serverId: searchParams.get("server") || "",
  }));
  // One anchor for the displayed query and its export; pagination keeps it.
  const [timeAnchor, setTimeAnchor] = useState(() => new Date());
  const [openId, setOpenId] = useState(null);
  const [exportAnchor, setExportAnchor] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  const { data: config } = useGatewayConfig(gatewayId);
  const { data: tools } = useMCPTools(gatewayId);
  const { data: keys } = useApiKeys(gatewayId);

  const params = useMemo(
    () => toolCallParams(filters, timeAnchor),
    [filters, timeAnchor],
  );
  const {
    data,
    isLoading,
    isError,
    refetch,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useMCPToolCalls(params, { gatewayId });

  const rows = useMemo(
    () => (data?.pages || []).flatMap((page) => page?.results || []),
    [data],
  );

  const mcp = config?.mcp;
  const vsNames = useMemo(() => virtualServerNames(mcp), [mcp]);
  const keyNames = useMemo(() => {
    const names = {};
    (keys || []).forEach((key) => {
      const id = key.gatewayKeyId || key.gateway_key_id;
      if (id) names[id] = key.name || id;
    });
    return names;
  }, [keys]);

  const serverOptions = useMemo(
    () =>
      Object.keys(mcp?.servers || {})
        .sort()
        .map((id) => ({ value: id, label: id })),
    [mcp],
  );
  const toolOptions = useMemo(
    () =>
      (tools || [])
        .filter(
          (tool) => !filters.serverId || tool.server_id === filters.serverId,
        )
        .map((tool) => tool.name)
        .sort()
        .map((name) => ({ value: name, label: name })),
    [tools, filters.serverId],
  );

  const setFilter = useCallback(
    (key) => (value) => {
      setTimeAnchor(new Date());
      setFilters((prev) => ({
        ...prev,
        [key]: value,
        // A tool belongs to one server: a new server clears the tool.
        ...(key === "serverId" ? { tool: "" } : {}),
      }));
    },
    [],
  );

  const refresh = () => {
    const now = new Date();
    setTimeAnchor(now);
    // A changed bound starts a new first-page query. All time (or a second
    // click in the same instant) still needs an explicit refetch.
    if (toolCallParams(filters, now).from === params.from) refetch();
  };

  const columnDefs = useMemo(
    () =>
      [
        {
          headerName: "Time",
          colId: "time",
          field: "started_at",
          valueFormatter: ({ value }) => formatCallTime(value),
          minWidth: 160,
          flex: 1.1,
        },
        {
          headerName: "Tool",
          colId: "tool",
          valueGetter: ({ data: row }) => toolLabel(row),
          minWidth: 160,
          flex: 1.4,
        },
        {
          headerName: "Server",
          colId: "server",
          field: "server_id",
          minWidth: 120,
        },
        {
          headerName: "Virtual server",
          colId: "virtual",
          valueGetter: ({ data: row }) =>
            row.virtual_server_id
              ? vsNames[row.virtual_server_id] || row.virtual_server_id
              : "—",
          minWidth: 150,
        },
        {
          headerName: "Outcome",
          colId: "outcome",
          field: "outcome",
          cellRenderer: OutcomeCell,
          minWidth: 120,
        },
        {
          headerName: "Latency",
          colId: "latency",
          field: "latency_ms",
          valueFormatter: ({ value }) => formatLatency(value),
          minWidth: 110,
          flex: 0.7,
        },
        {
          headerName: "Key",
          colId: "key",
          valueGetter: ({ data: row }) => keyLabel(keyNames, row),
          // The cell cuts a long key name; hovering shows all of it.
          tooltipValueGetter: ({ data: row }) => keyLabel(keyNames, row),
          minWidth: 130,
        },
        {
          headerName: "Billing mode",
          colId: "billing",
          field: "billing_mode",
          valueFormatter: ({ value }) => billingLabel(value),
          minWidth: 120,
          flex: 0.8,
        },
        {
          headerName: "Cost",
          colId: "cost",
          field: "billed_cost_micros",
          valueFormatter: ({ value }) => formatMicros(value),
          minWidth: 90,
          flex: 0.6,
        },
        {
          headerName: "Agent run",
          colId: "run",
          field: "agent_run_id",
          cellRenderer: RunCell,
          minWidth: 120,
          flex: 0.8,
        },
      ].filter((column) => virtualServers || column.colId !== "virtual"),
    [vsNames, keyNames, virtualServers],
  );

  useEffect(() => {
    if (!download) {
      setExportAnchor(null);
      setExportError("");
    }
  }, [download]);

  const runExport = async (format) => {
    if (!download) return;
    setExportAnchor(null);
    setExportError("");
    setExporting(true);
    try {
      await downloadToolCallExport(filters, format, timeAnchor);
    } catch (error) {
      setExportError(error?.message || "The export could not be made.");
    } finally {
      setExporting(false);
    }
  };

  const filtered = hasFilters(filters);
  const open = rows.find((row) => row.id === openId) || null;

  let body;
  if (isError) {
    body = (
      <GatewayEmptyState
        icon="monitoring"
        title="Tool calls could not be loaded"
        description="The tool-call log did not answer. Check the connection and try again."
        actionLabel="Try again"
        onAction={() => refetch()}
      />
    );
  } else if (!isLoading && rows.length === 0) {
    body = filtered ? (
      <GatewayEmptyState
        variant="no-results"
        title="No tool calls match"
        description="No recorded call matches these filters."
        actionLabel="Clear filters"
        onAction={() => setFilters(EMPTY_FILTERS)}
      />
    ) : (
      <GatewayEmptyState
        icon="mcp"
        title="No tool calls yet"
        description="Every call an agent or MCP client makes through the gateway's MCP endpoint is recorded here, allowed or denied."
      />
    );
  } else {
    const height = Math.min(
      Math.max(ROW_HEIGHT * (rows.length || 6) + 42, 240),
      640,
    );
    body = (
      <>
        <GatewayGrid
          rowData={isLoading ? undefined : rows}
          loading={isLoading}
          columnDefs={columnDefs}
          stateKey="gateway-mcp-tool-calls"
          getRowId={(params) => String(params.data.id)}
          onRowClicked={(event) => setOpenId(event.data.id)}
          rowStyle={{ cursor: "pointer" }}
          height={height}
        />
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          <Typography typography="s2" color="text.secondary">
            {isLoading
              ? "Loading tool calls"
              : `${rows.length} ${rows.length === 1 ? "call" : "calls"} shown${hasNextPage ? ", newest first" : ""}`}
          </Typography>
          {hasNextPage && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {isFetchingNextPage ? "Loading" : "Load more"}
            </Button>
          )}
        </Box>
      </>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <GatewayFilterBar
        actions={
          <>
            {download && (
              <>
                <Button
                  size="small"
                  variant="outlined"
                  aria-haspopup="menu"
                  disabled={exporting}
                  onClick={(event) => setExportAnchor(event.currentTarget)}
                >
                  {exporting ? "Exporting" : "Export"}
                </Button>
                <Menu
                  anchorEl={exportAnchor}
                  open={Boolean(exportAnchor)}
                  onClose={() => setExportAnchor(null)}
                  disableScrollLock
                >
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ px: 2, py: 0.75, display: "block", maxWidth: 260 }}
                  >
                    The calls these filters show, without arguments, results or
                    error text.
                  </Typography>
                  {EXPORT_FORMATS.map(({ value, label }) => (
                    <MenuItem
                      key={value}
                      onClick={() => runExport(value)}
                      sx={{ typography: "s2" }}
                    >
                      {label}
                    </MenuItem>
                  ))}
                </Menu>
              </>
            )}
            <Button
              size="small"
              variant="outlined"
              startIcon={<McpIcon name="refresh" size={16} />}
              onClick={refresh}
            >
              Refresh
            </Button>
          </>
        }
      >
        {[
          ["runId", "Run ID"],
          ["agentId", "Agent ID"],
          ["apiKeyId", "Key ID"],
        ].map(([key, label]) => (
          <TextField
            key={key}
            label={label}
            size="small"
            value={filters[key]}
            onChange={(event) => setFilter(key)(event.target.value)}
            sx={{
              flex: { xs: "1 1 140px", sm: "0 1 160px" },
              "& .MuiOutlinedInput-root": { height: "30px", typography: "s2" },
            }}
          />
        ))}
        <FilterSelect
          label="Billing mode"
          value={filters.billingMode}
          onChange={setFilter("billingMode")}
          options={["byok", "managed"].map((value) => ({
            value,
            label: billingLabel(value),
          }))}
        />
        <FilterSelect
          label="Server"
          value={filters.serverId}
          onChange={setFilter("serverId")}
          options={serverOptions}
          allLabel="All servers"
        />
        <FilterSelect
          label="Tool"
          value={filters.tool}
          onChange={setFilter("tool")}
          options={toolOptions}
          allLabel="All tools"
          minWidth={180}
        />
        <FilterSelect
          label="Outcome"
          value={filters.outcome}
          onChange={setFilter("outcome")}
          options={OUTCOMES.map(({ value, label }) => ({ value, label }))}
        />
        <FilterSelect
          label="Latency"
          value={filters.minLatency}
          onChange={setFilter("minLatency")}
          options={LATENCIES}
          allLabel="Any latency"
          minWidth={130}
        />
        <FilterSelect
          label="Time"
          value={filters.range}
          onChange={setFilter("range")}
          options={RANGES.map(({ value, label }) => ({ value, label }))}
          allLabel={null}
          minWidth={140}
        />
      </GatewayFilterBar>

      {download && exportError && (
        <Typography typography="s2" color="error.main" role="alert">
          {exportError}
        </Typography>
      )}

      {body}

      <ToolCallDrawer
        gatewayId={gatewayId}
        call={open}
        onClose={() => setOpenId(null)}
        virtualServerName={open ? vsNames[open.virtual_server_id] : undefined}
        keyName={open ? keyNames[open.api_key_id] : undefined}
      />
    </Box>
  );
}

EnabledToolCallsTab.propTypes = {
  gatewayId: PropTypes.string,
  download: PropTypes.bool,
};

ToolCallsTab.propTypes = {
  gatewayId: PropTypes.string,
};
