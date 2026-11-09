import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { DetailRow, GatewayStatusChip } from "../../components/frame";
import { GATEWAY_MONO } from "../../utils/fonts";
import { denialText, isSignInCredential } from "../catalog-model";
import ServedToolsPanel from "../../mcp/servers/ServedToolsPanel";

// Catalogue account facts and historical/declared posture stay separate from
// the runtime-backed served inventory below. The historical tool rules never
// supply a live count or a fallback when the served read fails.
const SHARED = "One shared account for the organization";
const OWN = "Each person's own account";
const OWNERS = "Owners and Admins of this organization";

// The account modes in play: the one the server was added in, else the
// ones the provider can be added in. The API lists a provider only in a
// mode it can run (CN9's plan_for).
function modesOf(provider, server) {
  if (server && isSignInCredential(provider?.credential)) {
    return server.credential_mode === "per_user" ? ["user"] : ["agent"];
  }
  return provider?.account_modes || [];
}

function accountText(modes) {
  if (modes.includes("user")) {
    return modes.includes("agent") ? `${OWN}, or ${SHARED.toLowerCase()}` : OWN;
  }
  return modes.includes("agent") ? SHARED : null;
}

function whoText(provider, modes) {
  if (!isSignInCredential(provider?.credential) || !modes.includes("user")) {
    return OWNERS;
  }
  return modes.includes("agent")
    ? "Owners and Admins add it, for each person's own account or one shared account."
    : "Owners and Admins add it. Each person then signs in with their own account.";
}

export default function AccessControl({
  appName,
  provider,
  server,
  gatewayId,
  canManage = false,
}) {
  const headingId = useId();
  const listed = Array.isArray(server?.tools);
  const tools = listed ? server.tools : provider?.tools || [];
  const total = tools.length;
  const allowed = tools.filter(
    (tool) => tool.gateway_posture === "allow",
  ).length;
  const modes = modesOf(provider, server);
  const account = accountText(modes);
  const virtualServer = server?.virtual_server?.slug;

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Typography
        id={headingId}
        component="h3"
        typography="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        Access control
      </Typography>
      <Box>
        <DetailRow label="Who can connect">
          {whoText(provider, modes)}
        </DetailRow>
        {account && <DetailRow label="Account">{account}</DetailRow>}
        {virtualServer && (
          <DetailRow label="Virtual server" mono>
            {virtualServer}
          </DetailRow>
        )}
        <Typography component="h4" typography="s1" color="text.secondary">
          Historical / declared posture — not live
        </Typography>
        {total > 0 && (
          <DetailRow label="Tools">{`${allowed} of ${total} tools allowed`}</DetailRow>
        )}
        {total === 0 && provider && (
          <DetailRow label="Tools">
            Read-only tools are allowed. Tools that change or delete data are
            blocked.
          </DetailRow>
        )}
      </Box>
      {total > 0 && (
        <Box
          component="ul"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {tools.map((tool, index) => {
            const isAllowed = tool.gateway_posture === "allow";
            const note = isAllowed
              ? tool.title || tool.description
              : denialText(tool.reason);
            return (
              <Box
                component="li"
                key={tool.name}
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  px: 1.5,
                  py: 1,
                  borderTop: index ? 1 : 0,
                  borderColor: "divider",
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    typography="s2_1"
                    color="text.primary"
                    sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
                  >
                    {tool.name}
                  </Typography>
                  {note && (
                    <Typography typography="s2" color="text.secondary">
                      {note}
                    </Typography>
                  )}
                </Box>
                <GatewayStatusChip
                  tone={isAllowed ? "healthy" : "neutral"}
                  label={isAllowed ? "Allowed" : "Blocked"}
                  showIcon={false}
                />
              </Box>
            );
          })}
        </Box>
      )}
      {total > 0 && !listed && (
        <Typography typography="s2" color="text.secondary">
          These are the catalog&apos;s rules, set on the app&apos;s virtual
          server when it is added. A later edit to that virtual server is not
          shown here.
        </Typography>
      )}
      {total > 0 && listed && (
        <Typography typography="s2" color="text.secondary">
          {`These are the tools ${appName} listed when it was added, and the gateway's rule for each, set on the app's virtual server. A later edit to that virtual server is not shown here.`}
        </Typography>
      )}
      {canManage && gatewayId && server?.virtual_server && (
        <ServedToolsPanel
          key={server.server_id}
          gatewayId={gatewayId}
          serverId={server.server_id}
          virtualServer={server.virtual_server.id || server.virtual_server.slug}
        />
      )}
    </Box>
  );
}

AccessControl.propTypes = {
  appName: PropTypes.string,
  gatewayId: PropTypes.string,
  canManage: PropTypes.bool,
  provider: PropTypes.shape({
    account_modes: PropTypes.arrayOf(PropTypes.string),
    tools: PropTypes.array,
  }),
  server: PropTypes.shape({
    credential_mode: PropTypes.string,
    server_id: PropTypes.string,
    virtual_server: PropTypes.shape({
      id: PropTypes.string,
      slug: PropTypes.string,
    }),
    tools: PropTypes.array,
  }),
};
