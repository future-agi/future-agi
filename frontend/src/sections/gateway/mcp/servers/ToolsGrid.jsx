import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CachedToolDialog from "./CachedToolDialog";

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

// A native button also lets keyboard users inspect cached metadata.
// NEW RL-1009 accessible action: Inspect <tool name>.
function CachedToolName({ data, context }) {
  return (
    <Button
      size="small"
      aria-label={`Inspect ${data.name}`}
      onClick={() => context.inspect(data)}
    >
      {data.name}
    </Button>
  );
}
CachedToolName.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
};

const ROW_HEIGHT = 40;

// Unfiltered administrative inventory, including quarantine review entries.
// The server drawer's separate runtime read is authoritative for served tools.
export default function ToolsGrid({
  tools,
  isLoading,
  isError = false,
  runtimeOff = false,
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
          `${tool.name} ${tool.upstream_name || ""}`
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
        cellRenderer: runtimeOff ? CachedToolName : undefined,
        minWidth: 240,
        flex: 1.6,
      },
      {
        headerName: "Server",
        colId: "server",
        field: "server_id",
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
        headerName: "State",
        colId: "state",
        field: "quarantined",
        cellRenderer: StateCell,
        minWidth: 130,
        flex: 0.7,
      },
    ],
    [runtimeOff],
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
              onRowClicked={
                runtimeOff ? ({ data }) => setInspectedTool(data) : undefined
              }
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
    </Box>
  );
}

ToolsGrid.propTypes = {
  runtimeOff: PropTypes.bool,
  tools: PropTypes.array,
  isLoading: PropTypes.bool,
  // The tools read failed (a gateway that is down); with MCP off for the
  // deployment the read answers an empty list, not an error.
  isError: PropTypes.bool,
};
