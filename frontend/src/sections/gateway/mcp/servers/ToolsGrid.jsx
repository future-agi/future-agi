import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tooltip from "@mui/material/Tooltip";
import CachedToolDialog from "./CachedToolDialog";
import ToolDetailsDrawer from "./ToolDetailsDrawer";
import { deprecationHint, toolFacts } from "./toolDetails";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import FilterSelect from "../components/FilterSelect";
import SectionTitle from "../components/SectionTitle";
import { emptyListText } from "../utils";
import RuntimeNotice from "../runtimeNotice";

function StateCell({ value }) {
  return value ? (
    <GatewayStatusChip tone="warning" label="Quarantined" />
  ) : (
    <GatewayStatusChip tone="neutral" label="In inventory" />
  );
}

StateCell.propTypes = { value: PropTypes.bool };

// A native button also lets keyboard users open a tool (Inspect <tool name>):
// its live facts while MCP is on, its saved copy while MCP is off. The
// tool's version and a Deprecated mark sit beside its name.
function ToolName({ data, context }) {
  const facts = toolFacts(data);
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        minWidth: 0,
        height: "100%",
      }}
    >
      <Button
        size="small"
        aria-label={`Inspect ${data.name}`}
        onClick={() => context.inspect(data)}
        sx={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          textDecoration: facts.deprecated ? "line-through" : "none",
          justifyContent: "flex-start",
        }}
      >
        {data.name}
      </Button>
      {facts.version && (
        <GatewayStatusChip
          tone="neutral"
          label={facts.version}
          showIcon={false}
        />
      )}
      {facts.deprecated && (
        <Tooltip title={deprecationHint(facts)}>
          <span>
            <GatewayStatusChip tone="warning" label="Deprecated" />
          </span>
        </Tooltip>
      )}
    </Box>
  );
}
ToolName.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
};

const ROW_HEIGHT = 40;

// Per-tool stats columns, when a caller has them, go between Description and
// State; the list itself adds none (it has no stats to show).
const NO_STATS_COLUMNS = [];

// Unfiltered administrative inventory, including quarantine review entries.
// The server drawer's separate runtime read is authoritative for served tools.
export default function ToolsGrid({
  tools,
  isLoading,
  isError = false,
  runtimeOff = false,
  statsColumns = NO_STATS_COLUMNS,
}) {
  const [search, setSearch] = useState("");
  const [serverId, setServerId] = useState("");
  const [inspectedTool, setInspectedTool] = useState(null);
  const context = useMemo(() => ({ inspect: setInspectedTool }), []);

  const servers = useMemo(
    () => [...new Set((tools || []).map((tool) => tool.server_id))].sort(),
    [tools],
  );
  const shown = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (tools || []).filter(
      (tool) =>
        (!serverId || tool.server_id === serverId) &&
        (!term ||
          [tool.name, tool.upstream_name, toolFacts(tool).description]
            .join(" ")
            .toLowerCase()
            .includes(term)),
    );
  }, [tools, search, serverId]);

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Tool",
        colId: "name",
        field: "name",
        cellRenderer: ToolName,
        // A long name is cut by the column: the full one on hover.
        tooltipField: "name",
        minWidth: 240,
        flex: 1.6,
      },
      {
        headerName: "Server",
        colId: "server",
        field: "server_id",
        tooltipField: "server_id",
        minWidth: 140,
      },
      {
        headerName: "Upstream name",
        colId: "upstream",
        field: "upstream_name",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 160,
      },
      {
        headerName: "Description",
        colId: "description",
        field: "description",
        valueFormatter: ({ value }) =>
          (typeof value === "string" && value.trim()) || "—",
        // A long description is cut by the column: the full one on hover.
        tooltipField: "description",
        minWidth: 220,
        flex: 2,
      },
      ...statsColumns,
      {
        headerName: "State",
        colId: "state",
        field: "quarantined",
        cellRenderer: StateCell,
        minWidth: 130,
        flex: 0.7,
      },
    ],
    [statsColumns],
  );

  const empty = !isLoading && !isError && (tools || []).length === 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <SectionTitle
        title={`Tools${tools?.length ? ` (${tools.length})` : ""}`}
        note={
          runtimeOff
            ? undefined
            : "Administrative inventory, before virtual-server and key policy. Open a server to inspect its live served tools. Quarantined tools remain here for review."
        }
      />
      {runtimeOff && <RuntimeNotice />}
      {isError && !runtimeOff ? (
        <GatewayEmptyState
          icon="mcp"
          title={emptyListText(true, [], "tools")}
          description="Nothing is shown until the gateway answers."
        />
      ) : empty && !runtimeOff ? (
        <GatewayEmptyState
          icon="mcp"
          title="No tools yet"
          description="Tools appear once the gateway connects to a server and lists them."
        />
      ) : (
        <>
          <GatewayFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search tools"
          >
            <FilterSelect
              label="Server"
              value={serverId}
              onChange={setServerId}
              options={servers.map((id) => ({ value: id, label: id }))}
              allLabel="All servers"
            />
          </GatewayFilterBar>
          {!isLoading && shown.length === 0 ? (
            <GatewayEmptyState
              variant={search || serverId ? "no-results" : "no-data"}
              title="No tools in this view."
              description={
                runtimeOff && (tools || []).length === 0
                  ? "No previously loaded tools are available."
                  : undefined
              }
              actionLabel="Clear filters"
              onAction={
                search || serverId
                  ? () => {
                      setSearch("");
                      setServerId("");
                    }
                  : undefined
              }
            />
          ) : (
            <GatewayGrid
              rowData={isLoading ? undefined : shown}
              loading={isLoading}
              columnDefs={columnDefs}
              context={context}
              onRowClicked={({ data }) => setInspectedTool(data)}
              stateKey="gateway-mcp-tools"
              getRowId={(params) => params.data.name}
              height={Math.min(
                Math.max(ROW_HEIGHT * Math.max(shown.length, 3) + 42, 200),
                520,
              )}
            />
          )}
        </>
      )}
      <CachedToolDialog
        tool={runtimeOff ? inspectedTool : null}
        onClose={() => setInspectedTool(null)}
      />
      <ToolDetailsDrawer
        tool={runtimeOff ? null : inspectedTool}
        onClose={() => setInspectedTool(null)}
      />
    </Box>
  );
}

ToolsGrid.propTypes = {
  runtimeOff: PropTypes.bool,
  // Extra columns for per-tool call stats (AG Grid column definitions).
  statsColumns: PropTypes.array,
  tools: PropTypes.array,
  isLoading: PropTypes.bool,
  // The tools read failed (a gateway that is down); with MCP off for the
  // deployment the read answers an empty list, not an error.
  isError: PropTypes.bool,
};
