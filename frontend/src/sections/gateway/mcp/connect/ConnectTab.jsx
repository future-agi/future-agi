import { useMCPReleaseFeatures } from "../hooks/useMCPReleaseFeatures";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import GatewayEmptyState from "../../components/GatewayEmptyState";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../../context/useGatewayContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import CopyField from "../components/CopyField";
import SectionTitle from "../components/SectionTitle";
import { clientInstalls } from "./clientInstalls";

export default function ConnectTab({ gatewayId }) {
  const navigate = useNavigate();
  const features = useMCPReleaseFeatures(gatewayId);
  const virtualServers =
    features.shown?.virtualServers ?? features.virtualServers;
  const { gateway } = useGatewayContext();
  // Creating or revoking a gateway key is Owner or Admin only (the keys API's
  // is_gateway_admin); anyone else is sent to the keys they can already see.
  const { canEdit: canCreateKey } = useGatewayAdmin();
  const {
    data: config,
    isLoading,
    isError,
    refetch,
  } = useGatewayConfig(gatewayId);
  const [selected, setSelected] = useState("");
  const [client, setClient] = useState("claude-code");
  const servers = config?.mcp?.virtual_servers || {};
  const slug = Object.hasOwn(servers, selected)
    ? selected
    : Object.keys(servers)[0];
  const generated = useMemo(() => {
    if (!slug || !gateway?.baseUrl) return { rows: [] };
    try {
      return { rows: clientInstalls(gateway.baseUrl, slug) };
    } catch (error) {
      return { rows: [], error: error.message };
    }
  }, [slug, gateway?.baseUrl]);
  if (!virtualServers) return null;
  if (isLoading)
    return <Typography typography="s2">Loading virtual servers…</Typography>;
  if (isError)
    return (
      <Alert severity="error" action={<Button onClick={refetch}>Retry</Button>}>
        Could not load this organization&apos;s gateway configuration.
      </Alert>
    );
  if (!slug)
    return (
      <GatewayEmptyState
        title="Create a virtual server first"
        description={canCreateKey
          ? "Choose the tools and access rules on the Virtual servers tab, then return here for client setup."
          : "Ask an Owner or Admin to create a virtual server and give your key access, then return here for client setup."}
        actionLabel="Create virtual server"
        onAction={canCreateKey
          ? () => navigate(`${paths.dashboard.gateway.mcp}?tab=virtual`)
          : undefined}
      />
    );
  if (generated.error) return <Alert severity="error">{generated.error}</Alert>;
  if (!generated.rows.length)
    return (
      <Alert severity="info">
        The gateway URL is not configured. Set it before creating a client
        install.
      </Alert>
    );
  const row = generated.rows.find((item) => item.id === client);
  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 800 }}
    >
      <SectionTitle
        title="Connect a client"
        note="Generate setup for this organization's virtual server. Nothing is installed or activated until you confirm it in your client."
      />
      {config?.mcp?.enabled !== true && (
        <Alert severity="info">
          MCP is off for this organization. These snippets do not enable it.
        </Alert>
      )}
      <TextField
        select
        size="small"
        label="Virtual server"
        value={slug}
        onChange={(event) => setSelected(event.target.value)}
      >
        {Object.entries(servers).map(([id, server]) => (
          <MenuItem key={id} value={id}>
            {server.title || id}
          </MenuItem>
        ))}
      </TextField>
      <CopyField label="Gateway endpoint" value={row.endpoint} />
      <TextField
        select
        size="small"
        label="Client"
        value={client}
        onChange={(event) => setClient(event.target.value)}
      >
        {generated.rows.map((item) => (
          <MenuItem key={item.id} value={item.id}>
            {item.name}
          </MenuItem>
        ))}
      </TextField>
      <Typography typography="s2" color="text.secondary">
        {row.note}
      </Typography>
      <TextField
        label="Client setup"
        multiline
        minRows={3}
        value={row.snippet}
        InputProps={{ readOnly: true }}
      />
      <CopyField label="Client setup" value={row.snippet} />
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
        {row.deepLink && (
          <Button
            component="a"
            href={row.deepLink}
            variant="contained"
            color="primary"
          >
            Open in {row.name}
          </Button>
        )}
        {canCreateKey ? (
          <Button
            component={RouterLink}
            to={`${paths.dashboard.gateway.keys}?create=1`}
            variant="outlined"
          >
            Create key
          </Button>
        ) : (
          <Button
            component={RouterLink}
            to={paths.dashboard.gateway.keys}
            variant="outlined"
          >
            View existing keys
          </Button>
        )}
        {row.docs && (
          <Button
            component="a"
            href={row.docs}
            target="_blank"
            rel="noopener noreferrer"
          >
            Client documentation
          </Button>
        )}
      </Box>
      {!canCreateKey && (
        <Typography typography="s2" color="text.secondary">
          Key creation and revocation require an Owner or Admin.
        </Typography>
      )}
      <Typography typography="s2" color="text.secondary">
        Use a key from this organization with access to the selected virtual
        server. No secret is embedded here. Configure FUTUREAGI_KEY on your
        device. Copying setup is not proof of a connected client.
      </Typography>
    </Box>
  );
}
ConnectTab.propTypes = { gatewayId: PropTypes.string };
