import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
} from "../../components/frame";
import { useApiKeys } from "../../keys/hooks/useApiKeys";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useMCPServedTools } from "../hooks/useMCPServedTools";
import FilterSelect from "../components/FilterSelect";
import SectionTitle from "../components/SectionTitle";

const columns = [
  { field: "name", headerName: "Served name", flex: 1, minWidth: 180 },
  {
    field: "upstream_name",
    headerName: "Original name",
    flex: 1,
    minWidth: 150,
  },
];
const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

function identityOf(tool) {
  const id = tool.identity;
  if (
    !["composio", "pipedream"].includes(id?.platform) ||
    id.source !== "from server configuration" ||
    !id.toolkit_slug ||
    !id.toolkit_name
  ) {
    return { key: "other", label: "Other", platform: "other" };
  }
  return {
    key: `${id.platform}/${id.toolkit_slug}`,
    label: id.toolkit_name,
    platform: id.platform,
  };
}

// Both the server drawer and catalogue use this runtime read. The server
// selector is a context, not a client-side approximation of access policy.
export default function ServedToolsPanel({
  gatewayId,
  serverId,
  virtualServer,
}) {
  const [selectedVS, setSelectedVS] = useState("");
  const [keyId, setKeyId] = useState("");
  const [search, setSearch] = useState("");
  const [toolkit, setToolkit] = useState("");
  const vs = virtualServer || selectedVS;
  const { data, isPending, isError, refetch } = useMCPServedTools(
    gatewayId,
    vs,
    keyId,
    serverId,
  );
  const { data: keys, isError: keysError } = useApiKeys(gatewayId, {
    meta: { errorHandled: true },
  });
  const { data: config } = useGatewayConfig(
    virtualServer ? undefined : gatewayId,
  );
  const servers = Object.entries(config?.mcp?.virtual_servers || {})
    .filter(
      ([, value]) =>
        !serverId ||
        value.members?.some((member) => member.split("@")[0] === serverId),
    )
    .map(([slug, value]) => ({
      value: value.id || slug,
      label: value.title || slug,
    }))
    .sort((a, b) => compare(a.label, b.label));
  const own = useMemo(() => data || [], [data]);
  const groups = useMemo(() => {
    const grouped = new Map();
    for (const tool of own) {
      const id = identityOf(tool);
      if (!grouped.has(id.key)) grouped.set(id.key, { ...id, tools: [] });
      const group = grouped.get(id.key);
      // Different display labels for one slug must not make ordering depend
      // on the upstream list order. Identity remains platform + slug.
      if (compare(id.label, group.label) < 0) group.label = id.label;
      group.tools.push(tool);
    }
    return [...grouped.values()]
      .sort((a, b) => {
        if (a.key === "other") return 1;
        if (b.key === "other") return -1;
        return compare(a.label, b.label) || compare(a.key, b.key);
      })
      .map((group) => ({
        ...group,
        tools: group.tools.sort((a, b) => compare(a.name, b.name)),
      }));
  }, [own]);
  const term = search.trim().toLowerCase();
  const shown = groups
    .filter((group) => !toolkit || group.key === toolkit)
    .map((group) => ({
      ...group,
      tools: group.tools.filter(
        (tool) =>
          !term ||
          `${group.label} ${group.key} ${tool.name} ${tool.upstream_name}`
            .toLowerCase()
            .includes(term),
      ),
    }))
    .filter((group) => group.tools.length);

  return (
    <Box
      component="section"
      aria-label="Live served tools"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <SectionTitle
        title={`Served tools${!isPending && !isError ? ` (${own.length})` : ""}`}
        note={
          vs
            ? "Served by this virtual server; select a key to apply its ACL."
            : "Served through the organization's default endpoint; select a virtual server to inspect its view."
        }
      />
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search served tools"
      >
        {!virtualServer && (
          <FilterSelect
            label="Virtual server"
            value={selectedVS}
            onChange={setSelectedVS}
            options={servers}
            allLabel="Org default"
          />
        )}
        <FilterSelect
          label="As seen by key"
          value={keyId}
          onChange={setKeyId}
          options={(keys || [])
            .filter((key) => key.gatewayKeyId && key.status === "active")
            .map((key) => ({
              value: key.gatewayKeyId,
              label: key.name || key.gatewayKeyId,
            }))}
          allLabel="No key restriction"
        />
        <FilterSelect
          label="Group"
          value={toolkit}
          onChange={setToolkit}
          options={groups.map((group) => ({
            value: group.key,
            label:
              group.platform === "other"
                ? group.label
                : `${group.label} — from server configuration`,
          }))}
          allLabel="All groups"
        />
      </GatewayFilterBar>
      {keysError && (
        <Typography typography="s2" color="text.secondary">
          Keys could not be loaded. The current context has not changed.
        </Typography>
      )}
      {isError ? (
        <GatewayEmptyState
          title="Served tools could not be loaded"
          description="No inventory fallback is shown. Check the virtual server and key, then retry."
          actionLabel="Try again"
          onAction={() => refetch()}
        />
      ) : isPending ? (
        <Typography role="status" typography="s2">
          Loading served tools
        </Typography>
      ) : own.length === 0 ? (
        <GatewayEmptyState
          title="No tools served"
          description="This context currently lists no tools from this server. Search-mode virtual servers list gateway search tools, not individual upstream tools."
        />
      ) : shown.length === 0 ? (
        <GatewayEmptyState
          variant="no-results"
          title="No served tools match"
          actionLabel="Clear filters"
          onAction={() => {
            setSearch("");
            setToolkit("");
          }}
        />
      ) : (
        shown.map((group) => (
          <Box key={group.key}>
            <SectionTitle
              title={`${group.label} (${group.tools.length})`}
              component="h3"
              note={
                group.platform === "other"
                  ? "No unambiguous group from server configuration; tool names alone do not establish identity."
                  : `${group.platform} — from server configuration`
              }
            />
            <GatewayGrid
              rowData={group.tools}
              columnDefs={columns}
              getRowId={({ data: row }) => row.name}
              stateKey={`mcp-served-${group.key}`}
              height={Math.min(
                360,
                Math.max(160, group.tools.length * 40 + 42),
              )}
            />
          </Box>
        ))
      )}
      {!isPending && !isError && (
        <Typography typography="s2" color="text.secondary">
          This is tools/list availability, not proof that credentials or a tool
          execution will succeed.
        </Typography>
      )}
    </Box>
  );
}
ServedToolsPanel.propTypes = {
  gatewayId: PropTypes.string,
  serverId: PropTypes.string,
  virtualServer: PropTypes.string,
};
