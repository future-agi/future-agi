import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { GatewayEmptyState } from "../components/frame";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useMCPTools } from "./hooks/useMCPConfig";
import ToolsGrid from "./servers/ToolsGrid";
import RuntimeNotice from "./runtimeNotice";
import { MCP_SAVED_SERVERS, MCP_SAVED_SERVER_NOTICE } from "./runtimeStrings";

// The off overview deliberately has no live stats, virtual endpoints, catalogue
// or call-history components/hooks. W07/W14 capabilities cannot expand it.
export function SavedOverview({ gatewayId }) {
  const navigate = useNavigate();
  const { canEdit } = useGatewayAdmin();
  const {
    data: config,
    isLoading,
    isError,
    refetch,
  } = useGatewayConfig(gatewayId);
  if (isLoading) return <Skeleton variant="rounded" height={132} />;
  if (isError)
    return (
      <GatewayEmptyState
        icon="monitoring"
        title="Servers could not be loaded"
        description="The organization's gateway config did not load. Try again."
        actionLabel="Try again"
        onAction={() => refetch?.()}
      />
    );
  const servers = Object.keys(config?.mcp?.servers || {}).sort();
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5, py: 2 }}>
      <RuntimeNotice />
      {servers.length === 0 ? (
        <GatewayEmptyState
          icon="mcp"
          title="No MCP servers yet"
          description={MCP_SAVED_SERVER_NOTICE}
          actionLabel="Add server"
          onAction={
            canEdit
              ? () =>
                  navigate(`${paths.dashboard.gateway.mcp}?tab=servers&add=1`)
              : undefined
          }
        />
      ) : (
        <>
          <Typography component="h2" typography="s1">
            {MCP_SAVED_SERVERS}
          </Typography>
          {servers.map((id) => (
            <Typography key={id} typography="s2">
              {id}
            </Typography>
          ))}
        </>
      )}
      <Box>
        <Button
          onClick={() => navigate(`${paths.dashboard.gateway.mcp}?tab=servers`)}
        >
          All servers
        </Button>
      </Box>
    </Box>
  );
}
SavedOverview.propTypes = { gatewayId: PropTypes.string };

// Tools has its own baseline tab only in the off allowlist. MCP-on legacy links
// continue to resolve to Servers, without changing that page's tab structure.
export function SavedTools({ gatewayId }) {
  const { data } = useMCPTools(gatewayId, { enabled: false });
  return <ToolsGrid tools={data || []} runtimeOff />;
}
SavedTools.propTypes = { gatewayId: PropTypes.string };
