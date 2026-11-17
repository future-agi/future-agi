import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
} from "../../components/frame";
import { useMCPResources } from "../hooks/useMCPConfig";
import { emptyListText } from "../utils";
import RuntimeNotice from "../runtimeNotice";

const ROW_HEIGHT = 40;

// The resources the org's MCP servers expose (resources/list through the
// gateway), on the FA grid.
const ResourcesTab = ({ gatewayId, runtimeOff = false }) => {
  const { data, isLoading, isError } = useMCPResources(gatewayId, {
    enabled: !runtimeOff,
  });
  const [search, setSearch] = useState("");

  const resources = useMemo(() => data || [], [data]);
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return resources;
    return resources.filter((r) =>
      [r.uri, r.name, r.description, r.server_id]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [resources, search]);

  const columnDefs = useMemo(
    () => [
      {
        headerName: "URI",
        colId: "uri",
        field: "uri",
        minWidth: 240,
        flex: 1.6,
      },
      {
        headerName: "Name",
        colId: "name",
        field: "name",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 160,
      },
      {
        headerName: "Server",
        colId: "server",
        field: "server_id",
        minWidth: 140,
      },
      {
        headerName: "Type",
        colId: "type",
        field: "mimeType",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 130,
      },
      {
        headerName: "Description",
        colId: "description",
        field: "description",
        valueFormatter: ({ value }) => value || "—",
        tooltipField: "description",
        minWidth: 200,
        flex: 1.4,
      },
    ],
    [],
  );

  // A read that failed is not an empty list: it says so (with MCP off for
  // the deployment the read answers an empty list, not an error).
  if (isError && !runtimeOff) {
    return (
      <GatewayEmptyState
        icon="inbox"
        title={emptyListText(true, [], "resources")}
        description="Nothing is shown until the gateway answers."
      />
    );
  }

  if (!runtimeOff && !isLoading && resources.length === 0) {
    return (
      <GatewayEmptyState
        icon="inbox"
        title="No resources"
        description="None of the organization's MCP servers lists a resource. Servers that expose files or documents show them here."
      />
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {runtimeOff && <RuntimeNotice />}
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search resources"
      />
      {!isLoading && shown.length === 0 ? (
        <GatewayEmptyState
          variant={search ? "no-results" : "no-data"}
          title="No resources in this view."
          description={
            runtimeOff && resources.length === 0
              ? "No previously loaded resources are available."
              : undefined
          }
          actionLabel="Clear search"
          onAction={search ? () => setSearch("") : undefined}
        />
      ) : (
        <GatewayGrid
          rowData={isLoading ? undefined : shown}
          loading={isLoading}
          columnDefs={columnDefs}
          stateKey="gateway-mcp-resources"
          getRowId={(params) => `${params.data.server_id}:${params.data.uri}`}
          height={Math.min(
            Math.max(ROW_HEIGHT * Math.max(shown.length, 3) + 42, 200),
            560,
          )}
        />
      )}
    </Box>
  );
};

ResourcesTab.propTypes = {
  runtimeOff: PropTypes.bool,
  gatewayId: PropTypes.string,
};

export default ResourcesTab;
